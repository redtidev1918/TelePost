"""Owner-only Telegram runtime configuration panel."""
import asyncio
import html
import os
import re
import signal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext

from config.settings import (
    API_REVIEW_REQUIRED,
    CHANNEL_ID,
    CHAT_REVIEW_REQUIRED,
    DB_PATH,
    REVIEW_CHAT_ID,
    SHOW_SUBMITTER,
)
from database.db_manager import get_db
from utils.blacklist import is_owner
from utils.runtime_policy import clear_runtime_policy, update_runtime_policy


def _policy_path() -> str:
    return os.getenv("RUNTIME_POLICY_PATH") or os.path.join(
        os.path.dirname(DB_PATH) or ".", "runtime-policy.json"
    )


def _on(value) -> str:
    return "开 ✅" if value else "关 ❌"


def _panel_text() -> str:
    bot_index = os.getenv("TELEPOST_BOT_INDEX", "1")
    return (
        f"⚙️ <b>Bot {html.escape(bot_index)} 运行配置</b>\n\n"
        f"📺 投稿频道：<code>{html.escape(str(CHANNEL_ID))}</code>\n"
        f"👥 审核群：<code>{html.escape(str(REVIEW_CHAT_ID or '未设置'))}</code>\n"
        f"🔌 API 投稿审核：{_on(API_REVIEW_REQUIRED)}\n"
        f"💬 聊天投稿审核：{_on(CHAT_REVIEW_REQUIRED)}\n"
        f"👤 频道显示投稿人：{_on(SHOW_SUBMITTER)}\n\n"
        "设置频道：<code>/botconfig channel @频道或-100ID</code>\n"
        "设置审核群：在目标群发送 <code>/botconfig review here</code>"
    )


def _panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"API 审核：{_on(API_REVIEW_REQUIRED)}",
                callback_data="botconfig:api_review",
            ),
            InlineKeyboardButton(
                f"聊天审核：{_on(CHAT_REVIEW_REQUIRED)}",
                callback_data="botconfig:chat_review",
            ),
        ],
        [InlineKeyboardButton(
            f"显示投稿人：{_on(SHOW_SUBMITTER)}",
            callback_data="botconfig:show_submitter",
        )],
        [InlineKeyboardButton(
            "📍 将当前群设为审核群",
            callback_data="botconfig:review_here",
        )],
        [InlineKeyboardButton(
            "♻️ 恢复部署配置",
            callback_data="botconfig:reset",
        )],
    ])


def _parse_chat_reference(raw: str) -> str:
    value = raw.strip()
    match = re.fullmatch(r"https?://t\.me/c/(\d+)(?:/\d+)?/?", value)
    if match:
        return f"-100{match.group(1)}"
    match = re.fullmatch(r"https?://t\.me/([A-Za-z][A-Za-z0-9_]{3,})/?", value)
    if match:
        return f"@{match.group(1)}"
    if re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,}|-?\d+", value):
        return value
    raise ValueError("请使用 @用户名、-100… ID 或 t.me 链接")


async def _validated_chat_id(context: CallbackContext, raw: str, kind: str) -> str:
    reference = _parse_chat_reference(raw)
    chat = await context.bot.get_chat(reference)
    chat_type = str(getattr(chat, "type", ""))
    expected = {"channel"} if kind == "channel" else {"group", "supergroup"}
    if chat_type not in expected:
        raise ValueError("目标不是频道" if kind == "channel" else "目标不是群组")

    me = await context.bot.get_me()
    member = await context.bot.get_chat_member(chat.id, me.id)
    if str(getattr(member, "status", "")) not in {"administrator", "creator", "owner"}:
        raise ValueError("请先把 Bot 设为目标频道/群组管理员")
    if kind == "channel" and getattr(member, "can_post_messages", True) is False:
        raise ValueError("Bot 没有频道发帖权限")
    return str(chat.id)


def _schedule_restart() -> bool:
    if os.getenv("TELEPOST_MANAGED_RESTART", "").lower() not in {"1", "true", "yes"}:
        return False
    asyncio.get_running_loop().call_later(
        1.0, os.kill, os.getpid(), signal.SIGTERM
    )
    return True


async def _pending_count() -> int:
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM pending_reviews WHERE status='pending'"
        )
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def _require_empty_review_queue() -> None:
    count = await _pending_count()
    if count:
        raise ValueError(f"仍有 {count} 条待审核投稿，请先批准或拒绝后再切换")


async def _deny(update: Update) -> bool:
    if update.effective_user and is_owner(update.effective_user.id):
        return False
    target = update.callback_query or update.effective_message
    if update.callback_query:
        await target.answer("仅 Bot 所有者可修改配置", show_alert=True)
    elif target:
        await target.reply_text("⛔ 此命令仅限 Bot 所有者使用")
    return True


async def _apply(update: Update, changes: dict, message: str) -> None:
    update_runtime_policy(_policy_path(), changes)
    target = update.effective_message
    managed = os.getenv("TELEPOST_MANAGED_RESTART", "").lower() in {"1", "true", "yes"}
    suffix = "Bot 将在约 6 秒内单独重载。" if managed else "请重启 Bot 后生效。"
    await target.reply_text(f"✅ {message}\n{suffix}")
    if managed:
        _schedule_restart()


async def botconfig(update: Update, context: CallbackContext) -> None:
    if await _deny(update):
        return
    args = list(context.args or [])
    if not args:
        await update.effective_message.reply_text(
            _panel_text(), parse_mode="HTML", reply_markup=_panel_keyboard()
        )
        return

    action = args[0].lower()
    try:
        if action == "channel" and len(args) == 2:
            await _require_empty_review_queue()
            chat_id = await _validated_chat_id(context, args[1], "channel")
            await _apply(update, {"CHANNEL_ID": chat_id}, f"投稿频道已设为 {chat_id}")
        elif action == "review" and len(args) == 2:
            await _require_empty_review_queue()
            raw = str(update.effective_chat.id) if args[1].lower() == "here" else args[1]
            chat_id = await _validated_chat_id(context, raw, "review")
            await _apply(update, {"REVIEW_CHAT_ID": chat_id}, f"审核群已设为 {chat_id}")
        elif action in {"api_review", "chat_review", "show_submitter"} and len(args) == 2:
            if args[1].lower() not in {"on", "off"}:
                raise ValueError("开关值必须是 on 或 off")
            key = {
                "api_review": "API_REVIEW_REQUIRED",
                "chat_review": "CHAT_REVIEW_REQUIRED",
                "show_submitter": "SHOW_SUBMITTER",
            }[action]
            enabled = args[1].lower() == "on"
            if key in {"API_REVIEW_REQUIRED", "CHAT_REVIEW_REQUIRED"} and enabled and not REVIEW_CHAT_ID:
                raise ValueError("请先设置审核群")
            await _apply(update, {key: enabled}, f"{action} 已设为 {args[1].lower()}")
        elif action == "reset" and len(args) == 1:
            await _require_empty_review_queue()
            clear_runtime_policy(_policy_path())
            managed = os.getenv("TELEPOST_MANAGED_RESTART", "").lower() in {"1", "true", "yes"}
            suffix = "Bot 将在约 6 秒内单独重载。" if managed else "请重启 Bot 后生效。"
            await update.effective_message.reply_text(f"✅ 已恢复部署配置。\n{suffix}")
            if managed:
                _schedule_restart()
        else:
            raise ValueError("用法：/botconfig [channel|review|api_review|chat_review|show_submitter|reset] …")
    except ValueError as exc:
        await update.effective_message.reply_text(f"❌ {exc}")
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ 配置失败：{str(exc)[:160]}")


async def botconfig_callback(update: Update, context: CallbackContext) -> None:
    if await _deny(update):
        return
    query = update.callback_query
    action = query.data.split(":", 1)[1]
    await query.answer()
    try:
        if action == "review_here":
            await _require_empty_review_queue()
            chat_id = await _validated_chat_id(context, str(update.effective_chat.id), "review")
            changes, message = {"REVIEW_CHAT_ID": chat_id}, f"审核群已设为 {chat_id}"
        elif action == "reset":
            await _require_empty_review_queue()
            clear_runtime_policy(_policy_path())
            changes, message = None, "已恢复部署配置"
        else:
            key, current = {
                "api_review": ("API_REVIEW_REQUIRED", API_REVIEW_REQUIRED),
                "chat_review": ("CHAT_REVIEW_REQUIRED", CHAT_REVIEW_REQUIRED),
                "show_submitter": ("SHOW_SUBMITTER", SHOW_SUBMITTER),
            }[action]
            if key in {"API_REVIEW_REQUIRED", "CHAT_REVIEW_REQUIRED"} and not current and not REVIEW_CHAT_ID:
                raise ValueError("请先设置审核群")
            changes, message = {key: not current}, "配置已切换"
        if changes:
            update_runtime_policy(_policy_path(), changes)
        managed = os.getenv("TELEPOST_MANAGED_RESTART", "").lower() in {"1", "true", "yes"}
        suffix = "Bot 将在约 6 秒内单独重载。" if managed else "请重启 Bot 后生效。"
        await query.edit_message_text(f"✅ {message}\n{suffix}")
        if managed:
            _schedule_restart()
    except (KeyError, ValueError) as exc:
        await query.edit_message_text(f"❌ {exc}")
    except Exception as exc:
        await query.edit_message_text(f"❌ 配置失败：{str(exc)[:160]}")
