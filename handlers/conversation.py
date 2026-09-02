"""
投稿会话状态机构建（UPLOAD → PREVIEW → EDIT）。

从 main.py 抽出，便于生产与集成测试复用同一份注册。
"""
from telegram import Update
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from models.state import STATE
from handlers.mode_selection import submit
from handlers.upload import handle_upload, done_upload, skip_upload, prompt_upload
from handlers.preview_handlers import (
    handle_edit_field_callback,
    handle_edit_input,
    handle_toggle_anon,
    handle_toggle_spoiler,
)
from handlers.publish import publish_submission
from handlers.command_handlers import cancel


def build_submission_conversation() -> ConversationHandler:
    """构建投稿 ConversationHandler：唯一入口 /submit 与「开始投稿」按钮。"""
    media_filters = (
        filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.AUDIO
        | filters.Document.ALL
    )
    return ConversationHandler(
        entry_points=[
            CommandHandler("submit", submit),
            # 底部菜单按钮"📝 开始投稿"（ReplyKeyboard 文本）必须作为 entry 进入
            # 状态机，否则只建 DB 会话、不建立内存状态，随后媒体会掉出状态机。
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Regex(r"开始投稿\s*$"),
                submit,
            ),
        ],
        states={
            STATE["UPLOAD"]: [
                CommandHandler("done_media", done_upload),
                CommandHandler("skip_media", skip_upload),
                MessageHandler(media_filters, handle_upload),
                MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_upload),
            ],
            STATE["PREVIEW"]: [
                CallbackQueryHandler(publish_submission, pattern="^publish$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
                CallbackQueryHandler(handle_toggle_anon, pattern="^toggle_anon$"),
                CallbackQueryHandler(handle_toggle_spoiler, pattern="^toggle_spoiler$"),
                CallbackQueryHandler(handle_edit_field_callback, pattern="^edit_(tag|title|note|link|media)$"),
            ],
            STATE["EDIT"]: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_input),
                MessageHandler(media_filters, handle_edit_input),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="submission_conversation",
    )
