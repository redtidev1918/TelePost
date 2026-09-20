"""
发布预览与快速编辑（PREVIEW / EDIT 状态）。

预览面板提供发布、字段编辑、补充媒体、匿名/剧透开关、取消。
编辑态收敛为单一 EDIT 状态，用 context.user_data['edit_field'] 区分字段。
"""
import logging
from datetime import datetime

from telegram import (
    LinkPreviewOptions,
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
)
from telegram.ext import ConversationHandler, CallbackContext

from models.state import STATE
from utils.helper_functions import process_tags, parse_json_list
from telepost.application.publication import channel_caption
from utils.submission import get_session, update_fields, append_entry, classify_message, entry_kind

logger = logging.getLogger(__name__)

from ui.messages import MessageFormatter

NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)

_EDIT_PROMPTS = {
    "edit_tag": MessageFormatter.edit_prompt("edit_tag"),
    "edit_title": MessageFormatter.edit_prompt("edit_title"),
    "edit_note": MessageFormatter.edit_prompt("edit_note"),
    "edit_link": MessageFormatter.edit_prompt("edit_link"),
    "edit_media": MessageFormatter.edit_prompt("edit_media"),
}


def _build_preview_text(row) -> str:
    """Private preview renders the same public caption the channel will show."""
    media_types = []
    for entry in parse_json_list(row["image_id"]):
        kind = entry.split(":", 1)[0].lower()
        media_types.append("photo" if kind == "image" else kind)
    media_types.extend("document" for _ in parse_json_list(row["document_id"]))
    return channel_caption({
        "link": row["link"] or "",
        "title": row["title"] or "",
        "note": row["note"] or "",
        "tags": row["tags"] or "",
        "spoiler": row["spoiler"] or "false",
        "anonymous": row["anonymous"] or "false",
        "media_types": media_types,
        "submitter_user_id": row["user_id"] if "user_id" in row.keys() else 0,
        "submitter_username": (row["username"] if "username" in row.keys() else "") or "",
    })


def _build_preview_keyboard(row=None) -> InlineKeyboardMarkup:
    is_anon = (row["anonymous"] if row and hasattr(row, "keys") and "anonymous" in row.keys() else "false") == "true"
    is_spoiler = (row["spoiler"] if row and hasattr(row, "keys") and "spoiler" in row.keys() else "false") == "true"
    has_tags = bool(row and hasattr(row, "keys") and "tags" in row.keys() and row["tags"])
    from telepost.domain.submission import (
        SubmissionDisposition,
        chat_disposition as _chat_disposition,
    )
    review_first = _chat_disposition() == SubmissionDisposition.REVIEW_REQUIRED
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✅ 提交审核" if (has_tags and review_first) else
            ("✅ 确认发布" if has_tags else
             ("🏷️ 填写标签后提交审核" if review_first else "🏷️ 填写标签后发布")),
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


async def _send_preview_media(update: Update, context: CallbackContext,
                               media_list, doc_list) -> None:
    """私聊里发真实媒体预览（与 Mini App 本地预览区分：chat 走真实 Telegram 媒体）。

    只在首次进入预览时发送一次（上下文标记 preview_media_sent），后续编辑刷新只改
    文字控制消息，不重复叠加媒体。发送失败逐条捕获并记日志，绝不让预览流程断裂。
    """
    if context is None or getattr(context, "user_data", None) is None:
        return
    if context.user_data.get("preview_media_sent"):
        return
    context.user_data["preview_media_sent"] = True

    try:
        chat_id = update.effective_chat.id
    except Exception:
        chat_id = update.effective_message.chat_id

    bot = getattr(context, "bot", None)
    if bot is None:
        return
    photos = []
    singles = []

    for entry in media_list:
        parts = entry.split(":", 2)
        kind = parts[0].lower()
        file_id = parts[1] if len(parts) > 1 else ""
        if not file_id:
            continue
        if kind == "video":
            singles.append(("video", file_id, None))
        elif kind == "animation":
            singles.append(("animation", file_id, None))
        elif kind == "audio":
            singles.append(("audio", file_id, None))
        else:
            photos.append(file_id)

    for entry in doc_list:
        parts = entry.split(":", 2)
        file_id = parts[1] if len(parts) > 1 else ""
        name = parts[2] if len(parts) > 2 and parts[2] else "未命名文件"
        if file_id:
            singles.append(("document", file_id, name))

    # 照片合并成相册（每批最多 10 张），其它媒体单独发送。
    for i in range(0, len(photos), 10):
        batch = photos[i:i + 10]
        try:
            await bot.send_media_group(chat_id=chat_id, media=[InputMediaPhoto(media=fid) for fid in batch])
        except Exception as exc:
            logger.warning("预览相册发送失败（忽略）: %s", exc)

    for kind, file_id, name in singles:
        try:
            if kind == "video":
                await bot.send_video(chat_id=chat_id, video=file_id)
            elif kind == "animation":
                await bot.send_animation(chat_id=chat_id, animation=file_id)
            elif kind == "audio":
                await bot.send_audio(chat_id=chat_id, audio=file_id)
            elif kind == "document":
                await bot.send_document(chat_id=chat_id, document=file_id, filename=name)
        except Exception as exc:
            logger.warning("预览媒体发送失败（忽略） kind=%s: %s", kind, exc)


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
            await update.callback_query.edit_message_text(
                text, reply_markup=keyboard, parse_mode="HTML",
                link_preview_options=NO_LINK_PREVIEW,
            )
        except Exception as e:
            logger.debug(f"刷新预览失败（内容未变化时属正常）: {e}")
    else:
        from utils.helper_functions import parse_json_list as _pl
        media_list = _pl(row["image_id"])
        doc_list = _pl(row["document_id"])
        await _send_preview_media(update, context, media_list, doc_list)
        await update.effective_message.reply_text(
            text, reply_markup=keyboard, parse_mode="HTML",
            link_preview_options=NO_LINK_PREVIEW,
        )
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
        await update.callback_query.edit_message_text(prompt, parse_mode="HTML")
    except Exception:
        try:
            await update.effective_message.reply_text(prompt, parse_mode="HTML")
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
            await message.reply_text("⚠️ 请发送支持的媒体（图片/视频/GIF/音频），或发送 /cancel 取消。", parse_mode="HTML")
            return STATE["EDIT"]
        count = await append_entry(user_id, entry)
        from telepost.domain.submission import (
            SubmissionDisposition,
            chat_disposition as _cd,
        )
        action = "提交审核" if _cd() == SubmissionDisposition.REVIEW_REQUIRED else "确认发布"
        await message.reply_text(
            f"✅ 已添加，当前共 {count} 个媒体。可继续发送，或发送 /done_media 返回预览并{action}。",
            parse_mode="HTML",
        )
        return await show_submission_preview(update, context)

    text = (message.text or "").strip()
    if field == "edit_tag":
        success, processed = process_tags(text)
        if not success or not processed:
            await message.reply_text("❌ 标签格式错误，请重新输入（最多 30 个，用逗号分隔）")
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
            await message.reply_text("⚠️ 链接须以 http:// 或 https:// 开头，或回复「无」清空")
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
