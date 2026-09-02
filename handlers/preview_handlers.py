"""
发布预览与快速编辑（PREVIEW / EDIT 状态）。

预览面板提供发布、字段编辑、补充媒体、匿名/剧透开关、取消。
编辑态收敛为单一 EDIT 状态，用 context.user_data['edit_field'] 区分字段。
"""
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CallbackContext

from models.state import STATE
from utils.helper_functions import process_tags, parse_json_list
from utils.submission import get_session, update_fields, append_entry, classify_message, entry_kind

logger = logging.getLogger(__name__)

_EDIT_PROMPTS = {
    "edit_tag": "🏷️ 请发送新的标签（用逗号分隔，直接覆盖原标签）：",
    "edit_title": "🔖 请发送新的标题（回复“无”清空，上限 100 字）：",
    "edit_note": "📝 请发送新的简介（回复“无”清空，上限 600 字）：",
    "edit_link": "🔗 请发送新的链接（回复“无”清空，须以 http:// 或 https:// 开头）：",
    "edit_media": "📎 请直接发送要补充的媒体（图片/视频/GIF/音频），完成后点“✅ 确认发布”：",
}


def _build_preview_text(row) -> str:
    media_list = parse_json_list(row["image_id"])
    doc_list = parse_json_list(row["document_id"])
    lines = ["📋 发布预览", ""]
    if media_list:
        lines.append(f"📎 媒体：{len(media_list)} 个")
    if doc_list:
        lines.append(f"📄 文档：{len(doc_list)} 个")
    lines.append(f"🏷 标签：{row['tags'] or '（未设置）'}")
    if row["link"]:
        lines.append(f"🔗 链接：{row['link']}")
    if row["title"]:
        lines.append(f"🔖 标题：{row['title']}")
    if row["note"]:
        note = row["note"]
        suffix = " …" if len(note) > 80 else ""
        lines.append(f"📝 简介：{note[:80]}{suffix}")
    is_anon = (row["anonymous"] if "anonymous" in row.keys() else "false") == "true"
    lines.append(f"🔞 剧透：{'是' if (row['spoiler'] or '') == 'true' else '否'}")
    lines.append(f"🕵️ 匿名：{'是（频道内不显示投稿人）' if is_anon else '否（显示投稿人）'}")
    lines.append("")
    lines.append("确认无误请点击下方按钮发布，或先快速修改。" if row["tags"] else "⚠️ 发布前必须填写标签；其余字段均可留空。")
    return "\n".join(lines)


def _build_preview_keyboard(row=None) -> InlineKeyboardMarkup:
    is_anon = (row["anonymous"] if row and hasattr(row, "keys") and "anonymous" in row.keys() else "false") == "true"
    is_spoiler = (row["spoiler"] if row and hasattr(row, "keys") and "spoiler" in row.keys() else "false") == "true"
    has_tags = bool(row and hasattr(row, "keys") and "tags" in row.keys() and row["tags"])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 确认发布" if has_tags else "🏷️ 填写标签后发布",
                              callback_data="publish" if has_tags else "edit_tag")],
        [InlineKeyboardButton("🏷️ 改标签", callback_data="edit_tag"),
         InlineKeyboardButton("🔖 改标题", callback_data="edit_title")],
        [InlineKeyboardButton("📝 改简介", callback_data="edit_note"),
         InlineKeyboardButton("🔗 改链接", callback_data="edit_link")],
        [InlineKeyboardButton("📎 补充媒体", callback_data="edit_media"),
         InlineKeyboardButton("❌ 取消投稿", callback_data="cancel")],
        [InlineKeyboardButton(f"🕵️ 匿名：{'开' if is_anon else '关'}", callback_data="toggle_anon"),
         InlineKeyboardButton(f"🔞 剧透：{'开' if is_spoiler else '关'}", callback_data="toggle_spoiler")],
    ])


async def show_submission_preview(update: Update, context: CallbackContext) -> int:
    """展示/刷新预览；回调编辑原消息，文本来源发送新消息。"""
    user_id = update.effective_user.id
    row = await get_session(user_id)
    if row is None:
        try:
            target = update.callback_query if update.callback_query else update.effective_message
            await target.reply_text("❌ 会话已过期，请重新发送 /submit")
        except Exception:
            pass
        return ConversationHandler.END

    text = _build_preview_text(row)
    keyboard = _build_preview_keyboard(row)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard)
        except Exception as e:
            logger.debug(f"刷新预览失败（内容未变化时属正常）: {e}")
    else:
        await update.effective_message.reply_text(text, reply_markup=keyboard)
    return STATE["PREVIEW"]


async def handle_edit_field_callback(update: Update, context: CallbackContext) -> int:
    """预览页字段编辑按钮：切到 EDIT 状态并记录编辑目标。"""
    field = update.callback_query.data
    prompt = _EDIT_PROMPTS.get(field)
    if not prompt:
        return STATE["PREVIEW"]
    context.user_data["edit_field"] = field
    try:
        await update.callback_query.answer()
    except Exception:
        pass
    try:
        await update.callback_query.edit_message_text(prompt)
    except Exception:
        await update.effective_message.reply_text(prompt)
    return STATE["EDIT"]


async def handle_edit_input(update: Update, context: CallbackContext) -> int:
    """EDIT 状态统一输入：按 edit_field 处理文本或媒体，完成后回预览。"""
    field = context.user_data.get("edit_field")
    if not field:
        return await show_submission_preview(update, context)

    user_id = update.effective_user.id
    message = update.message

    if field == "edit_media":
        entry = classify_message(message)
        if entry is None or entry_kind(entry) == "document":
            await message.reply_text("⚠️ 请发送支持的媒体（图片/视频/GIF/音频）")
            return STATE["EDIT"]
        count = await append_entry(user_id, entry)
        await message.reply_text(f"✅ 已添加，当前共 {count} 个媒体。可继续发送，或点“✅ 确认发布”")
        return await show_submission_preview(update, context)

    text = (message.text or "").strip()
    if field == "edit_tag":
        success, processed = process_tags(text)
        if not success or not processed:
            await message.reply_text("❌ 标签格式错误，请重新输入（最多30个，用逗号分隔）")
            return STATE["EDIT"]
        await update_fields(user_id, tags=processed)
        await message.reply_text("✅ 标签已更新")
    elif field == "edit_title":
        await update_fields(user_id, title=("" if text.lower() == "无" else text[:100]))
        await message.reply_text("✅ 标题已更新")
    elif field == "edit_note":
        await update_fields(user_id, note=("" if text.lower() == "无" else text[:600]))
        await message.reply_text("✅ 简介已更新")
    elif field == "edit_link":
        if text.lower() == "无":
            link = ""
        elif not text.startswith(("http://", "https://")):
            await message.reply_text("⚠️ 链接须以 http:// 或 https:// 开头，或回复“无”清空")
            return STATE["EDIT"]
        else:
            link = text
        await update_fields(user_id, link=link)
        await message.reply_text("✅ 链接已更新")
    else:
        await message.reply_text("⚠️ 未知编辑项，已返回预览")
    return await show_submission_preview(update, context)


async def handle_toggle_anon(update: Update, context: CallbackContext) -> int:
    row = await get_session(update.effective_user.id)
    current = (row["anonymous"] if row and "anonymous" in row.keys() else "false") == "true"
    await update_fields(update.effective_user.id, anonymous="false" if current else "true")
    try:
        await update.callback_query.answer()
    except Exception:
        pass
    return await show_submission_preview(update, context)


async def handle_toggle_spoiler(update: Update, context: CallbackContext) -> int:
    row = await get_session(update.effective_user.id)
    current = (row["spoiler"] if row and "spoiler" in row.keys() else "false") == "true"
    await update_fields(update.effective_user.id, spoiler="false" if current else "true")
    try:
        await update.callback_query.answer()
    except Exception:
        pass
    return await show_submission_preview(update, context)
