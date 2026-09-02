"""
命令处理器模块
"""
import logging
from datetime import datetime
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ConversationHandler, CallbackContext

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
    message_text = "❌ 投稿已取消" if session_exists else "ℹ️ 当前没有进行中的投稿"
    try:
        await update.message.reply_text(message_text, reply_markup=ReplyKeyboardRemove())
    except Exception:
        # 在极少数情况下 message 可能不存在
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=message_text)
        except Exception:
            pass
    return ConversationHandler.END



async def help_command(update: Update, context: CallbackContext):
    """
    帮助命令，显示机器人使用说明
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
    """
    logger.info(f"帮助命令被调用: 用户ID={update.effective_user.id}")
    
    user_id = update.effective_user.id
    is_admin = is_owner(user_id)
    
    # 基础帮助信息（所有用户可见）
    basic_help = """
📚 <b>使用指南</b>

<b>📝 投稿相关：</b>
/submit - 开始新投稿
/cancel - 取消当前投稿

<b>📊 统计查询：</b>
/hot - 查看热门内容
/mystats - 我的投稿统计
/myposts - 我的投稿列表

<b>🔍 搜索功能：</b>
/search &lt;关键词&gt; - 搜索内容
/tags - 查看热门标签云

<b>ℹ️ 其他：</b>
/help - 显示此帮助
/settings - 查看机器人设置
"""
    
    # 管理员专属帮助（仅管理员可见）
    admin_help = """
<b>👑 管理员专属命令：</b>
/debug - 查看系统调试信息
/blacklist_add &lt;ID&gt; [原因] - 添加黑名单
/blacklist_remove &lt;ID&gt; - 移除黑名单
/blacklist_list - 查看黑名单列表
/searchuser &lt;ID&gt; - 查询用户投稿
/botconfig - 热更新当前 Bot 的频道、审核群和署名策略
"""
    
    footer = """
💡 <b>小贴士：</b>
• 使用下方菜单按钮快速访问功能
• 投稿支持文字、图片、视频等多种格式
• 添加 #标签 让内容更易被发现
"""
    
    # 根据用户身份组合消息
    if is_admin:
        help_text = basic_help + admin_help + footer
    else:
        help_text = basic_help + footer
    
    try:
        await update.message.reply_text(help_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"发送帮助信息失败: {e}")
        await update.message.reply_text("❌ 发送帮助信息失败，请稍后重试")


# 管理面板相关功能已移除


async def handle_menu_shortcuts(update: Update, context: CallbackContext) -> None:
    """处理底部菜单（ReplyKeyboard）文本，映射到实际命令。"""
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
    try:
        # 如果处于搜索输入模式，优先交给搜索输入处理
        if context.user_data.get('search_mode'):
            from handlers.search_handlers import handle_search_input
            await handle_search_input(update, context)
            return
        # 开始投稿：已由 ConversationHandler 的 entry（Regex「开始投稿」）接管，
        # 这里不再直接调 submit——直接调只建 DB 会话、不建立状态机内存状态，
        # 用户随后发的媒体会掉出状态机而静默无响应。
        # 我的统计
        if text.endswith("我的统计"):
            from handlers.stats_handlers import get_user_stats
            await get_user_stats(update, context)
            return
        # 我的投稿
        if text.endswith("我的投稿"):
            from handlers.search_handlers import get_my_posts
            await get_my_posts(update, context)
            return
        # 热门内容
        if text.endswith("热门内容"):
            from handlers.stats_handlers import get_hot_posts
            await get_hot_posts(update, context)
            return
        # 标签云
        if text.endswith("标签云"):
            from handlers.search_handlers import get_tag_cloud
            await get_tag_cloud(update, context)
            return
        # 搜索
        if text.endswith("搜索"):
            await update.message.reply_text(
                "🔍 请输入搜索关键词，或点击下方选项：",
                reply_markup=Keyboards.search_options()
            )
            return
        # 帮助
        if text.endswith("帮助"):
            await help_command(update, context)
            return
        # 关于
        if text.endswith("关于"):
            await update.message.reply_text(MessageFormatter.about_message(), parse_mode="HTML")
            return
    except Exception as e:
        logger.error(f"处理菜单快捷操作失败: {e}")


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
        settings_info = f"""
⚙️ <b>机器人设置</b>

<b>📺 频道信息：</b>
• 频道ID: <code>{CHANNEL_ID}</code>

<b>🔄 投稿设置：</b>
• 机器人模式: {BOT_MODE}
• 最大标签数: {ALLOWED_TAGS}
• 会话超时: {TIMEOUT}秒

<b>👁️ 隐私设置：</b>
• 显示投稿人: {'是' if SHOW_SUBMITTER else '否'}

<b>💡 说明：</b>
• MEDIA - 仅支持图片/视频
• DOCUMENT - 仅支持文档
• MIXED - 支持所有类型
"""
        
        await update.message.reply_text(settings_info, parse_mode="HTML")
    except Exception as e:
        logger.error(f"发送设置信息失败: {e}")
        await update.message.reply_text("❌ 获取设置信息失败，请稍后重试")


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
        await update.message.reply_text("⛔ 此命令仅限管理员使用\n\n使用 /help 查看可用命令")
        return
    
    # 构建调试信息
    try:
        from config.settings import OWNER_ID, CHANNEL_ID, BOT_MODE, SHOW_SUBMITTER, NOTIFY_OWNER
        
        debug_info = (
            "🔍 **系统调试信息**\n\n"
            f"👤 您的用户ID: `{user_id}`\n"
            f"🤖 机器人所有者ID: `{OWNER_ID}`\n"
            f"✅ 您是所有者: {is_owner(user_id)}\n\n"
            f"📺 频道ID: {CHANNEL_ID}\n"
            f"🔄 机器人模式: {BOT_MODE}\n"
            f"👁️ 显示投稿人: {SHOW_SUBMITTER}\n"
            f"📲 通知所有者: {NOTIFY_OWNER}\n"
            f"⏱️ 会话超时: {TIMEOUT}秒\n\n"
            f"🗄️ 黑名单用户数: {len(_blacklist)}\n"
            f"📂 用户会话数: {len(get_all_user_states())}\n"
            f"🕒 服务器时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
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
                "\n📊 **系统信息**\n\n"
                f"💻 操作系统: {platform.system()} {platform.release()}\n"
                f"🐍 Python版本: {platform.python_version()}\n"
                f"📈 进程CPU: {cpu_percent:.1f}%\n"
                f"🧠 进程内存: {memory_usage:.1f} MB\n"
                f"💾 系统内存: {memory_info.percent:.1f}% ({memory_info.used/1024/1024/1024:.1f}GB/{memory_info.total/1024/1024/1024:.1f}GB)\n"
                f"⏲️ 运行时间: {int(uptime)} 分钟\n"
            )
            
            debug_info += system_info
        except Exception as e:
            logger.warning(f"获取系统信息失败: {e}")
            debug_info += "\n⚠️ 无法获取系统信息"
        
        # 搜索/数据库配置与索引统计
        try:
            from config.settings import (
                SEARCH_ENABLED, SEARCH_ANALYZER, SEARCH_HIGHLIGHT, SEARCH_INDEX_DIR, DB_CACHE_KB
            )
            search_info = (
                "\n🔎 **搜索/数据库配置**\n\n"
                f"🔍 搜索启用: {SEARCH_ENABLED}\n"
                f"🧩 分词器: {SEARCH_ANALYZER}\n"
                f"✨ 高亮: {SEARCH_HIGHLIGHT}\n"
                f"📁 索引目录: `{SEARCH_INDEX_DIR}`\n"
                f"🗃️ SQLite page cache: {DB_CACHE_KB} KB\n"
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
                search_info += f"📦 索引大小: {idx_bytes/1024/1024:.2f} MB\n"
            except Exception:
                pass
            # 索引文档统计
            try:
                from utils.search_engine import get_search_engine
                se = get_search_engine()
                stats = se.get_stats()
                search_info += f"📄 索引文档数: {stats.get('total_docs','N/A')}\n"
            except Exception as se_err:
                search_info += f"📄 索引文档数: N/A ({se_err})\n"

            debug_info += search_info
        except Exception as e:
            logger.warning(f"获取搜索/数据库配置失败: {e}")
            debug_info += "\n⚠️ 无法获取搜索/数据库配置"

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
                await update.message.reply_text("❌ 发送调试信息失败")
    except Exception as e:
        logger.error(f"生成调试信息时发生错误: {e}", exc_info=True)
        try:
            await update.message.reply_text(f"❌ 生成调试信息时发生错误: {str(e)[:100]}")
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
                await update.message.reply_text("ℹ️ 当前没有进行中的投稿。要开始新投稿请发送 /submit")
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
            "🤔 这条消息我没看懂。\n"
            "• 投稿请发送 /submit（按提示一步步来）\n"
            "• 全部命令请发送 /help"
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
            await update.message.reply_text("⚠️ 只有机器人所有者才能使用此命令")
        except Exception as e:
            logger.error(f"发送权限拒绝消息失败: {e}")
        return
    
    # 检查参数
    args = context.args
    if not args or len(args) < 1:
        try:
            await update.message.reply_text(
                "⚠️ 命令格式错误\n\n"
                "正确格式: /blacklist_add <用户ID> [原因]\n"
                "例如: /blacklist_add 123456789 发送垃圾内容\n\n"
                "用户ID必须是数字，可以通过用户的投稿通知获取"
            )
        except Exception as e:
            logger.error(f"发送格式提示消息失败: {e}")
        return
    
    try:
        target_user_id = int(args[0])
        reason = " ".join(args[1:]) if len(args) > 1 else "未指定原因"
        
        # 添加到黑名单
        success = await add_to_blacklist(target_user_id, reason)
        if success:
            try:
                await update.message.reply_text(f"✅ 已将用户 {target_user_id} 添加到黑名单\n原因: {reason}")
                logger.info(f"用户 {user_id} 成功将 {target_user_id} 添加到黑名单，原因: {reason}")
            except Exception as e:
                logger.error(f"发送成功消息失败: {e}")
        else:
            try:
                await update.message.reply_text(f"❌ 添加用户 {target_user_id} 到黑名单时出错")
            except Exception as e:
                logger.error(f"发送失败消息失败: {e}")
    except ValueError:
        try:
            await update.message.reply_text(
                "⚠️ 用户ID格式错误\n\n"
                "用户ID必须是数字（例如：123456789）\n"
                "您可以从投稿通知消息中获取用户ID，或者使用 @userinfobot 机器人查询"
            )
        except Exception as e:
            logger.error(f"发送ID格式错误消息失败: {e}")
    except Exception as e:
        logger.error(f"处理黑名单添加命令时出错: {e}", exc_info=True)
        try:
            await update.message.reply_text(f"❌ 处理命令时发生错误: {str(e)[:100]}")
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
            await update.message.reply_text("⚠️ 只有机器人所有者才能使用此命令")
        except Exception as e:
            logger.error(f"发送权限拒绝消息失败: {e}")
        return
    
    # 检查参数
    args = context.args
    if not args or len(args) < 1:
        try:
            await update.message.reply_text(
                "⚠️ 命令格式错误\n\n"
                "正确格式: /blacklist_remove <用户ID>\n"
                "例如: /blacklist_remove 123456789\n\n"
                "用户ID必须是数字，可以通过 /blacklist_list 命令查看所有黑名单用户"
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
                await update.message.reply_text(f"✅ 已将用户 {target_user_id} 从黑名单中移除")
                logger.info(f"用户 {user_id} 成功将 {target_user_id} 从黑名单中移除")
            except Exception as e:
                logger.error(f"发送成功消息失败: {e}")
        else:
            try:
                await update.message.reply_text(f"❓ 用户 {target_user_id} 不在黑名单中")
            except Exception as e:
                logger.error(f"发送失败消息失败: {e}")
    except ValueError:
        try:
            await update.message.reply_text(
                "⚠️ 用户ID格式错误\n\n"
                "用户ID必须是数字（例如：123456789）\n"
                "请使用 /blacklist_list 命令查看所有黑名单用户的ID"
            )
        except Exception as e:
            logger.error(f"发送ID格式错误消息失败: {e}")
    except Exception as e:
        logger.error(f"处理黑名单移除命令时出错: {e}", exc_info=True)
        try:
            await update.message.reply_text(f"❌ 处理命令时发生错误: {str(e)[:100]}")
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
            await update.message.reply_text("⚠️ 只有机器人所有者才能使用此命令")
        except Exception as e:
            logger.error(f"发送权限拒绝消息失败: {e}")
        return
    
    try:
        # 获取黑名单
        blacklist = await get_blacklist()
        
        if not blacklist:
            try:
                await update.message.reply_text("📋 黑名单为空")
                logger.info("黑名单为空，返回空列表")
            except Exception as e:
                logger.error(f"发送空黑名单消息失败: {e}")
            return
        
        # 格式化黑名单消息
        message = "📋 **黑名单用户列表**:\n\n"
        for i, user in enumerate(blacklist, 1):
            message += f"{i}. ID: `{user['user_id']}`\n"
            message += f"   原因: {user['reason']}\n"
            message += f"   添加时间: {user['added_at']}\n\n"
        
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
            await update.message.reply_text(f"❌ 获取黑名单时发生错误: {str(e)[:100]}")
        except Exception as e2:
            logger.error(f"发送错误消息失败: {e2}")
