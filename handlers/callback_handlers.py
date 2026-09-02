"""
回调查询处理器 - 处理所有按钮点击事件
"""
import logging
from telegram import Update
from telegram.ext import CallbackContext, ConversationHandler
from telegram.constants import ParseMode

from ui.keyboards import Keyboards
from ui.messages import MessageFormatter
from database.db_manager import get_db
from models.state import STATE
from utils.blacklist import remove_from_blacklist, is_owner
from handlers.publish import publish_submission
from handlers.stats_handlers import get_hot_posts
from handlers.search_handlers import search_posts_by_tag

logger = logging.getLogger(__name__)


async def _safe_answer(query, text: str = None, **kwargs):
    """
    安全应答回调查询。

    每个 callback query 只能被应答一次，重复应答会抛出 telegram.error.BadRequest。
    这里统一吞掉该类错误：先到的应答生效（例如带 show_alert 的提示），
    后续重复调用不再中断业务流程。
    """
    try:
        await query.answer(text=text, **kwargs)
    except Exception as e:
        logger.debug(f"回调应答失败（通常为重复应答或已过期）: {e}")


async def handle_callback_query(update: Update, context: CallbackContext):
    """
    处理所有回调查询（按钮点击）

    注意：这里不做全局抢先 answer，否则会占用唯一的应答机会，
    导致各分支中的 show_alert 弹窗全部失效。由各分支自行调用 _safe_answer。
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
    """
    query = update.callback_query
    
    data = query.data
    user_id = update.effective_user.id
    
    logger.info(f"收到回调查询: {data} 来自用户: {user_id}")
    
    try:
        # API 投稿审核（不进入普通 /submit 会话）
        if data.startswith("review_approve:"):
            from handlers.review import approve_review
            return await approve_review(update, context)
        elif data.startswith("review_reject:"):
            from handlers.review import reject_review
            return await reject_review(update, context)
        elif data.startswith("review_spoiler:"):
            from handlers.review import toggle_review_spoiler
            return await toggle_review_spoiler(update, context)
        elif data.startswith("review_refetch:"):
            from handlers.review import refetch_review
            return await refetch_review(update, context)

        # 投稿确认相关
        elif data.startswith("submit_confirm_"):
            await handle_submit_confirm(update, context)
        elif data.startswith("submit_edit_"):
            await handle_submit_edit(update, context)
        elif data.startswith("submit_addtag_"):
            await handle_submit_addtag(update, context)
        elif data.startswith("submit_media_"):
            await handle_submit_media(update, context)
        elif data.startswith("submit_cancel_"):
            await handle_submit_cancel(update, context)
        
        # 热门帖子筛选
        elif data.startswith("hot_filter_"):
            await handle_hot_filter(update, context)
        elif data.startswith("hot_limit_"):
            await handle_hot_limit(update, context)
        elif data == "hot_refresh":
            await handle_hot_refresh(update, context)
        
        # 搜索相关
        elif data.startswith("search_"):
            await handle_search_action(update, context)
        elif data.startswith("tag_search_"):
            await handle_tag_search(update, context)
        # 搜索-时间筛选
        elif data.startswith("time_"):
            # 记录时间筛选并提示输入关键词
            mapping = {
                'time_day': 'day',
                'time_week': 'week',
                'time_month': 'month',
                'time_all': 'all',
            }
            time_key = mapping.get(data)
            if time_key:
                context.user_data['time_filter'] = time_key
                context.user_data['search_mode'] = 'fulltext'
                if time_key == 'all':
                    await query.edit_message_text("🔍 已选择时间范围：全部\n请输入搜索关键词：")
                else:
                    zh = {'day': '今日', 'week': '本周', 'month': '本月'}[time_key]
                    await query.edit_message_text(f"🔍 已选择时间范围：{zh}\n请输入搜索关键词：")
            else:
                await query.edit_message_text("❌ 无效的时间范围")
        
        # 帖子操作
        elif data.startswith("view_post_"):
            await handle_view_post(update, context)
        elif data.startswith("stats_post_"):
            await handle_stats_post(update, context)
        elif data.startswith("delete_post_"):
            await handle_delete_post(update, context)
        
        # 管理面板（已移除）：给予提示并返回主菜单
        elif data.startswith("admin_"):
            await _safe_answer(query, "管理面板已下线", show_alert=True)
            await handle_back_to_main(update, context)
        
        # 黑名单操作
        elif data.startswith("unblock_"):
            await handle_unblock_user(update, context)
        elif data.startswith("userinfo_"):
            await handle_user_info(update, context)
        
        # 分页
        elif data.startswith("page_"):
            await handle_pagination(update, context)
        
        # 确认/取消操作
        elif data.startswith("confirm_"):
            await handle_confirm_action(update, context)
        elif data.startswith("cancel_"):
            await handle_cancel_action(update, context)
        
        # 返回主菜单
        elif data == "back_main":
            await handle_back_to_main(update, context)
        elif data == "back":
            await handle_back(update, context)
        
        else:
            await query.edit_message_text("❌ 未知操作")
            
    except Exception as e:
        logger.error(f"处理回调查询时出错: {e}", exc_info=True)
        try:
            await query.edit_message_text(
                MessageFormatter.error_message("general")
            )
        except:
            pass


async def handle_submit_confirm(update: Update, context: CallbackContext):
    """处理投稿确认"""
    query = update.callback_query
    await _safe_answer(query)
    
    await query.edit_message_text("⏳ 正在发布投稿...")
    
    # 调用发布函数
    result = await publish_submission(update, context)
    
    return result


async def handle_submit_edit(update: Update, context: CallbackContext):
    """处理编辑投稿内容"""
    query = update.callback_query
    await _safe_answer(query)
    
    await query.edit_message_text(
        "✏️ 编辑功能开发中...\n\n当前请取消后重新开始投稿。",
        reply_markup=None
    )


async def handle_submit_addtag(update: Update, context: CallbackContext):
    """处理添加标签"""
    query = update.callback_query
    await _safe_answer(query)
    
    await query.edit_message_text(
        "🏷️ 请发送要添加的标签（用逗号分隔）：",
        reply_markup=None
    )
    
    return STATE.get('TAG', 4)


async def handle_submit_media(update: Update, context: CallbackContext):
    """处理添加媒体"""
    query = update.callback_query
    await _safe_answer(query)
    
    await query.edit_message_text(
        "📎 请发送要添加的媒体文件：",
        reply_markup=None
    )
    
    return STATE.get('MEDIA', 2)


async def handle_submit_cancel(update: Update, context: CallbackContext):
    """处理取消投稿"""
    query = update.callback_query
    user_id = update.effective_user.id
    await _safe_answer(query)
    
    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("DELETE FROM submissions WHERE user_id=?", (user_id,))
            await conn.commit()
        
        await query.edit_message_text(
            "❌ 投稿已取消",
            reply_markup=None
        )
        
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"取消投稿时出错: {e}")
        await query.edit_message_text(
            MessageFormatter.error_message("general")
        )
        return ConversationHandler.END


async def handle_hot_filter(update: Update, context: CallbackContext):
    """处理热门帖子时间筛选"""
    query = update.callback_query
    await _safe_answer(query)
    time_filter = query.data.replace("hot_filter_", "")
    
    # 保存筛选条件到上下文
    context.user_data['hot_time_filter'] = time_filter
    
    # 重新获取热门帖子
    await get_hot_posts(update, context, edit_message=True)


async def handle_hot_limit(update: Update, context: CallbackContext):
    """处理热门帖子数量限制"""
    query = update.callback_query
    await _safe_answer(query)
    limit = int(query.data.replace("hot_limit_", ""))
    
    # 保存限制到上下文
    context.user_data['hot_limit'] = limit
    
    # 重新获取热门帖子
    await get_hot_posts(update, context, edit_message=True)


async def handle_hot_refresh(update: Update, context: CallbackContext):
    """刷新热门帖子"""
    query = update.callback_query
    
    await _safe_answer(query, "🔄 正在刷新...")
    
    # Bot API 无法无副作用读取任意频道帖统计；这里只重新读取本地记录。
    await get_hot_posts(update, context, edit_message=True)


async def handle_search_action(update: Update, context: CallbackContext):
    """处理搜索操作"""
    query = update.callback_query
    await _safe_answer(query)
    action = query.data.replace("search_", "")
    
    if action == "fulltext":
        await query.edit_message_text(
            "🔍 请输入搜索关键词：",
            reply_markup=None
        )
        context.user_data['search_mode'] = 'fulltext'
        
    elif action == "tag":
        await query.edit_message_text(
            "🏷️ 请输入要搜索的标签：",
            reply_markup=None
        )
        context.user_data['search_mode'] = 'tag'
        
    elif action == "myposts":
        from handlers.search_handlers import get_my_posts
        await get_my_posts(update, context)
        
    elif action == "time":
            # 先回应回调，避免界面长时间 loading
            await _safe_answer(query, "请选择时间范围")
            await query.edit_message_text(
                "📅 请选择时间范围：",
                reply_markup=Keyboards.time_filter()
            )


async def handle_tag_search(update: Update, context: CallbackContext):
    """处理标签搜索"""
    query = update.callback_query
    tag = query.data.replace("tag_search_", "")
    
    await _safe_answer(query, f"正在搜索标签: {tag}")
    
    # 调用标签搜索
    await search_posts_by_tag(update, context, tag)


async def handle_view_post(update: Update, context: CallbackContext):
    """查看原帖"""
    query = update.callback_query
    post_id = query.data.replace("view_post_", "")
    
    try:
        async with get_db() as conn:
            c = await conn.cursor()
            # published_posts 表没有 id 列（主键为 message_id），
            # 回调中的 post_id 是数据库 rowid
            await c.execute(
                "SELECT message_id FROM published_posts WHERE rowid=? AND is_deleted = 0",
                (post_id,)
            )
            row = await c.fetchone()
            
            if row:
                from config.settings import CHANNEL_ID
                message_id = row['message_id']
                
                # 生成频道消息链接
                channel_username = CHANNEL_ID.replace('@', '')
                link = f"https://t.me/{channel_username}/{message_id}"
                
                await _safe_answer(query, "正在跳转...")
                await query.edit_message_text(
                    f"📱 <a href='{link}'>点击查看原帖</a>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await _safe_answer(query, "❌ 帖子未找到", show_alert=True)
                
    except Exception as e:
        logger.error(f"查看帖子时出错: {e}")
        await _safe_answer(query, "❌ 操作失败", show_alert=True)


async def handle_stats_post(update: Update, context: CallbackContext):
    """查看帖子统计"""
    query = update.callback_query
    post_id = query.data.replace("stats_post_", "")
    
    try:
        from datetime import datetime
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute(
                """
                SELECT views, forwards, heat_score, publish_time 
                FROM published_posts 
                WHERE rowid=? AND is_deleted = 0
                """,
                (post_id,)
            )
            row = await c.fetchone()
            
            if row:
                # publish_time 为 Unix 时间戳（REAL），格式化展示
                try:
                    publish_time_str = datetime.fromtimestamp(row["publish_time"]).strftime("%Y-%m-%d %H:%M")
                except (TypeError, ValueError):
                    publish_time_str = "未知"
                stats_text = f"""
📊 <b>帖子统计</b>

👁️ 浏览量: {row['views']:,}
📤 转发量: {row['forwards']}
🔥 热度分: {row['heat_score']:.2f}
📅 发布时间: {publish_time_str}
"""
                await query.edit_message_text(
                    stats_text,
                    parse_mode=ParseMode.HTML
                )
            else:
                await _safe_answer(query, "❌ 统计数据未找到", show_alert=True)
                
    except Exception as e:
        logger.error(f"查看统计时出错: {e}")
        await _safe_answer(query, "❌ 操作失败", show_alert=True)


async def handle_delete_post(update: Update, context: CallbackContext):
    """
    删除帖子（仅 OWNER 可用）
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
    """
    query = update.callback_query
    message_id = query.data.replace("delete_post_", "")
    user_id = update.effective_user.id
    
    # 检查权限：只有 OWNER 可以删除
    if not is_owner(user_id):
        await _safe_answer(query, "⛔ 权限不足：只有管理员可以删除帖子", show_alert=True)
        logger.warning(f"用户 {user_id} 尝试删除帖子但权限不足")
        return
    
    # 显示确认对话框
    await query.edit_message_text(
        "⚠️ <b>删除确认</b>\n\n"
        f"确定要删除消息 ID 为 <code>{message_id}</code> 的帖子记录吗？\n\n"
        "⚠️ 此操作将：\n"
        "• ✅ 从频道删除消息（双向同步删除）\n"
        "• ✅ 从数据库标记为已删除（保留历史数据）\n"
        "• ✅ 从搜索索引删除\n\n"
        "💡 注意：数据会保留在数据库中，可通过数据库操作恢复",
        reply_markup=Keyboards.yes_no("delete_post", message_id),
        parse_mode=ParseMode.HTML
    )


# 管理面板相关逻辑已移除


async def handle_unblock_user(update: Update, context: CallbackContext):
    """移除黑名单"""
    query = update.callback_query
    target_user_id = int(query.data.replace("unblock_", ""))
    user_id = update.effective_user.id
    
    # 检查权限
    if not is_owner(user_id):
        await _safe_answer(query, "⛔ 权限不足", show_alert=True)
        return
    
    # 移除黑名单
    success = await remove_from_blacklist(target_user_id)
    
    if success:
        await _safe_answer(query, "✅ 已移除黑名单", show_alert=True)
        await query.edit_message_text(
            f"✅ 用户 {target_user_id} 已从黑名单移除"
        )
    else:
        await _safe_answer(query, "❌ 操作失败", show_alert=True)


async def handle_user_info(update: Update, context: CallbackContext):
    """查看用户信息"""
    query = update.callback_query
    target_user_id = query.data.replace("userinfo_", "")
    
    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute(
                "SELECT COUNT(*) as count FROM published_posts WHERE user_id=? AND is_deleted = 0",
                (target_user_id,)
            )
            row = await c.fetchone()
            
            info_text = f"""
👤 <b>用户信息</b>

🆔 用户ID: <code>{target_user_id}</code>
📝 投稿数: {row['count'] if row else 0}
"""
            await query.edit_message_text(
                info_text,
                parse_mode=ParseMode.HTML
            )
            
    except Exception as e:
        logger.error(f"查看用户信息时出错: {e}")
        await _safe_answer(query, "❌ 操作失败", show_alert=True)


async def handle_pagination(update: Update, context: CallbackContext):
    """处理分页：按存储的查询上下文 (user_data['pg']) 重新渲染指定页。"""
    query = update.callback_query

    if query.data == "page_info":
        await _safe_answer(query)
        return

    page = int(query.data.split("_")[-1])
    context.user_data['current_page'] = page
    pg = context.user_data.get('pg') or {}

    # 按上下文类型分发给对应的渲染器；两者都支持回调内编辑原消息。
    if pg.get('kind') == 'search':
        from handlers.search_handlers import render_search_page
        if await render_search_page(update, context, page):
            await _safe_answer(query)
            return
    elif pg.get('kind') == 'hot':
        from handlers.stats_handlers import get_hot_posts
        await get_hot_posts(update, context, edit_message=True, page=page)
        await _safe_answer(query)
        return

    # 无可用上下文（例如上下文已过期）：仅提示，不报错。
    await _safe_answer(query, f"跳转到第 {page} 页")


async def handle_confirm_action(update: Update, context: CallbackContext):
    """
    处理确认操作（仅 OWNER 可用）
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
    """
    query = update.callback_query
    action_data = query.data.replace("confirm_", "")
    user_id = update.effective_user.id
    
    # 检查权限：只有 OWNER 可以确认危险操作
    if not is_owner(user_id):
        await _safe_answer(query, "⛔ 权限不足：只有管理员可以执行此操作", show_alert=True)
        logger.warning(f"用户 {user_id} 尝试确认操作但权限不足")
        return
    
    # 解析操作类型
    if action_data.startswith("delete_post_"):
        message_id = action_data.replace("delete_post_", "")
        await execute_delete_post(query, message_id, context)
    else:
        # 其他确认操作
        await query.edit_message_text("✅ 操作已确认")


async def handle_cancel_action(update: Update, context: CallbackContext):
    """处理取消操作"""
    query = update.callback_query
    await _safe_answer(query)
    action_data = query.data.replace("cancel_", "")
    
    if action_data.startswith("delete_post_"):
        await query.edit_message_text("❌ 已取消删除操作")
    else:
        await query.edit_message_text("❌ 操作已取消")


async def execute_delete_post(query, message_id: str, context: CallbackContext):
    """
    执行删除帖子操作（仅 OWNER 可用）
    
    Args:
        query: CallbackQuery对象
        message_id: 频道消息ID
        context: 回调上下文
    """
    # 检查 message_id 是否有效
    if not message_id or message_id == 'None':
        await query.edit_message_text("❌ 无效的帖子ID")
        logger.error(f"尝试删除无效的帖子ID: {message_id}")
        return
    
    try:
        async with get_db() as conn:
            cursor = await conn.cursor()
            
            # 根据 message_id 获取帖子信息（包括已删除的帖子，用于检查状态）
            await cursor.execute(
                "SELECT rowid AS post_id, message_id, related_message_ids, is_deleted FROM published_posts WHERE message_id=?",
                (int(message_id),)
            )
            post_row = await cursor.fetchone()
            
            if not post_row:
                await query.edit_message_text("❌ 帖子不存在或已被删除")
                logger.warning(f"尝试删除不存在的帖子: message_id={message_id}")
                return
            
            # 检查是否已经标记为删除
            # 注意：post_row 是 sqlite3.Row，没有 .get()，需先判断列是否存在
            row_keys = post_row.keys() if hasattr(post_row, "keys") else []
            is_deleted = post_row["is_deleted"] if "is_deleted" in row_keys else 0
            if is_deleted == 1:
                await query.edit_message_text("ℹ️ 该帖子已被标记为删除")
                logger.info(f"帖子 {message_id} 已经被标记为删除")
                return
            
            post_id = post_row['post_id']
            related_ids_json = post_row['related_message_ids']
            
            # 先尝试删除频道消息（双向同步删除）
            from config.settings import CHANNEL_ID
            channel_deleted = False
            related_channel_deleted = 0
            channel_delete_failed = False
            
            try:
                # 尝试删除主消息
                try:
                    await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=int(message_id))
                    channel_deleted = True
                    logger.info(f"已从频道删除消息: {message_id}")
                except Exception as e:
                    error_msg = str(e).lower()
                    if "message to delete not found" in error_msg or "message can't be deleted" in error_msg:
                        # 消息已不存在或被删除，这是正常的
                        logger.info(f"频道消息 {message_id} 已不存在或无法删除: {e}")
                        channel_deleted = True  # 视为成功，因为目标已达成
                    else:
                        logger.warning(f"删除频道消息 {message_id} 失败: {e}")
                        channel_delete_failed = True
                
                # 尝试删除关联消息
                if related_ids_json:
                    import json
                    try:
                        related_ids = json.loads(related_ids_json)
                        for related_id in related_ids:
                            try:
                                await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=int(related_id))
                                related_channel_deleted += 1
                                logger.info(f"已从频道删除关联消息: {related_id}")
                            except Exception as e:
                                error_msg = str(e).lower()
                                if "message to delete not found" in error_msg or "message can't be deleted" in error_msg:
                                    related_channel_deleted += 1  # 视为成功
                                    logger.debug(f"关联消息 {related_id} 已不存在")
                                else:
                                    logger.warning(f"删除关联消息 {related_id} 失败: {e}")
                    except json.JSONDecodeError:
                        logger.warning(f"解析关联消息ID失败: {related_ids_json}")
            except Exception as e:
                logger.error(f"删除频道消息时出错: {e}")
                channel_delete_failed = True
            
            # 从搜索索引中删除（仅在搜索启用时操作，避免创建目录错位的索引）
            index_deleted = False
            related_count = 0
            try:
                from config.settings import SEARCH_ENABLED
                from utils.search_engine import get_search_engine
                search_engine = get_search_engine() if SEARCH_ENABLED else None
                if search_engine:
                    search_engine.delete_post(int(message_id))
                    index_deleted = True
                    logger.info(f"已从搜索索引删除帖子: {message_id}")
                    
                    # 如果有关联消息，也从索引删除
                    if related_ids_json:
                        import json
                        try:
                            related_ids = json.loads(related_ids_json)
                            for related_id in related_ids:
                                search_engine.delete_post(related_id)
                                related_count += 1
                            logger.info(f"已从索引删除 {related_count} 个关联消息")
                        except json.JSONDecodeError:
                            logger.warning(f"解析关联消息ID失败: {related_ids_json}")
            except Exception as e:
                logger.error(f"从搜索索引删除失败: {e}")
                # 继续执行，不因索引删除失败而中断
            
            # 标记为已删除而不是直接删除记录（保留历史数据）
            await cursor.execute("UPDATE published_posts SET is_deleted = 1 WHERE rowid=?", (post_id,))
            await conn.commit()
            logger.info(f"已标记帖子为已删除: ID={post_id}, message_id={message_id}")
            
            # 构建响应消息
            channel_link = f"https://t.me/{CHANNEL_ID.lstrip('@')}/{message_id}" if CHANNEL_ID.startswith('@') else f"消息ID: {message_id}"
            
            response = "✅ <b>删除操作完成</b>\n\n"
            response += f"📝 消息ID: <code>{message_id}</code>\n"
            response += f"🔗 频道链接: {channel_link}\n\n"
            response += "<b>已完成：</b>\n"
            
            # 频道消息删除状态
            if channel_deleted:
                if related_channel_deleted > 0:
                    response += f"✅ 从频道删除消息（包含 {related_channel_deleted} 个关联消息）\n"
                else:
                    response += "✅ 从频道删除消息\n"
            elif channel_delete_failed:
                response += "⚠️ 频道消息删除失败（可能无权限或消息已不存在）\n"
            else:
                response += "⚠️ 频道消息删除状态未知\n"
            
            # 数据库和索引删除状态
            response += "✅ 从数据库标记为已删除（保留历史数据）\n"
            if index_deleted:
                response += f"✅ 从搜索索引删除" + (f"（包含 {related_count} 个关联消息）" if related_count > 0 else "") + "\n"
            else:
                response += "⚠️ 搜索索引删除失败\n"
            
            await query.edit_message_text(response, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            
    except Exception as e:
        logger.error(f"删除帖子时出错: {e}", exc_info=True)
        await query.edit_message_text(f"❌ 删除失败: {str(e)[:100]}")


async def handle_back_to_main(update: Update, context: CallbackContext):
    """返回主菜单"""
    query = update.callback_query
    await _safe_answer(query)
    user_id = update.effective_user.id
    
    # 检查是否是管理员
    is_admin = is_owner(user_id)
    
    username = update.effective_user.first_name or update.effective_user.username
    
    await query.edit_message_text(
        MessageFormatter.welcome_message(username, is_admin),
        parse_mode=ParseMode.HTML,
        reply_markup=None
    )
    # 统一返回主菜单（不再显示管理员菜单提示文案）
    try:
        await query.message.reply_text("主菜单：", reply_markup=Keyboards.main_menu())
    except Exception:
        pass


async def handle_back(update: Update, context: CallbackContext):
    """返回上一页"""
    query = update.callback_query
    await _safe_answer(query)
    
    await query.edit_message_text(
        "🔙 返回上一页",
        reply_markup=None
    )
