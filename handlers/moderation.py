"""Admin moderation callbacks (§moderation).

These buttons ride the ADMIN DM (and the review card), so governance works for
chat_direct posts that never produced a review. Identity-based subjects never
point at a review row: user:{telegram_user_id} | api:{token_id}.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackContext

from config.settings import ADMIN_IDS

logger = logging.getLogger(__name__)


async def ban_user_command(update: Update, context: CallbackContext):
    """/ban_user <用户ID> [原因] — 手动封禁用户（按钮失效时的兜底）"""
    actor = update.effective_user
    from config.settings import ADMIN_IDS as allowed_admins
    if actor is None or actor.id not in allowed_admins:
        await update.message.reply_text("⛔ 此命令仅限管理员使用")
        return
    args = list(context.args or [])
    if not args or not str(args[0]).strip().lstrip("#").isdigit():
        await update.message.reply_text(
            "用法：/ban_user <用户ID> [原因]\n"
            "例如：/ban_user 123456789 发布违规内容"
        )
        return
    target = int(str(args[0]).strip().lstrip("#"))
    if target <= 0:
        await update.message.reply_text("❌ 用户ID必须为正整数")
        return
    reason = " ".join(args[1:]).strip() or "admin command"
    from telepost.storage.sqlite.moderation import ModerationRepository, user_subject
    from utils.blacklist import add_to_blacklist

    subject = user_subject(target)
    await ModerationRepository().add_block(
        subject, reason=reason, created_by=actor.id
    )
    # Legacy chat blacklist stays in sync so all entry points agree.
    await add_to_blacklist(target, reason)
    try:
        from telepost.observability import audit
        await audit.record_event(
            "moderation.user_blocked",
            actor=actor.id,
            detail={"subject": subject, "reason": reason},
        )
    except Exception:
        logger.debug("记录封禁审计事件失败", exc_info=True)
    await update.message.reply_text(
        f"✅ 已封禁用户 {target}\n原因：{reason}\n"
        "该用户后续投稿将自动被拦截。"
    )


async def ban_api_command(update: Update, context: CallbackContext):
    """/ban_api <token编号> [原因] — 手动禁用 API token（按钮失效时的兜底）"""
    actor = update.effective_user
    from config.settings import ADMIN_IDS as allowed_admins
    if actor is None or actor.id not in allowed_admins:
        await update.message.reply_text("⛔ 此命令仅限管理员使用")
        return
    args = list(context.args or [])
    if not args or not str(args[0]).strip().lstrip("#").isdigit():
        await update.message.reply_text(
            "用法：/ban_api <token编号> [原因]\n"
            "例如：/ban_api 9 投稿异常"
        )
        return
    token_id = int(str(args[0]).strip().lstrip("#"))
    if token_id <= 0:
        await update.message.reply_text("❌ token编号必须为正整数")
        return
    reason = " ".join(args[1:]).strip() or "admin command"
    from telepost.storage.sqlite.moderation import ModerationRepository, api_subject

    subject = api_subject(token_id)
    await ModerationRepository().add_block(
        subject, reason=reason, created_by=actor.id
    )
    try:
        from telepost.observability import audit
        await audit.record_event(
            "moderation.api_blocked",
            actor=actor.id,
            detail={"subject": subject, "reason": reason},
        )
    except Exception:
        logger.debug("记录禁用API审计事件失败", exc_info=True)
    await update.message.reply_text(
        f"✅ 已禁用 API token #{token_id}\n原因：{reason}\n"
        "该 token 后续自动投稿将不接受。"
    )


async def admin_block(update: Update, context: CallbackContext):
    query = update.callback_query
    try:
        data = query.data or ""
        if not data.startswith("admin_block:"):
            raise ValueError
        _, subject = data.split(":", 1)
        block_type, value = subject.split(":", 1)
    except ValueError:
        await query.answer("无效的封禁请求", show_alert=True)
        return
    if block_type not in ("user", "api"):
        await query.answer("无效的封禁类型", show_alert=True)
        return
    from config.settings import ADMIN_IDS as allowed_admins
    if update.effective_user.id not in allowed_admins:
        await query.answer("你没有审核权限", show_alert=True)
        return
    from telepost.storage.sqlite.moderation import (
        ModerationRepository, api_subject, user_subject,
    )

    canonical = user_subject(value) if block_type == "user" else api_subject(value)
    reason = "admin DM governance"
    await ModerationRepository().add_block(
        canonical, reason=reason, created_by=update.effective_user.id
    )
    try:
        from telepost.observability import audit
        await audit.record_event(
            f"moderation.{block_type}_blocked",
            actor=update.effective_user.id,
            detail={"subject": canonical, "reason": reason},
        )
    except Exception:
        logger.debug("记录封禁审计事件失败", exc_info=True)
    label = "用户" if block_type == "user" else "API token"
    await query.answer(f"已封禁{label}：{value}", show_alert=True)
    try:
        text = str(query.message.text or "")
        suffix = f"\n\n🚫 已封禁{label} #{value}（由管理员 {update.effective_user.id} 记录）"
        await query.edit_message_text(text + suffix if text else "已封禁")
    except Exception as exc:
        logger.debug("封禁后更新管理通知失败: %s", exc)
