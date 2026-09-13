"""Refetch application service — shared by Bot and Mini App (§39, §4).

One business command, two presentation surfaces:

* Telegram Bot  — ``handlers.review.refetch_review`` (callback button);
* Mini App      — ``POST /api/v1/reviews/{id}/refetch``.

Both call :func:`request_refetch`, which owns the full durable workflow:
review-state gate, chain/generation resolution, one-active-attempt admission
(data-layer enforced), remote PixivFlow submission, and remote-accepted
transition. This module never touches PTB or aiohttp.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Optional, Tuple

from database import db_manager
from telepost.storage.sqlite.refetch import RefetchRepository
from telepost.storage.sqlite.reviews import ReviewRepository

logger = logging.getLogger(__name__)

class RefetchError(Exception):
    """Base class for refetch command failures (user-safe message)."""

    code = "refetch_error"
    http_status = 409

    def __init__(self, message: str, *, code: str = None):
        super().__init__(message)
        if code is not None:
            self.code = code


class RefetchNotFoundError(RefetchError):
    code = "refetch_not_found"
    http_status = 404


class RefetchStateError(RefetchError):
    code = "refetch_state_error"


class RefetchAlreadyRunningError(RefetchError):
    code = "refetch_already_running"


class RefetchNotConfiguredError(RefetchError):
    code = "refetch_not_configured"


async def _record_refetch_event(event: str, *, review_id: int, request_id: str,
                                chain_id: str, generation: int,
                                actor: Optional[Any], **fields) -> None:
    from telepost.observability import audit
    try:
        await audit.record_event(
            event, review_id=review_id,
            actor=(f"telegram_user:{actor}" if isinstance(actor, int) else (actor or None)),
            execution_id=request_id or None,
            detail={"request_id": request_id, "review_chain_id": chain_id,
                    "generation": generation, **fields},
        )
    except Exception:
        logger.debug("记录重抓审计事件失败: %s", event, exc_info=True)


async def request_refetch(
    review_id: int,
    *,
    actor: Optional[Any] = None,
    surface: str = "service",
    callback_key: Optional[str] = None,
    on_remote_result: Optional[Any] = None,
) -> dict:
    """Admit one refetch attempt for a review (idempotent per callback/click).

    Returns a result dict consumed by both surfaces::

        {"state", "request_id", "replayed", "already_running",
         "review_chain_id", "generation", "target_id"}

    Raises RefetchError subclasses mapped to HTTP by the API layer / answered
    by the Bot with a user-safe alert.
    """
    repo = RefetchRepository()
    rows = ReviewRepository()

    row = await rows.get(int(review_id))
    if row is None:
        raise RefetchNotFoundError("审核记录不存在")

    # Review-state gate: only a still-pending review may be replaced.
    if row["status"] == "superseded":
        raise RefetchStateError("该审核稿已被替换，请在最新审核稿上操作")
    if row["status"] != "pending":
        raise RefetchStateError("该审核稿已结束，请操作最新审核稿")

    target_id = (row["target_id"] or "").strip()
    if not target_id:
        raise RefetchStateError("这条审核稿缺少抓取目标，无法重抓")
    if not os.getenv("PIXIVFLOW_REFETCH_BASE_URL") or not os.getenv("PIXIVFLOW_REFETCH_TOKEN"):
        raise RefetchNotConfiguredError("PixivFlow 重抓服务未配置")

    # Stable per-action identity: a transport retry with the SAME callback key
    # converges onto ONE attempt; a new intentional action (new key) starts a
    # new generation. HTTP callers generate a unique key per request.
    key = callback_key or f"api:{review_id}:{uuid.uuid4().hex}"

    existing = await repo.find_by_callback_key(key)
    if existing is not None:
        await _record_refetch_event(
            "review.refetch_replayed", review_id=review_id,
            request_id=existing["request_id"],
            chain_id=existing["review_chain_id"],
            generation=existing["generation"], actor=actor,
        )
        return {
            "state": existing["state"],
            "request_id": existing["request_id"],
            "replayed": True,
            "already_running": existing["state"] in ("requested", "admitted"),
            "review_chain_id": existing["review_chain_id"],
            "generation": existing["generation"],
            "target_id": target_id,
        }

    async with db_manager.get_db() as conn:
        chain_id, _ = await repo.chain_of_review(conn, row)
        await repo.seed_original_seen(conn, row)
    generation = await repo.next_generation(chain_id)

    attempt, refused = await repo.create_attempt(
        callback_key=key,
        review_chain_id=chain_id,
        generation=generation,
        source_review_id=int(review_id),
        source_candidate_id=row["pixiv_id"] or "",
    )
    if refused == "already_running":
        await _record_refetch_event(
            "review.refetch_already_running", review_id=review_id,
            request_id="", chain_id=chain_id, generation=generation, actor=actor,
        )
        raise RefetchAlreadyRunningError("正在重抓，请稍候")

    request_id = attempt["request_id"]
    await _record_refetch_event(
        "review.refetch_requested", review_id=review_id,
        request_id=request_id, chain_id=chain_id, generation=generation, actor=actor,
    )

    # Remote submission is async: the endpoint answers once the attempt is
    # DURABLE (requested), not once PixivFlow accepts (may be sleeping).
    async def _do_refetch():
        # The remote submitter is the *handlers.review* module-level seam
        # (kept patchable: tests/deployments replace
        # ``handlers.review._submit_pixivflow_refetch``). Application never
        # owns the HTTP client, but resolves the patchable name so Bot and
        # Mini App share the SAME submit function and the same test seam.
        import handlers.review as review_handlers
        submit = getattr(review_handlers, "_submit_pixivflow_refetch", None)
        classify = getattr(review_handlers, "_classify_refetch_error", None)
        if submit is None:
            submit = _default_submit_pixivflow_refetch
        if classify is None:
            classify = _default_classify_refetch_error
        try:
            result = await asyncio.to_thread(
                submit, target_id, request_id, chain_id
            )
            if not await repo.mark_admitted(request_id, result.get("slotId", "")):
                return  # attempt already terminal (rare race)
            await _record_refetch_event(
                "review.refetch_remote_accepted", review_id=review_id,
                request_id=request_id, chain_id=chain_id, generation=generation,
                actor=actor, detail_slot=result.get("slotId"),
            )
            # Surface-agnostic notification: the Bot passes a sender so the
            # review-group message is emitted AFTER remote acceptance (keeps
            # the "已提交重抓" text truthful); the Mini App passes None.
            if on_remote_result is not None:
                try:
                    await on_remote_result("accepted", result)
                except Exception:
                    logger.debug("重抓受理通知发送失败: review_id=%s", review_id,
                                 exc_info=True)
        except Exception as exc:
            code = classify(exc)
            await repo.mark_failed(request_id, code)
            logger.warning("重抓提交失败: review_id=%s request_id=%s: %s",
                           review_id, request_id, exc, exc_info=True)
            await _record_refetch_event(
                "review.refetch_failed", review_id=review_id,
                request_id=request_id, chain_id=chain_id, generation=generation,
                actor=actor, error_class=code,
            )
            if on_remote_result is not None:
                try:
                    await on_remote_result("failed", {"code": code})
                except Exception:
                    logger.debug("重抓失败通知发送失败: review_id=%s", review_id,
                                 exc_info=True)

    asyncio.create_task(_do_refetch())

    return {
        "state": attempt["state"],
        "request_id": request_id,
        "replayed": False,
        "already_running": False,
        "review_chain_id": chain_id,
        "generation": generation,
        "target_id": target_id,
    }


async def get_refetch_state(review_id: int) -> dict:
    """Detail view: current attempt + candidate lineage for a review (read-only).

    Used by GET /api/v1/reviews/{id}/refetch in the Mini App (§36 lineage UI).
    """
    repo = RefetchRepository()
    rows = ReviewRepository()
    row = await rows.get(int(review_id))
    if row is None:
        raise RefetchNotFoundError("审核记录不存在")
    chain_id = row["review_chain_id"] or f"chain-{review_id}"

    async with db_manager.get_db() as conn:
        attempt = await repo.find_active_by_chain(chain_id)
        lineage_rows = await conn.execute(
            "SELECT generation, candidate_id, source, created_at "
            "FROM refetch_seen_candidates WHERE review_chain_id=? "
            "ORDER BY created_at ASC, id ASC",
            (chain_id,),
        )
        lineage = [
            {"generation": int(r["generation"] or 0),
             "candidate_id": r["candidate_id"] or "",
             "source": r["source"] or "original",
             "created_at": r["created_at"]}
            for r in await lineage_rows.fetchall()
        ]
    return {
        "review_id": int(review_id),
        "review_chain_id": chain_id,
        "generation": int(row["generation"] or 0),
        "supersedes_review_id": row["supersedes_review_id"],
        "attempt": _attempt_dict(attempt) if attempt is not None else None,
        "lineage": lineage,
    }


def _attempt_dict(attempt) -> dict:
    return {
        "request_id": attempt["request_id"],
        "state": attempt["state"],
        "generation": int(attempt["generation"] or 0),
        "source_review_id": int(attempt["source_review_id"]),
        "result_candidate_id": attempt["result_candidate_id"] or "",
        "slot_id": attempt["slot_id"] or "",
        "failure_code": attempt["failure_code"] or "",
        "scanned": int(attempt["scanned"] or 0),
        "skipped_duplicate": int(attempt["skipped_duplicate"] or 0),
        "skipped_invalid": int(attempt["skipped_invalid"] or 0),
        "skipped_unavailable": int(attempt["skipped_unavailable"] or 0),
        "created_at": attempt["created_at"],
        "finished_at": attempt["finished_at"],
    }


import json as _json
from urllib.error import HTTPError as _HTTPError
from urllib.parse import quote as _quote, urlparse as _urlparse
from urllib.request import Request as _Request, urlopen as _urlopen


def _default_submit_pixivflow_refetch(target_id: str, request_id: str,
                                      correlation_id: str = "") -> dict:
    """Default remote submitter (used when handlers.review is not importable).

    Kept here only as a no-import-cycle fallback; the canonical patchable seam
    lives in ``handlers.review`` and is preferred.
    """
    base = os.environ["PIXIVFLOW_REFETCH_BASE_URL"].rstrip("/")
    token = os.environ["PIXIVFLOW_REFETCH_TOKEN"]
    parsed = _urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("无效的 PixivFlow 重抓地址")
    url = f"{base}/internal/targets/{_quote(target_id, safe='')}/refetch"
    body = {"requestId": request_id}
    if correlation_id:
        body["correlationId"] = correlation_id
    request = _Request(
        url,
        data=_json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _urlopen(request, timeout=REFETCH_TIMEOUT_SECONDS) as response:
            result = _json.load(response)
            if response.status != 202 or result.get("status") != "accepted":
                raise RuntimeError(f"PixivFlow 拒绝重抓（HTTP {response.status}）")
            return result
    except _HTTPError as error:
        raise RuntimeError(f"PixivFlow 拒绝重抓（HTTP {error.code}）") from error


def _default_classify_refetch_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "http 401" in lowered or "http 403" in lowered:
        return "unauthorized"
    if "http 404" in lowered:
        return "not_found"
    if "http 5" in lowered:
        return "remote_error"
    if "timed out" in lowered or "timeout" in lowered or "超时" in message:
        return "timeout"
    return "network_error"
