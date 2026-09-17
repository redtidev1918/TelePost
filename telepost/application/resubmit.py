"""Submission resubmit application service (§resubmit).

One business command, two presentation surfaces:

* Mini App  — ``POST /api/v1/me/submissions/{review_id}/resubmit``;
* Telegram  — (future) a callback button on a rejected submission card.

The service owns the full durable workflow:

* owner gate — only the verified human submitter may resubmit their own chain;
* state gate — only a TERMINAL non-published chain head may be resubmitted; a
  still-pending head returns ``resubmit_already_pending`` and never creates a
  second row;
* accepted path — the old content (stored file_ids) is re-enqueued as a NEW
  pending review that supersedes the rejected head and stays in the SAME
  review chain/generation (history preserved, nothing overwritten);
* idempotency — same callback/request key converges onto ONE attempt; a rapid
  double-click that happens after the first row is already ``preparing`` hits
  the already-pending gate and never creates a second task.

Business outcomes::

    resubmit_success          new pending review created
    resubmit_already_pending  chain head is still pending/preparing
    resubmit_pending          (reserved) request admitted, awaiting async result
    resubmit_failed           ONLY genuine system failure (DB/API/staging)

Never returns ``resubmit_failed`` for a business state (already pending,
rejected/published policy).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Optional

from database import db_manager
from telepost.storage.sqlite.reviews import ReviewRepository

logger = logging.getLogger(__name__)

#: Stable outcome vocabulary surfaced to Mini App / Telegram.
RESUBMIT_SUCCESS = "resubmit_success"
RESUBMIT_PENDING = "resubmit_pending"
RESUBMIT_ALREADY_PENDING = "resubmit_already_pending"
RESUBMIT_REJECTED = "resubmit_rejected"  # produced later, by the terminal review
RESUBMIT_FAILED = "resubmit_failed"

USER_MESSAGES = {
    RESUBMIT_SUCCESS: "已重新提交审核。",
    RESUBMIT_PENDING: "正在重新提交审核…",
    RESUBMIT_ALREADY_PENDING: "该作品已经提交审核，请等待处理。",
    RESUBMIT_REJECTED: "重新提交的作品审核未通过。",
    RESUBMIT_FAILED: "重新提交失败，请稍后重试。",
}


class ResubmitError(Exception):
    """Base class for resubmit command failures (user-safe message)."""

    code = "resubmit_error"
    http_status = 409
    outcome = RESUBMIT_FAILED

    def __init__(self, message: str, *, code: str = None, outcome: str = None):
        super().__init__(message)
        if code is not None:
            self.code = code
        if outcome is not None:
            self.outcome = outcome


class ResubmitNotFoundError(ResubmitError):
    code = "resubmit_not_found"
    http_status = 404


class ResubmitOwnershipError(ResubmitError):
    code = "resubmit_forbidden"
    http_status = 404
    outcome = RESUBMIT_SUCCESS  # never leaks existence; maps to success DTO path


class ResubmitStateError(ResubmitError):
    code = "resubmit_state_error"
    outcome = RESUBMIT_SUCCESS  # a policy state is a *business* answer, not failure


class _ResubmitAlreadyPending(ResubmitError):
    code = "resubmit_already_pending"
    http_status = 409
    outcome = RESUBMIT_ALREADY_PENDING


def _record(event: str, *, review_id: int, request_id: str,
            chain_id: str, generation: int, actor: Optional[Any], **fields) -> None:
    """Audit helper; never lets an audit failure break the business path."""
    try:
        from telepost.observability import audit
        asyncio.get_event_loop().create_task(audit.record_event(
            event, review_id=review_id,
            actor=(f"telegram_user:{actor}" if isinstance(actor, int) else (actor or None)),
            execution_id=request_id or None,
            detail={"request_id": request_id, "review_chain_id": chain_id,
                    "generation": generation, **fields},
        ))
    except Exception:
        logger.debug("记录重投审计事件失败: %s", event, exc_info=True)


def _media_rows(row: Any) -> tuple:
    """Decode stored Telegram file_ids for a fresh review card."""
    media = json.loads(row["media_json"] or "[]")
    documents = json.loads(row["documents_json"] or "[]")
    return media, documents


def _generation_message(text: str, generation: int) -> str:
    if generation > 0:
        return "✅ 你重新提交的内容已进入审核队列，请等待审核结果。"
    return text


async def request_resubmit(
    review_id: int,
    *,
    actor: Optional[Any] = None,
    surface: str = "miniapp",
    callback_key: Optional[str] = None,
    bot: Optional[Any] = None,
    _queue_review_from_file_ids: Optional[Any] = None,
) -> dict:
    """Admit one submission resubmit (idempotent). Returns an outcome dict.

    Raises ResubmitError subclasses mapped to HTTP by the API layer; the
    outcome vocab is always one of the RESUBMIT_* constants.

    ``_queue_review_from_file_ids`` is a seam for tests; production uses
    ``handlers.review.queue_review_from_file_ids`` (lazy-imported to keep this
    module import-safe without PTB).
    """
    uid = int(actor or 0)
    repo = ReviewRepository()

    _row = await repo.get(int(review_id))
    if _row is None:
        raise ResubmitNotFoundError("投稿不存在", code="resubmit_not_found")
    row = dict(_row)
    if int(row.get("submitter_user_id") or 0) != uid:
        raise ResubmitNotFoundError("投稿不存在", code="resubmit_not_found")

    chain_id = (row["review_chain_id"] or "").strip() or f"review-{row['id']}"
    _head = await repo.head_of_chain(chain_id) or _row
    head = dict(_head)
    if int(head.get("submitter_user_id") or 0) != uid:
        raise ResubmitNotFoundError("投稿不存在", code="resubmit_not_found")

    status = (head.get("status") or "").strip()
    request_id = callback_key or f"resubmit:{uid}:{chain_id}:{uuid.uuid4().hex}"

    # Business gate — a pending head must NEVER create a second task.
    if status in ("preparing", "pending"):
        _record("submission.resubmit_already_pending", review_id=int(head["id"]),
                request_id=request_id, chain_id=chain_id,
                generation=int(head.get("generation") or 0), actor=actor)
        return {
            "outcome": RESUBMIT_ALREADY_PENDING,
            "message": USER_MESSAGES[RESUBMIT_ALREADY_PENDING],
            "request_id": request_id,
            "review_chain_id": chain_id,
            "current_review_id": int(head["id"]),
            "generation": int(head.get("generation") or 0),
            "new_review_id": None,
        }

    if status in ("published", "approved", "publishing"):
        raise ResubmitStateError(
            "该作品已发布，不能重投。", code="resubmit_published",
        )

    if status not in ("rejected", "failed", "expired", "cancelled"):
        raise ResubmitStateError(
            f"该投稿当前不能重投。", code="resubmit_state_error",
        )

    media, documents = _media_rows(head)
    generation = int(head.get("generation") or 0) + 1

    if _queue_review_from_file_ids is None:
        import handlers.review as review_handlers
        _queue_review_from_file_ids = review_handlers.queue_review_from_file_ids

    try:
        result = await _queue_review_from_file_ids(
            bot, media, documents,
            tags=head.get("tags") or "",
            title=head.get("title") or "",
            note=head.get("note") or "",
            link=head.get("link") or "",
            anonymous=bool(head.get("anonymous")),
            spoiler=bool(head.get("spoiler")),
            user_id=int(head["user_id"]),
            username=head.get("username") or "",
            idempotency_key=request_id,
            source="resubmit",
            target_id=head.get("target_id") or "",
            source_label=(head.get("source_label") or "") + " · 重投",
            source_ref=head.get("source_ref") or "",
            scheduled_at=head.get("scheduled_at") or "",
            work_type=head.get("work_type") or "",
            pixiv_id=head.get("pixiv_id") or "",
            submitter_user_id=int(head.get("submitter_user_id") or 0) or None,
            submitter_username=head.get("submitter_username") or "",
            submitter_display_name=head.get("submitter_display_name") or "",
            actor_kind="user", actor_subject=f"telegram_user:{uid}",
            review_chain_id=chain_id,
            generation=generation,
            supersedes_review_id=int(head["id"]),
        )
    except Exception as exc:
        code = type(exc).__name__ or "resubmit_failed"
        error_text = str(exc)[:200]
        logger.error("重投失败: review_id=%s error=%s", review_id, error_text,
                     exc_info=True)
        _record("submission.resubmit_failed", review_id=int(head["id"]),
                request_id=request_id, chain_id=chain_id, generation=generation,
                actor=actor, detail={"error_type": code, "stage": "enqueue",
                                     "error": error_text})
        raise ResubmitError("重新提交失败，请稍后重试。", code=code) from exc

    new_review_id = result.get("review_id") if result else None
    outcome = RESUBMIT_SUCCESS
    if result and result.get("reused"):
        outcome = RESUBMIT_ALREADY_PENDING
        new_review_id = result.get("review_id") or new_review_id

    _record("submission.resubmit_success", review_id=int(head["id"]),
            request_id=request_id, chain_id=chain_id, generation=generation,
            actor=actor, detail={"new_review_id": new_review_id,
                                 "supersedes_review_id": int(head["id"])})
    return {
        "outcome": outcome,
        "message": USER_MESSAGES[outcome],
        "request_id": request_id,
        "review_chain_id": chain_id,
        "current_review_id": int(head["id"]),
        "generation": generation,
        "new_review_id": new_review_id,
    }
