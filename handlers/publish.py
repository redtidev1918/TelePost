"""
投稿发布模块
"""
import json
import logging
import asyncio
from datetime import datetime
from telegram import (
    Update,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument
)
from telegram.ext import ConversationHandler, CallbackContext

from config.settings import (
    CHANNEL_ID,
    CHAT_REVIEW_REQUIRED,
    NOTIFY_OWNER,
    OWNER_ID,
)
from database.db_manager import get_db, cleanup_old_data
from utils.helper_functions import build_caption, safe_send
from utils.search_engine import get_search_engine, PostDocument

logger = logging.getLogger(__name__)


def _review_items(media_list, doc_list):
    """Convert the chat session's compact file_id format to review payloads."""
    media = []
    for item in media_list:
        kind, file_id = item.split(":", 1)
        media.append({"type": kind, "file_id": file_id})

    documents = []
    for item in doc_list:
        parts = item.split(":", 2)
        file_id = parts[1] if len(parts) >= 2 else parts[0]
        filename = parts[2] if len(parts) >= 3 else "file"
        documents.append({"file_id": file_id, "filename": filename})
    return media, documents

async def save_published_post(user_id, message_id, data, media_list, doc_list, all_message_ids=None):
    """
    保存已发布的帖子信息到数据库和搜索索引
    
    Args:
        user_id: 用户ID
        message_id: 频道主消息ID
        data: 投稿数据（sqlite3.Row对象）
        media_list: 媒体列表
        doc_list: 文档列表
        all_message_ids: 所有相关消息ID列表（用于多组媒体的热度统计）
    """
    try:
        # 确定内容类型
        content_type = 'media' if media_list else 'document'
        if media_list and doc_list:
            content_type = 'mixed'
        
        # 获取文件ID列表
        file_ids = json.dumps(media_list if media_list else doc_list)
        
        # 提取标签（从tags字段）- 兼容 sqlite3.Row 对象
        tags = data['tags'] if 'tags' in data.keys() else ''
        
        # 构建说明
        caption = build_caption(data)
        
        # 提取信息 - 兼容 sqlite3.Row 对象
        title = data['title'] if data['title'] else ''
        note = data['note'] if data['note'] else ''
        link = data['link'] if data['link'] else ''
        username = data['username'] if 'username' in data.keys() and data['username'] else f'user{user_id}'
        publish_time = datetime.now()
        
        # 提取文件名（从文档列表中）
        filename = ''
        if doc_list:
            filenames = []
            for doc_item in doc_list:
                # 新格式：document:file_id:filename
                parts = doc_item.split(':', 2)
                if len(parts) >= 3:
                    filenames.append(parts[2])
                elif len(parts) == 2:
                    # 兼容旧格式 document:file_id
                    filenames.append('未知文件')
            filename = ' | '.join(filenames) if filenames else ''
        
        # 处理相关消息ID（用于多组媒体热度统计）
        related_ids_json = None
        if all_message_ids and len(all_message_ids) > 1:
            # 只保存除主消息外的其他消息ID
            related_ids = [mid for mid in all_message_ids if mid != message_id]
            if related_ids:
                related_ids_json = json.dumps(related_ids)
                logger.info(f"记录{len(related_ids)}个关联消息ID: {related_ids}")
        
        # 保存到数据库并获取 post_id
        post_id = None
        async with get_db() as conn:
            cursor = await conn.cursor()
            await cursor.execute("""
                INSERT INTO published_posts 
                (message_id, user_id, username, title, tags, link, note,
                 content_type, file_ids, caption, filename, publish_time, last_update, related_message_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                message_id,
                user_id,
                username,
                title,
                tags,
                link,
                note,
                content_type,
                file_ids,
                caption,
                filename,
                publish_time.timestamp(),
                publish_time.timestamp(),
                related_ids_json
            ))
            post_id = cursor.lastrowid  # 获取插入的行ID
            await conn.commit()
            logger.info(f"已保存帖子 {message_id} (post_id: {post_id}) 到published_posts表（文件名: {filename}）")
        
        # 添加到搜索索引（仅在搜索功能启用时；
        # get_search_engine 在未初始化时会以默认目录创建索引，与配置目录不符）
        try:
            from config.settings import SEARCH_ENABLED
            if not SEARCH_ENABLED:
                logger.debug("搜索功能已禁用，跳过索引写入")
                return
            search_engine = get_search_engine()
            
            # 构建搜索文档
            # 将 note 作为 description
            post_doc = PostDocument(
                message_id=message_id,
                post_id=post_id,  # 传入数据库ID
                title=title,
                description=note,  # 使用note作为描述
                tags=tags,
                filename=filename,  # 文件名
                link=link,
                user_id=user_id,
                username=username,
                publish_time=publish_time,
                views=0,
                heat_score=0
            )
            
            # 添加到索引
            search_engine.add_post(post_doc)
            logger.info(f"已添加帖子 {message_id} (post_id: {post_id}) 到搜索索引（文件名: {filename}）")
            
        except Exception as e:
            logger.error(f"添加到搜索索引失败: {e}", exc_info=True)
            # 继续执行，不影响发布流程
            
    except Exception as e:
        logger.error(f"保存帖子信息到数据库失败: {e}")

async def publish_submission(update: Update, context: CallbackContext) -> int:
    """
    发布投稿到频道
    
    处理逻辑:
    1. 仅媒体模式: 将媒体发送到频道
    2. 仅文档模式或文档优先模式: 
       - 若同时有媒体和文档，则以媒体为主贴，文档组合作为回复
       - 若仅有文档，则以文档进行组合发送（说明文本放在最后一条）
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
        
    Returns:
        int: 会话结束状态
    """
    user_id = update.effective_user.id
    publish_success = False  # 是否已发布或成功进入审核队列

    # 本函数既可能被消息流程直接调用（handle_spoiler），
    # 也可能被按钮回调触发（PUBLISH 状态 / submit_confirm 按钮）。
    # 回调来源时 update.message 为 None，必须统一走 _notify。
    is_callback = update.callback_query is not None

    async def _reply_to_user(text: str):
        """向用户反馈结果：回调来源编辑原消息，普通消息直接回复"""
        if is_callback:
            try:
                await update.callback_query.answer()
            except Exception:
                pass  # 可能已被应答或查询过期，不影响后续
            try:
                await update.callback_query.edit_message_text(text)
            except Exception:
                try:
                    await update.effective_message.reply_text(text)
                except Exception as e:
                    logger.error(f"发送结果通知失败: {e}")
        else:
            await update.message.reply_text(text)

    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("SELECT * FROM submissions WHERE user_id=?", (user_id,))
            data = await c.fetchone()

        if not data:
            await _reply_to_user("❌ 数据异常，请重新发送 /start")
            return ConversationHandler.END

        caption = build_caption(data)
        
        # 解析媒体和文档数据，增强型错误处理
        media_list = []
        doc_list = []
        
        try:
            if data["image_id"]:
                media_list = json.loads(data["image_id"])
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"解析媒体数据失败，user_id: {user_id}")
            media_list = []
            
        try:
            if data["document_id"]:
                doc_list = json.loads(data["document_id"])
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"解析文档数据失败，user_id: {user_id}")
            doc_list = []
        
        if not media_list and not doc_list:
            await _reply_to_user("❌ 未检测到任何上传文件，请重新发送 /start")
            # 数据异常的空记录直接清理
            async with get_db() as conn:
                c = await conn.cursor()
                await c.execute("DELETE FROM submissions WHERE user_id=?", (user_id,))
            return ConversationHandler.END

        # 安全处理spoiler字段，防止None值导致AttributeError
        spoiler_value = data["spoiler"] if "spoiler" in data.keys() and data["spoiler"] else "false"
        spoiler_flag = spoiler_value.lower() == "true"
        sent_message = None
        all_message_ids = []  # 用于记录所有发送的消息ID

        if CHAT_REVIEW_REQUIRED:
            from handlers.review import queue_review_from_file_ids

            review_media, review_documents = _review_items(media_list, doc_list)
            username = (
                data["username"]
                if "username" in data.keys() and data["username"]
                else (update.effective_user.username or f"user{user_id}")
            )
            anonymous_value = (
                data["anonymous"]
                if "anonymous" in data.keys() and data["anonymous"]
                else "false"
            )
            review_result = await queue_review_from_file_ids(
                context.bot,
                review_media,
                review_documents,
                tags=data["tags"] or "",
                title=data["title"] or "",
                note=data["note"] or "",
                link=data["link"] or "",
                anonymous=str(anonymous_value).lower() == "true",
                spoiler=spoiler_flag,
                user_id=user_id,
                username=username,
                idempotency_key=f"submission:{data['timestamp']}",
                source="chat",
            )
            await _reply_to_user(
                f"✅ 投稿已进入审核队列（#{review_result['review_id']}）。\n"
                "审核完成后机器人会通知你。"
            )
            publish_success = True
            return ConversationHandler.END
        
        # 处理媒体文件
        if media_list:
            sent_message, all_message_ids = await handle_media_publish(context, media_list, caption, spoiler_flag)
        
        # 处理文档文件
        if doc_list:
            if sent_message:
                # 如果已经发送了媒体，则文档作为回复
                doc_msg = await handle_document_publish(
                    context, 
                    doc_list, 
                    None,  # 不需要重复发送说明，回复到主贴即可
                    sent_message.message_id
                )
                if doc_msg:
                    all_message_ids.append(doc_msg.message_id)
            else:
                # 如果只有文档，直接发送
                sent_message = await handle_document_publish(context, doc_list, caption)
                if sent_message:
                    all_message_ids.append(sent_message.message_id)
        
        # 处理结果
        if not sent_message:
            await _reply_to_user(
                "❌ 内容发送失败。\n"
                "您的投稿数据已保留，请稍后重新发送 /submit 并完成相同步骤，或联系管理员处理。"
            )
            # 失败时不删除 submissions 记录：保留已上传的 file_id，
            # 避免用户因瞬时网络错误而重传所有媒体。
            return ConversationHandler.END
            
        # 生成投稿链接
        if CHANNEL_ID.startswith('@'):
            channel_username = CHANNEL_ID.lstrip('@')
            submission_link = f"https://t.me/{channel_username}/{sent_message.message_id}"
        else:
            submission_link = "频道无公开链接"

        await _reply_to_user(
            f"🎉 投稿已成功发布到频道！\n点击以下链接查看投稿：\n{submission_link}"
        )

        # 标记发布成功：finally 中据此决定是否清理会话记录
        publish_success = True

        # 保存已发布的帖子信息到数据库（用于热度统计和搜索）
        await save_published_post(user_id, sent_message.message_id, data, media_list, doc_list, all_message_ids)
        
        # 向所有者发送投稿通知
        if NOTIFY_OWNER and OWNER_ID:
            # 记录详细的调试信息
            logger.info(f"准备发送通知: NOTIFY_OWNER={NOTIFY_OWNER}, OWNER_ID={OWNER_ID}, 类型={type(OWNER_ID)}")
            
            # 获取用户名信息
            # 注意：对 sqlite3.Row，"col" in data 判断的是值而非列名，必须用 data.keys()
            username = None
            try:
                username = data["username"] if "username" in data.keys() else f"user{user_id}"
            except (KeyError, TypeError):
                username = f"user{user_id}"
                
            # 获取用户名信息，优先使用真实用户名
            user = update.effective_user
            real_username = user.username or username
            
            # 构建纯文本通知消息（不使用任何Markdown，确保最大兼容性）
            notification_text = (
                f"📨 新投稿通知\n\n"
                f"👤 投稿人信息:\n"
                f"  • ID: {user_id}\n"
                f"  • 用户名: {('@' + real_username) if user.username else real_username}\n"
                f"  • 昵称: {user.first_name}{f' {user.last_name}' if user.last_name else ''}\n\n"
                
                f"🔗 查看投稿: {submission_link}\n\n"
                
                f"⚙️ 管理操作:\n"
                f"封禁此用户: /blacklist_add {user_id} 违规内容\n"
                f"查看黑名单: /blacklist_list"
            )
            
            try:
                # OWNER_ID 已经在配置中转换为整数类型，直接使用
                logger.info(f"准备发送通知到所有者: {OWNER_ID}")
                
                # 记录通知消息内容
                logger.info(f"通知消息长度: {len(notification_text)}, 使用纯文本格式")
                
                # 网络异常后无法确定 Telegram 是否已接收；不重发，避免重复通知。
                try:
                    message = await context.bot.send_message(
                        chat_id=OWNER_ID,
                        text=notification_text
                    )
                    logger.info(f"通知发送成功！消息ID: {message.message_id}")
                except Exception as e:
                    logger.error(f"发送通知失败: {e}")
                    logger.warning("⚠️ 投稿已发布，但无法确认管理员通知是否送达")
            except Exception as e:
                logger.error(f"处理通知过程中发生错误: 错误类型: {type(e)}, 详细信息: {str(e)}")
                logger.error("异常追踪: ", exc_info=True)
        else:
            logger.info(f"不发送通知: NOTIFY_OWNER={NOTIFY_OWNER}, OWNER_ID={OWNER_ID}")
        
    except Exception as e:
        logger.error(f"发布投稿失败: {e}", exc_info=True)
        try:
            await _reply_to_user("❌ 发布失败，您的投稿数据已保留，请稍后重试或联系管理员。")
        except Exception as notify_err:
            logger.error(f"发送失败通知时出错: {notify_err}")
    finally:
        # 仅在发布成功或成功进入审核队列后清理会话数据；失败时保留，
        # 用户可重新 /submit 或由 cleanup_old_data 按超时自动回收
        if not publish_success:
            logger.warning(f"用户 {user_id} 投稿未完成，会话数据已保留待恢复或超时清理")
        else:
            try:
                async with get_db() as conn:
                    c = await conn.cursor()
                    await c.execute("DELETE FROM submissions WHERE user_id=?", (user_id,))
                logger.info(f"已删除用户 {user_id} 的投稿记录")
            except Exception as e:
                logger.error(f"删除数据错误: {e}")

        # 清理过期数据
        try:
            await cleanup_old_data()
        except Exception as e:
            logger.error(f"清理过期数据失败: {e}")

    return ConversationHandler.END

async def handle_media_publish(context, media_list, caption, spoiler_flag):
    """
    处理媒体发布
    
    Args:
        context: 回调上下文
        media_list: 媒体列表
        caption: 说明文本
        spoiler_flag: 是否剧透标志
        
    Returns:
        tuple: (主消息对象, 所有消息ID列表) 或 (None, [])
    """
    # 检查caption长度，如果过长先单独发送
    caption_message = None
    
    # 强制检查caption长度，保证媒体组发送的可靠性
    # 不管SHOW_SUBMITTER如何设置，当caption超过850字符时都单独发送
    # 使用较小的阈值（850而不是1000）来确保足够的安全边际
    if caption and len(caption) > 850:
        logger.info(f"Caption过长 ({len(caption)} 字符)，单独发送caption")
        try:
            caption_message = await safe_send(
                context.bot.send_message,
                chat_id=CHANNEL_ID,
                text=caption,
                parse_mode='HTML'
            )
            if caption_message:
                caption = None
            else:
                logger.warning("长 caption 单独发送失败，回退到媒体 caption")
        except Exception as e:
            logger.error(f"发送长caption失败: {e}")
            # 保留 caption，继续随媒体发送。

    # 单个媒体处理
    if len(media_list) == 1:
        typ, file_id = media_list[0].split(":", 1)
        try:
            # 如果已经单独发送了caption，则不再添加到媒体
            media_caption = None if caption_message else caption
            
            if typ == "photo":
                sent_message = await safe_send(
                    context.bot.send_photo,
                    chat_id=CHANNEL_ID,
                    photo=file_id,
                    caption=media_caption,
                    parse_mode='HTML' if media_caption else None,
                    has_spoiler=spoiler_flag,
                    reply_to_message_id=caption_message.message_id if caption_message else None
                )
            elif typ == "video":
                sent_message = await safe_send(
                    context.bot.send_video,
                    chat_id=CHANNEL_ID,
                    video=file_id,
                    caption=media_caption,
                    parse_mode='HTML' if media_caption else None,
                    has_spoiler=spoiler_flag,
                    reply_to_message_id=caption_message.message_id if caption_message else None
                )
            elif typ == "animation":
                sent_message = await safe_send(
                    context.bot.send_animation,
                    chat_id=CHANNEL_ID,
                    animation=file_id,
                    caption=media_caption,
                    parse_mode='HTML' if media_caption else None,
                    has_spoiler=spoiler_flag,
                    reply_to_message_id=caption_message.message_id if caption_message else None
                )
            elif typ == "audio":
                sent_message = await safe_send(
                    context.bot.send_audio,
                    chat_id=CHANNEL_ID,
                    audio=file_id,
                    caption=media_caption,
                    parse_mode='HTML' if media_caption else None,
                    reply_to_message_id=caption_message.message_id if caption_message else None
                )
            
            # 收集所有消息ID
            main_msg = caption_message or sent_message
            all_ids = []
            if caption_message:
                all_ids.append(caption_message.message_id)
            if sent_message:
                all_ids.append(sent_message.message_id)
            return (main_msg, all_ids)
        except Exception as e:
            logger.error(f"发送单条媒体失败: {e}")
            if caption_message:
                return (caption_message, [caption_message.message_id])
            return (None, [])
    
    # 多个媒体处理 - 将媒体分组，每组最多10个
    else:
        media_kinds = {item.split(":", 1)[0] for item in media_list}
        supported_kinds = {"photo", "video", "animation", "audio"}
        if not media_kinds <= supported_kinds:
            logger.error("媒体列表包含不支持的类型: %s", media_kinds - supported_kinds)
            return (None, [])

        # Telegram 相册只支持 photo/video；GIF 和音频逐条发送并串成回复链。
        if not media_kinds <= {"photo", "video"}:
            sent_messages = []
            first_message = caption_message
            previous_message = caption_message
            for item in media_list:
                typ, file_id = item.split(":", 1)
                media_caption = caption if first_message is None else None
                common = {
                    "chat_id": CHANNEL_ID,
                    "caption": media_caption,
                    "parse_mode": "HTML" if media_caption else None,
                    "reply_to_message_id": (
                        previous_message.message_id if previous_message else None
                    ),
                }
                if typ == "photo":
                    sent = await safe_send(
                        context.bot.send_photo, photo=file_id,
                        has_spoiler=spoiler_flag, **common,
                    )
                elif typ == "video":
                    sent = await safe_send(
                        context.bot.send_video, video=file_id,
                        has_spoiler=spoiler_flag, **common,
                    )
                elif typ == "animation":
                    sent = await safe_send(
                        context.bot.send_animation, animation=file_id,
                        has_spoiler=spoiler_flag, **common,
                    )
                else:
                    sent = await safe_send(
                        context.bot.send_audio, audio=file_id, **common,
                    )

                if not sent:
                    logger.error("媒体发送中断，回滚已知的频道消息")
                    known = ([caption_message] if caption_message else []) + sent_messages
                    for message in reversed(known):
                        try:
                            await context.bot.delete_message(
                                chat_id=CHANNEL_ID, message_id=message.message_id
                            )
                        except Exception:
                            logger.warning(
                                "回滚频道消息失败: %s", message.message_id,
                                exc_info=True,
                            )
                    return (None, [])

                if first_message is None:
                    first_message = sent
                previous_message = sent
                sent_messages.append(sent)

            ids = ([caption_message.message_id] if caption_message else [])
            ids.extend(message.message_id for message in sent_messages)
            return (first_message, ids)

        all_sent_messages = []
        first_message = caption_message
        previous_message = caption_message
        try:
            for chunk_index in range(0, len(media_list), 10):
                media_chunk = media_list[chunk_index:chunk_index + 10]
                media_group = []
                for i, item in enumerate(media_chunk):
                    typ, file_id = item.split(":", 1)
                    use_caption = caption if first_message is None and i == 0 else None
                    factory = InputMediaPhoto if typ == "photo" else InputMediaVideo
                    media_group.append(factory(
                        media=file_id,
                        caption=use_caption,
                        parse_mode="HTML" if use_caption else None,
                        has_spoiler=spoiler_flag,
                    ))

                reply_to = previous_message.message_id if previous_message else None
                if len(media_group) == 1:
                    typ, file_id = media_chunk[0].split(":", 1)
                    common = {
                        "chat_id": CHANNEL_ID,
                        "caption": caption if first_message is None else None,
                        "parse_mode": "HTML" if first_message is None and caption else None,
                        "has_spoiler": spoiler_flag,
                        "reply_to_message_id": reply_to,
                    }
                    method = (
                        context.bot.send_photo if typ == "photo"
                        else context.bot.send_video
                    )
                    sent = await safe_send(
                        method, **({"photo": file_id} if typ == "photo" else {"video": file_id}),
                        **common,
                    )
                    sent_messages = [sent] if sent else []
                else:
                    sent_messages = await asyncio.wait_for(
                        context.bot.send_media_group(
                            chat_id=CHANNEL_ID,
                            media=media_group,
                            reply_to_message_id=reply_to,
                        ),
                        timeout=60,
                    )

                if len(sent_messages) != len(media_chunk):
                    raise RuntimeError(
                        f"频道返回消息数 {len(sent_messages)} 与媒体数 {len(media_chunk)} 不一致"
                    )
                if first_message is None:
                    first_message = sent_messages[0]
                previous_message = sent_messages[-1]
                all_sent_messages.extend(sent_messages)

            ids = ([caption_message.message_id] if caption_message else [])
            ids.extend(message.message_id for message in all_sent_messages)
            return (first_message, ids)
        except Exception as e:
            logger.error("发送媒体组失败，回滚已知的频道消息: %s", e)
            known = ([caption_message] if caption_message else []) + all_sent_messages
            for message in reversed(known):
                try:
                    await context.bot.delete_message(
                        chat_id=CHANNEL_ID, message_id=message.message_id
                    )
                except Exception:
                    logger.warning(
                        "回滚频道消息失败: %s", message.message_id, exc_info=True
                    )
            return (None, [])

async def handle_document_publish(context, doc_list, caption=None, reply_to_message_id=None):
    """
    处理文档发布
    
    Args:
        context: 回调上下文
        doc_list: 文档列表
        caption: 说明文本，如果为None则不添加说明
        reply_to_message_id: 回复的消息ID，如果为None则创建新消息
        
    Returns:
        发送的消息对象或None
    """
    if len(doc_list) == 1:
        # 单个文档处理
        parts = doc_list[0].split(":", 2)
        file_id = parts[1] if len(parts) >= 2 else parts[0]
        try:
            return await safe_send(
                context.bot.send_document,
                chat_id=CHANNEL_ID,
                document=file_id,
                caption=caption,
                parse_mode='HTML' if caption else None,
                reply_to_message_id=reply_to_message_id
            )
        except Exception as e:
            logger.error(f"发送单个文档失败: {e}")
            return None
    else:
        # 多个文档处理，使用文档组
        try:
            doc_media_group = []
            for i, doc_item in enumerate(doc_list):
                # 新格式：document:file_id:filename 或 旧格式：document:file_id
                parts = doc_item.split(":", 2)
                file_id = parts[1] if len(parts) >= 2 else parts[0]
                # 只在最后一个文档添加说明，且caption不为None
                caption_to_use = caption if (i == len(doc_list) - 1 and caption is not None) else None
                doc_media_group.append(InputMediaDocument(
                    media=file_id,
                    caption=caption_to_use,
                    parse_mode='HTML' if caption_to_use else None
                ))
            
            sent_docs = await safe_send(
                context.bot.send_media_group,
                chat_id=CHANNEL_ID,
                media=doc_media_group,
                reply_to_message_id=reply_to_message_id
            )
            
            if sent_docs and len(sent_docs) > 0:
                return sent_docs[0]
            return None
        except Exception as e:
            logger.error(f"发送文档组失败: {e}")
            return None


async def publish_from_files(bot, files, *, tags="", title="", note="", link="",
                             anonymous=False, spoiler=False, user_id, username="") -> dict:
    """
    API 投稿核心：把本地文件直接发布到频道（不经 Telegram 会话流程）。

    files: [{"path": 本地路径, "kind": photo|video|animation|audio|document, "filename": 原始文件名}]
    返回: {"status": "published", "message_id": int, "link": str,
           "media_count": int, "document_count": int}
    抛出异常时由调用方转成 API 500。
    """
    import os as _os
    from contextlib import ExitStack
    from telegram import InputFile

    data = {
        "tags": tags, "title": title, "note": note, "link": link,
        "spoiler": "true" if spoiler else "false",
        "anonymous": "true" if anonymous else "false",
        "user_id": user_id, "username": username,
    }
    caption = build_caption(data)

    media_files = [f for f in files if f["kind"] != "document"]
    doc_files = [f for f in files if f["kind"] == "document"]

    all_message_ids = []
    media_list, doc_list = [], []
    main_message = None

    # 媒体组：每组最多 10 个，caption 只放在第一组第一项
    for chunk_index in range(0, len(media_files), 10):
        chunk = media_files[chunk_index:chunk_index + 10]
        with ExitStack() as handles:
            group = []
            for i, f in enumerate(chunk):
                cap = caption if (chunk_index == 0 and i == 0) else None
                kw = {"caption": cap, "parse_mode": "HTML" if cap else None}
                if f["kind"] in ("photo", "video", "animation"):
                    kw["has_spoiler"] = spoiler
                fh = handles.enter_context(open(f["path"], "rb"))
                upload = InputFile(
                    fh, filename=f["filename"], read_file_handle=False
                )
                if f["kind"] == "photo":
                    from telegram import InputMediaPhoto
                    group.append(InputMediaPhoto(media=upload, **kw))
                elif f["kind"] == "video":
                    from telegram import InputMediaVideo
                    group.append(InputMediaVideo(media=upload, **kw))
                elif f["kind"] == "animation":
                    from telegram import InputMediaAnimation
                    group.append(InputMediaAnimation(media=upload, **kw))
                else:
                    from telegram import InputMediaAudio
                    group.append(InputMediaAudio(media=upload, **kw))
            sent = await bot.send_media_group(chat_id=CHANNEL_ID, media=group)
        all_message_ids.extend(m.message_id for m in sent)
        if main_message is None:
            main_message = sent[0]
        for m, f in zip(sent, chunk):
            media_list.append(f"{f['kind']}:{_file_id_of(m)}")

    # 文档：有媒体时作为媒体主贴的回复，否则单独成组
    for chunk_index in range(0, len(doc_files), 10):
        chunk = doc_files[chunk_index:chunk_index + 10]
        with ExitStack() as handles:
            group = []
            for i, f in enumerate(chunk):
                cap = caption if (not media_files and chunk_index == 0 and i == len(chunk) - 1) else None
                fh = handles.enter_context(open(f["path"], "rb"))
                group.append(InputMediaDocumentFactory(fh, f["filename"], cap))
            sent = await bot.send_media_group(
                chat_id=CHANNEL_ID, media=group,
                reply_to_message_id=main_message.message_id if main_message else None,
            )
        all_message_ids.extend(m.message_id for m in sent)
        if main_message is None:
            main_message = sent[0]
        for m, f in zip(sent, chunk):
            doc_list.append(f"document:{_file_id_of(m)}:{f['filename']}")

    if main_message is None:
        raise RuntimeError("所有消息发送失败")

    await save_published_post(user_id, main_message.message_id, data, media_list, doc_list, all_message_ids)

    if str(CHANNEL_ID).startswith("@"):
        link = f"https://t.me/{str(CHANNEL_ID).lstrip('@')}/{main_message.message_id}"
    else:
        link = f"https://t.me/c/{str(CHANNEL_ID).replace('-100', '')}/{main_message.message_id}"

    # 清理临时文件
    for f in files:
        try:
            _os.remove(f["path"])
        except OSError:
            pass

    return {
        "status": "published",
        "message_id": main_message.message_id,
        "link": link,
        "media_count": len(media_list),
        "document_count": len(doc_list),
    }




def _link_of(message_id: int) -> str:
    if str(CHANNEL_ID).startswith("@"):
        return f"https://t.me/{str(CHANNEL_ID).lstrip('@')}/{message_id}"
    return f"https://t.me/c/{str(CHANNEL_ID).replace('-100', '')}/{message_id}"


async def publish_from_file_ids(bot, media, documents, *, tags="", title="", note="", link="",
                                anonymous=False, spoiler=False, user_id, username="") -> dict:
    """
    API file_id 直投核心：素材已在 Telegram 服务器上（file_id 归属本 bot），
    直接用 file_id 发布到频道——零媒体文件传输。

    media:     [{"type": "photo|video|animation|audio", "file_id": str}]
    documents: [{"file_id": str, "filename": str}]
    """
    data = {
        "tags": tags, "title": title, "note": note, "link": link,
        "spoiler": "true" if spoiler else "false",
        "anonymous": "true" if anonymous else "false",
        "user_id": user_id, "username": username,
    }
    caption = build_caption(data)

    all_message_ids = []
    media_list = [f"{m['type']}:{m['file_id']}" for m in media]
    doc_list = [f"document:{d['file_id']}:{d.get('filename', 'file')}" for d in documents]

    from telegram import InputMediaPhoto, InputMediaVideo, InputMediaDocument
    input_factories = {
        "photo": InputMediaPhoto,
        "video": InputMediaVideo,
        "document": InputMediaDocument,
    }

    main_message = None

    async def send_single_media(item, cap):
        common = {
            "chat_id": CHANNEL_ID,
            "caption": cap,
            "parse_mode": "HTML" if cap else None,
        }
        kind = item["type"]
        if kind == "photo":
            return await bot.send_photo(
                photo=item["file_id"], has_spoiler=spoiler, **common
            )
        if kind == "video":
            return await bot.send_video(
                video=item["file_id"], has_spoiler=spoiler, **common
            )
        if kind == "animation":
            return await bot.send_animation(
                animation=item["file_id"], has_spoiler=spoiler, **common
            )
        return await bot.send_audio(audio=item["file_id"], **common)

    # photo/video 只有在 2-10 项时才能使用 send_media_group。
    # GIF/animation 不是 Bot API media group 支持的成员，单独发送。
    for chunk_index in range(0, len(media), 10):
        chunk = media[chunk_index:chunk_index + 10]
        can_group = len(chunk) >= 2 and all(
            item["type"] in ("photo", "video") for item in chunk
        )
        if can_group:
            group = []
            for i, item in enumerate(chunk):
                factory = input_factories[item["type"]]
                cap = caption if main_message is None and i == 0 else None
                group.append(factory(
                    media=item["file_id"],
                    caption=cap,
                    parse_mode="HTML" if cap else None,
                    has_spoiler=spoiler,
                ))
            sent = await bot.send_media_group(chat_id=CHANNEL_ID, media=group)
            all_message_ids.extend(m.message_id for m in sent)
            if main_message is None:
                main_message = sent[0]
        else:
            for item in chunk:
                sent_message = await send_single_media(
                    item, caption if main_message is None else None
                )
                all_message_ids.append(sent_message.message_id)
                if main_message is None:
                    main_message = sent_message

    # 文档组（≤10 个一组；有媒体时作为媒体主贴的回复）
    for chunk_index in range(0, len(documents), 10):
        chunk = documents[chunk_index:chunk_index + 10]
        reply_to = main_message.message_id if main_message and media else None
        if len(chunk) == 1:
            item = chunk[0]
            cap = caption if main_message is None else None
            sent_message = await bot.send_document(
                chat_id=CHANNEL_ID,
                document=item["file_id"],
                filename=item.get("filename") or None,
                caption=cap,
                parse_mode="HTML" if cap else None,
                reply_to_message_id=reply_to,
            )
            all_message_ids.append(sent_message.message_id)
            if main_message is None:
                main_message = sent_message
        else:
            group = []
            for i, item in enumerate(chunk):
                cap = caption if main_message is None and i == 0 else None
                group.append(InputMediaDocument(
                    media=item["file_id"],
                    caption=cap,
                    parse_mode="HTML" if cap else None,
                    filename=item.get("filename") or None,
                ))
            sent = await bot.send_media_group(
                chat_id=CHANNEL_ID, media=group,
                reply_to_message_id=reply_to,
            )
            all_message_ids.extend(m.message_id for m in sent)
            if main_message is None:
                main_message = sent[0]

    if main_message is None:
        raise RuntimeError("没有可发布的媒体或文档")

    await save_published_post(user_id, main_message.message_id, data, media_list, doc_list, all_message_ids)

    return {
        "status": "published",
        "message_id": main_message.message_id,
        "link": _link_of(main_message.message_id),
        "media_count": len(media_list),
        "document_count": len(doc_list),
    }

def _file_id_of(message):
    for attr in ("photo", "video", "animation", "audio", "document"):
        value = getattr(message, attr, None)
        if value:
            # python-telegram-bot exposes Message.photo as a tuple in current
            # releases, while older releases and our stored mocks used lists.
            if isinstance(value, (list, tuple)):
                return value[-1].file_id
            return value.file_id
    return None


def InputMediaDocumentFactory(file_handle, filename, caption):
    from telegram import InputMediaDocument, InputFile
    kw = {"caption": caption, "parse_mode": "HTML" if caption else None}
    return InputMediaDocument(
        media=InputFile(file_handle, filename=filename, read_file_handle=False),
        **kw,
    )
