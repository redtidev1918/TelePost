"""
上传阶段（UPLOAD 状态）：统一接收媒体与文档。

取代原 media_handlers / document_handlers 两套互转逻辑——归类走
utils.submission.classify_message 单一实现，媒体与文档共享一个状态。
"""
import logging

from telegram import Update
from telegram.ext import ConversationHandler, CallbackContext

from config.settings import (
    BOT_MODE,
    MODE_MEDIA,
    MODE_DOCUMENT,
    ALLOWED_FILE_TYPES,
    MAX_SUBMISSION_FILES,
)
from models.state import STATE
from utils.file_validator import create_file_validator
from utils.submission import classify_message, entry_kind, append_entry, get_session

logger = logging.getLogger(__name__)

_file_validator = create_file_validator(ALLOWED_FILE_TYPES)


async def handle_upload(update: Update, context: CallbackContext) -> int:
    """接收一条媒体/文档消息，归类后追加到会话。"""
    user_id = update.effective_user.id
    message = update.message

    entry = classify_message(message)
    if entry is None:
        from ui.messages import MessageFormatter
        await message.reply_text(
            MessageFormatter.upload_supported_types(),
            parse_mode="HTML",
        )
        return STATE["UPLOAD"]

    kind = entry_kind(entry)

    # 模式限制（BOT_MODE 为部署级配置）
    if kind == "document":
        if BOT_MODE == MODE_MEDIA:
            from ui.messages import MessageFormatter
            await message.reply_text(MessageFormatter.upload_mode_limited("document"), parse_mode="HTML")
            return STATE["UPLOAD"]
        if not _file_validator.validate(message.document.file_name, message.document.mime_type)[0]:
            await message.reply_text(
                "⚠️ 不支持的文件类型。\n\n允许：" + _file_validator.get_allowed_types_description()
            )
            return STATE["UPLOAD"]
    elif BOT_MODE == MODE_DOCUMENT:
        from ui.messages import MessageFormatter
        await message.reply_text(MessageFormatter.upload_mode_limited("media"), parse_mode="HTML")
        return STATE["UPLOAD"]

    session = await get_session(user_id)
    from ui.messages import MessageFormatter
    if session is None:
        await message.reply_text(MessageFormatter.session_expired(), parse_mode="HTML")
        return ConversationHandler.END
    current_count = len(_parse(session["image_id"])) + len(_parse(session["document_id"]))
    if current_count >= MAX_SUBMISSION_FILES:
        await message.reply_text(
            MessageFormatter.upload_max_files(MAX_SUBMISSION_FILES),
            parse_mode="HTML",
        )
        return STATE["UPLOAD"]

    count = await append_entry(user_id, entry)
    if count == 0:
        await message.reply_text(MessageFormatter.session_expired(), parse_mode="HTML")
        return ConversationHandler.END

    total = current_count + (1 if entry else 0)
    await message.reply_text(
        MessageFormatter.upload_received(kind, total, MAX_SUBMISSION_FILES),
        parse_mode="HTML",
    )
    return STATE["UPLOAD"]


async def done_upload(update: Update, context: CallbackContext) -> int:
    """完成上传：校验至少有内容后进入预览。"""
    user_id = update.effective_user.id
    session = await get_session(user_id)
    from ui.messages import MessageFormatter
    if session is None:
        await update.message.reply_text(MessageFormatter.session_expired(), parse_mode="HTML")
        return ConversationHandler.END

    media_count = len(_parse(session["image_id"]))
    doc_count = len(_parse(session["document_id"]))

    if BOT_MODE == MODE_MEDIA and not media_count:
        await update.message.reply_text(MessageFormatter.upload_requires_content("media"), parse_mode="HTML")
        return STATE["UPLOAD"]
    if BOT_MODE == MODE_DOCUMENT and not doc_count:
        await update.message.reply_text(MessageFormatter.upload_requires_content("document"), parse_mode="HTML")
        return STATE["UPLOAD"]
    if not media_count and not doc_count:
        await update.message.reply_text(MessageFormatter.upload_requires_content("any"), parse_mode="HTML")
        return STATE["UPLOAD"]

    from handlers.preview_handlers import show_submission_preview
    return await show_submission_preview(update, context)


async def skip_upload(update: Update, context: CallbackContext) -> int:
    """跳过上传阶段（可选内容都没传时直接预览）。"""
    user_id = update.effective_user.id
    if await get_session(user_id) is None:
        from ui.messages import MessageFormatter
        await update.message.reply_text(MessageFormatter.session_expired(), parse_mode="HTML")
        return ConversationHandler.END
    from handlers.preview_handlers import show_submission_preview
    return await show_submission_preview(update, context)


async def prompt_upload(update: Update, context: CallbackContext) -> int:
    """上传阶段收到文字时的提示。"""
    from ui.messages import MessageFormatter
    await update.message.reply_text(MessageFormatter.prompt_upload_text(), parse_mode="HTML")
    return STATE["UPLOAD"]


def _parse(raw):
    from utils.helper_functions import parse_json_list
    return parse_json_list(raw)
