"""
入口处理：/start、/submit 与投稿模式提示。

会话状态只有 UPLOAD → PREVIEW → EDIT（见 models.state）。
上传阶段统一收媒体/文档（归类和限制见 handlers.upload），不再有
MEDIA/DOCUMENT 两套状态互转。
"""
import logging
from datetime import datetime

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ConversationHandler, CallbackContext

from config.settings import BOT_MODE, MODE_MEDIA, MODE_DOCUMENT, SUBMIT_LIMIT_PER_HOUR
from models.state import STATE
from utils.blacklist import is_blacklisted
from utils.submission import create_session

logger = logging.getLogger(__name__)


async def submit(update: Update, context: CallbackContext) -> int:
    """开始新投稿：建会话 → 进入上传阶段。"""
    logger.info(f"收到 /submit，user_id: {update.effective_user.id}")
    user_id = update.effective_user.id
    user = update.effective_user
    username = user.username or user.first_name or f"user{user.id}"

    if is_blacklisted(user_id):
        await update.message.reply_text("⚠️ 您已被列入黑名单，无法投稿。")
        return ConversationHandler.END

    # 投稿频率限制（内存滑动窗口，重启清零）
    if SUBMIT_LIMIT_PER_HOUR > 0:
        import time as _t
        now = _t.time()
        history = context.bot_data.setdefault("submit_times", {}).setdefault(user_id, [])
        history[:] = [t for t in history if now - t < 3600]
        if len(history) >= SUBMIT_LIMIT_PER_HOUR:
            await update.message.reply_text(
                f"⚠️ 投稿过于频繁（每小时最多 {SUBMIT_LIMIT_PER_HOUR} 次），请稍后再试。"
            )
            return ConversationHandler.END
        history.append(now)

    try:
        await create_session(user_id, username, BOT_MODE.lower())
    except Exception as e:
        logger.error(f"初始化会话失败: {e}", exc_info=True)
        await update.message.reply_text("❌ 初始化失败，请稍后再试")
        return ConversationHandler.END

    await update.message.reply_text(_upload_hint(BOT_MODE), reply_markup=ReplyKeyboardRemove())
    return STATE["UPLOAD"]


def _upload_hint(mode: str) -> str:
    common = "\n\n预览页仅标签必填；匿名和剧透默认关闭。\n随时发送 /cancel 取消投稿。"
    if mode == MODE_MEDIA:
        return (
            "📮 请直接上传媒体：\n"
            "• 相册图片、视频、GIF、音频会归为媒体\n"
            "• 最多 50 个；上传完成后发送 /done_media 打开预览" + common
        )
    if mode == MODE_DOCUMENT:
        return (
            "📮 请上传文档：\n"
            "• 以附件发送的图片、压缩包、PDF 等会归为文件\n"
            "• 最多 10 个；上传完成后发送 /done_media 打开预览" + common
        )
    return (
        "📮 请直接上传内容：\n"
        "• 相册图片、视频、GIF、音频会归为媒体\n"
        "• 以附件发送的图片、压缩包、PDF 等会归为文件\n"
        "• 可以混合上传，完成后发送 /done_media 打开预览" + common
    )


async def start(update: Update, context: CallbackContext) -> int:
    """/start：显示欢迎与命令清单（不进投稿会话）。"""
    logger.info(f"收到 /start，user_id: {update.effective_user.id}")
    user = update.effective_user
    username = user.username or user.first_name or f"user{user.id}"

    if is_blacklisted(user.id):
        await update.message.reply_text("⚠️ 您已被列入黑名单，无法使用。")
        return ConversationHandler.END

    welcome = (
        f"👋 你好 {username}！欢迎使用投稿机器人！\n\n"
        "📮 **投稿**：发送 /submit 开始（图片/视频/压缩包/PDF 等）\n"
        "📊 **查询**：/search 搜索 · /mystats 统计 · /myposts 我的投稿\n"
        "🔥 **热门**：/hot 排行 · /tags 标签云\n"
        "❓ /help 完整帮助 · /cancel 取消投稿\n\n"
        "💡 想要投稿？直接发送 /submit 即可开始！"
    )
    try:
        from ui.keyboards import Keyboards
        reply_markup = Keyboards.main_menu()
    except Exception:
        reply_markup = ReplyKeyboardRemove()
    await update.message.reply_text(welcome, reply_markup=reply_markup)
    return ConversationHandler.END
