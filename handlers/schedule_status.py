"""/status 与 /pin_status：在 Telegram 里直接看/置顶最近一次计划终态。"""
import datetime
import logging

from telegram import Update
from telegram.ext import CallbackContext

from database import db_manager
from utils.blacklist import is_owner

logger = logging.getLogger(__name__)


async def schedule_status_text() -> str:
    """Read-only current schedule status, same facts as the public /status page."""
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT slot_id, sent_at, status FROM schedule_outcome_notifications"
        )
        rows = await cur.fetchall()
    last_by_schedule = {}
    for r in rows:
        slot = r["slot_id"] or ""
        sid = slot.split("@", 1)[0] if "@" in slot else ""
        if not sid:
            continue
        sent = float(r["sent_at"] or 0)
        prev = last_by_schedule.get(sid)
        if prev is None or sent > prev[0]:
            last_by_schedule[sid] = (sent, r["status"] or "")
    lines = ["TelePost Schedule Status", "Last terminal notification per schedule:"]
    if not last_by_schedule:
        lines.append("  (no schedule outcome yet)")
    for sid, (sent, status) in sorted(last_by_schedule.items(),
                                      key=lambda kv: kv[1][0], reverse=True):
        iso = (datetime.datetime.fromtimestamp(
            sent, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            if sent else "never")
        lines.append(f"  {sid}  last {iso} · {status or 'unknown'}")
    return "\n".join(lines) + "\n"


async def status_command(update: Update, context: CallbackContext) -> int:
    """/status —— 查看最近一次计划终态（公开）"""
    try:
        text = await schedule_status_text()
    except Exception:
        logger.warning("schedule/status text failed", exc_info=True)
        text = "⚠️ 无法读取计划状态，请稍后再试。"
    await update.message.reply_text(text)
    return 0


async def pin_status_command(update: Update, context: CallbackContext) -> int:
    """/pin_status —— 发送状态并把消息置顶到当前群/频道（仅 OWNER）"""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text("⛔ 此命令仅限机器人所有者使用")
        return 0
    try:
        text = await schedule_status_text()
    except Exception:
        logger.warning("schedule/status text failed (pin)", exc_info=True)
        text = "⚠️ 无法读取计划状态，请稍后再试。"
    try:
        sent = await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
        try:
            await context.bot.pin_chat_message(
                chat_id=update.effective_chat.id,
                message_id=sent.message_id,
                disable_notification=True,
            )
        except Exception:
            logger.warning("pin_chat_message failed (bot may lack admin): chat=%s",
                           update.effective_chat.id, exc_info=True)
            await update.message.reply_text("✅ 状态已发送（置顶失败：机器人需要频道/群管理员权限）")
        else:
            await update.message.reply_text("✅ 状态已置顶")
    except Exception:
        logger.warning("send schedule status failed: chat=%s", update.effective_chat.id, exc_info=True)
        await update.message.reply_text("❌ 发送状态失败，请稍后再试")
    return 0
