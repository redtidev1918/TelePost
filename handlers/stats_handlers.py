"""
帖子统计和热度排行模块
"""
import json
import html as _html
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import CallbackContext

from config.settings import CHANNEL_ID
from database.db_manager import get_db

logger = logging.getLogger(__name__)


async def get_hot_posts(update: Update, context: CallbackContext, edit_message: bool = False, page: int = 1, week_alias_flag: bool = False):
    """
    获取热门帖子排行 - 只显示主贴，优化预览样式

    命令格式：
    /hot [数量] [时间范围]

    示例：
    /hot - 查看热门帖子（默认10个）
    /hot 20 - 查看前20个热门帖子
    /hot 10 week - 查看本周前10个热门帖子

    Args:
        edit_message: 由按钮回调触发时为 True，编辑原消息而非发送新消息
        page: 页码（从 1 开始），由分页按钮回调传入
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
    """
    try:
        from telepost.domain.hot import HotArgError, HotQuery, parse_hot_query
        week_alias = week_alias_flag  # noqa
        try:
            hq = parse_hot_query(list(context.args or []), week_alias=week_alias)
        except HotArgError as e:
            msg = f"⚠️ {e}"
            if update.message:
                await update.message.reply_text(msg)
            elif update.callback_query:
                await update.callback_query.answer(msg, show_alert=True)
            return

        scope = hq.scope
        limit = hq.limit
        time_filter = scope
        time_desc = hq.scope_label

        # Canonical HotService: Bot and Mini App MUST NOT drift.
        from telepost.application.hot import HotService
        service = HotService()
        page_result = await service.page(hq, max(1, int(page or 1)))
        if page_result.page > page_result.pages:
            page_result = await service.page(hq, page_result.pages)
        page = page_result.page
        pages = page_result.pages
        hot_posts = page_result.items
        if not hot_posts:
            empty_text = f"📊 暂无{time_desc}热门帖子数据"
            if edit_message and update.callback_query:
                try:
                    await update.callback_query.edit_message_text(empty_text)
                    return
                except Exception:
                    pass
            await update.message.reply_text(empty_text)
            return
        
        # 再次验证帖子是否仍然存在（防止并发问题）
        # 批量检查消息ID是否已删除
        message_ids = [post.message_id for post in hot_posts]
        valid_hot_posts = []
        
        if message_ids:
            async with get_db() as conn:
                cursor = await conn.cursor()
                # 使用 IN 查询批量检查
                placeholders = ','.join('?' * len(message_ids))
                await cursor.execute(
                    f"SELECT message_id FROM published_posts WHERE message_id IN ({placeholders}) AND is_deleted = 0",
                    message_ids
                )
                valid_message_ids = {row['message_id'] for row in await cursor.fetchall()}
            
            # 只保留未删除的帖子
            for post in hot_posts:
                if post.message_id in valid_message_ids:
                    valid_hot_posts.append(post)
        
        if not valid_hot_posts:
            empty_text = f"📊 暂无{time_desc}热门帖子数据（或所有结果已被删除）"
            if edit_message and update.callback_query:
                try:
                    await update.callback_query.edit_message_text(empty_text)
                    return
                except Exception:
                    pass  # 编辑失败则退回普通发送
            await update.message.reply_text(empty_text)
            return
        
        # 构建消息 - 优化显示格式
        message = f"🔥 <b>{time_desc}热榜 TOP {len(valid_hot_posts)}</b>\n"
        if scope == "week":
            from telepost.domain.hot import week_range_label
            message += f"📅 {week_range_label()}\n"
        message += "\n"
        
        for idx, post in enumerate(valid_hot_posts, 1):
            # 生成帖子链接
            if CHANNEL_ID.startswith('@'):
                channel_username = CHANNEL_ID.lstrip('@')
                post_link = f"https://t.me/{channel_username}/{post.message_id}"
            else:
                post_link = f"消息ID: {post.message_id}"
            
            # 解析标签
            tags_display = ""
            if post.tags:
                try:
                    # 尝试解析JSON格式的标签
                    tags = json.loads(post.tags)
                    if isinstance(tags, list):
                        tags_display = ' '.join([f"#{tag}" for tag in tags[:5]])  # 显示最多5个标签
                    else:
                        tags_display = post.tags  # 如果不是列表，直接显示
                except (json.JSONDecodeError, TypeError, ValueError):
                    # 如果解析失败，假设是空格分隔的字符串
                    tags_list = post.tags.split()[:5]
                    tags_display = ' '.join([f"#{tag.lstrip('#')}" for tag in tags_list])
            
            # 处理标题（纯文本上截断，转义后再进 HTML，防止 < > & 破坏解析）
            title = post.title or '无标题'
            if len(title) > 40:
                title = title[:37] + '...'
            title = _html.escape(str(title))

            # 处理简介（note）——同样转义
            note_preview = ""
            if post.note:
                note = post.note.strip()
                if note:
                    # 去掉换行，限制长度
                    note = note.replace('\n', ' ').replace('\r', ' ')
                    if len(note) > 60:
                        note = note[:57] + '...'
                    note_preview = f"\n   💬 {_html.escape(note)}"

            # 标签转义
            if tags_display:
                tags_display = _html.escape(str(tags_display))
            
            # 格式化发布时间
            publish_time = datetime.fromtimestamp(post.publish_time)
            time_ago = _format_time_ago(publish_time)
            
            # 构建单个帖子的显示
            # 仅当链接是真实 URL 时才用 <a> 包裹（私有频道时 post_link 是纯文本）
            if str(post_link).startswith("http"):
                message += f"<b>{idx}.</b> <a href=\"{post_link}\">{title}</a>\n"
            else:
                message += f"<b>{idx}.</b> {title}\n"
            
            if tags_display:
                message += f"   🏷 {tags_display}\n"
            
            if note_preview:
                message += note_preview + "\n"
            
            # 统计数据
            stats_parts = []
            if post.reactions > 0:
                stats_parts.append(f"❤️ {post.reactions}")
            
            if stats_parts:
                message += f"   📊 {' | '.join(stats_parts)}\n"
            
            # 热度和时间
            message += f"   🔥 热度: <code>{post.heat_score:.1f}</code> • 🕐 {time_ago}\n"
            message += "\n"
            
            # 防止消息过长
            if len(message) > 3500:
                message += "...\n\n💡 更多帖子请使用 /search 搜索"
                break
        
        message += f"━━━━━━━━━━━━━━━\n"
        message += f"💡 /hot 全部 · /hot 20 · /hotweek 本周 · /hotweek 10"
        
        
        # 分页导航：多页时附加 ⬅️/➡️ 按钮，并记录翻页上下文
        from ui.keyboards import Keyboards
        if pages > 1:
            context.user_data['pg'] = {'kind': 'hot', 'tf': time_filter, 'limit': limit}
            keyboard = Keyboards.page_nav(page, pages)
        else:
            context.user_data.pop('pg', None)
            keyboard = None

        # 回调触发时编辑原消息；命令触发时发送新消息
        if edit_message and update.callback_query:
            try:
                await update.callback_query.edit_message_text(
                    message,
                    disable_web_page_preview=True,
                    parse_mode='HTML',
                    reply_markup=keyboard,
                )
            except Exception:
                await update.effective_message.reply_text(
                    message,
                    disable_web_page_preview=True,
                    parse_mode='HTML',
                    reply_markup=keyboard,
                )
        else:
            await update.message.reply_text(
                message,
                disable_web_page_preview=True,
                parse_mode='HTML',
                reply_markup=keyboard,
            )
        
    except Exception as e:
        logger.error(f"获取热门帖子失败: {e}")
        try:
            target = update.callback_query if update.callback_query else update.effective_message
            await target.reply_text("❌ 获取热门帖子失败，请稍后重试")
        except Exception:
            pass


def _format_time_ago(publish_time: datetime) -> str:
    """
    格式化时间为"多久前"的形式
    
    Args:
        publish_time: 发布时间
        
    Returns:
        str: 格式化的时间字符串
    """
    now = datetime.now()
    delta = now - publish_time
    
    if delta.days > 30:
        months = delta.days // 30
        return f"{months}月前"
    elif delta.days > 0:
        return f"{delta.days}天前"
    elif delta.seconds >= 3600:
        hours = delta.seconds // 3600
        return f"{hours}小时前"
    elif delta.seconds >= 60:
        minutes = delta.seconds // 60
        return f"{minutes}分钟前"
    else:
        return "刚刚"


def _format_number(num: int) -> str:
    """
    格式化数字，大数字使用k、w等单位
    
    Args:
        num: 要格式化的数字
        
    Returns:
        str: 格式化后的字符串
    """
    if num >= 10000:
        return f"{num / 10000:.1f}w"
    elif num >= 1000:
        return f"{num / 1000:.1f}k"
    else:
        return str(num)


async def get_user_stats(update: Update, context: CallbackContext):
    """
    获取用户投稿统计
    
    命令格式：
    /mystats - 查看自己的投稿统计
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
    """
    user_id = update.effective_user.id
    
    try:
        async with get_db() as conn:
            cursor = await conn.cursor()
            
            # 获取用户的所有投稿（过滤已删除的帖子）
            await cursor.execute(
                "SELECT * FROM published_posts WHERE user_id = ? AND is_deleted = 0 ORDER BY publish_time DESC",
                (user_id,)
            )
            user_posts = await cursor.fetchall()
        
        if not user_posts:
            await update.message.reply_text("📊 您还没有发布过投稿")
            return
        
        # 统计数据
        total_posts = len(user_posts)
        total_reactions = sum(post['reactions'] for post in user_posts)
        
        # 最热的帖子
        hottest_post = max(user_posts, key=lambda x: x['heat_score'])
        
        # 生成链接
        if CHANNEL_ID.startswith('@'):
            channel_username = CHANNEL_ID.lstrip('@')
            hottest_link = f"https://t.me/{channel_username}/{hottest_post['message_id']}"
        else:
            hottest_link = f"消息ID: {hottest_post['message_id']}"
        
        message = (
            f"📊 您的投稿统计\n\n"
            f"📝 总投稿数：{total_posts}\n"
            f"❤️ 总反应数：{total_reactions}\n\n"
            f"🔥 最热帖子：\n"
            f"   标题：{hottest_post['title'] or '无标题'}\n"
            f"   热度：{hottest_post['heat_score']:.1f}\n"
            f"   链接：{hottest_link}\n\n"
            f"💡 使用 /hot 查看全站热门帖子"
        )
        
        await update.message.reply_text(message, disable_web_page_preview=True)
        
    except Exception as e:
        logger.error(f"获取用户统计失败: {e}")
        await update.message.reply_text("❌ 获取统计失败，请稍后重试")



async def stats_command(update: Update, context: CallbackContext):
    """仅 Owner：查看全局统计（/stats）。"""
    from utils.blacklist import is_owner, get_blacklist
    from ui.messages import MessageFormatter
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await update.message.reply_text(MessageFormatter.error_message("permission"), parse_mode="HTML")
        return
    total_posts = total_views = total_forwards = 0
    total_users = active_users = 0
    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("SELECT COUNT(*) AS c FROM published_posts WHERE is_deleted = 0")
            row = await c.fetchone()
            total_posts = int(row["c"] or 0) if row else 0
            await c.execute("SELECT COUNT(DISTINCT user_id) AS c FROM published_posts WHERE is_deleted = 0 AND user_id IS NOT NULL")
            row = await c.fetchone()
            total_users = int(row["c"] or 0) if row else 0
            await c.execute("SELECT COALESCE(SUM(reactions),0) AS s FROM published_posts WHERE is_deleted = 0")
            row = await c.fetchone()
            total_reactions = int(row["s"] or 0) if row else 0
            cutoff = (datetime.now() - timedelta(days=7)).timestamp()
            await c.execute(
                "SELECT COUNT(DISTINCT user_id) AS c FROM published_posts "
                "WHERE is_deleted = 0 AND user_id IS NOT NULL AND publish_time > ?",
                (cutoff,),
            )
            row = await c.fetchone()
            active_users = int(row["c"] or 0) if row else 0
        blacklist = await get_blacklist()
        blacklist_count = len(blacklist) if blacklist else 0
        stats = {
            "total_users": total_users,
            "active_users_7d": active_users,
            "blacklist_count": blacklist_count,
            "total_posts": total_posts,
            "total_reactions": total_reactions,
        }
        text = MessageFormatter.admin_stats(stats)
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as exc:
        logger.error("全局统计失败: %s", exc, exc_info=True)
        await update.message.reply_text("❌ 获取全局统计失败，请稍后重试。")




async def hot_week_posts(update: Update, context: CallbackContext):
    """/hotweek — 本周热榜（自然周，周一 00:00 起）。"""
    await get_hot_posts(update, context, week_alias_flag=True)


async def build_hot_message(hq=None, *, scope: str = "all", limit: int = 10) -> str:
    """Build the hot list message text (no Telegram send). Used by /hot and automation."""
    from telepost.domain.hot import HotQuery as _HQ, week_range_label
    from telepost.application.hot import HotService
    if hq is None:
        hq = _HQ(scope=scope, limit=limit)
    time_desc = hq.scope_label

    page = await HotService().page(hq)
    posts = page.items

    if not posts:
        return f"📊 暂无{time_desc}热榜数据"

    message = f"🔥 <b>{time_desc}热榜 TOP {len(posts)}</b>\n"
    if hq.scope == "week":
        message += f"📅 {week_range_label()}\n"
    message += "\n"

    for idx, post in enumerate(posts, 1):
        if CHANNEL_ID.startswith('@'):
            channel_username = CHANNEL_ID.lstrip('@')
            post_link = f"https://t.me/{channel_username}/{post.message_id}"
        else:
            post_link = f"消息ID: {post.message_id}"
        title = post.title or '无标题'
        if len(title) > 40:
            title = title[:37] + '...'
        title = _html.escape(str(title))
        if str(post_link).startswith("http"):
            message += f"<b>{idx}.</b> <a href=\"{post_link}\">{title}</a>\n"
        else:
            message += f"<b>{idx}.</b> {title}\n"
        stats_parts = []
        if post.reactions > 0:
            stats_parts.append(f"❤️ {post.reactions}")
        if stats_parts:
            message += f"   📊 {' | '.join(stats_parts)}\n"
        message += f"   🔥 <code>{post.heat_score:.1f}</code>\n\n"
    return message


