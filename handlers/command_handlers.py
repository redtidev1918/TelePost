"""
命令处理器模块
"""
from ui.i18n import tr
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ConversationHandler, CallbackContext, ApplicationHandlerStop

from database.db_manager import get_db
from utils.blacklist import (
    is_owner, 
    add_to_blacklist, 
    remove_from_blacklist, 
    get_blacklist, 
    _blacklist
)
from config.settings import TIMEOUT
from ui.keyboards import Keyboards
from ui.messages import MessageFormatter
from utils.database import get_all_user_states

logger = logging.getLogger(__name__)

async def cancel(update: Update, context: CallbackContext) -> int:
    """
    处理 /cancel 命令，取消当前会话
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
        
    Returns:
        int: 结束会话状态
    """
    logger.info(f"收到 /cancel 命令，user_id: {update.effective_user.id}")
    user_id = update.effective_user.id
    session_exists = False
    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("SELECT 1 FROM submissions WHERE user_id=?", (user_id,))
            session_exists = await c.fetchone() is not None
            await c.execute("DELETE FROM submissions WHERE user_id=?", (user_id,))
    except Exception as e:
        logger.error(f"取消时删除数据错误: {e}")
    # 根据是否存在会话给出不同提示
    message_text = (
        tr("""🗑️ 投稿已取消，所有临时进度已清除。

想继续？发送 /submit 重新开始。""")
        if session_exists
        else tr("""ℹ️ 当前没有进行中的投稿。

想投稿？发送 /submit 即可开始。""")
    )
    try:
        await update.message.reply_text(message_text, parse_mode="HTML", reply_markup=Keyboards.main_menu())
    except Exception:
        # 在极少数情况下 message 可能不存在
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=message_text, parse_mode="HTML")
        except Exception:
            pass
    return ConversationHandler.END



async def help_command(update: Update, context: CallbackContext):
    """帮助命令，显示机器人使用说明。文案收敛到 ui.messages.MessageFormatter。"""
    logger.info(f"帮助命令被调用: 用户ID={update.effective_user.id}")
    user_id = update.effective_user.id
    is_admin = is_owner(user_id)
    help_text = MessageFormatter.help_message(is_admin=is_admin)
    try:
        await update.message.reply_text(help_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"发送帮助信息失败: {e}")
        await update.message.reply_text(tr('❌ 发送帮助信息失败，请稍后重试'))



async def about_command(update: Update, context: CallbackContext):
    """关于机器人（/about）。"""
    logger.info(f"关于命令被调用: 用户ID={update.effective_user.id}")
    try:
        await update.message.reply_text(MessageFormatter.about_message(), parse_mode="HTML")
    except Exception as exc:
        logger.error("发送关于信息失败: %s", exc)
        await update.message.reply_text(tr('❌ 发送关于信息失败，请稍后重试'))


# 管理面板相关功能已移除


async def handle_menu_shortcuts(update: Update, context: CallbackContext) -> None:
    """处理底部菜单（ReplyKeyboard）文本，映射到实际命令。

    命中任意分支后必须 **抛出** ApplicationHandlerStop：PTB 会顺序执行每一个
    group，只有抛异常才会中断链路，仅仅 return 不会。否则同一条菜单文本在正常
    回复之后还会落到 group=1000 的 catch_all，用户会额外收到一条
    「🤔 这条消息我没看懂」。
    """
    # 排除频道消息
    if update.channel_post or update.edited_channel_post:
        return
    
    # 检查是否是频道或群组
    if update.message and update.message.chat:
        chat_type = getattr(update.message.chat, 'type', None)
        if chat_type == 'channel':
            return
    
    if not update.message:
        return
    
    text = (update.message.text or "").strip()
    # Accept keyboards sent before a language change as well as the current one.
    for english, chinese in (
        ("My stats", "我的统计"), ("My posts", "我的投稿"),
        ("Top posts", "热门内容"), ("Tag cloud", "标签云"),
        ("Search", "搜索"), ("Help", "帮助"), ("About", "关于"),
    ):
        if text.endswith(english):
            text = chinese
            break
    handled = False
    try:
        # 如果处于搜索输入模式，优先交给搜索输入处理
        if context.user_data.get('search_mode'):
            from handlers.search_handlers import handle_search_input
            await handle_search_input(update, context)
            handled = True
        # 开始投稿：已由 ConversationHandler 的 entry（Regex「开始投稿」）接管，
        # 这里不再直接调 submit——直接调只建 DB 会话、不建立状态机内存状态，
        # 用户随后发的媒体会掉出状态机而静默无响应。
        # 我的统计
        elif text.endswith("我的统计"):
            from handlers.stats_handlers import get_user_stats
            await get_user_stats(update, context)
            handled = True
        # 我的投稿
        elif text.endswith("我的投稿"):
            from handlers.search_handlers import get_my_posts
            await get_my_posts(update, context)
            handled = True
        # 热门内容
        elif text.endswith("热门内容"):
            from handlers.stats_handlers import get_hot_posts
            await get_hot_posts(update, context)
            handled = True
        # 标签云
        elif text.endswith("标签云"):
            from handlers.search_handlers import get_tag_cloud
            await get_tag_cloud(update, context)
            handled = True
        # 搜索
        elif text.endswith("搜索"):
            from handlers.search_handlers import (
                reply_search_disabled, search_enabled,
            )
            if not search_enabled():
                await reply_search_disabled(update)
            else:
                await update.message.reply_text(
                    tr('🔍 请输入搜索关键词，或点击下方选项：'),
                    reply_markup=Keyboards.search_options()
                )
            handled = True
        # 帮助
        elif text.endswith("帮助"):
            await help_command(update, context)
            handled = True
        # Mini App：Reply Keyboard 的「📱 Mini App」是普通文本按钮，点击后
        # 回复一个 Inline Web App 按钮，再由此进入 authenticated Mini App。
        # 绝不直接挂 KeyboardButton.web_app（Simple WebView 不带用户身份）。
        elif text.endswith("Mini App"):
            markup = None
            try:
                markup = Keyboards.miniapp_launch()
            except Exception:
                logger.exception("生成 Mini App 入口失败")
            if markup is not None:
                await update.message.reply_text(
                    tr('📱 点击下方按钮打开 Mini App 投稿。'),
                    parse_mode="HTML",
                    reply_markup=markup,
                )
                handled = True
            else:
                await update.message.reply_text(
                    tr('ℹ️ Mini App 尚未启用，请用 /submit 或直接发送内容投稿。'),
                    parse_mode="HTML",
                )
                handled = True
        # 关于
        elif text.endswith("关于"):
            await update.message.reply_text(MessageFormatter.about_message(), parse_mode="HTML")
            handled = True
    except Exception as e:
        # 处理失败时不中止链路，交由 catch_all 给出兜底指引
        logger.error(f"处理菜单快捷操作失败: {e}")
        return

    if handled:
        raise ApplicationHandlerStop()


async def settings(update: Update, context: CallbackContext):
    """
    设置命令，显示机器人配置信息
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
    """
    logger.info(f"设置命令被调用: 用户ID={update.effective_user.id}")
    
    try:
        from config.settings import CHANNEL_ID, BOT_MODE, SHOW_SUBMITTER, TIMEOUT, ALLOWED_TAGS
        
        # 基础设置信息（所有用户可见）
        settings_info = tr("""
⚙️ <b>机器人设置</b>

<b>📺 频道信息：</b>
• 频道ID: <code>{p0}</code>

<b>🔄 投稿设置：</b>
• 机器人模式: {p1}
• 最大标签数: {p2}
• 会话超时: {p3}秒

<b>👁️ 隐私设置：</b>
• 显示投稿人: {p4}

<b>💡 说明：</b>
• MEDIA - 仅支持图片/视频
• DOCUMENT - 仅支持文档
• MIXED - 支持所有类型
""").format(p0=CHANNEL_ID, p1=BOT_MODE, p2=ALLOWED_TAGS, p3=TIMEOUT, p4='是' if SHOW_SUBMITTER else '否')
        
        await update.message.reply_text(settings_info, parse_mode="HTML")
    except Exception as e:
        logger.error(f"发送设置信息失败: {e}")
        await update.message.reply_text(tr('❌ 获取设置信息失败，请稍后重试'))


async def debug(update: Update, context: CallbackContext):
    """
    调试命令，显示系统调试信息（仅管理员可用）
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
    """
    logger.info(f"调试命令被调用: 用户ID={update.effective_user.id}")
    
    user_id = update.effective_user.id
    
    # 检查权限
    if not is_owner(user_id):
        logger.warning(f"非管理员用户 {user_id} 尝试使用调试命令")
        await update.message.reply_text(tr("""⛔ 此命令仅限管理员使用

使用 /help 查看可用命令"""))
        return
    
    # 构建调试信息
    try:
        from config.settings import OWNER_ID, CHANNEL_ID, BOT_MODE, SHOW_SUBMITTER, NOTIFY_OWNER
        
        debug_info = (
            tr("""🔍 **系统调试信息**

👤 您的用户ID: `{p0}`
🤖 机器人所有者ID: `{p1}`
✅ 您是所有者: {p2}

📺 频道ID: {p3}
🔄 机器人模式: {p4}
👁️ 显示投稿人: {p5}
📲 通知所有者: {p6}
⏱️ 会话超时: {p7}秒

🗄️ 黑名单用户数: {p8}
📂 用户会话数: {p9}
🕒 服务器时间: {p10}
""").format(p0=user_id, p1=OWNER_ID, p2=is_owner(user_id), p3=CHANNEL_ID, p4=BOT_MODE, p5=SHOW_SUBMITTER, p6=NOTIFY_OWNER, p7=TIMEOUT, p8=len(_blacklist), p9=len(get_all_user_states()), p10=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        
        # 获取系统信息
        import platform
        import psutil
        
        try:
            process = psutil.Process()
            memory_info = psutil.virtual_memory()
            memory_usage = process.memory_info().rss / 1024 / 1024  # MB
            cpu_percent = process.cpu_percent(interval=0.1)
            uptime = (datetime.now() - datetime.fromtimestamp(process.create_time())).total_seconds() / 60  # 分钟
            
            system_info = (
                tr("""
📊 **系统信息**

💻 操作系统: {p0} {p1}
🐍 Python版本: {p2}
📈 进程CPU: {p3:.1f}%
🧠 进程内存: {p4:.1f} MB
💾 系统内存: {p5:.1f}% ({p6:.1f}GB/{p7:.1f}GB)
⏲️ 运行时间: {p8} 分钟
""").format(p0=platform.system(), p1=platform.release(), p2=platform.python_version(), p3=cpu_percent, p4=memory_usage, p5=memory_info.percent, p6=memory_info.used / 1024 / 1024 / 1024, p7=memory_info.total / 1024 / 1024 / 1024, p8=int(uptime))
            )
            
            debug_info += system_info
        except Exception as e:
            logger.warning(f"获取系统信息失败: {e}")
            debug_info += tr("""
⚠️ 无法获取系统信息""")
        
        # 搜索/数据库配置与索引统计
        try:
            from config.settings import (
                SEARCH_ENABLED, SEARCH_ANALYZER, SEARCH_HIGHLIGHT, SEARCH_INDEX_DIR, DB_CACHE_KB
            )
            search_info = (
                tr("""
🔎 **搜索/数据库配置**

🔍 搜索启用: {p0}
🧩 分词器: {p1}
✨ 高亮: {p2}
📁 索引目录: `{p3}`
🗃️ SQLite page cache: {p4} KB
""").format(p0=SEARCH_ENABLED, p1=SEARCH_ANALYZER, p2=SEARCH_HIGHLIGHT, p3=SEARCH_INDEX_DIR, p4=DB_CACHE_KB)
            )
            # 目录大小
            try:
                import os
                def _dir_size_bytes(path: str) -> int:
                    total = 0
                    for root, _, files in os.walk(path):
                        for name in files:
                            fp = os.path.join(root, name)
                            try:
                                total += os.path.getsize(fp)
                            except Exception:
                                pass
                    return total
                idx_bytes = _dir_size_bytes(SEARCH_INDEX_DIR)
                search_info += tr("""📦 索引大小: {p0:.2f} MB
""").format(p0=idx_bytes / 1024 / 1024)
            except Exception:
                pass
            # 索引文档统计
            try:
                from utils.search_engine import get_search_engine
                se = get_search_engine()
                stats = se.get_stats()
                search_info += tr("""📄 索引文档数: {p0}
""").format(p0=stats.get('total_docs', 'N/A'))
            except Exception as se_err:
                search_info += tr("""📄 索引文档数: N/A ({p0})
""").format(p0=se_err)

            debug_info += search_info
        except Exception as e:
            logger.warning(f"获取搜索/数据库配置失败: {e}")
            debug_info += tr("""
⚠️ 无法获取搜索/数据库配置""")

        try:
            # 尝试使用Markdown格式发送
            await update.message.reply_text(debug_info, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Markdown格式发送失败: {e}，尝试纯文本")
            try:
                # 如果Markdown失败，尝试纯文本
                plain_debug_info = debug_info.replace('**', '').replace('`', '')
                await update.message.reply_text(plain_debug_info)
            except Exception as e2:
                logger.error(f"发送调试信息失败: {e2}")
                await update.message.reply_text(tr('❌ 发送调试信息失败'))
    except Exception as e:
        logger.error(f"生成调试信息时发生错误: {e}", exc_info=True)
        try:
            await update.message.reply_text(tr('❌ 生成调试信息时发生错误: {p0}').format(p0=str(e)[:100]))
        except Exception as e2:
            logger.error(f"发送错误消息失败: {e2}")

async def catch_all(update: Update, context: CallbackContext):
    """
    捕获所有未处理的消息
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
    """
    # 排除频道消息（频道消息由专门的处理器处理）
    if update.channel_post or update.edited_channel_post:
        return
    
    # 检查是否是频道或群组
    if update.message and update.message.chat:
        chat_type = getattr(update.message.chat, 'type', None)
        if chat_type == 'channel':
            return
    
    logger.debug(f"收到未知消息: {update}")
    
    # 兜底引导：此前这里完全静默，用户在会话中断/输错指令时会觉得"bot 无回应"。
    # 但同一用户 10 分钟内只提示一次，避免闲聊刷屏
    if update.message and update.message.chat.type == 'private':
        from utils.cache import TTLCache
        global _guidance_cache
        try:
            _guidance_cache
        except NameError:
            _guidance_cache = TTLCache(default_ttl=600, max_size=1024)
        user_key = f"guide:{update.effective_user.id}"
        if _guidance_cache.get(user_key):
            logger.info(f"引导冷却中，跳过提示: {user_key}")
            return
        _guidance_cache.set(user_key, "1", ttl=600)

        text = (update.message.text or "").strip()
        if text == "/cancel":
            # 会话外 /cancel：干净告知当前无投稿（会话内的取消由 fallback 处理）
            logger.info(f"会话外收到 /cancel，回复无进行中投稿: {update.effective_user.id}")
            try:
                await update.message.reply_text(tr('ℹ️ 当前没有进行中的投稿。要开始新投稿请发送 /submit'))
            except Exception as e:
                logger.debug(f"发送提示失败: {e}")
            return
        if text.startswith("/"):
            # 命令类消息：正常命令早已在更早的组被处理并回复，
            # 走到这里的一定是无效命令。追加引导反而会造成
            # "欢迎信息 + 我不太明白" 的双重回复（用户实际遇到的 bug），故只记日志
            logger.info(f"未识别的命令（不重复打扰）: {text[:30]}")
            return
        reply = (
            tr("""🤔 这条消息我没看懂。
• 投稿请发送 /submit（按提示一步步来）
• 全部命令请发送 /help""")
        )
        logger.info(f"发送兜底引导给 {update.effective_user.id}: {text[:30]}")
        try:
            await update.message.reply_text(reply)
        except Exception as e:
            logger.debug(f"发送兜底引导失败: {e}")

async def blacklist_add(update: Update, context: CallbackContext):
    """
    添加用户到黑名单
    
    命令格式: /blacklist_add <user_id> [reason]
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
    """
    logger.info(f"黑名单添加命令被调用: 用户ID={update.effective_user.id}")
    
    user_id = update.effective_user.id
    
    # 检查是否为所有者
    if not is_owner(user_id):
        logger.warning(f"非所有者用户 {user_id} 尝试使用黑名单添加命令")
        try:
            await update.message.reply_text(tr('⚠️ 只有机器人所有者才能使用此命令'))
        except Exception as e:
            logger.error(f"发送权限拒绝消息失败: {e}")
        return
    
    # 检查参数
    args = context.args
    if not args or len(args) < 1:
        try:
            await update.message.reply_text(
                tr("""⚠️ 命令格式错误

正确格式: /blacklist_add <用户ID> [原因]
例如: /blacklist_add 123456789 发送垃圾内容

用户ID必须是数字，可以通过用户的投稿通知获取""")
            )
        except Exception as e:
            logger.error(f"发送格式提示消息失败: {e}")
        return
    
    try:
        target_user_id = int(args[0])
        reason = " ".join(args[1:]) if len(args) > 1 else tr('未指定原因')
        
        # 添加到黑名单
        success = await add_to_blacklist(target_user_id, reason)
        if success:
            try:
                await update.message.reply_text(tr("""✅ 已将用户 {p0} 添加到黑名单
原因: {p1}""").format(p0=target_user_id, p1=reason))
                logger.info(f"用户 {user_id} 成功将 {target_user_id} 添加到黑名单，原因: {reason}")
            except Exception as e:
                logger.error(f"发送成功消息失败: {e}")
        else:
            try:
                await update.message.reply_text(tr('❌ 添加用户 {p0} 到黑名单时出错').format(p0=target_user_id))
            except Exception as e:
                logger.error(f"发送失败消息失败: {e}")
    except ValueError:
        try:
            await update.message.reply_text(
                tr("""⚠️ 用户ID格式错误

用户ID必须是数字（例如：123456789）
您可以从投稿通知消息中获取用户ID，或者使用 @userinfobot 机器人查询""")
            )
        except Exception as e:
            logger.error(f"发送ID格式错误消息失败: {e}")
    except Exception as e:
        logger.error(f"处理黑名单添加命令时出错: {e}", exc_info=True)
        try:
            await update.message.reply_text(tr('❌ 处理命令时发生错误: {p0}').format(p0=str(e)[:100]))
        except Exception as e2:
            logger.error(f"发送错误消息失败: {e2}")

async def blacklist_remove(update: Update, context: CallbackContext):
    """
    从黑名单中移除用户
    
    命令格式: /blacklist_remove <user_id>
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
    """
    logger.info(f"黑名单移除命令被调用: 用户ID={update.effective_user.id}")
    
    user_id = update.effective_user.id
    
    # 检查是否为所有者
    if not is_owner(user_id):
        logger.warning(f"非所有者用户 {user_id} 尝试使用黑名单移除命令")
        try:
            await update.message.reply_text(tr('⚠️ 只有机器人所有者才能使用此命令'))
        except Exception as e:
            logger.error(f"发送权限拒绝消息失败: {e}")
        return
    
    # 检查参数
    args = context.args
    if not args or len(args) < 1:
        try:
            await update.message.reply_text(
                tr("""⚠️ 命令格式错误

正确格式: /blacklist_remove <用户ID>
例如: /blacklist_remove 123456789

用户ID必须是数字，可以通过 /blacklist_list 命令查看所有黑名单用户""")
            )
        except Exception as e:
            logger.error(f"发送格式提示消息失败: {e}")
        return
    
    try:
        target_user_id = int(args[0])
        
        # 从黑名单中移除
        success = await remove_from_blacklist(target_user_id)
        if success:
            try:
                await update.message.reply_text(tr('✅ 已将用户 {p0} 从黑名单中移除').format(p0=target_user_id))
                logger.info(f"用户 {user_id} 成功将 {target_user_id} 从黑名单中移除")
            except Exception as e:
                logger.error(f"发送成功消息失败: {e}")
        else:
            try:
                await update.message.reply_text(tr('❓ 用户 {p0} 不在黑名单中').format(p0=target_user_id))
            except Exception as e:
                logger.error(f"发送失败消息失败: {e}")
    except ValueError:
        try:
            await update.message.reply_text(
                tr("""⚠️ 用户ID格式错误

用户ID必须是数字（例如：123456789）
请使用 /blacklist_list 命令查看所有黑名单用户的ID""")
            )
        except Exception as e:
            logger.error(f"发送ID格式错误消息失败: {e}")
    except Exception as e:
        logger.error(f"处理黑名单移除命令时出错: {e}", exc_info=True)
        try:
            await update.message.reply_text(tr('❌ 处理命令时发生错误: {p0}').format(p0=str(e)[:100]))
        except Exception as e2:
            logger.error(f"发送错误消息失败: {e2}")

async def blacklist_list(update: Update, context: CallbackContext):
    """
    列出所有黑名单用户
    
    命令格式: /blacklist_list
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
    """
    logger.info(f"黑名单列表命令被调用: 用户ID={update.effective_user.id}")
    
    user_id = update.effective_user.id
    
    # 检查是否为所有者
    if not is_owner(user_id):
        logger.warning(f"非所有者用户 {user_id} 尝试使用黑名单列表命令")
        try:
            await update.message.reply_text(tr('⚠️ 只有机器人所有者才能使用此命令'))
        except Exception as e:
            logger.error(f"发送权限拒绝消息失败: {e}")
        return
    
    try:
        # 获取黑名单
        blacklist = await get_blacklist()
        
        if not blacklist:
            try:
                await update.message.reply_text(tr('📋 黑名单为空'))
                logger.info("黑名单为空，返回空列表")
            except Exception as e:
                logger.error(f"发送空黑名单消息失败: {e}")
            return
        
        # 格式化黑名单消息
        message = tr("""📋 **黑名单用户列表**:

""")
        for i, user in enumerate(blacklist, 1):
            message += f"{i}. ID: `{user['user_id']}`\n"
            message += tr("""   原因: {p0}
""").format(p0=user['reason'])
            message += tr("""   添加时间: {p0}

""").format(p0=user['added_at'])
        
        try:
            # 尝试带Markdown格式发送
            await update.message.reply_text(message, parse_mode="Markdown")
            logger.info(f"成功发送黑名单列表给用户 {user_id}")
        except Exception as e:
            logger.warning(f"Markdown格式发送失败: {e}，尝试纯文本")
            try:
                # 如果Markdown失败，尝试纯文本
                plain_message = message.replace('**', '').replace('`', '')
                await update.message.reply_text(plain_message)
                logger.info(f"成功以纯文本格式发送黑名单列表给用户 {user_id}")
            except Exception as e2:
                logger.error(f"发送黑名单列表失败: {e2}")
    except Exception as e:
        logger.error(f"处理黑名单列表命令时出错: {e}", exc_info=True)
        try:
            await update.message.reply_text(tr('❌ 获取黑名单时发生错误: {p0}').format(p0=str(e)[:100]))
        except Exception as e2:
            logger.error(f"发送错误消息失败: {e2}")
