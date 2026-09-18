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

from config.settings import (
    BOT_MODE,
    MODE_MEDIA,
    MODE_DOCUMENT,
    SUBMIT_LIMIT_PER_HOUR,
    MAX_SUBMISSION_FILES,
)
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

    # 每次 /submit 是一次新投稿：清除上一个会话的“已发媒体预览”标记，
    # 否则再次投稿时真实媒体预览不会重新发送。
    context.user_data.pop("preview_media_sent", None)

    await update.message.reply_text(_upload_hint(BOT_MODE), parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
    return STATE["UPLOAD"]


def _upload_hint(mode: str) -> str:
    """投稿上传阶段提示（兼容旧调用，内容收敛到 ui.messages）。"""
    from ui.messages import MessageFormatter
    return MessageFormatter.submit_hint(mode, MAX_SUBMISSION_FILES)


async def start(update: Update, context: CallbackContext) -> int:
    """/start：显示欢迎与命令清单（不进投稿会话）。"""
    logger.info(f"收到 /start，user_id: {update.effective_user.id}")
    user = update.effective_user
    username = user.username or user.first_name or f"user{user.id}"

    if is_blacklisted(user.id):
        await update.message.reply_text("⚠️ 您已被列入黑名单，无法使用。")
        return ConversationHandler.END

    welcome = (
        f"👋 <b>你好，{username}！</b>\n\n"
        "我是投稿机器人，帮你把图文内容发布到频道。\n"
        "想投稿？发送 <code>/submit</code> 就开始。\n\n"
        "📚 <b>常用功能</b>\n"
        "<code>/submit</code> 开始投稿\n"
        "<code>/search</code> 搜索内容\n"
        "<code>/mystats</code> 我的统计 · <code>/myposts</code> 我的投稿\n"
        "<code>/hot</code> 热门排行 · <code>/tags</code> 标签云\n"
        "<code>/help</code> 完整帮助 · <code>/cancel</code> 取消投稿\n\n"
        "💡 <i>也可以点击下方菜单按钮快速操作。</i>"
    )
    try:
        from ui.keyboards import Keyboards
        reply_markup = Keyboards.main_menu()
    except Exception:
        reply_markup = ReplyKeyboardRemove()
    await update.message.reply_text(welcome, parse_mode="HTML", reply_markup=reply_markup)
    return ConversationHandler.END
