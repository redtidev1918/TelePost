"""
发布前预览与快速编辑

投稿流程在剧透确认后不再直接发布，而是进入 PUBLISH 预览页：
用户可以确认发布、快速修改字段、补充媒体，或取消。
"""
import json
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CallbackContext

from models.state import STATE
from database.db_manager import get_db
from utils.helper_functions import process_tags

logger = logging.getLogger(__name__)


def _load_list(raw):
    try:
        v = json.loads(raw) if raw else []
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _build_preview_text(row) -> str:
    """根据 submissions 行构建预览文本（纯文本，无 parse_mode，避免转义问题）"""
    media_list = _load_list(row["image_id"])
    doc_list = _load_list(row["document_id"])

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
    is_anonymous = (row["anonymous"] if "anonymous" in row.keys() else "false") == "true"
    lines.append(f"🔞 剧透：{'是' if (row['spoiler'] or '') == 'true' else '否'}")
    lines.append(f"🕵️ 匿名：{'是（频道内不显示投稿人）' if is_anonymous else '否（显示投稿人）'}")
    lines.append("")
    if row["tags"]:
        lines.append("确认无误请点击下方按钮发布，或先快速修改。")
    else:
        lines.append("⚠️ 发布前必须填写标签；其余字段均可留空。")
    return "\n".join(lines)


def _build_preview_keyboard(row=None) -> InlineKeyboardMarkup:
    is_anonymous = False
    is_spoiler = False
    if row is not None and hasattr(row, "keys"):
        if "anonymous" in row.keys():
            is_anonymous = (row["anonymous"] or "false") == "true"
        if "spoiler" in row.keys():
            is_spoiler = (row["spoiler"] or "false") == "true"
    has_tags = bool(row and hasattr(row, "keys") and "tags" in row.keys() and row["tags"])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✅ 确认发布" if has_tags else "🏷️ 填写标签后发布",
            callback_data="publish" if has_tags else "edit_tag",
        )],
        [
            InlineKeyboardButton("🏷️ 改标签", callback_data="edit_tag"),
            InlineKeyboardButton("🔖 改标题", callback_data="edit_title"),
        ],
        [
            InlineKeyboardButton("📝 改简介", callback_data="edit_note"),
            InlineKeyboardButton("🔗 改链接", callback_data="edit_link"),
        ],
        [
            InlineKeyboardButton("📎 补充媒体", callback_data="edit_media"),
            InlineKeyboardButton("❌ 取消投稿", callback_data="cancel"),
        ],
        [
            InlineKeyboardButton(f"🕵️ 匿名：{'开' if is_anonymous else '关'}", callback_data="toggle_anon"),
            InlineKeyboardButton(f"🔞 剧透：{'开' if is_spoiler else '关'}", callback_data="toggle_spoiler"),
        ],
    ])


async def show_submission_preview(update: Update, context: CallbackContext) -> int:
    """
    展示/刷新发布预览。
    回调来源编辑原消息，文本来源发送新消息；始终返回 PUBLISH 状态。
    """
    user_id = update.effective_user.id
    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute("SELECT * FROM submissions WHERE user_id=?", (user_id,))
        row = await c.fetchone()

    if not row:
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
    return STATE['PUBLISH']


async def handle_edit_field_callback(update: Update, context: CallbackContext) -> int:
    """
    处理预览页的字段编辑按钮，
    切换到对应的快速编辑状态。
    """
    query = update.callback_query
    field = query.data

    prompts = {
        "edit_tag": "🏷️ 请发送新的标签（用逗号分隔，直接覆盖原标签）：",
        "edit_title": "🔖 请发送新的标题（回复\"无\"清空，上限 100 字）：",
        "edit_note": "📝 请发送新的简介（回复\"无\"清空，上限 600 字）：",
        "edit_link": "🔗 请发送新的链接（回复\"无\"清空，须以 http:// 或 https:// 开头）：",
        "edit_media": "📎 请直接发送要补充的媒体文件（可多次发送，完成后点击\"✅ 确认发布\"）：",
    }
    prompt = prompts.get(field)
    if not prompt:
        return STATE['PUBLISH']

    try:
        await query.answer()
    except Exception:
        pass

    next_state = {
        "edit_tag": STATE['EDIT_TAG'],
        "edit_title": STATE['EDIT_TITLE'],
        "edit_note": STATE['EDIT_NOTE'],
        "edit_link": STATE['EDIT_LINK'],
        "edit_media": STATE['EDIT_MEDIA'],
    }[field]

    try:
        await query.edit_message_text(prompt)
    except Exception as e:
        logger.debug(f"编辑提示消息失败: {e}")
        await update.effective_message.reply_text(prompt)
    return next_state


async def handle_toggle_anon(update: Update, context: CallbackContext) -> int:
    """预览页匿名开关：就地切换后刷新预览"""
    query = update.callback_query
    user_id = update.effective_user.id

    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute("SELECT anonymous FROM submissions WHERE user_id=?", (user_id,))
        row = await c.fetchone()
        current = (row["anonymous"] if row and "anonymous" in row.keys() else "false") == "true"
        new_value = "false" if current else "true"
        await c.execute("UPDATE submissions SET anonymous=?, timestamp=? WHERE user_id=?",
                        (new_value, datetime.now().timestamp(), user_id))

    try:
        await query.answer()
    except Exception:
        pass
    return await show_submission_preview(update, context)


async def handle_toggle_spoiler(update: Update, context: CallbackContext) -> int:
    """预览页剧透开关：就地切换后刷新预览"""
    query = update.callback_query
    user_id = update.effective_user.id

    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute("SELECT spoiler FROM submissions WHERE user_id=?", (user_id,))
        row = await c.fetchone()
        current = (row["spoiler"] if row and "spoiler" in row.keys() else "false") == "true"
        new_value = "false" if current else "true"
        await c.execute("UPDATE submissions SET spoiler=?, timestamp=? WHERE user_id=?",
                        (new_value, datetime.now().timestamp(), user_id))

    try:
        await query.answer()
    except Exception:
        pass
    return await show_submission_preview(update, context)


async def handle_edit_tag(update: Update, context: CallbackContext) -> int:
    """快速编辑：覆盖标签后回到预览"""
    user_id = update.effective_user.id
    raw_tags = update.message.text.strip()
    success, processed_tags = process_tags(raw_tags)
    if not success or not processed_tags:
        await update.message.reply_text("❌ 标签格式错误，请重新输入（最多30个，用逗号分隔）")
        return STATE['EDIT_TAG']

    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute("UPDATE submissions SET tags=?, timestamp=? WHERE user_id=?",
                        (processed_tags, datetime.now().timestamp(), user_id))

    await update.message.reply_text("✅ 标签已更新")
    return await show_submission_preview(update, context)


async def handle_edit_note(update: Update, context: CallbackContext) -> int:
    """快速编辑：覆盖简介后回到预览"""
    user_id = update.effective_user.id
    note = update.message.text.strip()
    note_to_store = "" if note.lower() == "无" else note[:600]

    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute("UPDATE submissions SET note=?, timestamp=? WHERE user_id=?",
                        (note_to_store, datetime.now().timestamp(), user_id))

    await update.message.reply_text("✅ 简介已更新")
    return await show_submission_preview(update, context)


async def handle_edit_title(update: Update, context: CallbackContext) -> int:
    """快速编辑：覆盖标题后回到预览"""
    title = update.message.text.strip()
    title = "" if title.lower() == "无" else title[:100]
    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute("UPDATE submissions SET title=?, timestamp=? WHERE user_id=?",
                        (title, datetime.now().timestamp(), update.effective_user.id))
    await update.message.reply_text("✅ 标题已更新")
    return await show_submission_preview(update, context)


async def handle_edit_link(update: Update, context: CallbackContext) -> int:
    """快速编辑：覆盖链接后回到预览"""
    link = update.message.text.strip()
    if link.lower() == "无":
        link = ""
    elif not link.startswith(("http://", "https://")):
        await update.message.reply_text("⚠️ 链接须以 http:// 或 https:// 开头，或回复\"无\"清空")
        return STATE['EDIT_LINK']
    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute("UPDATE submissions SET link=?, timestamp=? WHERE user_id=?",
                        (link, datetime.now().timestamp(), update.effective_user.id))
    await update.message.reply_text("✅ 链接已更新")
    return await show_submission_preview(update, context)


async def handle_edit_media(update: Update, context: CallbackContext) -> int:
    """快速编辑：补充媒体后回到预览"""
    user_id = update.effective_user.id
    message = update.message

    new_media = _extract_media_entry(message)
    if not new_media:
        await message.reply_text("⚠️ 请发送支持的媒体文件（图片/视频/GIF/音频）")
        return STATE['EDIT_MEDIA']

    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute("SELECT image_id FROM submissions WHERE user_id=?", (user_id,))
        row = await c.fetchone()
        media_list = _load_list(row["image_id"]) if row else []
        if len(media_list) >= 50:
            await message.reply_text("⚠️ 已达到媒体上传上限（50个）")
            return await show_submission_preview(update, context)
        media_list.append(new_media)
        await c.execute("UPDATE submissions SET image_id=?, timestamp=? WHERE user_id=?",
                        (json.dumps(media_list), datetime.now().timestamp(), user_id))

    await message.reply_text(f"✅ 已添加，当前共 {len(media_list)} 个媒体。可继续发送，或点击\"✅ 确认发布\"")
    return await show_submission_preview(update, context)


def _extract_media_entry(message):
    """从消息中提取 'type:file_id' 形式的媒体条目（与 media_handlers 保持一致）"""
    if message.photo:
        return f"photo:{message.photo[-1].file_id}"
    if message.video:
        return f"video:{message.video.file_id}"
    if message.animation:
        return f"animation:{message.animation.file_id}"
    if message.audio:
        return f"audio:{message.audio.file_id}"
    if message.document:
        mime = message.document.mime_type or ""
        if mime == "image/gif":
            return f"animation:{message.document.file_id}"
        if mime.startswith("audio/"):
            return f"audio:{message.document.file_id}"
    return None
