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

        return (
            f"👋 <b>你好，{username}！</b>\n\n"
            f"我是投稿机器人，欢迎你，帮你把图文内容发布到频道。\n"
            f"🎭 身份：{role}\n\n"
            "📌 <b>快速开始</b>\n"
            "点击下方「开始投稿」，或发送 <code>/submit</code>\n\n"
            "📚 <b>常用功能</b>\n"
            "<code>/submit</code> 开始投稿\n"
            "<code>/search</code> 搜索内容\n"
            "<code>/mystats</code> 我的统计\n"
            "<code>/myposts</code> 我的投稿\n"
            "<code>/hot</code> 热门排行\n"
            "<code>/help</code> 完整帮助\n\n"
            "💡 <i>想直接投稿？发送 <code>/submit</code> 就开始。</i>"
        )
    
    @staticmethod
    def help_message(is_admin: bool = False) -> str:
        """帮助消息"""
        basic_help = """
📚 <b>使用指南</b>

<b>📝 投稿</b>
<code>/submit</code> 开始新投稿
<code>/done_media</code> 上传完成后打开预览
<code>/cancel</code> 取消当前投稿

<b>📊 统计查询</b>
<code>/hot [数量] [时间]</code> 热门排行 · 如 /hot 20 week
<code>/mystats</code> 我的投稿统计
<code>/myposts [数量]</code> 我的投稿列表

<b>🔍 搜索</b>
<code>/search 关键词</code> 搜索内容 · 如 /search Python
<code>/tags [数量]</code> 热门标签

<b>ℹ️ 其它</b>
<code>/help</code> 完整帮助
<code>/settings</code> 查看机器人设置
<code>/about</code> 关于机器人
"""
        
        admin_help = """
👑 <b>管理员命令</b>
<code>/addblacklist &lt;ID&gt; [原因]</code> 添加黑名单
<code>/removeblacklist &lt;ID&gt;</code> 移除黑名单
<code>/blacklist</code> 查看黑名单
<code>/searchuser &lt;ID&gt;</code> 查询用户投稿
<code>/broadcast &lt;消息&gt;</code> 广播消息
<code>/stats</code> 全局统计信息

"""
        
        footer = """
💡 <b>小贴士</b>
• 投稿支持图片 / 视频 / 压缩包 / PDF 等
• 添加 #标签 让内容更易被发现
• 任一环节都可发送 /cancel 取消投稿
"""
        
        if is_admin:
            return basic_help + admin_help + footer
        return basic_help + footer
    
    @staticmethod
    def about_message() -> str:
        """关于消息"""
        return """
ℹ️ <b>关于投稿机器人</b>

<b>🤖 版本：</b> v2.0
<b>⚡ 框架：</b> python-telegram-bot

<b>✨ 亮点</b>
• 图文 / 文件投稿
• 预览后发布 / 审核
• 匿名与剧透开关

<b>🔗 开源：</b> https://github.com/redtidev1918/TelePost

<i>感谢使用！有问题可私聊管理员。</i>
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
    def error_message(error_type: str = "general") -> str:
        """错误消息"""
        errors = {
            "general": "❌ 操作失败，请稍后重试。\n\n如果反复出现，请私聊管理员。",
            "permission": "⛔ 权限不足，仅管理员可用。\n\n请输入 <code>/help</code> 查看可用命令。",
            "blacklist": "🚫 你已被加入黑名单，无法投稿。\n\n如有疑问请联系管理员。",
            "session": "❌ 投稿会话已过期，请发送 <code>/submit</code> 重新开始。",
            "invalid_format": "❌ 格式错误，请检查输入后重试。",
            "not_found": "❌ 未找到相关内容，换个关键词试试吧。",
            "rate_limit": "⏰ 操作太频繁，请稍等片刻再试。"
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

<b>支持的内容</b>
• 🖼️ 图片（单张或多张）
• 🎥 视频 / 📹 动图 (GIF) / 🔉 音频
• 📁 压缩包 / PDF 等文档附件

<b>投稿流程</b>
1️⃣ 发送 <code>/submit</code> 开始
2️⃣ 上传内容（选填标签、标题、简介、链接）
3️⃣ 发送 <code>/done_media</code> 打开预览
4️⃣ 修改确认后发布 / 提交审核

<b>小提示</b>
• 内容里加 #标签 更容易被发现
• 随时发送 <code>/cancel</code> 取消投稿

<i>准备好了吗？发送 <code>/submit</code> 开始！</i>
"""
    
    # ── 投稿流程文案（UPLOAD → PREVIEW → EDIT 各阶段集中在此） ──────────────

    @staticmethod
    def submit_hint(mode: str, max_files: int = 10) -> str:
        """/submit 进入上传阶段时的引导提示。mode: MEDIA / DOCUMENT / MIXED。"""
        common = (
            "\n\n📋 <b>下一步</b>\n"
            "1️⃣ 继续上传内容，或发送 <code>/done_media</code> 打开预览\n"
            "2️⃣ 在预览页填写标签（必填）、标题、简介、链接\n"
            "3️⃣ 确认无误后点击按钮发布 / 提交审核\n\n"
            "💡 <b>小提示</b>\n"
            "• 匿名、剧透默认关闭，可在预览页开启\n"
            "• 任一环节发送 <code>/cancel</code> 可取消投稿"
        )
        if mode == "MEDIA":
            return (
                "📮 <b>开始投稿：上传媒体</b>\n"
                "请直接上传图片、视频、GIF 或音频\n"
                f"• 最多 {max_files} 个\n"
                "• 每收到一条会显示当前数量\n"
                "• 上传完成发送 <code>/done_media</code> 打开预览，或 <code>/cancel</code> 取消"
            ) + common
        if mode == "DOCUMENT":
            return (
                "📮 <b>开始投稿：上传文件</b>\n"
                "请直接上传（以附件发送图片、压缩包、PDF 等文件）\n"
                f"• 最多 {max_files} 个\n"
                "• 每收到一条会显示当前数量\n"
                "• 上传完成发送 <code>/done_media</code> 打开预览，或 <code>/cancel</code> 取消"
            ) + common
        return (
            "📮 <b>开始投稿</b>\n"
            "请直接上传内容\n"
            "• 相册图片、视频、GIF、音频 → 媒体\n"
            "• 以附件发送的图片、压缩包、PDF → 文件\n"
            f"• 合计最多 {max_files} 个，媒体/文件可混合\n"
            "• 上传完成发送 <code>/done_media</code> 打开预览，或 <code>/cancel</code> 取消"
        ) + common

    @staticmethod
    def upload_received(kind: str, total: int, max_files: int) -> str:
        """每条媒体/文件成功入库后的计数提示。"""
        label = "文件" if kind == "document" else "媒体"
        return (
            f"✅ 已接收{label}，当前 {total}/{max_files} 个。\n"
            "💡 可继续上传，或发送 <code>/done_media</code> 打开预览、<code>/cancel</code> 取消。"
        )

    @staticmethod
    def upload_supported_types() -> str:
        return (
            "⚠️ 这里只接收媒体或文件。\n\n"
            "• 🖼️ 图片 / 📹 视频 / GIF / 音频：直接发送\n"
            "• 📦 压缩包 / PDF 等：以附件发送\n\n"
            "🏷️ 标签、标题、简介和链接请在 <code>/done_media</code> 打开预览后填写。"
        )

    @staticmethod
    def upload_mode_limited(kind: str) -> str:
        if kind == "document":
            return "⚠️ 当前为媒体投稿模式，不支持文件附件。\n\n请直接发送图片、视频、GIF 或音频。"
        return "⚠️ 当前为文档投稿模式，请以附件发送文件。\n\n请发送压缩包、PDF 等文件，或以附件发送图片。"

    @staticmethod
    def upload_max_files(max_files: int) -> str:
        return (
            f"⚠️ 单条投稿最多 {max_files} 个文件。\n\n"
            "请发送 <code>/done_media</code> 进入预览，或 <code>/cancel</code> 后重新投稿。"
        )

    @staticmethod
    def session_expired() -> str:
        return "❌ 投稿会话已过期，请发送 <code>/submit</code> 重新开始。"

    @staticmethod
    def upload_requires_content(kind: str) -> str:
        if kind == "media":
            return "⚠️ 请至少发送一个媒体文件。\n\n发送 <code>/cancel</code> 可放弃本次投稿。"
        if kind == "document":
            return "⚠️ 请至少发送一个文件。\n\n发送 <code>/cancel</code> 可放弃本次投稿。"
        return "⚠️ 请至少上传一个媒体或文件。\n\n发送 <code>/cancel</code> 可放弃本次投稿。"

    @staticmethod
    def prompt_upload_text() -> str:
        return (
            "📮 这里只接收媒体或文件。\n\n"
            "• 🖼️ 图片 / 📹 视频 / GIF / 音频：直接发送\n"
            "• 📦 压缩包 / PDF 等：以附件发送\n\n"
            "🏷️ 标签、标题、简介和链接请在 <code>/done_media</code> 打开预览后填写；\n"
            "完成上传后发送 <code>/done_media</code> 打开预览，或 <code>/cancel</code> 取消。"
        )

    @staticmethod
    def preview_text(row, review_first: bool = False) -> str:
        """发布/审核预览的字段化文本（私聊）。"""
        from utils.helper_functions import parse_json_list
        media_list = parse_json_list(row["image_id"])
        doc_list = parse_json_list(row["document_id"])
        lines = ["📋 <b>发布预览</b>", ""]
        if media_list or doc_list:
            if media_list:
                lines.append(f"📎 <b>媒体：{len(media_list)} 个</b>")
            if doc_list:
                lines.append(f"📄 <b>文档：{len(doc_list)} 个</b>")
                for entry in doc_list:
                    parts = entry.split(":", 2)
                    name = parts[2] if len(parts) > 2 and parts[2] else "未命名文件"
                    lines.append(f"   • {name[:60]}{'…' if len(name) > 60 else ''}")
            lines.append("")
        lines.append(f"🏷 <b>标签：</b>{row['tags'] or '（未设置）'}")
        if row["link"]:
            lines.append(f"🔗 <b>链接：</b>{row['link']}")
        if row["title"]:
            lines.append(f"🔖 <b>标题：</b>{row['title']}")
        if row["note"]:
            note = row["note"]
            lines.append(f"📝 <b>简介：</b>{note[:80]}{' …' if len(note) > 80 else ''}")
        is_anon = (row["anonymous"] if "anonymous" in row.keys() else "false") == "true"
        lines.append(f"🔞 <b>剧透：</b>{'是' if (row['spoiler'] or '') == 'true' else '否'}")
        lines.append(f"🕵️ <b>匿名：</b>{'是（频道内不显示投稿人）' if is_anon else '否（显示投稿人）'}")
        lines.append("")
        if row["tags"]:
            lines.append(
                "✅ 确认无误请点击下方按钮<b>提交审核</b>，审核通过后发布到频道；\n"
                "💡 也可以先修改标签 / 标题 / 简介 / 链接，或开启匿名、剧透。"
                if review_first
                else "✅ 确认无误请点击下方按钮<b>发布到频道</b>；\n"
                     "💡 也可以先修改标签 / 标题 / 简介 / 链接，或开启匿名、剧透。"
            )
        else:
            lines.append(
                "⚠️ <b>提交审核前必须填写标签</b>；其余字段均可留空。"
                if review_first
                else "⚠️ <b>发布前必须填写标签</b>；其余字段均可留空。"
            )
        return "\n".join(lines)

    @staticmethod
    def edit_prompt(field: str) -> str:
        prompts = {
            "edit_tag": "🏷️ 请发送新的标签（用逗号分隔，直接覆盖原标签）：",
            "edit_title": "🔖 请发送新的标题（回复「无」清空，上限 100 字）：",
            "edit_note": "📝 请发送新的简介（回复「无」清空，上限 600 字）：",
            "edit_link": "🔗 请发送新的链接（回复「无」清空，须以 http:// 或 https:// 开头）：",
            "edit_media": "📎 请发送要补充的媒体（图片/视频/GIF/音频），完成后点下方按钮返回预览：",
        }
        return prompts.get(field, "✏️ 请输入新内容：")

    @staticmethod
    def field_updated(field: str) -> str:
        text = {
            "edit_tag": "标签已更新",
            "edit_title": "标题已更新",
            "edit_note": "简介已更新",
            "edit_link": "链接已更新",
            "edit_media": "媒体已更新",
        }
        return f"✅ {text.get(field, '内容')}，正在返回预览…"

    @staticmethod
    def publication_result(review: bool, review_id=None) -> str:
        if review:
            return (
                f"✅ 投稿已进入审核队列（#{review_id}）。\n\n"
                "⌛ 请留意私信：审核通过或未通过时机器人都会通知你。\n"
                "💡 审核期间请不要重复提交同一内容。"
            )
        return "✅ 发布成功！你的内容已发布到频道。\n\n💡 可通过 <code>/myposts</code> 查看自己的投稿。"

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

