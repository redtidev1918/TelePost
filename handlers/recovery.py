"""Review-group manual recovery button handler (§manual-recovery).

``sched_recover|<target_id>|<normal|relaxed>`` — one button row per FAILED
target on the terminal schedule message. Clicking admits a durable recovery
attempt (idempotent per callback id; one active recovery per target), shows the
operator a business-friendly "accepted/queued" answer, and removes the buttons
so the same outcome cannot be re-clicked into a second attempt.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackContext

logger = logging.getLogger(__name__)

ACCEPTED_TEXT = "已受理，系统会自动继续处理。"
ALREADY_TEXT = "该目标已在处理中，请稍候。"
MODE_LABEL = {"normal": "再试一次", "relaxed": "放宽条件重试"}


async def _answer(query, text: str = "") -> None:
    try:
        if text:
            await query.answer(text=text, show_alert=False)
        else:
            await query.answer()
    except Exception as exc:  # pragma: no cover - best effort
        logger.debug("回调应答失败: %s", exc)


async def schedule_recover(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    data = query.data or ""
    try:
        _, target_id, mode = data.split("|", 2)
    except ValueError:
        await _answer(query, "无效的恢复请求")
        return
    mode = mode.strip().lower()
    if mode not in ("normal", "relaxed"):
        await _answer(query, "无效的恢复方式")
        return

    from telepost.application.recovery import (
        RELAXED_EXPLANATION,
        RecoveryAlreadyRunningError,
        RecoveryError,
        request_target_recovery,
    )

    try:
        result = await request_target_recovery(
            target_id,
            retry_mode=mode,
            callback_key=str(query.id),
        )
    except RecoveryAlreadyRunningError as exc:
        await _answer(query, str(exc))
        return
    except RecoveryError as exc:
        lines = ["❌ 恢复请求失败", "", f"原因：{exc}"]
        code = getattr(exc, "code", "recovery_error")
        if code:
            lines.append(f"错误码：{code}")
        retryable = getattr(exc, "retryable", None)
        if isinstance(retryable, bool):
            lines.append("后续处理：可重试" if retryable else "后续处理：需先人工处理")
        hint = getattr(exc, "hint", "")
        if hint:
            lines.append(f"建议：{hint}")
        await _answer(query, "\n".join(lines), show_alert=True)
        return

    label = MODE_LABEL.get(mode, "重试")
    reply = f"✅ 已提交{label}：系统会自动继续处理。"
    if mode == "relaxed":
        reply += f"\n{RELAXED_EXPLANATION}"
    await _answer(query, reply)
    if result.get("already_running"):
        await _answer(query, ALREADY_TEXT)
        return
    # Remove the buttons: the outcome message is terminal history; the recovery
    # itself reports through its own schedule-outcome notification.
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as exc:
        logger.debug("移除恢复按钮失败: %s", exc)