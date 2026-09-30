"""Review re-render application service (§rerender).

One business command: re-stage a review's STORED file_ids (``media_json`` /
``documents_json``) as a fresh pending card under the CURRENTLY RUNNING code,
so a card authored by an older build is re-rendered with the live formatter
(e.g. the 2.72.0 ``N 个文档（含 M 份原图）`` line). Two presentation surfaces:

* Mini App / API  — ``POST /api/v1/reviews/{review_id}/rerender`` (reviewer RBAC);
* Telegram        — (future) a callback button on a review card.

The service owns the full durable workflow:

* target gate — the review being re-rendered must already have staged content
  (stored file_ids); a card with no media/documents has nothing to re-render;
* chain gate — the chain the target belongs to must not have been decided
  (published / approved / publishing). A decided chain cannot be re-rendered
  because the card is already immutable history on the channel;
* accepted path — the target's stored content is re-enqueued as a NEW pending
  review that stays in the SAME review_chain_id, generation+1, and supersedes
  the CURRENT chain head (so a re-rendered card is always the single live
  card); the superseded head is marked ``superseded``, its editorial drafts
  superseded, and its old card invalidated;
* idempotency — same callback/request key converges onto ONE attempt; a rapid
  double-click hits the already-pending reuse path of the enqueue and never
  creates a second new head past the first.

Business outcomes::

    rerender_success          new pending review created (supersedes old head)
    rerender_already_pending  replay/second attempt reused the same new head
    rerender_pending          (reserved) request admitted, awaiting async result
    rerender_failed           ONLY genuine system failure (DB/API/staging)

Never returns ``rerender_failed`` for a business state decision.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Optional

from database import db_manager
from telepost.storage.sqlite.reviews import ReviewRepository

logger = logging.getLogger(__name__)

#: Stable outcome vocabulary surfaced to Mini App / API / Telegram.
RERENDER_SUCCESS = "rerender_success"
RERENDER_PENDING = "rerender_pending"
RERENDER_ALREADY_PENDING = "rerender_already_pending"
RERENDER_FAILED = "rerender_failed"

USER_MESSAGES = {
    RERENDER_SUCCESS: "已按当前版本重新渲染该审核稿。",
    RERENDER_PENDING: "正在重新渲染…",
    RERENDER_ALREADY_PENDING: "该审核稿已在重新渲染中，请等待处理。",
    RERENDER_FAILED: "重新渲染失败，请稍后重试。",
}

#: Chain states that are immutable once reached — a re-render would re-author
#: history that is already published/approved on the channel.
_DECIDED_STATES = ("published", "approved", "publishing")


class RerenderError(Exception):
    """Base class for rerender command failures (user-safe message)."""

    code = "rerender_error"
    http_status = 409
    outcome = RERENDER_FAILED

    def __init__(self, message: str, *, code: str = None, outcome: str = None):
        super().__init__(message)
        if code is not None:
            self.code = code
        if outcome is not None:
            self.outcome = outcome


class RerenderNotFoundError(RerenderError):
    code = "rerender_not_found"
    http_status = 404


class RerenderStateError(RerenderError):
    code = "rerender_state_error"
    outcome = RERENDER_SUCCESS  # a policy state is a *business* answer, not failure


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
        logger.debug("记录重渲染审计事件失败: %s", event, exc_info=True)


def _media_rows(row: Any) -> tuple:
    """Decode stored Telegram file_ids of the card being re-rendered."""
    media = json.loads(row["media_json"] or "[]")
    documents = json.loads(row["documents_json"] or "[]")
    return media, documents


async def _supersede_old_head(bot, *, old_head_id: int, new_review_id: int) -> None:
    """Best-effort post-commit invalidation of the superseded head.

    Refetch marks the old head superseded inside its replacement transaction;
    a NON-refetch insert does not, so the rerender service owns that bookkeeping
    here. None of this ever rolls back the re-render — every moderation path
    re-checks the chain head server-side.
    """
    repo = ReviewRepository()
    try:
        changed = await repo.mark_superseded(int(old_head_id))
    except Exception:
        logger.warning("标记被替代审核稿失败: old=%s", old_head_id, exc_info=True)
        changed = False
    if changed:
        try:
            from telepost.application.editorial import EditorialService
            await EditorialService().supersede_for_review(int(old_head_id))
        except Exception:
            logger.warning("终结被替代审核稿的编辑草稿失败: review_id=%s",
                           old_head_id, exc_info=True)
    try:
        import handlers.review as review_handlers
        stager = review_handlers._stager(bot)
        _, message_ids = await repo.superseded_context(int(new_review_id))
        await stager.notify_superseded(
            source_review_id=int(old_head_id),
            old_message_ids=list(message_ids or []),
            new_review_id=int(new_review_id),
        )
    except Exception:
        logger.warning("更新被替代审核卡失败: old=%s", old_head_id, exc_info=True)


async def request_rerender(
    review_id: int,
    *,
    actor: Optional[Any] = None,
    surface: str = "api",
    callback_key: Optional[str] = None,
    bot: Optional[Any] = None,
    _queue_review_from_file_ids: Optional[Any] = None,
) -> dict:
    """Admit one review re-render (idempotent). Returns an outcome dict.

    Raises RerenderError subclasses mapped to HTTP by the API layer; the
    outcome vocab is always one of the RERENDER_* constants.

    ``_queue_review_from_file_ids`` is a seam for tests; production uses
    ``handlers.review.queue_review_from_file_ids`` (lazy-imported to keep this
    module import-safe without PTB). ``bot`` is required for the superseded
    card's surface invalidation; tests pass a stubbed bot.
    """
    repo = ReviewRepository()

    _row = await repo.get(int(review_id))
    if _row is None:
        raise RerenderNotFoundError("审核稿不存在", code="rerender_not_found")
    target = dict(_row)

    media, documents = _media_rows(target)
    if not media and not documents:
        raise RerenderStateError(
            "该审核稿没有可重新渲染的媒体内容",
            code="rerender_no_content",
        )

    chain_id = (target["review_chain_id"] or "").strip() or f"review-{target['id']}"
    _head = await repo.head_of_chain(chain_id) or _row
    head = dict(_head)

    status = (head.get("status") or "").strip()
    if status in _DECIDED_STATES:
        raise RerenderStateError(
            "该审核稿已发布或已通过，不能重新渲染。",
            code="rerender_decided",
        )

    request_id = callback_key or f"rerender:{review_id}:{chain_id}:{uuid.uuid4().hex}"
    generation = int(head.get("generation") or 0) + 1
    old_head_id = int(head["id"])

    if _queue_review_from_file_ids is None:
        import handlers.review as review_handlers
        _queue_review_from_file_ids = review_handlers.queue_review_from_file_ids

    try:
        result = await _queue_review_from_file_ids(
            bot, media, documents,
            tags=target.get("tags") or "",
            title=target.get("title") or "",
            note=target.get("note") or "",
            link=target.get("link") or "",
            anonymous=bool(target.get("anonymous")),
            spoiler=bool(target.get("spoiler")),
            user_id=int(target["user_id"]),
            username=target.get("username") or "",
            idempotency_key=request_id,
            source="rerender",
            target_id=target.get("target_id") or "",
            source_label=(target.get("source_label") or "") + " · 重新渲染",
            source_ref=target.get("source_ref") or "",
            scheduled_at=target.get("scheduled_at") or "",
            work_type=target.get("work_type") or "",
            pixiv_id=target.get("pixiv_id") or "",
            submitter_user_id=int(target.get("submitter_user_id") or 0) or None,
            submitter_username=target.get("submitter_username") or "",
            submitter_display_name=target.get("submitter_display_name") or "",
            actor_kind="service", actor_subject=str(actor or surface) or "reviewer",
            review_chain_id=chain_id,
            generation=generation,
            supersedes_review_id=old_head_id,
        )
    except Exception as exc:
        code = type(exc).__name__ or "rerender_failed"
        error_text = str(exc)[:200]
        logger.error("重渲染失败: review_id=%s error=%s", review_id, error_text,
                     exc_info=True)
        _record("review.rerender_failed", review_id=old_head_id,
                request_id=request_id, chain_id=chain_id, generation=generation,
                actor=actor, detail={"error_type": code, "stage": "enqueue",
                                     "error": error_text})
        raise RerenderError("重新渲染失败，请稍后重试。", code=code) from exc

    new_review_id = result.get("review_id") if result else None
    reused = bool(result and result.get("reused"))

    if reused:
        _record("review.rerender_reused", review_id=old_head_id,
                request_id=request_id, chain_id=chain_id, generation=generation,
                actor=actor, detail={"new_review_id": new_review_id})
        return {
            "outcome": RERENDER_ALREADY_PENDING,
            "message": USER_MESSAGES[RERENDER_ALREADY_PENDING],
            "request_id": request_id,
            "review_chain_id": chain_id,
            "current_review_id": old_head_id,
            "generation": generation,
            "new_review_id": new_review_id,
            "reused": True,
        }

    # Non-refetch insert does not auto-supersede the old head; do it now.
    await _supersede_old_head(bot, old_head_id=old_head_id,
                              new_review_id=int(new_review_id or 0))

    _record("review.rerender_success", review_id=old_head_id,
            request_id=request_id, chain_id=chain_id, generation=generation,
            actor=actor, detail={"new_review_id": new_review_id,
                                 "supersedes_review_id": old_head_id})
    return {
        "outcome": RERENDER_SUCCESS,
        "message": USER_MESSAGES[RERENDER_SUCCESS],
        "request_id": request_id,
        "review_chain_id": chain_id,
        "current_review_id": old_head_id,
        "generation": generation,
        "new_review_id": new_review_id,
        "media_count": len(media),
        "document_count": len(documents),
    }