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
import time
import uuid
from typing import Any, Optional, Tuple

from database import db_manager
from telepost.domain import refetch_state as fsm
from telepost.storage.sqlite.refetch import RefetchRepository
from telepost.storage.sqlite.reviews import ReviewRepository

logger = logging.getLogger(__name__)

#: Live remote-submit tasks. The submit runs off-path (the click must answer
#: immediately) but it must never be UNSUPERVISED: a bare create_task keeps no
#: reference, so an exception inside it is only logged by asyncio and a shutdown
#: cannot converge it. Registered here, reported by its done callback, and
#: drained by ``shutdown_refetch_tasks`` on the real shutdown path.
_refetch_tasks: set = set()


def _on_refetch_task_done(task) -> None:
    _refetch_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        # The attempt row keeps its own failure_code (mark_failed ran inside),
        # so this is the observable trail for an otherwise silent death.
        logger.warning("重抓提交后台任务异常退出: %s", exc, exc_info=exc)


def _track_refetch_task(task) -> None:
    _refetch_tasks.add(task)
    task.add_done_callback(_on_refetch_task_done)


async def shutdown_refetch_tasks(*, timeout: float = 15.0) -> int:
    """Drain in-flight remote-submit tasks on shutdown; returns how many there were.

    The durable attempt rows stay the truth: a task cancelled here leaves a
    non-terminal attempt with a stopped heartbeat, which the next process's
    startup sweep adopts (never a silent disappearance).
    """
    pending = [t for t in list(_refetch_tasks) if not t.done()]
    if not pending:
        return 0
    try:
        await asyncio.wait(pending, timeout=timeout)
    except Exception:
        logger.warning("等待重抓后台任务失败", exc_info=True)
    for task in pending:
        if not task.done():
            task.cancel()
    return len(pending)


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
        raise RefetchStateError("该审核稿已被重抓结果替代，请审核最新版本。")
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

    _track_refetch_task(asyncio.create_task(_do_refetch()))

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
        if attempt is None:
            # §refetch-lifecycle — a terminal attempt is still the answer the user
            # asked for: the Mini App must be able to show HOW the last refetch
            # ended (and why), not just "nothing running".
            attempt = await repo.find_latest_by_chain(chain_id)
        lineage_rows = await conn.execute(
            "SELECT generation, candidate_id, source, created_at, request_id, "
            "outcome, reason, decided_at, replaced_by "
            "FROM refetch_seen_candidates WHERE review_chain_id=? "
            "ORDER BY created_at ASC, id ASC",
            (chain_id,),
        )
        lineage = [
            {"generation": int(r["generation"] or 0),
             "candidate_id": r["candidate_id"] or "",
             "source": r["source"] or "original",
             "request_id": r["request_id"] or "",
             # Candidate lifecycle: who was rejected, why, when and by what.
             "outcome": r["outcome"] or "current",
             "reason": r["reason"] or "",
             "decided_at": r["decided_at"],
             "replaced_by": r["replaced_by"] or "",
             "created_at": r["created_at"]}
            for r in await lineage_rows.fetchall()
        ]
    events = []
    if attempt is not None:
        events = [
            {
                "from_state": e["from_state"] or "",
                "to_state": e["to_state"] or "",
                "state": fsm.normalize(e["to_state"] or e["from_state"] or ""),
                "label": fsm.label(e["to_state"] or e["from_state"] or ""),
                "reason": e["reason"] or "",
                "actor": e["actor"] or "",
                "remote_state": e["remote_state"] or "",
                "created_at": e["created_at"],
            }
            for e in await repo.list_events(attempt["request_id"])
        ]
    return {
        "review_id": int(review_id),
        "review_chain_id": chain_id,
        "generation": int(row["generation"] or 0),
        "supersedes_review_id": row["supersedes_review_id"],
        "attempt": _attempt_dict(attempt) if attempt is not None else None,
        "events": events,
        "lineage": lineage,
    }


def refetch_task_id(attempt) -> str:
    """The stable, human-quotable id of one attempt (shown on the card).

    Derived from the durable row (never random) so every surface — review card,
    Mini App, log line, doctor output — prints the same string for the same
    attempt: ``refetch-<source_review_id>-<epoch seconds>``.
    """
    created = float(attempt["created_at"] or 0)
    return f"refetch-{int(attempt['source_review_id'])}-{int(created)}"


def _row_get(row, key: str, default=None):
    """Read a column defensively (pre-migration rows / plain dict callers)."""
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _attempt_dict(attempt) -> dict:
    """Project one attempt row for the API/Mini App.

    ``state``/``attempt_state`` stay LEGACY-mapped so existing clients keep
    working; the canonical vocabulary plus the progress projection (stage,
    Chinese label, elapsed seconds, task id, notify count) ride alongside, and
    the durable timeline is appended by :func:`get_refetch_state`.
    """
    canonical = fsm.normalize(attempt["state"])
    created = float(attempt["created_at"] or 0)
    finished = attempt["finished_at"]
    end = float(finished) if finished else time.time()
    elapsed = max(0, int(end - created)) if created else 0
    task_id = refetch_task_id(attempt)
    # §refetch-lifecycle heartbeat clocks (additive keys, migration-safe): read
    # through ``_row_get`` so a pre-upgrade row — or a caller that passes a
    # plain dict — never raises here.
    heartbeat_at = _row_get(attempt, "heartbeat_at")
    remote_heartbeat_at = _row_get(attempt, "remote_heartbeat_at")
    next_poll_at = _row_get(attempt, "next_poll_at")
    heartbeat_age = None
    if heartbeat_at:
        heartbeat_age = max(0, int(end - float(heartbeat_at)))
    data = {
        "request_id": attempt["request_id"],
        "task_id": task_id,
        "state": fsm.to_legacy(canonical),
        "attempt_state": fsm.to_legacy(canonical),
        "canonical_state": canonical,
        "stage": canonical,
        "label": fsm.label(canonical),
        "generation": int(attempt["generation"] or 0),
        "source_review_id": int(attempt["source_review_id"]),
        "result_candidate_id": attempt["result_candidate_id"] or "",
        "slot_id": attempt["slot_id"] or "",
        "failure_code": attempt["failure_code"] or "",
        "terminal_reason": attempt["terminal_reason"] or "",
        "last_remote_state": attempt["last_remote_state"] or "",
        "notify_count": int(attempt["notify_count"] or 0),
        "scanned": int(attempt["scanned"] or 0),
        "skipped_duplicate": int(attempt["skipped_duplicate"] or 0),
        "skipped_invalid": int(attempt["skipped_invalid"] or 0),
        "skipped_unavailable": int(attempt["skipped_unavailable"] or 0),
        "created_at": attempt["created_at"],
        "finished_at": finished,
        "heartbeat_at": heartbeat_at,
        "heartbeat_count": int(_row_get(attempt, "heartbeat_count") or 0),
        "remote_heartbeat_at": remote_heartbeat_at,
        "next_poll_at": next_poll_at,
        "poll_failures": int(_row_get(attempt, "poll_failures") or 0),
    }
    data["progress"] = {
        "task_id": task_id,
        "stage": canonical,
        "label": fsm.label(canonical),
        "elapsed_seconds": elapsed,
        "notify_count": data["notify_count"],
        "last_remote_state": data["last_remote_state"],
        "terminal_reason": data["terminal_reason"],
        "heartbeat_at": heartbeat_at,
        "heartbeat_count": data["heartbeat_count"],
        "remote_heartbeat_at": remote_heartbeat_at,
        "heartbeat_age_seconds": heartbeat_age,
    }
    return data


def _default_submit_pixivflow_refetch(target_id: str, request_id: str,
                                      correlation_id: str = "") -> dict:
    """Fallback remote submitter when ``handlers.review`` is not importable.

    It DELEGATES to the PixivFlow job port instead of assembling a URL: the port
    (``telepost/application/pixivflow_jobs.py``) is the only module allowed to
    know the remote route/field shape. Building a second HTTP client here was a
    boundary violation (Workflow Protocol v1 §boundary discipline) — the port is
    also where ``PIXIVFLOW_REFETCH_BASE_URL``/``PIXIVFLOW_REFETCH_TOKEN`` are read.
    """
    from telepost.application import pixivflow_jobs as pixivflow_jobs_port

    client = pixivflow_jobs_port.default_job_client()
    try:
        receipt = client.submit(
            "refetch", request_id, correlation_id=correlation_id,
            params={"target_id": target_id},
        )
    except Exception as exc:
        code = pixivflow_jobs_port.classify_error(exc)
        raise RuntimeError(f"PixivFlow 拒绝重抓（{code}）") from exc
    return {
        "status": "accepted",
        "replayed": bool(receipt.replayed),
        "slotId": receipt.slot_id(),
        "slot_id": receipt.slot_id(),
    }


def _default_classify_refetch_error(exc: Exception) -> str:
    from telepost.application import pixivflow_jobs as pixivflow_jobs_port

    return pixivflow_jobs_port.classify_error(exc)
