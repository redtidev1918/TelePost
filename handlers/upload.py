"""
上传阶段（UPLOAD 状态）：统一接收媒体与文档。

取代原 media_handlers / document_handlers 两套互转逻辑——归类走
utils.submission.classify_message 单一实现，媒体与文档共享一个状态。
"""
import logging

from telegram import Update
from telegram.ext import ConversationHandler, CallbackContext

from config.settings import BOT_MODE, MODE_MEDIA, MODE_DOCUMENT, ALLOWED_FILE_TYPES
from models.state import STATE
from utils.file_validator import create_file_validator
from utils.submission import classify_message, entry_kind, append_entry, get_session

logger = logging.getLogger(__name__)

_file_validator = create_file_validator(ALLOWED_FILE_TYPES)

MEDIA_LIMIT = 50
DOCUMENT_LIMIT = 10


async def handle_upload(update: Update, context: CallbackContext) -> int:
    """接收一条媒体/文档消息，归类后追加到会话。"""
    user_id = update.effective_user.id
    message = update.message

    entry = classify_message(message)
    if entry is None:
        await message.reply_text(
            "⚠️ 请发送支持的媒体或文件：\n"
            "• 图片/视频/GIF/音频：直接发送\n"
            "• 压缩包/PDF/其它：以附件发送"
        )
        return STATE["UPLOAD"]

    kind = entry_kind(entry)

    # 模式限制（BOT_MODE 为部署级配置）
    if kind == "document":
        if BOT_MODE == MODE_MEDIA:
            await message.reply_text("⚠️ 当前为媒体投稿模式，不支持文件附件。")
            return STATE["UPLOAD"]
        if not _file_validator.validate(message.document.file_name, message.document.mime_type)[0]:
            await message.reply_text(
                "⚠️ 不支持的文件类型。允许：" + _file_validator.get_allowed_types_description()
            )
            return STATE["UPLOAD"]
    elif BOT_MODE == MODE_DOCUMENT:
        await message.reply_text("⚠️ 当前为文档投稿模式，请以附件发送文件。")
        return STATE["UPLOAD"]

    count = await append_entry(user_id, entry)
    if count == 0:
        await message.reply_text("❌ 会话已过期，请重新发送 /submit")
        return ConversationHandler.END

    if kind == "document":
        await message.reply_text(
            f"✅ 已接收文件，共计 {count} 个。\n继续上传，完成后发送 /done_media。"
        )
    else:
        await message.reply_text(
            f"✅ 已接收媒体，共计 {count} 个。\n继续上传，完成后发送 /done_media。"
        )
    return STATE["UPLOAD"]


async def done_upload(update: Update, context: CallbackContext) -> int:
    """完成上传：校验至少有内容后进入预览。"""
    user_id = update.effective_user.id
    session = await get_session(user_id)
    if session is None:
        await update.message.reply_text("❌ 会话已过期，请重新发送 /submit")
        return ConversationHandler.END

    media_count = len(_parse(session["image_id"]))
    doc_count = len(_parse(session["document_id"]))

    if BOT_MODE == MODE_MEDIA and not media_count:
        await update.message.reply_text("⚠️ 请至少发送一个媒体文件")
        return STATE["UPLOAD"]
    if BOT_MODE == MODE_DOCUMENT and not doc_count:
        await update.message.reply_text("⚠️ 请至少发送一个文件")
        return STATE["UPLOAD"]
    if not media_count and not doc_count:
        await update.message.reply_text("⚠️ 请至少上传一个媒体或文件")
        return STATE["UPLOAD"]

    from handlers.preview_handlers import show_submission_preview
    return await show_submission_preview(update, context)


async def skip_upload(update: Update, context: CallbackContext) -> int:
    """跳过上传阶段（可选内容都没传时直接预览）。"""
    user_id = update.effective_user.id
    if await get_session(user_id) is None:
        await update.message.reply_text("❌ 会话已过期，请重新发送 /submit")
        return ConversationHandler.END
    from handlers.preview_handlers import show_submission_preview
    return await show_submission_preview(update, context)


async def prompt_upload(update: Update, context: CallbackContext) -> int:
    """上传阶段收到文字时的提示。"""
    await update.message.reply_text(
        "请直接发送媒体或文件，或发送 /done_media 打开预览、/cancel 取消。"
    )
    return STATE["UPLOAD"]


def _parse(raw):
    from utils.helper_functions import parse_json_list
    return parse_json_list(raw)
