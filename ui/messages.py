"""
现代化的消息格式化模块
提供美观的 HTML/Markdown 格式消息
"""
from ui.i18n import tr
from datetime import datetime
from typing import List, Dict, Any


class MessageFormatter:
    """消息格式化器"""
    
    @staticmethod
    def welcome_message(username: str, is_admin: bool = False) -> str:
        """欢迎消息"""
        role = tr('👑 管理员') if is_admin else tr('👤 用户')

        return (
            tr("""👋 <b>你好，{p0}！</b>

我是投稿机器人，欢迎你，帮你把图文内容发布到频道。
🎭 身份：{p1}

📌 <b>快速开始</b>
点击下方「开始投稿」，或发送 /submit

📚 <b>常用功能</b>
/submit 开始投稿
/search 搜索内容
/mystats 我的统计
/myposts 我的投稿
/hot 全部热榜
/hotweek 本周热榜
/help 完整帮助

💡 <i>想直接投稿？发送 /submit 就开始。</i>""").format(p0=username, p1=role)
        )
    
    @staticmethod
    def help_message(is_admin: bool = False) -> str:
        """帮助消息"""
        basic_help = tr("""📚 <b>使用帮助</b>

<b>📝 投稿</b>
/submit 开始投稿
/done_media 完成上传、打开预览
/skip_media 跳过媒体，直接进入预览
/cancel 取消投稿

<b>🔥 热榜</b>
/hot 全部时间热榜 TOP 10
/hot 20 全部时间热榜 TOP 20
/hotweek 本周热榜 TOP 10
/hotweek 10 本周热榜 TOP 10

<b>🔍 内容</b>
/search 关键词
/tags 标签云

<b>📋 我的内容</b>
/myposts 我的投稿
/mystats 我的统计

<b>📱 Mini App</b>
点击下方键盘的「📱 Mini App」按钮进入小程序
（或发送 /start 后点「📱 打开投稿」）
首页即热门内容，可浏览帖子与投稿

<b>ℹ️ 其他</b>
/status 最近计划状态
/settings 机器人设置
/about 关于
/help 完整帮助
""")

        admin_help = tr("""
👑 <b>管理员</b>
/schedule 定时任务（发 /schedule 查看引导）
/stats 全局统计
/searchuser &lt;ID&gt; 查询用户投稿
/ban_user &lt;ID&gt; 封禁用户
/ban_api &lt;编号&gt; 禁用 API
/blacklist 黑名单管理
/tokens 查看 API Token
/gen_token 生成 API Token
/revoke_token 撤销 API Token
/delete_posts 批量删除投稿
/pin_status 置顶状态
/rebuild_index 重建索引
/sync_index 同步索引
/index_stats 索引统计
/optimize_index 优化索引
/botconfig 机器人配置
/debug 调试
""")

        footer = tr("""💡 投稿支持图片 / 视频 / 压缩包 / PDF。
任一环节发 /cancel 可取消。""")

        if is_admin:
            return basic_help + admin_help + footer
        return basic_help + footer

    @staticmethod
    def about_message() -> str:
        """关于消息"""
        return tr("""
ℹ️ <b>关于投稿机器人</b>

这是一个投稿机器人，支持图文 / 文件投稿，提交前可预览确认。

<b>常用命令</b>
/submit 发起投稿
/mystats 查看我的投稿统计
/cancel 取消当前投稿
/help 查看完整帮助

<b>🔗 开源：</b> https://github.com/redtidev1918/TelePost

<i>有问题可以私聊管理员。</i>
""")
    
    @staticmethod
    def submission_preview(content: str, tags: List[str] = None, media_count: int = 0) -> str:
        """投稿预览"""
        preview = tr("""
📋 <b>投稿预览</b>

<b>内容：</b>
{p0}{p1}

""").format(p0=content[:200], p1='...' if len(content) > 200 else '')
        if tags:
            preview += tr("""<b>标签：</b> {p0}
""").format(p0=' '.join(tags))
        
        if media_count > 0:
            preview += tr("""<b>媒体文件：</b> {p0} 个
""").format(p0=media_count)
        
        preview += tr("""
<i>请确认信息无误后发布</i>""")
        
        return preview
    
    @staticmethod
    def hot_posts_header(limit: int = 10, time_filter: str = "all") -> str:
        """热门帖子标题"""
        time_map = {
            "day": tr('今天'),
            "week": tr('本周'),
            "month": tr('本月'),
            "all": tr('全部时间')
        }
        time_text = time_map.get(time_filter, tr('全部时间'))
        
        return tr("""
🔥 <b>热门内容排行</b>

📅 时间范围：{p0}
🔢 显示数量：前 {p1} 名

━━━━━━━━━━━━━━━━
""").format(p0=time_text, p1=limit)
    
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
        
        # Telegram Bot API 不提供频道帖子的浏览量 / 转发量；不要展示恒为 0
        # 的假指标。Reaction 是当前唯一真实互动信号。
        reactions = int(post.get('reactions', 0) or 0)

        # 发布时间
        created_at = post.get('created_at', '')
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at)
                time_str = dt.strftime("%m-%d %H:%M")
            except (ValueError, TypeError):
                time_str = created_at
        else:
            time_str = tr('未知')
        
        return tr("""
{p0} <b>热度：{p1:.1f}</b>
📝 {p2}
❤️ {p3} 反应
🕒 {p4}
""").format(p0=rank_emoji, p1=heat, p2=content, p3=reactions, p4=time_str)
    
    @staticmethod
    def search_results_header(keyword: str, count: int) -> str:
        """搜索结果标题"""
        return tr("""
🔍 <b>搜索结果</b>

关键词：<code>{p0}</code>
找到 <b>{p1}</b> 条结果

━━━━━━━━━━━━━━━━
""").format(p0=keyword, p1=count)
    
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
            time_str = tr('未知')
        
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
        total_reactions = stats.get('total_reactions', 0)
        avg_heat = stats.get('avg_heat', 0)
        top_tags = stats.get('top_tags', [])
        
        msg = tr("""
📊 <b>我的统计数据</b>

<b>📝 投稿概况：</b>
• 总投稿数：{p0} 篇
• ❤️ 总反应数：{p1:,} 次

<b>📈 平均表现：</b>
• 平均热度：{p2:.1f}


""").format(p0=total_posts, p1=total_reactions, p2=avg_heat)
        
        if top_tags:
            msg += tr("""<b>🏷️ 常用标签：</b>
""")
            for tag, count in top_tags[:5]:
                msg += tr("""• {p0}: {p1} 次
""").format(p0=tag, p1=count)
        
        msg += tr("""
<i>继续加油！💪</i>""")
        
        return msg
    
    
    @staticmethod
    def admin_stats(stats: Dict[str, Any]) -> str:
        """管理员统计信息"""
        total_users = stats.get('total_users', 0)
        total_posts = stats.get('total_posts', 0)
        total_reactions = stats.get('total_reactions', 0)
        active_users = stats.get('active_users_7d', 0)
        blacklist_count = stats.get('blacklist_count', 0)
        
        return tr("""
👑 <b>全局统计数据</b>

<b>👥 用户统计：</b>
• 总用户数：{p0}
• 7日活跃：{p1}
• 黑名单：{p2}

<b>📝 内容统计：</b>
• 总投稿数：{p3}
• ❤️ 总反应数：{p4:,}

<b>📈 平均数据：</b>
• 人均投稿：{p5:.1f}
• 篇均反应：{p6:.1f}


<i>最后更新：{p7}</i>
""").format(p0=total_users, p1=active_users, p2=blacklist_count, p3=total_posts, p4=total_reactions, p5=total_posts / total_users if total_users > 0 else 0, p6=total_reactions / total_posts if total_posts > 0 else 0, p7=datetime.now().strftime('%Y-%m-%d %H:%M'))
    
    
    @staticmethod
    def error_message(error_type: str = "general") -> str:
        """错误消息"""
        errors = {
            "general": tr("""❌ 操作失败，请稍后重试。

如果反复出现，请私聊管理员。"""),
            "permission": tr("""⛔ 权限不足，仅管理员可用。

请输入 /help 查看可用命令。"""),
            "blacklist": tr("""🚫 你已被加入黑名单，无法投稿。

如有疑问请联系管理员。"""),
            "session": tr('❌ 投稿会话已过期，请发送 /submit 重新开始。'),
            "invalid_format": tr('❌ 格式错误，请检查输入后重试。'),
            "not_found": tr('❌ 未找到相关内容，换个关键词试试吧。'),
            "rate_limit": tr('⏰ 操作太频繁，请稍等片刻再试。')
        }
        return errors.get(error_type, errors["general"])
    
    @staticmethod
    def success_message(action: str = "") -> str:
        """成功消息"""
        return tr('✅ {p0}成功！').format(p0=action or tr("操作"))
    
    @staticmethod
    def loading_message() -> str:
        """加载消息"""
        return tr('⏳ 处理中，请稍候...')
    
    @staticmethod
    def submission_guide() -> str:
        """投稿指南"""
        return tr("""
📝 <b>投稿指南</b>

<b>支持的内容</b>
• 🖼️ 图片（单张或多张）
• 🎥 视频 / 📹 动图 (GIF) / 🔉 音频
• 📁 压缩包 / PDF 等文档附件

<b>投稿流程</b>
1️⃣ 发送 /submit 开始
2️⃣ 上传内容（选填标签、标题、简介、链接）
3️⃣ 发送 /done_media 打开预览
4️⃣ 修改确认后发布 / 提交审核

<b>小提示</b>
• 内容里加 #标签 更容易被发现
• 随时发送 /cancel 取消投稿

<i>准备好了吗？发送 /submit 开始！</i>
""")
    
    # ── 投稿流程文案（UPLOAD → PREVIEW → EDIT 各阶段集中在此） ──────────────

    @staticmethod
    def submit_hint(mode: str, max_files: int = 10) -> str:
        """/submit 进入上传阶段时的引导提示。mode: MEDIA / DOCUMENT / MIXED。"""
        common = (
            tr("""

📋 <b>下一步</b>
1️⃣ 继续上传内容，或发送 /done_media 打开预览
2️⃣ 在预览页填写标签（必填）、标题、简介、链接
3️⃣ 确认无误后点击按钮发布 / 提交审核

💡 <b>小提示</b>
• 匿名、剧透默认关闭，可在预览页开启
• 任一环节发送 /cancel 可取消投稿""")
        )
        if mode == "MEDIA":
            return (
                tr("""📮 <b>开始投稿：上传媒体</b>
请直接上传图片、视频、GIF 或音频
• 最多 {p0} 个
• 每收到一条会显示当前数量
• 上传完成发送 /done_media 打开预览，或 /cancel 取消""").format(p0=max_files)
            ) + common
        if mode == "DOCUMENT":
            return (
                tr("""📮 <b>开始投稿：上传文件</b>
请直接上传（以附件发送图片、压缩包、PDF 等文件）
• 最多 {p0} 个
• 每收到一条会显示当前数量
• 上传完成发送 /done_media 打开预览，或 /cancel 取消""").format(p0=max_files)
            ) + common
        return (
            tr("""📮 <b>开始投稿</b>
请直接上传内容
• 相册图片、视频、GIF、音频 → 媒体
• 以附件发送的图片、压缩包、PDF → 文件
• 合计最多 {p0} 个，媒体/文件可混合
• 上传完成发送 /done_media 打开预览，或 /cancel 取消""").format(p0=max_files)
        ) + common

    @staticmethod
    def upload_received(kind: str, total: int, max_files: int) -> str:
        """每条媒体/文件成功入库后的计数提示。"""
        label = tr('文件') if kind == "document" else tr('媒体')
        return (
            tr("""✅ 已接收{p0}，当前 {p1}/{p2} 个。
💡 可继续上传，或发送 /done_media 打开预览、/cancel 取消。""").format(p0=label, p1=total, p2=max_files)
        )

    @staticmethod
    def upload_supported_types() -> str:
        return (
            tr("""⚠️ 这里只接收媒体或文件。

• 🖼️ 图片 / 📹 视频 / GIF / 音频：直接发送
• 📦 压缩包 / PDF 等：以附件发送

🏷️ 标签、标题、简介和链接请在 /done_media 打开预览后填写。""")
        )

    @staticmethod
    def upload_mode_limited(kind: str) -> str:
        if kind == "document":
            return tr("""⚠️ 当前为媒体投稿模式，不支持文件附件。

请直接发送图片、视频、GIF 或音频。""")
        return tr("""⚠️ 当前为文档投稿模式，请以附件发送文件。

请发送压缩包、PDF 等文件，或以附件发送图片。""")

    @staticmethod
    def upload_blocked_type(file_name: str, blocked_desc: str) -> str:
        """命中文件类型黑名单时的拒绝提示。"""
        name = file_name or "未知文件"
        return (
            tr("""⚠️ 暂不支持此类文件，已被自动拦截。

📄 文件：{p0}

这是为了防止可执行软件或压缩包被当作普通投稿上传。
可改发图片、视频、GIF、音频或文本文档（如 TXT、PDF、MD）。""").format(p0=name)
        )

    @staticmethod
    def upload_max_files(max_files: int) -> str:
        return (
            tr("""⚠️ 单条投稿最多 {p0} 个文件。

请发送 /done_media 进入预览，或 /cancel 后重新投稿。""").format(p0=max_files)
        )

    @staticmethod
    def session_expired() -> str:
        return tr('❌ 投稿会话已过期，请发送 /submit 重新开始。')

    @staticmethod
    def upload_requires_content(kind: str) -> str:
        if kind == "media":
            return tr("""⚠️ 请至少发送一个媒体文件。

发送 /cancel 可放弃本次投稿。""")
        if kind == "document":
            return tr("""⚠️ 请至少发送一个文件。

发送 /cancel 可放弃本次投稿。""")
        return tr("""⚠️ 请至少上传一个媒体或文件。

发送 /cancel 可放弃本次投稿。""")

    @staticmethod
    def prompt_upload_text() -> str:
        return (
            tr("""📮 这里只接收媒体或文件。

• 🖼️ 图片 / 📹 视频 / GIF / 音频：直接发送
• 📦 压缩包 / PDF 等：以附件发送

🏷️ 标签、标题、简介和链接请在 /done_media 打开预览后填写；
完成上传后发送 /done_media 打开预览，或 /cancel 取消。""")
        )

    @staticmethod
    def edit_prompt(field: str) -> str:
        prompts = {
            "edit_tag": tr('🏷️ 请发送新的标签（用逗号分隔，直接覆盖原标签）：'),
            "edit_title": tr('🔖 请发送新的标题（回复「无」清空，上限 100 字）：'),
            "edit_note": tr('📝 请发送新的简介（回复「无」清空，上限 600 字）：'),
            "edit_link": tr('🔗 请发送新的链接（回复「无」清空，须以 http:// 或 https:// 开头）：'),
            "edit_media": tr('📎 请发送要补充的媒体（图片/视频/GIF/音频），完成后点下方按钮返回预览：'),
        }
        return prompts.get(field, tr('✏️ 请输入新内容：'))

    @staticmethod
    def field_updated(field: str) -> str:
        text = {
            "edit_tag": tr('标签已更新'),
            "edit_title": tr('标题已更新'),
            "edit_note": tr('简介已更新'),
            "edit_link": tr('链接已更新'),
            "edit_media": tr('媒体已更新'),
        }
        return tr('✅ {p0}，正在返回预览…').format(p0=text.get(field, '内容'))

    @staticmethod
    def publication_result(review: bool, review_id=None) -> str:
        if review:
            return (
                tr("""✅ 投稿已进入审核队列（#{p0}）。

⌛ 请留意私信：审核通过或未通过时机器人都会通知你。
💡 审核期间请不要重复提交同一内容。""").format(p0=review_id)
            )
        return tr("""✅ 发布成功！你的内容已发布到频道。

💡 可通过 /myposts 查看自己的投稿。""")

    @staticmethod
    def pagination_info(current: int, total: int) -> str:
        """分页信息"""
        return tr('📄 第 {p0}/{p1} 页').format(p0=current, p1=total)
    
    @staticmethod
    def empty_result() -> str:
        """空结果"""
        return tr("""
🔍 <b>暂无结果</b>

没有找到相关内容
试试其他关键词或筛选条件吧
""")
    
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






