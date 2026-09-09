"""
投稿发布模块
"""
import json
import logging
import asyncio
import os
import time
from datetime import datetime
from telegram import (
    Update,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument
)
from telegram.error import NetworkError
from telegram.ext import ConversationHandler, CallbackContext

from config.settings import (
    CHANNEL_ID,
    CHAT_REVIEW_REQUIRED,
    NOTIFY_OWNER,
    OWNER_ID,
)
from database.db_manager import get_db, cleanup_old_data
from models.state import STATE
from utils.helper_functions import build_caption
from utils.search_engine import get_search_engine, PostDocument

logger = logging.getLogger(__name__)

TELEGRAM_SEND_TIMEOUT_SECONDS = max(
    5.0,
    float(os.getenv("TELEGRAM_SEND_TIMEOUT_SECONDS", os.getenv("REVIEW_PREVIEW_TIMEOUT_SECONDS", "120"))),
)


def _telegram_timeout_kwargs():
    return {
        "read_timeout": TELEGRAM_SEND_TIMEOUT_SECONDS,
        "write_timeout": TELEGRAM_SEND_TIMEOUT_SECONDS,
        "connect_timeout": min(TELEGRAM_SEND_TIMEOUT_SECONDS, 30.0),
        "pool_timeout": min(TELEGRAM_SEND_TIMEOUT_SECONDS, 30.0),
    }


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

        if not (data["tags"] or "").strip():
            if is_callback:
                await update.callback_query.answer("请先填写标签", show_alert=True)
            else:
                await _reply_to_user("⚠️ 发布前必须填写标签")
            return STATE['PREVIEW']

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
        
        # 媒体 + 文档统一投递：内部按"相册→GIF/音频→文档组"回复链串联，
        # caption 只挂在整条投递的第一条消息。
        chat_items = _normalize_chat_items(media_list, doc_list)
        if chat_items:
            try:
                sent_messages, sent_message = await deliver_items_to_chat(
                    context.bot, CHANNEL_ID, chat_items,
                    caption=caption, spoiler=spoiler_flag,
                )
                all_message_ids = _channel_message_ids(sent_messages, sent_message)
            except Exception as e:
                logger.error("发布到频道失败: %s", e, exc_info=True)
                sent_message, all_message_ids = None, []
        
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

# ---- 统一投递布局（频道发布与审核群预览共用同一套层级规则）----
#
# 主贴/回复层级（媒体在前、文档在后，每一段都回复前一条形成一条链）：
#   1) photo/video 按每 10 个组成相册（Telegram 相册上限）；
#   2) animation(GIF)/audio 不能进相册，逐条单独发送；
#   3) document（小说 .txt、ugoira .zip、超 10 MiB 的图片页）按每 10 个成组，
#      若投稿里有媒体则作为媒体主贴的回复，否则独立成主贴；
#   4) caption 永远只放在"整条投递的第一条消息"上；
#   5) 相册发送失败自动降级为逐条发送（小内存机型超时兜底），RSS 峰值只与
#      单文件相关，整条投稿不因一个相册超时而失败。
# 本地文件必须带 attach=True（否则 PTB 序列化时丢掉 media 字段，
# Telegram 报 media not found，图片/文档发不出去）。
CHANNEL_ALBUM_SIZE = 10
# 超出一组相册（如 >10 图）时，频道里多个相册的回复层级：
#   chain（默认）：每个相册回复上一个相册，形成一条逐级回复链；
#   post：后续相册都回复第一条主贴（或外部锚点），整组"跟着帖子走"。
CHANNEL_ALBUM_REPLY = os.getenv("CHANNEL_ALBUM_REPLY", "chain").strip().lower()
DISCUSSION_FORWARD_TIMEOUT_SECONDS = max(
    1.0, float(os.getenv("DISCUSSION_FORWARD_TIMEOUT_SECONDS", "10"))
)
_discussion_forwards = {}
_discussion_waiters = {}
# 近期频道自动转发的滚动列表（(源频道id, 源消息id, 讨论组id, 讨论消息id, 时间)）。
# 首贴发送"结果不确定"（响应丢失）时反查它，判断 Telegram 是否其实收下并已转发，
# 以便连频道首贴带讨论组锚点一起删干净——不确定→可安全重试。
_recent_forwards = []
# Telegram 图片（含相册）单张上限 10 MiB，超过必须按文档发送。
PHOTO_MAX_BYTES = int((10.0 - 0.5) * 1024 * 1024)


def capture_discussion_forward(update):
    """记住频道帖自动转发到关联讨论组后的消息 ID。"""
    message = getattr(update, "message", None)
    if not message or not getattr(message, "is_automatic_forward", False):
        return
    origin = getattr(message, "forward_origin", None)
    source_chat = getattr(origin, "chat", None)
    source_id = getattr(origin, "message_id", None)
    if source_chat is None or source_id is None:
        source_chat = getattr(message, "forward_from_chat", None)
        source_id = getattr(message, "forward_from_message_id", None)
    if source_chat is None or source_id is None:
        return

    now = time.monotonic()
    for key, (_, seen_at) in list(_discussion_forwards.items()):
        if now - seen_at > 60:
            _discussion_forwards.pop(key, None)
    key = (source_chat.id, source_id)
    target = (message.chat.id, message.message_id)
    # 保留最近 60s 的转发用于"首贴发送结果不确定"反查；ponytail: 有界列表即可。
    _recent_forwards.append((source_chat.id, source_id, message.chat.id, message.message_id, now))
    del _recent_forwards[:-200]
    while _recent_forwards and now - _recent_forwards[0][4] > 60:
        _recent_forwards.pop(0)
    waiter = _discussion_waiters.pop(key, None)
    if waiter is not None and not waiter.done():
        waiter.set_result(target)
    else:
        _discussion_forwards[key] = (target, now)


def _pop_recent_forward(channel_id, channel_msg_id):
    """反查某频道消息是否已自动转发到讨论组；命中则返回 (讨论组id, 讨论消息id)。"""
    now = time.monotonic()
    while _recent_forwards and now - _recent_forwards[0][4] > 60:
        _recent_forwards.pop(0)
    for i, (cid, mid, dchat, dmsg, _t) in enumerate(_recent_forwards):
        if cid == channel_id and mid == channel_msg_id:
            _recent_forwards.pop(i)
            return dchat, dmsg
    return None


async def _wait_for_discussion_forward(channel_id, message_id):
    key = (channel_id, message_id)
    cached = _discussion_forwards.pop(key, None)
    if cached is not None:
        return cached[0]
    waiter = asyncio.get_running_loop().create_future()
    _discussion_waiters[key] = waiter
    try:
        return await asyncio.wait_for(waiter, DISCUSSION_FORWARD_TIMEOUT_SECONDS)
    finally:
        if _discussion_waiters.get(key) is waiter:
            _discussion_waiters.pop(key, None)


def _is_local_item(item: dict) -> bool:
    return bool(item.get("path"))


def _local_input_file(path: str, filename: str):
    from telegram import InputFile
    return InputFile(open(path, "rb"), filename=filename,
                     read_file_handle=False, attach=True)


def _close_item_handle(media):
    """关闭本地文件句柄（发送完成后；file_id 是字符串，无句柄可关）。"""
    handle = getattr(media, "input_file_content", None)
    if handle and hasattr(handle, "close"):
        try:
            handle.close()
        except Exception:
            pass


def _compress_photo(src_path: str, max_bytes: int) -> bool:
    """把本地图片原地压缩到 <= max_bytes（转 JPEG，逐级降尺寸/质量）。

    返回是否成功；失败时调用方降级为 document 兜底。Pillow 缺失或图片
    无法解码时视为失败，不影响投稿（只回退到旧行为）。
    """
    try:
        from PIL import Image
    except ImportError:
        return False
    import io

    try:
        im = Image.open(src_path)
        try:
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            # 尺寸先缩到 Telegram 常见上限，再逐级降 JPEG 质量
            if max(im.size) > 4096:
                im.thumbnail((4096, 4096), Image.LANCZOS)
            for quality in (92, 85, 78, 70, 60):
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
                if buf.tell() <= max_bytes:
                    tmp = src_path + ".compressed"
                    with open(tmp, "wb") as fh:
                        fh.write(buf.getvalue())
                    os.replace(tmp, src_path)
                    return True
            return False
        finally:
            im.close()
    except Exception as e:
        logger.warning("图片压缩失败，回退为文档发送: %s", e)
        return False


def reclassify_oversized_photos(items: list, *, max_bytes: int = PHOTO_MAX_BYTES) -> list:
    """本地图片超过 max_bytes 时先压缩到可发范围（保持 photo，频道内直接看图）；
    压缩失败才改按文档发送（Telegram 照片上限 10 MiB，文档上限 50 MiB）。
    file_id 已是 Telegram 托管资源，不受此限。返回新列表。"""
    out = []
    for item in items:
        it = dict(item)
        if it.get("kind") == "photo" and it.get("path"):
            try:
                if os.path.getsize(it["path"]) > max_bytes:
                    if _compress_photo(it["path"], max_bytes):
                        logger.info("图片压缩到 %d 字节内，仍按图片发送: %s",
                                    max_bytes, it.get("filename"))
                        base = os.path.splitext(it.get("filename"))[0]
                        it["filename"] = f"{base}.jpg"
                    else:
                        logger.info("图片超过 %d 字节且无法压缩，按文档发送: %s",
                                    max_bytes, it.get("filename"))
                        it["kind"] = "document"
            except OSError:
                pass
        out.append(it)
    return out


def _media_kwargs(item: dict, caption) -> dict:
    """单条消息（非相册）的发送参数，按类型映射到 send_* 方法。"""
    kind = item["kind"]
    media = _local_input_file(item["path"], item["filename"]) if _is_local_item(item) else item["file_id"]
    kw = {"caption": caption, "parse_mode": "HTML" if caption else None}
    if kind == "photo":
        return {"method": "send_photo", "photo": media, **kw, "has_spoiler": item.get("spoiler", False)}
    if kind == "video":
        return {"method": "send_video", "video": media, **kw, "has_spoiler": item.get("spoiler", False)}
    if kind == "animation":
        return {"method": "send_animation", "animation": media, **kw, "has_spoiler": item.get("spoiler", False)}
    if kind == "audio":
        return {"method": "send_audio", "audio": media, **kw}
    return {"method": "send_document", "document": media,
            "filename": item.get("filename") or "file", **kw}


def _album_input_media(item: dict, caption):
    """相册成员（仅 photo/video/document 能进相册）。本地文件必须 attach=True。"""
    kind = item["kind"]
    media = _local_input_file(item["path"], item["filename"]) if _is_local_item(item) else item["file_id"]
    parse = "HTML" if caption else None
    if kind == "photo":
        return InputMediaPhoto(media=media, caption=caption, parse_mode=parse,
                               has_spoiler=item.get("spoiler", False))
    if kind == "video":
        return InputMediaVideo(media=media, caption=caption, parse_mode=parse,
                               has_spoiler=item.get("spoiler", False))
    return InputMediaDocument(media=media, caption=caption, parse_mode=parse,
                              filename=item.get("filename") or "file")


def _item_batches(items: list, album_size: int):
    """按固定相册族顺序切片：photo/video 相册 → animation/audio 逐条 →
    document 相册。同族内保持原顺序；每族连续段不超过 album_size
    （animation/audio 永远逐条）。"""
    def family(kind):
        if kind in ("photo", "video"):
            return "visual"
        return kind  # animation / audio / document 各自独立成族

    order = {"visual": 0, "animation": 1, "audio": 2, "document": 3}
    ordered = sorted(items, key=lambda item: order.get(family(item["kind"]), 9))

    runs = []
    for item in ordered:
        fam = family(item["kind"])
        if runs and runs[-1][0] == fam and fam in ("visual", "document") \
                and len(runs[-1][1]) < album_size:
            runs[-1][1].append(item)
        else:
            runs.append((fam, [item]))
    return runs


async def _run_item_batches(items, *, caption, album_size,
                            send_one, send_album, fallback_single=True, anchor_id=None,
                            reply_mode="chain", on_sent=None):
    """共享的投递编排（不绑定 bot/chat）：

    统一"主贴+回复"层级，频道发布与审核群预览共用同一套规则，
    不再各写一份分组/排序/串联逻辑：
      - 顺序：photo/video 相册 → GIF/音频逐条 → document 相册；
      - chain 模式：每批回复上一批（第一条回复 anchor_id）；
      - post 模式：后续批次都回复主贴/anchor_id，不逐级串联；
      - caption 只挂在整条投递的第一条消息；
      - 相册失败自动降级逐条（send_one）。

    send_one(item, caption, reply_to) -> Message
    send_album(media_built_list, reply_to, caption) -> [Message]
        （media 构造交给调用方，因为审核群 RetryAfter 需要重建 InputFile）
    返回 (sent_messages, main_message)。
    """
    sent_messages = []
    previous_id = None
    main_message = None

    for fam, batch in _item_batches(items, album_size):
        can_album = fam in ("visual", "document") and len(batch) > 1
        if reply_mode == "post":
            # 都跟着锚点（外部指定帖）或主贴走，避免相册逐级嵌套成链。
            reply_to = anchor_id if anchor_id is not None else (
                main_message.message_id if main_message is not None else None
            )
        else:
            reply_to = previous_id if previous_id is not None else anchor_id
        batch_caption = caption if main_message is None else None
        messages = None

        if can_album:
            media_group = None
            try:
                media_group = [
                    _album_input_media(item, batch_caption if i == 0 else None)
                    for i, item in enumerate(batch)
                ]
                messages = await send_album(media_group, reply_to)
                if messages is not None and len(messages) != len(batch):
                    raise RuntimeError(
                        f"返回消息数 {len(messages)} 与文件数 {len(batch)} 不一致"
                    )
                for member in media_group:
                    _close_item_handle(member.media)
            except NetworkError:
                if media_group:
                    for member in media_group:
                        _close_item_handle(member.media)
                # Telegram may have accepted a request before the response was
                # lost. Falling back here can duplicate an entire album.
                raise
            except Exception as exc:
                if media_group:
                    for member in media_group:
                        _close_item_handle(member.media)
                logger.warning("相册发送失败（%s），降级为逐条发送 %d 个文件",
                               exc, len(batch))
                messages = None
        if messages is None:
            messages = []
            for index, item in enumerate(batch):
                item_caption = batch_caption if index == 0 else None
                if index == 0:
                    item_reply = reply_to
                elif reply_mode == "post":
                    item_reply = reply_to if reply_to is not None else messages[0].message_id
                else:
                    item_reply = messages[-1].message_id
                messages.append(await send_one(item, item_caption, item_reply))

        for message in messages:
            sent_messages.append(message)
            if main_message is None:
                main_message = message
            previous_id = message.message_id
        # 每批成功后回调（评论区模式用它登记已落地消息，失败时完整回滚）。
        if on_sent:
            on_sent(messages)

    return sent_messages, main_message


def _normalize_chat_items(media_list, doc_list):
    """聊天会话的紧凑格式 "kind:file_id[:filename]" → 统一 item dict。"""
    items = []
    for entry in media_list:
        kind, file_id = entry.split(":", 1)
        items.append({"kind": kind, "file_id": file_id, "spoiler_key": kind in ("photo", "video", "animation")})
    for entry in doc_list:
        parts = entry.split(":", 2)
        file_id = parts[1] if len(parts) >= 2 else parts[0]
        filename = parts[2] if len(parts) >= 3 else "file"
        items.append({"kind": "document", "file_id": file_id, "filename": filename})
    return items


class DiscussionPublishError(RuntimeError):
    """评论区发布失败。uncertain=True 表示无法确定是否已部分落地（可能重复）。"""
    def __init__(self, message, *, uncertain=False, sent=None):
        super().__init__(message)
        self.uncertain = uncertain
        self.sent = sent if sent is not None else {"cover": [], "anchor": [], "rest": []}


async def _delete_message(bot, chat_id, message_id):
    """删一条消息；消息已不存在视为成功，返回是否无需处理。"""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except Exception as exc:
        msg = str(exc).lower()
        # 消息本就不在，无需清理；其余失败需人工注意。
        if "not found" in msg or "message can't be deleted" in msg:
            return True
        logger.exception("回滚删除消息失败 chat=%s msg=%s", chat_id, message_id)
        return False


async def _discussion_rollback(bot, sent):
    """删除本次评论区发布已落地的全部消息；返回是否全部清理干净。"""
    clean = True
    # 顺序：先讨论组内容与锚点，再频道首贴（让评论区先消失）。
    for chat_id, msg_id in sent.get("rest", []) + sent.get("anchor", []) + sent.get("cover", []):
        clean = await _delete_message(bot, chat_id, msg_id) and clean
    return clean


async def _scan_recent_forward(channel_id):
    """首贴发送"响应丢失"后，轮询近期自动转发，确认 Telegram 是否其实已收下首贴。

    命中则返回该频道最新转发 (源消息id, 讨论组id, 讨论消息id)，供回滚连首贴
    带讨论组锚点一起删干净；未命中说明首贴大概率没发出去。
    """
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        for i in range(len(_recent_forwards) - 1, -1, -1):
            cid, mid, dchat, dmsg, _t = _recent_forwards[i]
            if cid == channel_id:
                _recent_forwards.pop(i)
                return mid, dchat, dmsg
        await asyncio.sleep(1.0)
    return None


async def _deliver_discussion(bot, channel, items, *, caption, spoiler, album_size, timeout_kwargs):
    """频道只发首贴；其余图片回复到关联讨论组该帖评论串。

    每一步发出的消息都登记，失败完整回滚。首贴发送"结果不确定"（响应丢失）时
    从自动转发缓存反查：若 Telegram 实际已收下，则连频道首贴带讨论组锚点删干净，
    让状态回到确定态并自动重试一次。评论相册"发了没成功"无法确认是否重复，
    不自动重试，抛 uncertain 交人工核对。返回 (sent_messages, main_message)。
    """
    linked = channel.linked_chat_id

    async def attempt():
        sent = {"cover": [], "anchor": [], "rest": []}

        # 阶段1：频道首贴（响应丢失时不确定是否到达，反查自动转发自愈）。
        first_sent = None
        try:
            first_sent, main = await deliver_items_to_chat(
                bot, channel.id, items[:1], caption=caption, spoiler=spoiler,
                album_size=album_size, timeout_kwargs=timeout_kwargs, reply_mode="post",
            )
        except NetworkError:
            found = await _scan_recent_forward(channel.id)
            if found is not None:
                cover_id, dchat, dmsg = found
                sent["cover"] = [(channel.id, cover_id)]
                sent["anchor"] = [(dchat, dmsg)]
                raise DiscussionPublishError(
                    "频道首贴发送响应丢失，已反查到帖子并回滚", uncertain=False, sent=sent)
            raise DiscussionPublishError(
                "频道首贴发送响应丢失，未在讨论区发现转发，重发一次", uncertain=False, sent=sent)
        except Exception as exc:
            raise DiscussionPublishError(f"频道首贴发送失败：{exc}", uncertain=False, sent=sent)
        sent["cover"] = [(m.chat.id, m.message_id) for m in first_sent]

        # 阶段2：等待自动转发到讨论组锚点（失败时首贴一定已落地，回滚它）。
        try:
            dchat, dmsg = await _wait_for_discussion_forward(channel.id, main.message_id)
        except Exception:
            raise DiscussionPublishError("等待频道帖转发到讨论组超时", uncertain=False, sent=sent)
        if dchat != linked:
            raise DiscussionPublishError("频道自动转发落到了非预期讨论组", uncertain=False, sent=sent)
        sent["anchor"] = [(dchat, dmsg)]

        # 阶段3：其余图片回复到讨论组锚点。响应丢失可能已部分送达，不自动重试。
        rest_collected = []
        try:
            rest_sent, _ = await deliver_items_to_chat(
                bot, dchat, items[1:], caption=None, spoiler=spoiler,
                album_size=album_size, timeout_kwargs=timeout_kwargs,
                reply_to_message_id=dmsg, reply_mode="post",
                on_sent=lambda msgs: rest_collected.extend(
                    (m.chat.id, m.message_id) for m in msgs),
            )
        except NetworkError as exc:
            sent["rest"] = rest_collected
            raise DiscussionPublishError(
                "评论区相册发送响应丢失，可能已部分送达，不自动重试", uncertain=True, sent=sent) from exc
        except Exception as exc:
            sent["rest"] = rest_collected
            raise DiscussionPublishError(f"评论区相册发送失败：{exc}", uncertain=False, sent=sent)
        sent["rest"] = rest_collected
        return first_sent + rest_sent, main

    last = None
    for try_no in (1, 2):
        try:
            return await attempt()
        except DiscussionPublishError as exc:
            last = exc
            if exc.uncertain:
                # 无法确认是否重复：先回滚能确定的部分，再交人工核对，不自动重试。
                await _discussion_rollback(bot, exc.sent)
                raise
            # 确定态：完整回滚；删干净才自动重试一次。
            if not await _discussion_rollback(bot, exc.sent):
                raise DiscussionPublishError(
                    f"{exc}；且回滚未能删净，请人工检查", uncertain=True, sent=exc.sent)
            if try_no == 2:
                raise
            await asyncio.sleep(2.0)
    raise last or DiscussionPublishError("评论区发布失败", uncertain=True)


async def deliver_items_to_chat(bot, chat_id, items, *, caption, spoiler=False,
                                album_size=CHANNEL_ALBUM_SIZE, timeout_kwargs=None,
                                reply_to_message_id=None, reply_mode=None, on_sent=None):
    # reply_to_message_id 作为整条链的锚点：媒体在前会自然成为主贴，
    # 只有当整条投递全是文档且外部指定锚点时才会回复它。
    """统一投递入口（频道发布与审核群预览共用）。

    items: [{"kind": photo|video|animation|audio|document,
             本地文件加 "path"+"filename"；Telegram 资源加 "file_id"(+"filename")}]
    caption 只挂在整条投递的第一条消息；默认每批回复上一批，形成主贴回复链；
    reply_mode="post" 时后续批次都回复主贴（跟随帖子，不逐级串联）。
    返回 (sent_messages[list], main_message)。
    """
    timeout_kwargs = _telegram_timeout_kwargs() if timeout_kwargs is None else timeout_kwargs
    reply_mode = (reply_mode or CHANNEL_ALBUM_REPLY) or "chain"
    items = [dict(item, spoiler=item.get("spoiler", spoiler)) for item in items]

    if reply_mode == "discussion" and len(items) > 1:
        channel = await bot.get_chat(chat_id)
        if not channel.linked_chat_id:
            raise RuntimeError("频道未关联讨论组，无法把其余图片发到主贴评论区")
        return await _deliver_discussion(
            bot, channel, items, caption=caption, spoiler=spoiler,
            album_size=album_size, timeout_kwargs=timeout_kwargs,
        )

    async def _album(media_group, reply_to):
        kwargs = dict(chat_id=chat_id, media=media_group,
                      reply_to_message_id=reply_to, **timeout_kwargs)
        return await bot.send_media_group(**kwargs)

    async def _single(item, cap, reply_to):
        kw = _media_kwargs(item, cap)
        method = getattr(bot, kw.pop("method"))
        try:
            return await method(chat_id=chat_id, reply_to_message_id=reply_to,
                                **timeout_kwargs, **kw)
        finally:
            # kw 里的本地 InputFile 句柄发送后关闭；file_id 是字符串无需关闭
            for value in kw.values():
                _close_item_handle(value)

    return await _run_item_batches(
        items, caption=caption, album_size=album_size,
        send_one=_single, send_album=_album,
        anchor_id=reply_to_message_id,
        reply_mode=reply_mode, on_sent=on_sent,
    )


def _channel_message_ids(messages, main_message):
    """只记录主贴所在频道的消息，避免把讨论组 ID 当频道帖删除。"""
    main_chat_id = getattr(getattr(main_message, "chat", None), "id", None)
    return [
        message.message_id for message in messages
        if main_chat_id is None
        or getattr(getattr(message, "chat", None), "id", main_chat_id) == main_chat_id
    ]


async def handle_media_publish(context, media_list, caption, spoiler_flag):
    """聊天投稿：发布媒体（file_id 列表 "kind:file_id"）到频道。

    统一走 deliver_items_to_chat；caption 直接挂在第一条消息（build_caption 已按
    1024 上限硬截断，不再单独发文本头消息）。
    Returns: (主消息对象, 所有消息ID列表) 或 (None, [])
    """
    items = [{"kind": e.split(":", 1)[0], "file_id": e.split(":", 1)[1],
              "spoiler": spoiler_flag} for e in media_list]
    try:
        sent, main = await deliver_items_to_chat(
            context.bot, CHANNEL_ID, items, caption=caption, spoiler=spoiler_flag
        )
    except Exception as e:
        logger.error("发送媒体失败: %s", e, exc_info=True)
        return (None, [])
    if not sent:
        return (None, [])
    return (main, [m.message_id for m in sent])


async def handle_document_publish(context, doc_list, caption=None, reply_to_message_id=None):
    """聊天投稿：发布文档（"document:file_id[:filename]"）到频道。

    Returns: 主消息对象或 None。
    """
    items = []
    for entry in doc_list:
        parts = entry.split(":", 2)
        file_id = parts[1] if len(parts) >= 2 else parts[0]
        filename = parts[2] if len(parts) >= 3 else "file"
        items.append({"kind": "document", "file_id": file_id, "filename": filename})
    try:
        sent, main = await deliver_items_to_chat(
            context.bot, CHANNEL_ID, items, caption=caption,
            reply_to_message_id=reply_to_message_id,
        )
    except Exception as e:
        logger.error("发送文档失败: %s", e, exc_info=True)
        return None
    return main


async def _ledger_find_by_key(idempotency_key: str):
    if not idempotency_key:
        return None
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT * FROM delivery_ledger WHERE idempotency_key=?",
            (idempotency_key,),
        )
        return await cursor.fetchone()


async def _ledger_find_work(target_id: str, work_type: str, pixiv_id: str, window_seconds: int):
    if not pixiv_id:
        return None
    async with get_db() as conn:
        sql = (
            "SELECT * FROM delivery_ledger WHERE pixiv_id=? AND work_type=? "
            "AND status='published' AND created_at >= ?"
        )
        params = [pixiv_id, work_type, time.time() - window_seconds]
        if target_id:
            sql += " AND target_id=?"
            params.append(target_id)
        sql += " ORDER BY created_at DESC LIMIT 1"
        cursor = await conn.execute(sql, params)
        return await cursor.fetchone()


async def _ledger_record(idempotency_key: str, *, target_id, pixiv_id, work_type,
                         message_id, related_message_ids, user_id):
    if not idempotency_key:
        return False
    async with get_db() as conn:
        try:
            await conn.execute(
                "INSERT INTO delivery_ledger "
                "(idempotency_key, target_id, pixiv_id, work_type, status, message_id, "
                "related_message_ids, user_id, created_at) "
                "VALUES (?, ?, ?, ?, 'published', ?, ?, ?, ?)",
                (idempotency_key, target_id or "", pixiv_id or "", work_type or "",
                 message_id, json.dumps(related_message_ids or []), user_id, time.time()),
            )
            return True
        except Exception:
            # UNIQUE race / replay: the other attempt owns the channel post.
            return False


def _ledger_replay(row) -> dict:
    return {
        "status": "published",
        "message_id": row["message_id"],
        "link": _link_of(row["message_id"]) if row["message_id"] else "",
        "reused": True,
        "reuse_reason": "idempotent_replay",
        "matched_idempotency_key": row["idempotency_key"],
        "delivery_status": "published",
    }


async def publish_from_files(bot, files, *, tags="", title="", note="", link="",
                             anonymous=False, spoiler=False, user_id, username="",
                             idempotency_key="", target_id="", work_type="", pixiv_id="") -> dict:
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

    from handlers.review import PUBLISHED_DEDUP_WINDOW_SECONDS, _pixiv_id_from_link
    key = idempotency_key.strip()[:240]
    pid = (pixiv_id or _pixiv_id_from_link(link or "")).strip()
    replay = await _ledger_find_by_key(key)
    if replay is not None:
        for fobj in files:
            try:
                os.remove(fobj["path"])
            except OSError:
                pass
        return _ledger_replay(replay)
    historical = await _ledger_find_work(target_id, work_type, pid, PUBLISHED_DEDUP_WINDOW_SECONDS)
    if historical is not None and historical["idempotency_key"] != key:
        for fobj in files:
            try:
                os.remove(fobj["path"])
            except OSError:
                pass
        result = _ledger_replay(historical)
        result["reuse_reason"] = "duplicate_existing"
        return result

    data = {
        "tags": tags, "title": title, "note": note, "link": link,
        "spoiler": "true" if spoiler else "false",
        "anonymous": "true" if anonymous else "false",
        "user_id": user_id, "username": username,
    }
    caption = build_caption(data)

    # 超大原图（>9.5 MiB）Telegram 无法作为照片发送，自动改按文档投递。
    items = reclassify_oversized_photos(
        [{"kind": f["kind"], "path": f["path"], "filename": f["filename"],
          "spoiler": spoiler} for f in files]
    )
    # Persist file IDs against the same media-family order used for delivery.
    items = [item for _, batch in _item_batches(items, CHANNEL_ALBUM_SIZE) for item in batch]

    media_list, doc_list = [], []
    sent_messages, main_message = await deliver_items_to_chat(
        bot, CHANNEL_ID, items, caption=caption, spoiler=spoiler
    )
    if main_message is None:
        raise RuntimeError("所有消息发送失败")

    all_message_ids = _channel_message_ids(sent_messages, main_message)
    for message, item in zip(sent_messages, items):
        file_id = _file_id_of(message)
        if item["kind"] == "document":
            doc_list.append(f"document:{file_id}:{item.get('filename', 'file')}")
        else:
            media_list.append(f"{item['kind']}:{file_id}")

    await save_published_post(user_id, main_message.message_id, data, media_list, doc_list, all_message_ids)
    await _ledger_record(key, target_id=target_id, pixiv_id=pid, work_type=work_type,
                         message_id=main_message.message_id,
                         related_message_ids=all_message_ids, user_id=user_id)

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
                                anonymous=False, spoiler=False, user_id, username="",
                                idempotency_key="", target_id="", work_type="", pixiv_id="") -> dict:
    """
    API file_id 直投核心：素材已在 Telegram 服务器上（file_id 归属本 bot），
    直接用 file_id 发布到频道——零媒体文件传输。

    media:     [{"type": "photo|video|animation|audio", "file_id": str}]
    documents: [{"file_id": str, "filename": str}]
    """
    from handlers.review import PUBLISHED_DEDUP_WINDOW_SECONDS, _pixiv_id_from_link
    key = idempotency_key.strip()[:240]
    pid = (pixiv_id or _pixiv_id_from_link(link or "")).strip()
    replay_row = await _ledger_find_by_key(key)
    if replay_row is not None:
        return _ledger_replay(replay_row)
    historical_row = await _ledger_find_work(target_id, work_type, pid, PUBLISHED_DEDUP_WINDOW_SECONDS)
    if historical_row is not None and historical_row["idempotency_key"] != key:
        out = _ledger_replay(historical_row)
        out["reuse_reason"] = "duplicate_existing"
        return out

    data = {
        "tags": tags, "title": title, "note": note, "link": link,
        "spoiler": "true" if spoiler else "false",
        "anonymous": "true" if anonymous else "false",
        "user_id": user_id, "username": username,
    }
    caption = build_caption(data)

    items = [
        {"kind": m["type"], "file_id": m["file_id"], "spoiler": spoiler}
        for m in media
    ] + [
        {"kind": "document", "file_id": d["file_id"],
         "filename": d.get("filename") or "file"}
        for d in documents
    ]

    sent_messages, main_message = await deliver_items_to_chat(
        bot, CHANNEL_ID, items, caption=caption, spoiler=spoiler
    )
    if main_message is None:
        raise RuntimeError("没有可发布的媒体或文档")

    all_message_ids = _channel_message_ids(sent_messages, main_message)
    media_list = [f"{m['type']}:{m['file_id']}" for m in media]
    doc_list = [f"document:{d['file_id']}:{d.get('filename', 'file')}" for d in documents]

    await save_published_post(user_id, main_message.message_id, data, media_list, doc_list, all_message_ids)
    await _ledger_record(key, target_id=target_id, pixiv_id=pid, work_type=work_type,
                         message_id=main_message.message_id,
                         related_message_ids=all_message_ids, user_id=user_id)

    return {
        "status": "published",
        "message_id": main_message.message_id,
        "link": _link_of(main_message.message_id),
        "media_count": len(media_list),
        "document_count": len(doc_list),
        "delivery_status": "published",
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
    """兼容旧调用：本地文档文件 → InputMediaDocument（attach 模式）。"""
    from telegram import InputMediaDocument, InputFile
    return InputMediaDocument(
        media=InputFile(file_handle, filename=filename,
                        read_file_handle=False, attach=True),
        caption=caption, parse_mode="HTML" if caption else None,
        filename=filename,
    )
