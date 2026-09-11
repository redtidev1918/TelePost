"""
Telegram 投稿机器人主程序
支持媒体和文档投稿
"""
import sys
import signal
import asyncio
import platform
import logging
import os
from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    BaseHandler,
    filters,
    ConversationHandler,
    CallbackContext,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    PicklePersistence
)
from dotenv import load_dotenv

# 配置相关导入
from config.settings import (
    TOKEN, RUN_MODE, RUN_MODE_REQUESTED, WEBHOOK_URL, WEBHOOK_PORT, WEBHOOK_PATH,
    WEBHOOK_SECRET_TOKEN, DB_PATH
)
from models.state import STATE

# 数据库相关导入
from database.db_manager import init_db, cleanup_old_data
from utils.database import (
    is_blacklisted, 
    initialize_database
)

# 工具函数导入
from utils.logging_config import setup_logging
from utils.helper_functions import CONFIG
from utils.maintenance_jobs import clean_logs_job, pixivflow_maintain_job, scheduled_time

# 处理程序导入 - 按功能分组
# 基础命令
from handlers import (
    start, help_command, cancel, settings
)

# 黑名单管理
from utils.blacklist import manage_blacklist, init_blacklist
from handlers.command_handlers import blacklist_add, blacklist_remove, blacklist_list, catch_all, debug, handle_menu_shortcuts
from handlers.botconfig import botconfig, botconfig_callback

# 投稿处理（状态机由 handlers.conversation 构建）
from handlers.review import expire_stale_reviews, reconcile_incomplete_reviews

# 错误处理
from handlers.error_handler import error_handler

# API 令牌管理
from handlers.api_commands import gen_token, tokens as api_tokens_command, revoke_token as revoke_token_command

# 统计和搜索功能
from handlers.stats_handlers import get_hot_posts, get_user_stats
from handlers.search_handlers import (
    search_posts, 
    get_tag_cloud, 
    get_my_posts, 
    search_by_user, 
    delete_posts_batch,
    handle_search_input
)
# 频道消息监听器
from handlers.channel_listener import handle_channel_message
from handlers.index_handlers import (
    rebuild_index_command,
    sync_index_command,
    index_stats_command,
    optimize_index_command
)

# 搜索引擎
from utils.search_engine import init_search_engine
from utils.index_manager import auto_rebuild_index_if_needed

# 设置日志
logger = logging.getLogger(__name__)
setup_logging()

# 加载环境变量
load_dotenv()

# 全局变量
TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT", "900"))  # 默认15分钟

# 会话超时检查函数
async def check_conversation_timeout(update: Update, context: CallbackContext) -> None:
    """
    检查会话是否超时的处理函数
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
    """
    # 排除频道消息和频道回复
    if update.channel_post or update.edited_channel_post:
        return
    
    # 检查是否是频道或群组中的消息（通过 chat.type 判断）
    if update.message and update.message.chat:
        chat_type = getattr(update.message.chat, 'type', None)
        if chat_type in ('channel', 'supergroup', 'group'):
            # 对于频道和群组，不进行会话超时检查
            return
    
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    # 对命令消息进行特殊处理 - 命令直接通过，不检查超时
    if update.message and update.message.text and update.message.text.startswith('/'):
        command = update.message.text.split()[0]  # 获取命令部分
        logger.debug(f"跳过命令消息的超时检查: {command}")
        # 关键点：对于命令消息，不进行任何阻止，直接通过
        return
    
    # 检查用户是否在黑名单中
    if is_blacklisted(user_id):
        logger.warning(f"黑名单用户 {user_id} 尝试发送消息")
        await update.message.reply_text("❌ 您已被列入黑名单，无法使用此机器人。")
        # 必须 raise：return 不会中断后续 group，用户会再收到一条兜底回复
        raise ApplicationHandlerStop()
    
    # 检查投稿会话是否超时。
    # 说明：会话活跃时间以 submissions.timestamp 为准（投稿流程每一步都会刷新）。
    # 早期的 user_sessions 会话库从未在生产路径写入，原检查形同虚设。
    try:
        from database.db_manager import get_db
        import time as _time

        last_activity = 0.0
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("SELECT timestamp FROM submissions WHERE user_id=?", (user_id,))
            row = await c.fetchone()
            if row and row["timestamp"]:
                last_activity = float(row["timestamp"])

        if not last_activity:
            logger.debug(f"用户 {user_id} 没有活跃投稿会话，不检查超时")
            return

        current_time = _time.time()
        time_diff = current_time - last_activity

        if time_diff > TIMEOUT_SECONDS:
            logger.info(f"用户 {user_id} 会话超时 ({time_diff:.2f}秒 > {TIMEOUT_SECONDS}秒)")

            # 删除过期会话数据
            async with get_db() as conn:
                c = await conn.cursor()
                await c.execute("DELETE FROM submissions WHERE user_id=?", (user_id,))

            # 向用户发送超时通知
            try:
                await update.message.reply_text(
                    "⏱️ 您的投稿会话已超时，未发布的内容已被清理。请发送 /submit 重新开始。"
                )
            except Exception as e:
                logger.error(f"发送超时通知失败: {e}")

            # 必须 raise：return 不会中断后续 group，超时通知后会再跟一条兜底回复
            raise ApplicationHandlerStop()

        logger.debug(f"用户 {user_id} 会话活跃 ({time_diff:.2f}秒 < {TIMEOUT_SECONDS}秒)")
    except ApplicationHandlerStop:
        # 原样抛回：ApplicationHandlerStop 是 Exception 子类，
        # 若不在此处先拦截，会被下面的 except Exception 吃掉而失效
        raise
    except Exception as e:
        logger.error(f"检查会话超时时发生错误: {e}")
        # 出错时不阻止消息处理继续，而是让正常流程继续

    return

# 会话外媒体兜底：状态机丢失（重启/超时/按钮路径）时用户直接发媒体，给明确提示
_orphan_media_cache = None

async def orphan_media_guard(update: Update, context: CallbackContext) -> None:
    if update.channel_post or update.edited_channel_post:
        return
    if not update.message or not update.effective_user:
        return
    chat_type = getattr(getattr(update.message, "chat", None), "type", None)
    if chat_type != "private":
        return
    user_id = update.effective_user.id
    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("SELECT 1 FROM submissions WHERE user_id=?", (user_id,))
            if await c.fetchone():
                return  # 有活跃会话，交给 ConversationHandler
    except Exception:
        return

    global _orphan_media_cache
    if _orphan_media_cache is None:
        from utils.cache import TTLCache
        _orphan_media_cache = TTLCache(default_ttl=600, max_size=4096)
    key = f"orphan-media:{user_id}"
    if _orphan_media_cache.get(key):
        return
    _orphan_media_cache.set(key, "1", ttl=600)
    try:
        await update.message.reply_text(
            "⚠️ 当前没有进行中的投稿。请先发送 /submit 开始投稿，再上传媒体或文件。"
        )
    except Exception as e:
        logger.warning("发送会话外媒体提示失败: %s", e)
    # 必须 raise：否则 group=1000 的 catch_all 会对同一条媒体再回一次「我没看懂」
    raise ApplicationHandlerStop()

# 添加全局更新记录器
async def log_all_updates(update: Update, context: CallbackContext) -> None:
    """记录所有接收到的更新"""
    if update.message and update.message.text:
        logger.info(f"收到命令: {update.message.text} 来自用户: {update.effective_user.id}")
    return None  # 允许更新继续传递给其他处理器

async def setup_bot_commands(application):
    """
    设置机器人命令菜单（左侧斜杠按钮）
    """
    commands = [
        BotCommand("start", "🚀 启动机器人"),
        BotCommand("submit", "📝 发起投稿"),
        BotCommand("search", "🔍 搜索投稿内容"),
        BotCommand("tags", "🏷️ 查看标签云"),
        BotCommand("myposts", "📋 查看我的投稿"),
        BotCommand("mystats", "📊 查看个人统计"),
        BotCommand("hot", "🔥 查看热门投稿"),
        BotCommand("help", "❓ 查看帮助信息"),
        BotCommand("cancel", "❌ 取消当前操作"),
        BotCommand("settings", "⚙️ 机器人设置"),
    ]
    
    try:
        await application.bot.set_my_commands(commands)
        logger.info(f"成功设置 {len(commands)} 个命令菜单项")
    except Exception as e:
        logger.error(f"设置命令菜单失败: {e}", exc_info=True)


def _load_or_create_persisted_webhook_secret() -> str:
    """Return a stable webhook secret persisted next to the database.

    Survives machine stop/restart so the secret Telegram already has stays
    valid even before the first setWebhook of a fresh process. File is 0600.
    """
    try:
        secret_path = os.path.join(os.path.dirname(DB_PATH) or "data", "webhook.secret")
        os.makedirs(os.path.dirname(secret_path), exist_ok=True)
        if os.path.exists(secret_path):
            with open(secret_path, "r", encoding="utf-8") as fh:
                value = fh.read().strip()
                if value:
                    return value
        value = __import__("secrets").token_urlsafe(32)
        fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value)
        logger.info("已生成并持久化 Webhook Secret Token（值不写入日志）")
        return value
    except Exception as exc:  # read-only FS fallback: ephemeral but still functional
        logger.warning("无法持久化 Webhook Secret，回退为本次启动随机值: %s", exc)
        return __import__("secrets").token_urlsafe(32)


async def main():
    """
    主函数 - 设置并启动机器人
    """
    from telepost.build_info import release_info
    _build = release_info()
    logger.info(
        f"启动TelePost机器人。版本: {_build['version']} commit={_build['commit']}"
    )
    logger.info(f"会话超时时间: {TIMEOUT_SECONDS}秒")
    
    # 初始化数据库
    await init_db()
    # 初始化用户会话数据库
    initialize_database()
    # 初始化黑名单
    await init_blacklist()
    
    # 搜索引擎（Whoosh）初始化/同步/重建被推迟到机器人 READY 之后后台执行。
    # 它是可降级的非关键能力：大索引重建曾同步阻塞事件循环，把冷启动从
    # 秒级拉长到分钟级，并卡住健康检查/Webhook 绑定。搜索未就绪时命令侧
    # 自行降级提示，绝不拖慢 bot 上线。
    async def _post_ready_search_init():
        logger.info("正在初始化搜索引擎（后台，不阻塞上线）...")
        try:
            from config.settings import SEARCH_INDEX_DIR, SEARCH_ENABLED
            if not SEARCH_ENABLED:
                logger.info("搜索功能已禁用")
                return
            def _init_blocking():
                return init_search_engine(index_dir=SEARCH_INDEX_DIR, from_scratch=False)
            search_engine = await asyncio.to_thread(_init_blocking)
            logger.info(f"搜索引擎初始化完成，索引目录: {SEARCH_INDEX_DIR}")
            if hasattr(search_engine, "_needs_reindex") and search_engine._needs_reindex:
                logger.warning("检测到索引已重建，需要重新索引所有帖子")
                try:
                    result = await auto_rebuild_index_if_needed()
                    action = result.get("action")
                    if action in ("none", "sync", "rebuild"):
                        ok = (result.get("result") or {}).get("success", action == "none")
                        if ok:
                            search_engine._needs_reindex = False
                            logger.info("后台索引重建/同步完成 action=%s", action)
                        else:
                            logger.warning("后台索引处理未完全成功 action=%s", action)
                except Exception as rebuild_err:
                    logger.error(f"自动重建索引失败: {rebuild_err}", exc_info=True)
            else:
                try:
                    result = await auto_rebuild_index_if_needed()
                    if result.get("action") == "sync" and (result.get("result") or {}).get("success"):
                        logger.info("后台索引增量同步完成")
                except Exception as idx_err:
                    logger.error(f"后台索引检查失败: {idx_err}", exc_info=True)
        except Exception as e:
            logger.error(f"搜索引擎初始化失败: {e}", exc_info=True)
            logger.warning("将继续运行，但搜索功能可能不可用")

    post_ready_tasks = []

    # 创建和启动应用程序
    token = TOKEN
    if not token:
        logger.error("未设置机器人 Token：请通过环境变量 TOKEN（或 BOT_TOKEN / TELEGRAM_BOT_TOKEN）或 config.ini [BOT] TOKEN 配置")
        sys.exit(1)
        
    # 创建Application实例
    # 会话持久化：auto_stop 停机 / 重启 / 发版后自动恢复投稿会话状态，
    # 根治"对话中途失去响应"（此前会话状态仅存内存，停机即失忆）
    persistence_dir = os.path.dirname(DB_PATH) or "data"
    persistence_path = os.path.join(persistence_dir, "persistence.pickle")
    os.makedirs(persistence_dir, exist_ok=True)
    persistence = PicklePersistence(filepath=persistence_path)
    application = Application.builder().token(token).persistence(persistence).build()
    
    # 设置应用程序
    setup_application(application)
    
    # 初始化应用程序
    logger.info(f"机器人正在启动，运行模式: {RUN_MODE}")
    await application.initialize()
    await application.start()
    application.bot_data["telepost_ready"] = False

    # Review DB is the source of truth. Repair any row left between preview
    # staging and control-message creation before exposing /ready to producers.
    await reconcile_incomplete_reviews(application.bot, stale_seconds=0)
    application.bot_data["telepost_ready"] = True
    
    # 命令菜单是非关键 Telegram API 调用，放到上线后后台设置，避免其
    # 网络延迟/限流拖慢就绪。
    
    # 根据运行模式选择启动方式
    webhook_server = None
    polling_server = None

    async def ensure_polling_server():
        nonlocal polling_server
        if polling_server is not None:
            return
        from utils.polling_server import PollingApiServer
        polling_server = PollingApiServer(
            application=application,
            port=int(os.getenv("HEALTH_PORT", "8080")),
        )
        await polling_server.start()
    
    if RUN_MODE == 'WEBHOOK':
        # Webhook 模式
        logger.info("📡 启动 Webhook 模式...")
        
        # 验证 Webhook URL
        if not WEBHOOK_URL:
            logger.error("❌ Webhook 模式需要设置 WEBHOOK_URL")
            sys.exit(1)
        
        # 导入 Webhook 服务器模块
        from utils.webhook_server import WebhookServer, setup_webhook
        
        # 使用稳定的 Secret Token。显式配置优先；否则在持久化目录里生成并
        # 复用同一枚 token——auto_stop 唤醒/进程重启后 Telegram 仍按已注册的
        # secret 投递，随机 token 会在“重启到重新 setWebhook”的窗口里把更新
        # 全部判为非法（403）。
        import secrets as _secrets
        secret_token = WEBHOOK_SECRET_TOKEN or _load_or_create_persisted_webhook_secret()
        
        # 创建服务器并向 Telegram 注册。AUTO 模式会把监听端口、DNS、
        # TLS 或 Telegram API 侧的任何启动失败统一回退到 Polling。
        success = False
        try:
            webhook_server = WebhookServer(
                application=application,
                port=WEBHOOK_PORT,
                path=WEBHOOK_PATH,
                secret_token=secret_token
            )
            await webhook_server.start()

            success = await setup_webhook(
                application=application,
                webhook_url=WEBHOOK_URL,
                webhook_path=WEBHOOK_PATH,
                secret_token=secret_token
            )
        except Exception as e:
            logger.error(f"❌ Webhook 启动失败: {e}", exc_info=True)
        
        if not success and RUN_MODE_REQUESTED == 'AUTO':
            logger.warning("⚠️ AUTO 模式启动或注册 Webhook 失败，自动回退 Polling")
            if webhook_server:
                try:
                    await webhook_server.stop()
                except Exception as e:
                    logger.warning(f"清理失败的 Webhook 服务时出错: {e}")
            webhook_server = None
            await ensure_polling_server()
            await application.updater.start_polling(allowed_updates=[
                "message",
                "edited_message",
                "channel_post",
                "edited_channel_post",
                "callback_query",
                "inline_query",
            ])
            logger.info("✅ Polling 回退模式已启动")
        elif not success:
            logger.error("❌ Webhook 设置失败")
            if webhook_server:
                try:
                    await webhook_server.stop()
                except Exception as e:
                    logger.warning(f"清理失败的 Webhook 服务时出错: {e}")
            sys.exit(1)

        if success:
            logger.info("✅ Webhook 模式已启动")
            logger.info(f"   监听地址: 0.0.0.0:{WEBHOOK_PORT}{WEBHOOK_PATH}")
            logger.info(f"   外部地址: {WEBHOOK_URL}{WEBHOOK_PATH}")
            logger.info(f"   健康检查: http://0.0.0.0:{WEBHOOK_PORT}/health")
            logger.info("   Secret Token: 已设置")
        
    else:
        # Polling 模式（默认）
        logger.info("🔄 启动 Polling 模式...")
        await ensure_polling_server()
        # 明确指定需要接收的更新类型，包括频道消息
        allowed_updates = [
            "message",           # 普通消息
            "edited_message",    # 编辑的消息
            "channel_post",      # 频道消息（重要！）
            "edited_channel_post",  # 编辑的频道消息
            "callback_query",   # 回调查询
            "inline_query",      # 内联查询
        ]
        await application.updater.start_polling(allowed_updates=allowed_updates)
        logger.info("✅ Polling 模式已启动")
    
    # 信号回调只唤醒主协程；由主协程完成 shutdown，避免后台任务被
    # asyncio.run() 提前取消，也避免 loop.stop() 造成非零退出。
    loop = asyncio.get_running_loop()
    stop_signal = loop.create_future()

    def request_stop(received):
        if not stop_signal.done():
            stop_signal.set_result(received)

    stop_signals = (signal.SIGINT, signal.SIGTERM, signal.SIGABRT)
    try:
        for s in stop_signals:
            loop.add_signal_handler(s, request_stop, s)
    except NotImplementedError:  # Windows event loop
        for s in stop_signals:
            signal.signal(
                s,
                lambda signum, _frame: loop.call_soon_threadsafe(
                    request_stop, signal.Signals(signum)
                ),
            )
        
    # READY: bot 已开始接收更新。现在才跑非关键后台初始化（搜索索引、命令菜单）。
    post_ready_tasks.append(asyncio.create_task(_post_ready_search_init(), name="post-ready-search"))
    post_ready_tasks.append(asyncio.create_task(setup_bot_commands(application), name="post-ready-commands"))

    logger.info("机器人运行中，使用 Ctrl+C 停止")
    
    # 保持应用程序运行
    received_signal = await stop_signal
    await shutdown(
        application, received_signal, webhook_server, polling_server
    )
    
    logger.info("机器人已停止")


async def shutdown(
    application, signal, webhook_server=None, polling_server=None
):
    """
    优雅地关闭机器人
    
    Args:
        application: telegram.ext.Application 实例
        signal: 信号类型
        webhook_server: Webhook 服务器实例（可选，预留参数）
    """
    logger.info(f"收到信号 {signal.name}，正在关闭...")
    
    # 停止本地服务，但保留 Telegram Webhook；Fly auto-stop 依赖它唤醒机器。
    if webhook_server:
        try:
            logger.info("正在停止 Webhook 服务器...")
            await webhook_server.stop()
            logger.info("Webhook 服务器已停止")
        except Exception as e:
            logger.warning(f"停止 Webhook 服务器失败: {e}")
    if polling_server:
        try:
            logger.info("正在停止 Polling HTTP API 服务器...")
            await polling_server.stop()
            logger.info("Polling HTTP API 服务器已停止")
        except Exception as e:
            logger.warning(f"停止 Polling HTTP API 服务器失败: {e}")
    
    # 关闭机器人更新器
    # 注意：Webhook 模式下 Updater 从未通过 start_polling/start_webhook 启动，
    # 直接 stop() 会抛 RuntimeError 并中断后续的 stop/shutdown 清理
    try:
        if application.updater and application.updater.is_connected:
            await application.updater.stop()
    except Exception as e:
        logger.warning(f"停止 Updater 失败（可忽略）: {e}")
    await application.stop()
    await application.shutdown()
    
def setup_application(application):
    """
    初始化和配置应用程序
    """
    # Discussion auto-forward capture must run before every handler in BOTH
    # ingestion paths. Webhook mode captures manually before queueing (see
    # utils/webhook_server.py); polling relies on this PTB pre-handler.
    from telegram.ext import TypeHandler
    from telegram import Update as _Update
    from handlers.publish import capture_discussion_forward as _capture_forward

    async def _capture_discussion_update(_update, _context):
        _capture_forward(_update)

    application.add_handler(
        TypeHandler(_Update, _capture_discussion_update), group=-1000
    )

    # 首先设置全局记录器为最高优先级
    application.add_handler(MessageHandler(filters.ALL, log_all_updates), group=-999)

    # 添加黑名单管理命令和调试命令（设置为最高优先级，不可被其他处理器拦截）
    try:
        logger.info("注册高优先级命令处理器...")
        application.add_handler(CommandHandler('debug', debug), group=-998)
        application.add_handler(CommandHandler('blacklist_add', blacklist_add), group=-998)
        application.add_handler(CommandHandler('blacklist_remove', blacklist_remove), group=-998)
        application.add_handler(CommandHandler('blacklist_list', blacklist_list), group=-998)
        application.add_handler(CommandHandler('botconfig', botconfig), group=-998)
        # 不再注册高优先级的cancel命令，只在ConversationHandler的fallbacks中注册
        # application.add_handler(CommandHandler('cancel', cancel), group=-998)  # 注释掉这行
        logger.info("高优先级命令处理器注册完成")
    except Exception as e:
        logger.error(f"注册高优先级命令处理器失败: {e}", exc_info=True)
    
    # 注册错误处理
    application.add_error_handler(error_handler)
    
    # 注册基本命令处理器
    application.add_handler(CommandHandler("help", help_command))
    # /cancel 不在此处注册：会话内由 ConversationHandler fallback 处理，
    # 会话外由 catch_all 兜底回复——否则会双重处理，导致两条回复
    application.add_handler(CommandHandler("settings", settings))
    application.add_handler(CommandHandler("blacklist", manage_blacklist), group=1)
    
    # 注册统计和搜索命令处理器
    application.add_handler(CommandHandler("hot", get_hot_posts))
    application.add_handler(CommandHandler("mystats", get_user_stats))
    application.add_handler(CommandHandler("search", search_posts))
    application.add_handler(CommandHandler("tags", get_tag_cloud))
    application.add_handler(CommandHandler("myposts", get_my_posts))
    application.add_handler(CommandHandler("searchuser", search_by_user))
    application.add_handler(CommandHandler("delete_posts", delete_posts_batch))
    application.add_handler(CommandHandler("gen_token", gen_token))
    application.add_handler(CommandHandler("tokens", api_tokens_command))
    application.add_handler(CommandHandler("revoke_token", revoke_token_command))
    
    # 注册索引管理命令处理器（仅管理员）
    application.add_handler(CommandHandler("rebuild_index", rebuild_index_command))
    application.add_handler(CommandHandler("sync_index", sync_index_command))
    application.add_handler(CommandHandler("index_stats", index_stats_command))
    application.add_handler(CommandHandler("optimize_index", optimize_index_command))
    
    # 注册会话超时检查处理器
    application.add_handler(MessageHandler(filters.ALL, check_conversation_timeout), group=0)
    
    # 注册频道消息监听器（监听频道新消息，自动记录到数据库）
    try:
        logger.info("注册频道消息监听器...")
        # 频道消息不会触发普通的 MessageHandler，需要使用 BaseHandler 捕获所有更新
        # 然后在处理器内部检查 update.channel_post 或 update.edited_channel_post
        # 创建一个自定义的 BaseHandler 来捕获所有更新
        class ChannelPostHandler(BaseHandler):
            """自定义处理器，用于捕获频道消息"""
            def __init__(self, callback):
                super().__init__(callback)
            
            def check_update(self, update):
                """检查更新是否包含频道消息"""
                return update.channel_post is not None or update.edited_channel_post is not None
        
        application.add_handler(ChannelPostHandler(handle_channel_message), group=2)
        logger.info("频道消息监听器注册完成")
    except Exception as e:
        logger.error(f"注册频道消息监听器失败: {e}", exc_info=True)
    
    try:
        # 添加独立的 /start 命令处理器（只显示欢迎信息）
        logger.info("注册 /start 命令处理器...")
        application.add_handler(CommandHandler("start", start), group=1)
        
        # 添加会话处理器
        logger.info("注册会话处理器...")
        from handlers.conversation import build_submission_conversation
        conv_handler = build_submission_conversation()
        application.add_handler(conv_handler, group=2)
        logger.info("会话处理器注册完成")
    except Exception as e:
        logger.error(f"注册会话处理器失败: {e}", exc_info=True)
    
    # 添加回调查询处理器（统一处理所有回调）
    application.add_handler(
        CallbackQueryHandler(botconfig_callback, pattern="^botconfig:"), group=3
    )
    from handlers.callback_handlers import handle_callback_query
    application.add_handler(CallbackQueryHandler(handle_callback_query), group=3)
    
    # 添加周期性清理任务
    try:
        logger.info("设置定期任务...")
        job_queue = application.job_queue
        async def cleanup_runtime_data(context):
            await cleanup_old_data()
            await expire_stale_reviews(context.bot)
            await reconcile_incomplete_reviews(context.bot)

        job_queue.run_repeating(cleanup_runtime_data, interval=300, first=10)
        
        is_primary_bot = os.getenv("TELEPOST_PRIMARY_BOT", "true").strip().lower() in {
            "1", "true", "yes", "on"
        }
        if is_primary_bot:
            # 全局文件/子进程维护只注册一次；回调本身为 async，并把阻塞工作放入线程。
            job_queue.run_daily(clean_logs_job, time=scheduled_time(hour=3))
            job_queue.run_daily(pixivflow_maintain_job, time=scheduled_time(hour=4))
        else:
            logger.info("跳过全局日志/PixivFlow 维护任务（仅主 Bot 注册）")
        
        logger.info("定期任务设置完成")
    except Exception as e:
        logger.error(f"设置定期任务失败: {e}", exc_info=True)
    
    # 将底部菜单文本映射到命令（在最低优先级前处理）
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_shortcuts), group=998)
    # 不再捕获任意文本的“取消”，以免误触
    # 搜索模式下的自然语言输入处理（在更低优先级，避免干扰其他文本处理）
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_input), group=999)
    # 会话外的媒体/文档兜底：状态机因重启/超时/按钮路径丢失时，用户直接发媒体
    # 不再石沉大海——明确提示先 /submit（有活跃会话时不打扰，交给流程处理）。
    application.add_handler(
        MessageHandler(
            filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.AUDIO | filters.Document.ALL,
            orphan_media_guard,
        ),
        group=997,
    )
    # 添加未处理消息的捕获处理器 (最低优先级组)
    application.add_handler(MessageHandler(filters.ALL, catch_all), group=1000)
    
    logger.info("应用程序设置完成")


def entrypoint() -> None:
    """进程入口：Windows 用 Selector 事件循环策略，启动后常驻。

    冻结版（PyInstaller）的 worker 子进程也复用此入口，保证与源码运行一致。
    """
    try:
        # 根据系统设置正确的事件循环策略
        if platform.system() == "Windows":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
        # 确保使用新的事件循环
        asyncio.set_event_loop(asyncio.new_event_loop())
        
        # 启动主函数
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("程序中断，正在退出...")
    except Exception as e:
        logger.error(f"发生异常: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    entrypoint()
