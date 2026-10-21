"""Time-bounded review-media lifecycle (§review-media-ttl).

Telegram's deleteMessage only works within 48 hours of sending, so a
14-day review window cannot wait until expiry to delete its preview
media. Instead the media is cleaned ~36h after it was sent (control card
untouched, file_ids retained), and admins regenerate a preview on demand
from the stored Telegram file_ids.

    present ──TTL──▶ partial ──▶ cleared ──admin click──▶ regenerating ──▶ present
       │                │            ▲ (delete blocked by the 48h limit /     │
       │                │            │  permission)                          │
       └──restricted◀───┴────────────┘                                       │
        (permanent, visible; media stays in the group, needs a human)          │
                                                                              │
        regenerating ──process died before ids were recorded──▶ uncertain ◀────┘
        (terminal, visible: media may or may not have been re-sent)

The control card is never rewritten by cleanup — only its inline keyboard
gains/loses the 「恢复预览」 row, so moderation actions and text stay intact.
"""
from __future__ import annotations

import json
import logging
import os
from typing import List, Optional

from telegram.error import BadRequest

from telepost.storage.sqlite.reviews import ReviewRepository
from ui.i18n import tr

logger = logging.getLogger(__name__)

#: Hours after a preview was sent before its media is deleted. Telegram
#: allows deletion only within 48h; 36h leaves margin for task delay and
#: transient failures. 0 disables the whole lifecycle.
REVIEW_MEDIA_TTL_HOURS = max(
    0.0, float(os.getenv("REVIEW_MEDIA_TTL_HOURS", "36")))
REVIEW_MEDIA_CLEANUP_BATCH_SIZE = max(
    1, min(50, int(os.getenv("REVIEW_MEDIA_CLEANUP_BATCH_SIZE", "10"))))
#: Hard stop for transient delete failures. Telegram refuses deletions past
#: 48h, so retrying beyond this can only lie about the outcome; the media is
#: then marked 'restricted' and left to a human.
REVIEW_MEDIA_DELETE_DEADLINE_HOURS = max(
    1.0, float(os.getenv("REVIEW_MEDIA_DELETE_DEADLINE_HOURS", "47")))
#: A 'regenerating' claim older than this belongs to a crashed attempt (the
#: Telegram send may or may not have landed): the message ids are unknowable,
#: so the row becomes a visible 'uncertain' instead of retrying blindly.
REVIEW_MEDIA_REGEN_TIMEOUT_MINUTES = max(
    1.0, float(os.getenv("REVIEW_MEDIA_REGEN_TIMEOUT_MINUTES", "5")))


def _message_ids(row) -> List[int]:
    try:
        return [int(v) for v in json.loads(row["review_message_ids"] or "[]")]
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _classify_delete_error(exc: Exception) -> str:
    """'gone' | 'restricted' | 'transient' for a delete_message failure."""
    if isinstance(exc, BadRequest):
        text = str(exc).lower()
        if "message to delete not found" in text:
            return "gone"
        if "message can't be deleted" in text or "message cannot be deleted" in text:
            return "restricted"
    return "transient"


async def clean_review_preview_media(bot, *, now: Optional[float] = None) -> int:
    """Delete overdue preview media of still-pending reviews.

    Returns the number of reviews whose media fully cleared this run. The
    control card keeps its moderation buttons; a cleared review's keyboard
    gains the on-demand 「恢复预览」 row (best-effort markup edit).
    """
    import time as _time
    now = _time.time() if now is None else now
    if REVIEW_MEDIA_TTL_HOURS <= 0:
        return 0
    repo = ReviewRepository()
    rows = await repo.due_preview_cleanup(
        now=now, ttl_seconds=REVIEW_MEDIA_TTL_HOURS * 3600,
        limit=REVIEW_MEDIA_CLEANUP_BATCH_SIZE)
    cleared = 0
    for row in rows:
        review_id = row["id"]
        chat = row["review_chat_id"]
        if str(chat).lstrip("-").isdigit():
            chat = int(chat)
        remaining = [i for i in _message_ids(row)
                     if i != row["control_message_id"]]
        restricted: List[int] = []
        transient = False
        for message_id in list(remaining):
            try:
                await bot.delete_message(chat_id=chat, message_id=message_id)
            except Exception as exc:
                kind = _classify_delete_error(exc)
                if kind == "gone":
                    pass
                elif kind == "restricted":
                    restricted.append(message_id)
                else:
                    # Network/flood/unknown: stop this row, retry next tick
                    # while the 48h window allows it.
                    transient = True
                    logger.warning("审核预览清理暂缓: review_id=%s message=%s "
                                   "error_class=%s", review_id, message_id,
                                   type(exc).__name__)
                    break
            remaining.remove(message_id)
        if transient:
            anchor = row["preview_sent_at"] or row["created_at"]
            if (anchor is not None and (now - float(anchor))
                    >= REVIEW_MEDIA_DELETE_DEADLINE_HOURS * 3600):
                # Past the window Telegram will honour: stop pretending a
                # retry can still work and make the residue visible.
                await repo.checkpoint_preview(
                    review_id, remaining_ids=remaining, state="restricted")
                logger.error("审核预览媒体无法在 Telegram 删除时限内清理，"
                             "需人工删除: review_id=%s messages=%s",
                             review_id, remaining)
                await _audit(review_id, "review.preview_delete_restricted",
                             {"messages": remaining,
                              "reason": "delete_deadline_exceeded"})
                continue
            await repo.checkpoint_preview(
                review_id, remaining_ids=remaining, state="partial",
                bump_attempts=True)
            continue
        if restricted:
            # Telegram refuses (past 48h / permission): permanent, visible.
            await repo.checkpoint_preview(
                review_id, remaining_ids=restricted, state="restricted")
            logger.warning("审核预览媒体超过 Telegram 删除时限，需人工清理: "
                           "review_id=%s messages=%s", review_id, restricted)
            await _audit(review_id, "review.preview_delete_restricted",
                         {"messages": restricted})
            continue
        await repo.checkpoint_preview(
            review_id, remaining_ids=[], state="cleared", sent_at=None)
        cleared += 1
        await _audit(review_id, "review.preview_cleared",
                     {"cleared_ids": _message_ids(row)})
        await _offer_preview_regen(bot, row)
    return cleared


async def regenerate_review_preview(bot, row) -> dict:
    """Re-send a cleared review's preview from the stored file_ids.

    Caller must hold the 'regenerating' CAS claim (begin_preview_regen).
    Returns {"ok": bool, "message_ids": [...]}: on failure the row keeps
    whatever was actually sent (state 'present' with partial ids) so a
    repeated click can never duplicate media."""
    import time as _time
    repo = ReviewRepository()
    review_id = row["id"]
    media = json.loads(row["media_json"] or "[]")
    documents = json.loads(row["documents_json"] or "[]")
    if not media and not documents:
        await repo.checkpoint_preview(
            review_id, remaining_ids=[], state="cleared")
        return {"ok": False, "reason": "no_file_ids",
                "message": tr('历史投稿缺少可用的媒体 file_id，预览无法恢复。')}

    from .review_stager import TelegramReviewStager
    from config.settings import REVIEW_CHAT_ID
    chat = row["review_chat_id"]
    if not str(chat).lstrip("-").isdigit():
        chat = REVIEW_CHAT_ID
    stager = TelegramReviewStager(bot, chat, chat_id_getter=lambda: chat)
    message_ids: List[int] = []
    sent_at = _time.time()
    try:
        await stager.stage_file_ids(
            media, documents, caption="", spoiler=bool(row["spoiler"]),
            message_ids=message_ids)
    except Exception as exc:
        # Whatever was sent is deletable fresh media: track it and keep the
        # state 'present' so the TTL sweep manages it. The CAS state stays
        # non-'cleared', which blocks duplicate regenerations until then.
        await repo.checkpoint_preview(
            review_id, remaining_ids=message_ids, state="present",
            sent_at=sent_at if message_ids else None)
        if not message_ids:
            await repo.checkpoint_preview(
                review_id, remaining_ids=[], state="cleared")
        logger.warning("恢复审核预览失败: review_id=%s error_class=%s",
                       review_id, type(exc).__name__)
        return {"ok": False, "reason": type(exc).__name__,
                "message": tr('恢复预览失败，请稍后重试。'), "message_ids": message_ids}
    await repo.complete_preview_regen(
        review_id, message_ids=message_ids, sent_at=sent_at)
    await _audit(review_id, "review.preview_regenerated",
                 {"messages": message_ids})
    return {"ok": True, "message_ids": message_ids}


async def resolve_stale_preview_regens(*, now: Optional[float] = None) -> int:
    """Settle 'regenerating' claims abandoned by a dead process.

    The claim is written *before* the media is re-sent, so a crash between the
    Telegram send and the id checkpoint cannot be told apart from a crash
    before it. Rather than re-sending (which could duplicate media) the row
    becomes 'uncertain': terminal, visible in the card handler, audited, and
    counted by the doctor for a human to verify in the review group.
    """
    import time as _time
    now = _time.time() if now is None else now
    repo = ReviewRepository()
    rows = await repo.stale_preview_regens(
        cutoff=now - REVIEW_MEDIA_REGEN_TIMEOUT_MINUTES * 60)
    settled = 0
    for row in rows:
        review_id = row["id"]
        if not await repo.mark_preview_uncertain(review_id):
            continue
        settled += 1
        logger.error("审核预览恢复结果不确定（记录消息 ID 前中断），需人工核对: "
                     "review_id=%s chat=%s", review_id, row["review_chat_id"])
        await _audit(review_id, "review.preview_regen_uncertain",
                     {"claimed_at": row["updated_at"]})
    return settled


async def _offer_preview_regen(bot, row) -> None:
    """Best-effort: swap the control card's keyboard to add the regen row."""
    from telegram import InlineKeyboardMarkup
    from . import review_keyboard
    try:
        markup = review_keyboard.review_keyboard(
            row["id"], row["link"] or "", spoiler=bool(row["spoiler"]),
            source=row["source"], work_id=_work_id(row),
            submitter_user_id=row["submitter_user_id"],
            actor_kind=row["actor_kind"] or "user",
            actor_subject=row["actor_subject"] or "", media_cleared=True)
        chat = row["review_chat_id"]
        if str(chat).lstrip("-").isdigit():
            chat = int(chat)
        await bot.edit_message_reply_markup(
            chat_id=chat, message_id=row["control_message_id"],
            reply_markup=markup)
    except Exception:
        # The backend regen path re-checks state; a failed markup edit only
        # means the button is missing until the next card refresh.
        logger.warning("更新审核卡「恢复预览」按钮失败: review_id=%s", row["id"],
                       exc_info=True)


def _work_id(row) -> str:
    from ..application.review_queue import work_id_from_link
    return work_id_from_link(row["link"] or "")


async def _audit(review_id: int, event: str, detail: dict) -> None:
    try:
        from telepost.observability import audit
        await audit.record_event(event, review_id=review_id, detail=detail)
    except Exception:
        logger.debug("审计事件写入失败: %s", event, exc_info=True)
