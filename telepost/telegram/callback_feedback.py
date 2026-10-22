"""Non-destructive callback feedback + failure classification (§review-card-recovery).

A review button (发布/拒绝/重抓/恢复预览) is the operator's entry point into a
business action. When something goes wrong the handler must TELL the operator —
``answerCallbackQuery`` with ``show_alert`` — and leave the card alone.
Replacing the card with a buttonless error text destroys the only Telegram-side
handle on that review: no retry, no alternative action, and the object id is
gone from the moderator's screen.

Answering contract: Telegram accepts exactly ONE answer per callback query, so
``answer_callback`` is the only place that answers. It is idempotent per query —
the first call wins (and may carry the operator-visible alert), every later call
is a no-op. Feedback discovered *after* the answer must therefore use a card
state sync, never a second answer.

Two failure windows are kept apart:

* **real failure** — the handler raised and the business layer may or may not
  have committed. The operator gets an alert (when the callback is still
  answerable) AND the exception is re-raised so PTB's error observers (and the
  durable inbox worker) see it; that is what turns a failure into a bounded
  retry instead of a silent "done".
* **UI-only failure** — Telegram rejected an edit of an already-sent message
  (not modified / not found / cannot be edited). This classification is only
  valid *around a message-edit call*: the same words coming out of a channel
  publish or a media upload are a real business failure and must propagate.
  ``is_ui_only_error`` therefore requires a Telegram ``BadRequest`` and must not
  be used to classify arbitrary exceptions.

Neither path mutates review state; the card is re-rendered from the database row
by ``handlers.review.restore_review_card``.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections import OrderedDict

from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

logger = logging.getLogger(__name__)

# Telegram objects are slotted and frozen, so the "answered" marker lives here:
# keyed by callback-query id (stable across the in-process object lifetime) with
# a bounded FIFO so a long-running process cannot grow it without limit.
_ANSWERED_MAX = 4096
_ANSWERED: "OrderedDict[str, None]" = OrderedDict()

# Telegram ``BadRequest`` texts that prove the *message* is unusable, not that
# our business action failed. Only meaningful for an edit of a message we already
# sent: replaying the update cannot repair any of them.
# Deliberately excludes delete-failures (publish rollback must surface those) and
# callback-answer failures (``answer_callback`` already absorbs them).
_UI_ONLY_MARKERS = (
    "message is not modified",
    "message to edit not found",
    "message can't be edited",
    "message cannot be edited",
    "there is no text in the message to edit",
    "message media not modified",
)


def _query_key(query) -> str | None:
    """Stable key for the answer-once bookkeeping, or None.

    Telegram callback ids are strings (unique per click), so they are the
    truthful key. Test doubles (MagicMock) expose a non-string ``id`` and are
    not tracked here — the real-limit enforcement for those lives in the
    recording bots of the route-level suites, and tracking them would collide on
    recycled mock attributes.
    """
    query_id = getattr(query, "id", None)
    if isinstance(query_id, str) and query_id:
        return f"cb:{query_id}"
    return None


def has_answered(query) -> bool:
    """True when this callback query already received its one answer."""
    key = _query_key(query)
    return key is not None and key in _ANSWERED


async def answer_callback(query, text: str = None, *, show_alert: bool = False) -> bool:
    """Answer a callback query at most once; returns True when this call answered.

    The failure of the answer itself (expired query, network) is logged and
    swallowed: the callback was already consumed and no business decision may
    depend on the popup reaching the client.
    """
    key = _query_key(query)
    if key is not None and key in _ANSWERED:
        logger.debug(
            "回调查询已应答过，忽略重复应答（不影响业务状态）: %s",
            text,
        )
        return False
    if key is not None:
        _ANSWERED[key] = None
        _ANSWERED.move_to_end(key)
        while len(_ANSWERED) > _ANSWERED_MAX:
            _ANSWERED.popitem(last=False)
    try:
        await query.answer(text=text, show_alert=show_alert)
    except Exception as exc:  # noqa: BLE001 - expired/duplicate answers are benign
        logger.debug("回应按钮失败（已过期或不可应答）: %s", exc)
    return True


def is_ui_only_error(exc: BaseException) -> bool:
    """True when a *message edit* failed for Telegram-side reasons.

    Requires a Telegram ``BadRequest``: a text match on a business exception
    (channel send, media upload, publish) must never be treated as UI noise.
    """
    if not isinstance(exc, BadRequest):
        return False
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
