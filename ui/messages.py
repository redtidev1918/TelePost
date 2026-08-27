"""
现代化的消息格式化模块
提供美观的 HTML/Markdown 格式消息
"""
from datetime import datetime
from typing import List, Dict, Any


class MessageFormatter:
    """消息格式化器"""
    
    @staticmethod
    def welcome_message(username: str, is_admin: bool = False) -> str:
        """欢迎消息"""
        role = "👑 管理员" if is_admin else "👤 用户"
        
        return f"""
🎉 <b>欢迎使用投稿机器人！</b>

👋 你好，<b>{username}</b>！
🎭 身份：{role}

<b>📝 主要功能：</b>
• 快速投稿到频道
• 查看热门内容排行
• 搜索历史投稿
• 个人统计分析

<b>🚀 快速开始：</b>
点击下方菜单按钮，或输入 /help 查看完整帮助

<i>让我们开始吧！</i> ✨
"""
    
    @staticmethod
    def help_message(is_admin: bool = False) -> str:
        """帮助消息"""
        basic_help = """
📚 <b>使用指南</b>

<b>📝 投稿相关：</b>
/submit - 开始新投稿
/cancel - 取消当前投稿

<b>📊 统计查询：</b>
/hot [数量] [时间] - 查看热门内容
  示例: /hot 20 week
/mystats - 我的投稿统计
/myposts [数量] - 我的投稿列表

<b>🔍 搜索功能：</b>
/search <关键词> [-t 时间] - 搜索内容
  示例: /search Python -t month
/tags [数量] - 查看热门标签

<b>ℹ️ 其他：</b>
/help - 显示此帮助
/about - 关于机器人
"""
        
        admin_help = """
<b>👑 管理员命令：</b>
/addblacklist <ID> - 添加黑名单
/removeblacklist <ID> - 移除黑名单
/blacklist - 查看黑名单
/searchuser <ID> - 查询用户投稿
/broadcast <消息> - 广播消息
/stats - 全局统计信息

"""
        
        footer = """
💡 <b>小贴士：</b>
• 使用下方菜单按钮快速访问功能
• 投稿支持文字、图片、视频等多种格式
• 可以添加 #标签 让内容更易被发现
"""
        
        if is_admin:
            return basic_help + admin_help + footer
        return basic_help + footer
    
    @staticmethod
    def about_message() -> str:
        """关于消息"""
        return """
ℹ️ <b>关于投稿机器人</b>

<b>🤖 机器人版本：</b> v2.0
<b>⚡ 框架：</b> python-telegram-bot
<b>🎨 特性：</b> 现代化 UI，智能统计

<b>✨ 主要亮点：</b>
• 📊 智能热度算法
• 🔍 全文搜索引擎
• 📈 详细数据分析
• 🎯 个性化推荐

<b>👨‍💻 开发者：</b> redtidev1918
<b>📦 开源地址：</b> https://github.com/redtidev1918/TelePost

<i>感谢使用！如有问题请联系管理员。</i>
"""
    
    @staticmethod
    def submission_preview(content: str, tags: List[str] = None, media_count: int = 0) -> str:
        """投稿预览"""
        preview = f"""
📋 <b>投稿预览</b>

<b>内容：</b>
{content[:200]}{'...' if len(content) > 200 else ''}

"""
        if tags:
            preview += f"<b>标签：</b> {' '.join(tags)}\n"
        
        if media_count > 0:
            preview += f"<b>媒体文件：</b> {media_count} 个\n"
        
        preview += "\n<i>请确认信息无误后发布</i>"
        
        return preview
    
    @staticmethod
    def hot_posts_header(limit: int = 10, time_filter: str = "all") -> str:
        """热门帖子标题"""
        time_map = {
            "day": "今天",
            "week": "本周",
            "month": "本月",
            "all": "全部时间"
        }
        time_text = time_map.get(time_filter, "全部时间")
        
        return f"""
🔥 <b>热门内容排行</b>

📅 时间范围：{time_text}
🔢 显示数量：前 {limit} 名

━━━━━━━━━━━━━━━━
"""
    
    @staticmethod
    def hot_post_item(rank: int, post: Dict[str, Any]) -> str:
        """单个热门帖子条目"""
        # 热度评分
        heat = post.get('heat_score', 0)
        
        # Emoji 奖牌
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        rank_emoji = medals.get(rank, f"#{rank}")
        
        # 内容预览
        content = post.get('content', '')[:80]
        if len(post.get('content', '')) > 80:
            content += "..."
        
        # 统计数据
        views = post.get('views', 0)
        forwards = post.get('forwards', 0)
        
        # 发布时间
        created_at = post.get('created_at', '')
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at)
                time_str = dt.strftime("%m-%d %H:%M")
            except (ValueError, TypeError):
                time_str = created_at
        else:
            time_str = "未知"
        
        return f"""
{rank_emoji} <b>热度：{heat:.1f}</b>
📝 {content}
👁️ {views} 浏览 | 📤 {forwards} 转发
🕒 {time_str}
"""
    
    @staticmethod
    def search_results_header(keyword: str, count: int) -> str:
        """搜索结果标题"""
        return f"""
🔍 <b>搜索结果</b>

关键词：<code>{keyword}</code>
找到 <b>{count}</b> 条结果

━━━━━━━━━━━━━━━━
"""
    
    @staticmethod
    def search_result_item(post: Dict[str, Any], highlight: str = "") -> str:
        """单个搜索结果"""
        content = post.get('content', '')[:100]
        if len(post.get('content', '')) > 100:
            content += "..."
        
        # 高亮关键词
        if highlight:
            content = content.replace(highlight, f"<b>{highlight}</b>")
        
        tags = post.get('tags', '')
        created_at = post.get('created_at', '')
        
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at)
                time_str = dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                time_str = created_at
        else:
            time_str = "未知"
        
        result = f"""
📝 {content}
"""
        if tags:
            result += f"🏷️ {tags}\n"
        
        result += f"🕒 {time_str}\n"
        
        return result
    
    @staticmethod
    def user_stats(stats: Dict[str, Any]) -> str:
        """用户统计信息"""
        total_posts = stats.get('total_posts', 0)
        total_views = stats.get('total_views', 0)
        total_forwards = stats.get('total_forwards', 0)
        avg_heat = stats.get('avg_heat', 0)
        top_tags = stats.get('top_tags', [])
        
        # 计算平均数据
        avg_views = total_views / total_posts if total_posts > 0 else 0
        avg_forwards = total_forwards / total_posts if total_posts > 0 else 0
        
        msg = f"""
📊 <b>我的统计数据</b>

<b>📝 投稿概况：</b>
• 总投稿数：{total_posts} 篇
• 总浏览量：{total_views:,} 次
• 总转发量：{total_forwards} 次

<b>📈 平均表现：</b>
• 平均浏览：{avg_views:.1f} 次/篇
• 平均转发：{avg_forwards:.1f} 次/篇
• 平均热度：{avg_heat:.1f}

"""
        
        if top_tags:
            msg += "<b>🏷️ 常用标签：</b>\n"
            for tag, count in top_tags[:5]:
                msg += f"• {tag}: {count} 次\n"
        
        msg += "\n<i>继续加油！💪</i>"
        
        return msg
    
    @staticmethod
    def tag_cloud_header(total_tags: int) -> str:
        """标签云标题"""
        return f"""
🏷️ <b>热门标签云</b>

共有 <b>{total_tags}</b> 个标签

<i>点击标签查看相关内容</i>
━━━━━━━━━━━━━━━━
"""
    
    @staticmethod
    def admin_stats(stats: Dict[str, Any]) -> str:
        """管理员统计信息"""
        total_users = stats.get('total_users', 0)
        total_posts = stats.get('total_posts', 0)
        total_views = stats.get('total_views', 0)
        total_forwards = stats.get('total_forwards', 0)
        active_users = stats.get('active_users_7d', 0)
        blacklist_count = stats.get('blacklist_count', 0)
        
        return f"""
👑 <b>全局统计数据</b>

<b>👥 用户统计：</b>
• 总用户数：{total_users}
• 7日活跃：{active_users}
• 黑名单：{blacklist_count}

<b>📝 内容统计：</b>
• 总投稿数：{total_posts}
• 总浏览量：{total_views:,}
• 总转发量：{total_forwards}

<b>📈 平均数据：</b>
• 人均投稿：{total_posts/total_users if total_users > 0 else 0:.1f}
• 篇均浏览：{total_views/total_posts if total_posts > 0 else 0:.1f}
• 篇均转发：{total_forwards/total_posts if total_posts > 0 else 0:.1f}

<i>最后更新：{datetime.now().strftime("%Y-%m-%d %H:%M")}</i>
"""
    
    @staticmethod
    def blacklist_user_info(user_info: Dict[str, Any]) -> str:
        """黑名单用户信息"""
        user_id = user_info.get('user_id', 'Unknown')
        username = user_info.get('username', '无用户名')
        reason = user_info.get('reason', '无')
        added_at = user_info.get('added_at', '未知')
        post_count = user_info.get('post_count', 0)
        
        return f"""
🚫 <b>黑名单用户详情</b>

<b>用户信息：</b>
• ID: <code>{user_id}</code>
• 用户名: @{username}
• 投稿数: {post_count}

<b>封禁信息：</b>
• 原因: {reason}
• 时间: {added_at}
"""
    
    @staticmethod
    def error_message(error_type: str = "general") -> str:
        """错误消息"""
        errors = {
            "general": "❌ 操作失败，请稍后重试",
            "permission": "⛔ 权限不足，仅管理员可用",
            "blacklist": "🚫 你已被加入黑名单，无法投稿",
            "session": "❌ 未找到投稿会话，请使用 /submit 开始",
            "invalid_format": "❌ 格式错误，请检查输入",
            "not_found": "❌ 未找到相关内容",
            "rate_limit": "⏰ 操作过于频繁，请稍后再试"
        }
        return errors.get(error_type, errors["general"])
    
    @staticmethod
    def success_message(action: str = "操作") -> str:
        """成功消息"""
        return f"✅ {action}成功！"
    
    @staticmethod
    def loading_message() -> str:
        """加载消息"""
        return "⏳ 处理中，请稍候..."
    
    @staticmethod
    def submission_guide() -> str:
        """投稿指南"""
        return """
📝 <b>投稿指南</b>

<b>支持的内容类型：</b>
• 📄 纯文字消息
• 🖼️ 图片（单张或多张）
• 🎥 视频
• 📹 动图 (GIF)
• 📁 文档

<b>使用标签：</b>
在内容中添加 #标签 让你的投稿更易被发现
示例: 今天学习了 Python #编程 #学习

<b>投稿流程：</b>
1️⃣ 发送你的内容
2️⃣ 可选：添加标签或媒体
3️⃣ 预览并确认
4️⃣ 发布到频道

<i>准备好了吗？发送你的内容开始投稿！</i>
"""
    
    @staticmethod
    def pagination_info(current: int, total: int) -> str:
        """分页信息"""
        return f"📄 第 {current}/{total} 页"
    
    @staticmethod
    def empty_result() -> str:
        """空结果"""
        return """
🔍 <b>暂无结果</b>

没有找到相关内容
试试其他关键词或筛选条件吧
"""
    
    @staticmethod
    def format_number(num: int) -> str:
        """格式化数字"""
        if num >= 1000000:
            return f"{num/1000000:.1f}M"
        elif num >= 1000:
            return f"{num/1000:.1f}K"
        return str(num)
    
    @staticmethod
    def progress_bar(current: int, total: int, width: int = 10) -> str:
        """进度条"""
        if total == 0:
            return "▱" * width
        
        filled = int(width * current / total)
        empty = width - filled
        
        return "▰" * filled + "▱" * empty

