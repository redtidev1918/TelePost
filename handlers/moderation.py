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
    if update.effective_user.id not in ADMIN_IDS:
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
