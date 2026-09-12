"""Review-queue application service: staging + idempotent enqueue.

The service owns dedupe/order and persistence; the *staging port* is a
Telegram adapter that uploads media to the private review chat and returns
durable ``file_id`` values (plus the preview message ids). This keeps the
service PTB-free and unit-testable with a fake stager.

Idempotency contract
--------------------
* same ``idempotency_key`` with a pending/failed row  → reuse that review;
* same key with a recently published row              → idempotent_replay;
* different key but same (target/work/pixiv) recently published
                                                      → duplicate_existing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Optional, Protocol, Tuple

from ..observability import audit
from ..observability.errors import classify as classify_error
from ..storage.sqlite.reviews import NewReview, ReviewRepository

logger = logging.getLogger(__name__)

# The routing layer (run.py) answers 502 for any request slower than
# ROUTER_TIMEOUT_SECONDS (300s default), which reports an IN-FLIGHT submission
# as a transport failure. Staging must therefore finish its own Telegram work
# well inside that budget, and must renew its review row often enough that the
# periodic reconciliation sweep (60s staleness) can tell a live request apart
# from a crashed one.
STAGING_DEADLINE_SECONDS = float(
    os.environ.get("REVIEW_STAGING_DEADLINE_SECONDS", "240")
)
PREPARATION_HEARTBEAT_SECONDS = float(
    os.environ.get("REVIEW_HEARTBEAT_SECONDS", "20")
)


def _actor(user_id: Any = None) -> str:
    return f"telegram_user:{user_id}" if user_id else "api"


def _common(command: QueueCommand) -> dict:
    return {
        "idempotency_key": command.idempotency_key or None,
        "execution_id": audit.execution_id_from_ref(command.source_ref),
        "actor": _actor(command.user_id),
        "target_id": command.target_id or None,
        "work_type": command.work_type or None,
        "pixiv_id": command.pixiv_id or None,
    }


async def _record(event: str, command: QueueCommand, **fields) -> None:
    payload = dict(_common(command))
    payload.update(fields)
    await audit.record_event(
        event, **{k: v for k, v in payload.items()
                  if v is not None or k in ("review_id", "detail")}
    )

PUBLISHED_DEDUP_WINDOW_SECONDS = 7 * 86400
_PIXIV_ID_RE = re.compile(r"pixiv\.net/(?:artworks/|novel/show\.php\?id=)(\d+)")


def pixiv_id_from_link(link: str) -> str:
    if not link:
        return ""
    match = _PIXIV_ID_RE.search(link)
    return match.group(1) if match else ""


def normalize_idempotency_key(user_id: int, value: str, source: str) -> str:
    raw = (value or "").strip()[:240]
    return f"{source}:{user_id}:{raw or uuid.uuid4().hex}"


@dataclass
class QueueCommand:
    user_id: int
    username: str
    tags: str
    title: str
    note: str
    link: str
    anonymous: bool
    spoiler: bool
    source: str = "api"
    idempotency_key: str = ""
    target_id: str = ""
    work_type: str = ""
    pixiv_id: str = ""
    source_label: str = ""
    source_ref: str = ""
    scheduled_at: str = ""
    review_chat_id: str = ""


class StagingPort(Protocol):
    async def stage_local(self, files, *, caption: str, spoiler: bool
                          ) -> Tuple[list, list, List[int], list]: ...

    async def stage_file_ids(self, media, documents, *, caption: str, spoiler: bool
                             ) -> Tuple[list, list, List[int]]: ...

    async def delete_preview_messages(self, message_ids: List[int]) -> None: ...

    async def send_control_message_id(self, *, review_id: int, command: QueueCommand,
                                      preview_message_ids: List[int],
                                      media_count: int, document_count: int) -> int: ...

    async def notify_reused(self, row) -> None: ...

    async def cleanup_files(self, files) -> None: ...


class ReviewQueueService:
    def __init__(self, repo: Optional[ReviewRepository] = None,
                 dedup_window_seconds: int = PUBLISHED_DEDUP_WINDOW_SECONDS,
                 staging_deadline_seconds: float = STAGING_DEADLINE_SECONDS,
                 heartbeat_seconds: float = PREPARATION_HEARTBEAT_SECONDS):
        self._repo = repo or ReviewRepository()
        self._dedup_window = dedup_window_seconds
        self._staging_deadline_seconds = staging_deadline_seconds
        self._heartbeat_seconds = heartbeat_seconds

    # ---- in-flight liveness ---------------------------------------------
    async def _heartbeat_preparing(self, review_id: int) -> None:
        """Renew the review row while this request is genuinely working.

        Without it a submission whose staging outlived the sweep's staleness
        window was indistinguishable from a crash leftover, so the sweeper
        deleted the previews this request had just uploaded and marked the
        review failed (see ReviewRepository.touch_preparing).
        """
        try:
            while True:
                await asyncio.sleep(self._heartbeat_seconds)
                if not await self._repo.touch_preparing(review_id):
                    return  # left 'preparing': this request lost the row
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("审核准备心跳失败: review_id=%s", review_id, exc_info=True)

    @staticmethod
    async def _stop_heartbeat(task: "asyncio.Task") -> None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    # ---- result mapping --------------------------------------------------
    @staticmethod
    def result_from_row(row, *, reused: bool = False,
                        reuse_reason: str = "") -> dict:
        status = "pending_review" if row["status"] == "pending" else row["status"]
        result = {
            "status": status,
            "review_id": row["id"],
            "media_count": len(json.loads(row["media_json"] or "[]")),
            "document_count": len(json.loads(row["documents_json"] or "[]")),
            "reused": reused,
        }
        if reused:
            result["reuse_reason"] = reuse_reason or "idempotent_replay"
            result["matched_idempotency_key"] = row["idempotency_key"]
            result["delivery_status"] = status
        if row["published_message_id"]:
            result["message_id"] = row["published_message_id"]
        return result

    async def enqueue(self, command: QueueCommand, stager: StagingPort,
                      *, files=None, media=None, documents=None) -> dict:
        """Stage then create/reuse a review. Exactly one media source is set."""
        is_local = files is not None
        key = command.idempotency_key
        existing = await self._repo.find_active_by_key(key, self._dedup_window)
        if existing is not None:
            try:
                if existing["target_id"] in (None, "") and command.target_id:
                    await self._repo.backfill_target(existing["id"], command.target_id)
                    existing = await self._repo.get(existing["id"])
                await stager.notify_reused(existing)
                await _record(
                    "submission.duplicate", command,
                    review_id=existing["id"],
                    detail={"reused_id": existing["id"],
                            "status": existing["status"]},
                )
                return self.result_from_row(existing, reused=True)
            finally:
                if is_local:
                    await stager.cleanup_files(files)

        pixiv_id = command.pixiv_id or pixiv_id_from_link(command.link)
        if pixiv_id:
            historical = await self._repo.find_published_work(
                command.target_id, command.work_type, pixiv_id, self._dedup_window
            )
            if historical is not None and historical["idempotency_key"] != key:
                try:
                    return self.result_from_row(
                        historical, reused=True, reuse_reason="duplicate_existing"
                    )
                finally:
                    if is_local:
                        await stager.cleanup_files(files)

        caption = _caption_from_command(command)
        preview_ids: List[int] = []
        try:
            review_id = await self._reserve(
                command, pixiv_id=pixiv_id, stager=stager
            )
        except ReusedReview as reused:
            if is_local:
                await stager.cleanup_files(files)
            reused_id = (reused.result or {}).get("review_id")
            if reused_id is not None:
                await _record(
                    "submission.duplicate", command, review_id=reused_id,
                    detail={"reused_id": reused_id},
                )
            return reused.result

        await _record("review.created", command, review_id=review_id)

        # A live staging request and a crashed one look identical in the ledger
        # (status='preparing', control_message_id NULL), so the reconciliation
        # sweep used to hijack submissions whose staging outlived its staleness
        # window. Renew the row while we work on it, and bound the staging so
        # this request answers before the router's timeout turns it into a
        # synthetic 502 (which hides whether the submission was processed).
        heartbeat = asyncio.create_task(self._heartbeat_preparing(review_id))
        set_deadline = getattr(stager, "set_staging_deadline", None)
        if callable(set_deadline):
            set_deadline(time.monotonic() + self._staging_deadline_seconds)
        media_decisions: list = []
        try:
            # Pass the id list in-out: when staging fails mid-way, ids of
            # already-uploaded previews survive for rollback deletion.
            if is_local:
                staged_media, staged_documents, preview_ids, media_decisions = \
                    await stager.stage_local(
                        files, caption=caption, spoiler=command.spoiler,
                        message_ids=preview_ids,
                    )
            else:
                staged_media, staged_documents, preview_ids = await stager.stage_file_ids(
                    media or [], documents or [],
                    caption=caption, spoiler=command.spoiler,
                    message_ids=preview_ids,
                )
        except RuntimeError as exc:
            message = str(exc)
            await stager.delete_preview_messages(preview_ids)
            await self._repo.mark_preparation_failed(
                review_id, message, clear_previews=True
            )
            await _record(
                "review.failed", command, review_id=review_id,
                error_class=classify_error(exc),
                detail={"stage": "preview_staging", "error": message[:200]},
            )
            if is_local:
                await stager.cleanup_files(files)
            if message.startswith("返回消息数") and "与文件数" in message:
                raise RuntimeError(message.replace("返回消息数", "消息数", 1))
            raise
        except Exception as exc:
            await stager.delete_preview_messages(preview_ids)
            await self._repo.mark_preparation_failed(
                review_id, "preview staging failed", clear_previews=True
            )
            await _record(
                "review.failed", command, review_id=review_id,
                error_class=classify_error(exc),
                detail={"stage": "preview_staging", "error": str(exc)[:200]},
            )
            if is_local:
                await stager.cleanup_files(files)
            raise
        finally:
            if callable(set_deadline):
                set_deadline(None)
            await self._stop_heartbeat(heartbeat)

        try:
            staged = await self._repo.update_staged(
                review_id, media=staged_media, documents=staged_documents,
                preview_message_ids=preview_ids,
            )
            if not staged:
                raise RuntimeError("审核记录在预览完成前被并发修改")
            await _record(
                "review.preview_staged", command, review_id=review_id,
                detail={"media": len(staged_media),
                        "documents": len(staged_documents),
                        "preview_messages": len(preview_ids)},
            )
            if is_local and media_decisions:
                await _record(
                    "media.prepared", command, review_id=review_id,
                    detail={"items": media_decisions},
                )
            control_id = await stager.send_control_message_id(
                review_id=review_id, command=command,
                preview_message_ids=preview_ids,
                media_count=len(staged_media),
                document_count=len(staged_documents),
            )
            await _record("review.control_created", command,
                          review_id=review_id,
                          detail={"control_message_id": control_id})
            if not await self._repo.finalize_control(review_id, control_id):
                await stager.delete_preview_messages([control_id])
                raise RuntimeError("审核控制消息无法绑定到记录")
            await _record("review.pending", command, review_id=review_id)
        except Exception as exc:
            await stager.delete_preview_messages(preview_ids)
            await self._repo.mark_preparation_failed(
                review_id, str(exc), clear_previews=True
            )
            await _record(
                "review.failed", command, review_id=review_id,
                error_class=classify_error(exc),
                detail={"stage": "control_message", "error": str(exc)[:200]},
            )
            raise
        finally:
            if is_local:
                await stager.cleanup_files(files)
        return {
            "status": "pending_review",
            "review_id": review_id,
            "media_count": len(staged_media),
            "document_count": len(staged_documents),
            "reused": False,
        }

    async def _reserve(self, command: QueueCommand, *, pixiv_id: str,
                       stager: StagingPort) -> int:
        """Persist the source of truth before any Telegram preview is sent."""
        new_review = NewReview(
            idempotency_key=command.idempotency_key,
            source=command.source,
            user_id=command.user_id,
            username=command.username,
            title=command.title,
            tags=command.tags,
            note=command.note,
            link=command.link,
            anonymous=command.anonymous,
            spoiler=command.spoiler,
            media=[],
            documents=[],
            review_chat_id=command.review_chat_id,
            review_message_ids=[],
            target_id=command.target_id,
            source_label=command.source_label,
            source_ref=command.source_ref,
            scheduled_at=command.scheduled_at,
            pixiv_id=pixiv_id,
            work_type=command.work_type,
            delivery_target=command.target_id,
            status="preparing",
        )
        for _attempt in range(2):
            try:
                review_id = await self._repo.insert(new_review)
                break
            except Exception as exc:
                # aiosqlite.IntegrityError (unique key) – import lazily.
                import aiosqlite
                if not isinstance(exc, aiosqlite.IntegrityError):
                    raise
                existing = await self._repo.find_active_by_key(
                    command.idempotency_key, self._dedup_window
                )
                if existing is None:
                    existing_by_key = await self._repo_get_by_key(
                        command.idempotency_key
                    )
                    if existing_by_key is None:
                        raise
                    existing = existing_by_key
                if existing["status"] in ("preparing", "pending", "failed"):
                    raise ReusedReview(
                        self.result_from_row(existing, reused=True),
                        [],
                    )
                if (existing["status"] == "published"
                        and (existing["decided_at"] or 0)
                        >= time.time() - self._dedup_window):
                    raise ReusedReview(
                        self.result_from_row(existing, reused=True),
                        [],
                    )
                # rejected/expired/old published: drop old row + previews.
                old_ids = _loads(existing["review_message_ids"])
                await stager.delete_preview_messages(old_ids)
                if existing["control_message_id"]:
                    await stager.delete_preview_messages(
                        [existing["control_message_id"]]
                    )
                await self._repo.delete(existing["id"])
                continue
        else:
            raise RuntimeError("创建审核记录失败：幂等键冲突且无法覆盖旧记录")

        return review_id

    async def reconcile_incomplete(self, stager: StagingPort, *,
                                   stale_seconds: float = 60.0) -> int:
        """Repair crash-left review rows so previews never remain buttonless."""
        rows = await self._repo.list_incomplete(
            cutoff=time.time() - max(0.0, stale_seconds)
        )
        repaired = 0
        for row in rows:
            preview_ids = _loads(row["review_message_ids"])
            media = _loads(row["media_json"])
            documents = _loads(row["documents_json"])
            ready = bool(media or documents)
            command = _command_from_row(row)

            async def record_reconciled(action, *, error=None):
                await audit.record_event(
                    "review.reconciled", review_id=row["id"],
                    actor="system_reconciler",
                    pixiv_id=row["pixiv_id"] or None,
                    work_type=row["work_type"] or None,
                    target_id=row["target_id"] or None,
                    idempotency_key=row["idempotency_key"] or None,
                    execution_id=audit.execution_id_from_ref(row["source_ref"]),
                    **({"error_class": "reconciliation_failed"}
                       if action == "manual_intervention_required" else {}),
                    detail=({"action": action} if error is None
                            else {"action": action, "error": error}),
                )

            if not ready and preview_ids:
                await stager.delete_preview_messages(preview_ids)
                preview_ids = []
                await self._repo.mark_preparation_failed(
                    row["id"], "preview preparation interrupted by process restart",
                    clear_previews=True,
                )
                await record_reconciled("orphan_preview_removed")
            try:
                control_id = await stager.send_control_message_id(
                    review_id=row["id"], command=command,
                    preview_message_ids=preview_ids,
                    media_count=len(media), document_count=len(documents),
                )
                if await self._repo.finalize_control(
                    row["id"], control_id, ready=ready
                ):
                    repaired += 1
                    await record_reconciled("control_message_recreated")
            except Exception as exc:
                logger.warning(
                    "修复审核控制消息失败: review_id=%s", row["id"],
                    exc_info=True,
                )
                await record_reconciled(
                    "manual_intervention_required", error=str(exc)[:200]
                )
        return repaired

    async def _repo_get_by_key(self, key: str):
        # Terminal rows (rejected/expired/old published) are not returned by
        # find_active_by_key; look them up directly for collision replacement.
        from database import db_manager
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM pending_reviews WHERE idempotency_key=?", (key,)
            )
            return await cur.fetchone()


class ReusedReview(Exception):
    """A UNIQUE collision resolved to reusing an existing review."""

    def __init__(self, result: dict, preview_message_ids: list):
        super().__init__("review reused")
        self.result = result
        self.preview_message_ids = preview_message_ids


def _loads(value) -> list:
    try:
        return json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _caption_from_command(command: QueueCommand) -> str:
    from utils.helper_functions import build_caption
    return build_caption({
        "tags": command.tags,
        "title": command.title,
        "note": command.note,
        "link": command.link,
        "anonymous": "true" if command.anonymous else "false",
        "spoiler": "true" if command.spoiler else "false",
        "user_id": command.user_id,
        "username": command.username,
    })


def _command_from_row(row) -> QueueCommand:
    return QueueCommand(
        user_id=row["user_id"], username=row["username"] or "",
        tags=row["tags"] or "", title=row["title"] or "",
        note=row["note"] or "", link=row["link"] or "",
        anonymous=bool(row["anonymous"]), spoiler=bool(row["spoiler"]),
        source=row["source"] or "api",
        idempotency_key=row["idempotency_key"] or "",
        target_id=row["target_id"] or "", work_type=row["work_type"] or "",
        pixiv_id=row["pixiv_id"] or "", source_label=row["source_label"] or "",
        source_ref=row["source_ref"] or "", scheduled_at=row["scheduled_at"] or "",
        review_chat_id=str(row["review_chat_id"] or ""),
    )
