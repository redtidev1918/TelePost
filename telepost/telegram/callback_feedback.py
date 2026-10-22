"""Non-destructive callback feedback + failure classification (§review-card-recovery).

A review button (发布/拒绝/重抓/恢复预览) is the operator's entry point into a
business action. When something goes wrong the handler must TELL the operator —
``answerCallbackQuery`` with ``show_alert`` — and leave the card alone.
Replacing the card with a buttonless error text destroys the only Telegram-side
handle on that review: no retry, no alternative action, and the object id is
gone from the moderator's screen.

Two failure windows are kept apart:

* **real failure** — the handler raised and the business layer may or may not
  have committed. The operator gets an alert AND the exception is re-raised so
  PTB's error observers (and the durable inbox worker) see it; that is what
  turns a failure into a bounded retry instead of a silent "done".
* **UI-only failure** — Telegram rejected the edit/answer itself (message not
  modified, message not found, query too old, no text to edit). Nothing in the
  business layer failed, so it is logged and never retried.

Neither path mutates review state; the card is re-rendered from the database row
by ``handlers.review.restore_review_card``.
"""
from __future__ import annotations

import asyncio
import sqlite3

from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

# Telegram errors that prove the *message* is unusable, not that our business
# action failed. Replaying the update cannot repair any of them.
_UI_ONLY_MARKERS = (
    "message is not modified",
    "message to edit not found",
    "message can't be edited",
    "message cannot be edited",
    "there is no text in the message to edit",
    "message media not modified",
    "message to delete not found",
    "message can't be deleted",
    "message cannot be deleted",
    "query is too old",
    "query id is invalid",
)


def is_ui_only_error(exc: BaseException) -> bool:
    """True when the failure belongs to the Telegram message, not the business."""
    text = str(exc).strip().lower()
    return any(marker in text for marker in _UI_ONLY_MARKERS)


def is_retryable_error(exc: BaseException) -> bool:
    """True when replaying the update may succeed (transient boundary failure)."""
    if isinstance(exc, (NetworkError, TimedOut, RetryAfter, asyncio.TimeoutError,
                        ConnectionError, OSError)):
        return True
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


async def edit_card_message(bot, chat_id, message_id, text: str,
                            markup=None) -> str:
    """Edit a card, classifying the outcome instead of raising on UI errors.

    Returns ``"edited"``, ``"unchanged"`` (Telegram refused a no-op edit) or
    ``"gone"`` (the message no longer exists / cannot be edited).
    """
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=int(message_id),
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
        return "edited"
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return "unchanged"
        if is_ui_only_error(exc):
            return "gone"
        raise
