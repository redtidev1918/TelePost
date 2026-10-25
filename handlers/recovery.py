"""Review-group manual recovery button handler (§manual-recovery).

``sched_recover|<target_id>|<normal|relaxed>`` — one button row per FAILED
target on the terminal schedule message. Clicking admits a durable recovery
attempt (idempotent per callback id; one active recovery per target), shows the
operator a business-friendly "accepted/queued" answer, and removes the buttons
so the same outcome cannot be re-clicked into a second attempt.
"""
from __future__ import annotations
from ui.i18n import tr

import logging

from telegram import Update
from telegram.ext import CallbackContext

logger = logging.getLogger(__name__)

ACCEPTED_TEXT = tr('已受理，系统会自动继续处理。')
ALREADY_TEXT = tr('该目标已在处理中，请稍候。')
MODE_LABEL = {"normal": tr('再试一次'), "relaxed": tr('放宽条件重试')}


async def _answer(query, text: str = "", **kwargs) -> None:
    try:
        if text or kwargs:
            await query.answer(text=text, **kwargs)
        else:
            await query.answer()
    except Exception as exc:  # pragma: no cover - best effort
        logger.debug("回调应答失败: %s", exc)


def _failure_lines(exc) -> str:
    lines = [tr('❌ 恢复请求失败'), "", tr('原因：{p0}').format(p0=exc)]
    code = getattr(exc, "code", "recovery_error")
    if code:
        lines.append(tr('错误码：{p0}').format(p0=code))
    retryable = getattr(exc, "retryable", None)
    if isinstance(retryable, bool):
        lines.append(tr('后续处理：可重试') if retryable else tr('后续处理：需先人工处理'))
    hint = getattr(exc, "hint", "")
    if hint:
        lines.append(tr('建议：{p0}').format(p0=hint))
    return "\n".join(lines)


async def schedule_recover(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    data = query.data or ""
    try:
        _, target_id, mode = data.split("|", 2)
    except ValueError:
        await _answer(query, tr('无效的恢复请求'))
        return
    mode = mode.strip().lower()
    if mode not in ("normal", "relaxed"):
        await _answer(query, tr('无效的恢复方式'))
        return

    from telepost.application.recovery import (
        RELAXED_EXPLANATION,
        RecoveryAlreadyRunningError,
        RecoveryError,
        admit_recovery,
        submit_recovery,
    )

    # Answer-first (2026-10-25): this handler runs inside the strictly-serial
    # Durable Inbox worker. The admission below touches ONLY the local DB; the
    # remote POST to PixivFlow (cold start ~8s+, worst case the submit timeout)
    # happens in a background task with the 300s resubmit sweep as its crash
    # net — waiting on it inline blew Telegram's ~10s callback-answer window
    # AND head-of-line blocked every later update of this bot.
    try:
        result = await admit_recovery(
            target_id,
            retry_mode=mode,
            callback_key=str(query.id),
        )
    except RecoveryAlreadyRunningError as exc:
        await _answer(query, str(exc))
        return
    except RecoveryError as exc:
        await _answer(query, _failure_lines(exc), show_alert=True)
        return
    except Exception:
        # Any unexpected local failure must still answer: a silent raise here
        # leaves the operator with a dead spinner and dead-letters the update.
        logger.exception("恢复请求处理异常: target=%s", target_id)
        await _answer(query, tr('恢复请求处理异常，请稍后重试'), show_alert=True)
        return

    label = MODE_LABEL.get(mode, tr('重试'))
    reply = tr('✅ 已提交{p0}：系统会自动继续处理。').format(p0=label)
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
    # Out-of-band remote submission (same requestId; idempotent on PixivFlow).
    # Only for rows still awaiting submission — a replayed/terminal row must
    # not be resurrected by a second submit.
    if result.get("state") == "requested" and result.get("request_id"):
        message = getattr(query, "message", None)
        notify_chat_id = getattr(message, "chat_id", None)

        async def _submit() -> None:
            try:
                await submit_recovery(
                    target_id, result["request_id"], mode,
                    terminal_on_failure=False,
                )
            except Exception as exc:
                # Terminal (non-retryable) failure: the ✅ answer already used
                # the callback window, so the operator is told in-chat instead.
                logger.error("后台提交恢复请求失败: target=%s request=%s",
                             target_id, result.get("request_id"), exc_info=True)
                from telepost.application.recovery import RecoveryError
                if isinstance(exc, RecoveryError) and notify_chat_id:
                    bot = getattr(context, "bot", None)
                    if bot is not None:
                        try:
                            await bot.send_message(
                                chat_id=notify_chat_id,
                                text=_failure_lines(exc),
                            )
                        except Exception:
                            logger.warning("恢复失败通知发送失败", exc_info=True)

        try:
            import asyncio
            tasks = context.bot_data.setdefault("_recovery_submit_tasks", set())
            task = asyncio.create_task(_submit())
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        except Exception:
            logger.exception("创建恢复后台提交任务失败: target=%s", target_id)