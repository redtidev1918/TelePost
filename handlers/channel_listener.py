"""
频道消息监听器模块
监听频道新消息，自动记录到数据库
支持处理非本项目bot发布的不规范帖子
"""
import json
import re
import logging
import asyncio
from datetime import datetime
from typing import Optional, Set
from telegram import Update
from telegram.ext import CallbackContext

from config.settings import CHANNEL_ID
from database.db_manager import get_db
from utils.search_engine import get_search_engine, PostDocument

logger = logging.getLogger(__name__)

# 并发控制：正在处理的消息ID集合（防止重复处理）
_processing_messages: Set[int] = set()
_processing_lock = asyncio.Lock()

# 字段长度限制（防止数据库溢出）
MAX_TITLE_LENGTH = 200
MAX_NOTE_LENGTH = 2000
MAX_CAPTION_LENGTH = 4000
MAX_FILENAME_LENGTH = 500
MAX_TAGS_LENGTH = 500


def clean_text(text: str, max_length: Optional[int] = None) -> str:
    """
    清理文本：移除多余空格、换行，处理特殊字符
    
    Args:
        text: 原始文本
        max_length: 最大长度限制
        
    Returns:
        str: 清理后的文本
    """
    if not text:
        return ''
    
    # 移除控制字符（保留换行符）
    text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]', '', text)
    
    # 规范化换行符
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # 移除多余的空格和换行
    text = re.sub(r'[ \t]+', ' ', text)  # 多个空格合并为一个
    text = re.sub(r'\n{3,}', '\n\n', text)  # 多个换行合并为两个
    
    # 去除首尾空白
    text = text.strip()
    
    # 限制长度
    if max_length and len(text) > max_length:
        text = text[:max_length].rstrip()
        logger.debug(f"文本被截断到 {max_length} 字符")
    
    return text


def extract_tags_from_text(text: str) -> str:
    """
    从文本中提取标签（支持多种格式）
    
    Args:
        text: 文本内容
        
    Returns:
        str: 提取的标签（空格分隔）
    """
    if not text:
        return ''
    
    tags = []
    
    # 方法1: 查找 #标签
    hashtag_tags = re.findall(r'#(\w+)', text)
    tags.extend(hashtag_tags)
    
    # 方法2: 查找 [标签] 格式
    bracket_tags = re.findall(r'\[([^\]]+)\]', text)
    # 只保留看起来像标签的（短文本，不含空格）
    bracket_tags = [tag.strip() for tag in bracket_tags if len(tag.strip()) < 30 and ' ' not in tag.strip()]
    tags.extend(bracket_tags)
    
    # 去重并转换为 #标签 格式
    unique_tags = list(dict.fromkeys([tag.lower() for tag in tags]))  # 保持顺序的去重，转为小写
    result = ' '.join([f"#{tag}" for tag in unique_tags])
    
    # 限制长度
    if len(result) > MAX_TAGS_LENGTH:
        result = result[:MAX_TAGS_LENGTH].rstrip()
        logger.debug(f"标签被截断到 {MAX_TAGS_LENGTH} 字符")
    
    return result


def extract_title_from_text(text: str, filename: str = '') -> str:
    """
    从文本中智能提取标题（支持多种格式）
    
    Args:
        text: 文本内容
        filename: 文件名（可选，用于推断标题）
        
    Returns:
        str: 提取的标题
    """
    if not text and not filename:
        return ''
    
    # 方法1: 查找明确的标题标记（如 "标题:"、"Title:" 等）
    title_patterns = [
        r'(?:标题|标题：|Title|title)[:：]\s*(.+?)(?:\n|$)',
        r'^【(.+?)】',  # 【标题】格式
        r'^\[(.+?)\]',  # [标题] 格式
        r'^(.+?)[:：]\s*\n',  # 第一行以冒号结尾
    ]
    
    for pattern in title_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            title = match.group(1).strip()
            if title and len(title) > 3:  # 至少3个字符
                return clean_text(title, MAX_TITLE_LENGTH)
    
    # 方法2: 从文件名推断（如果没有文本）
    if not text and filename:
        # 移除扩展名
        name_without_ext = re.sub(r'\.[^.]+$', '', filename)
        if name_without_ext and len(name_without_ext) > 3:
            return clean_text(name_without_ext, MAX_TITLE_LENGTH)
    
    # 方法3: 取第一行（移除标签和链接后）
    if text:
        # 移除标签和链接
        text_clean = re.sub(r'#\w+', '', text)
        text_clean = re.sub(r'https?://[^\s]+', '', text_clean)
        text_clean = text_clean.strip()
        
        if text_clean:
            # 取第一行
            first_line = text_clean.split('\n')[0].strip()
            if first_line:
                # 如果第一行太长，取前部分
                if len(first_line) > MAX_TITLE_LENGTH:
                    # 尝试在句号、问号、感叹号处截断
                    sentence_end = re.search(r'[。！？.!?]', first_line[:MAX_TITLE_LENGTH])
                    if sentence_end:
                        first_line = first_line[:sentence_end.end()].strip()
                    else:
                        first_line = first_line[:MAX_TITLE_LENGTH].rstrip()
                
                return clean_text(first_line, MAX_TITLE_LENGTH)
    
    # 方法4: 使用文件名作为后备
    if filename:
        name_without_ext = re.sub(r'\.[^.]+$', '', filename)
        if name_without_ext:
            return clean_text(name_without_ext, MAX_TITLE_LENGTH)
    
    return ''


def extract_link_from_text(text: str) -> str:
    """
    从文本中提取链接（支持多种格式）
    
    Args:
        text: 文本内容
        
    Returns:
        str: 提取的链接（第一个有效链接）
    """
    if not text:
        return ''
    
    # 查找URL（支持 http:// 和 https://）
    url_pattern = r'https?://[^\s\)]+'
    urls = re.findall(url_pattern, text)
    
    if urls:
        # 返回第一个有效链接（移除可能的尾随标点）
        link = urls[0].rstrip('.,;!?)')
        # 验证链接格式
        if re.match(r'https?://.+', link):
            return link
    
    return ''


def validate_and_normalize_message_info(message_info: dict) -> dict:
    """
    验证和规范化消息信息（处理不规范数据）
    
    Args:
        message_info: 原始消息信息字典
        
    Returns:
        dict: 规范化后的消息信息字典
        
    Raises:
        ValueError: 如果消息ID缺失或数据格式严重错误
    """
    if not message_info or not isinstance(message_info, dict):
        raise ValueError("消息信息必须是字典类型")
    
    normalized = message_info.copy()
    warnings = []
    
    # 验证和清理 message_id
    message_id = normalized.get('message_id')
    if not message_id:
        raise ValueError("消息ID缺失")
    
    # 确保 message_id 是整数
    try:
        normalized['message_id'] = int(message_id)
    except (ValueError, TypeError):
        raise ValueError(f"消息ID格式无效: {message_id}")
    
    # 验证和清理标题
    title = normalized.get('title')
    if not title or not isinstance(title, str) or len(title.strip()) < 2:
        # 如果没有标题，尝试从其他字段生成
        caption = normalized.get('caption') or ''
        filename = normalized.get('filename') or ''
        if not isinstance(caption, str):
            caption = str(caption) if caption else ''
        if not isinstance(filename, str):
            filename = str(filename) if filename else ''
        
        title = extract_title_from_text(caption, filename)
        if not title:
            # 最后的默认值
            if filename:
                title = filename
            elif caption:
                title = caption[:30] + '...' if len(caption) > 30 else caption
            else:
                title = f"消息 {normalized['message_id']}"
            warnings.append(f"标题缺失，已自动生成: {title[:50]}")
    
    normalized['title'] = clean_text(str(title) if title else '', MAX_TITLE_LENGTH)
    
    # 验证和清理标签
    tags = normalized.get('tags') or ''
    if not isinstance(tags, str):
        tags = str(tags) if tags else ''
    normalized['tags'] = clean_text(tags, MAX_TAGS_LENGTH)
    
    # 验证和清理链接
    link = normalized.get('link') or ''
    if link:
        if not isinstance(link, str):
            link = str(link)
        # 验证链接格式
        if not re.match(r'https?://.+', link):
            warnings.append(f"链接格式无效，已清除: {link[:50]}")
            normalized['link'] = ''
        else:
            normalized['link'] = link[:500]  # 限制链接长度
    else:
        normalized['link'] = ''
    
    # 验证和清理说明
    note = normalized.get('note') or ''
    if not isinstance(note, str):
        note = str(note) if note else ''
    normalized['note'] = clean_text(note, MAX_NOTE_LENGTH)
    
    # 验证和清理 caption
    caption = normalized.get('caption') or ''
    if not isinstance(caption, str):
        caption = str(caption) if caption else ''
    normalized['caption'] = clean_text(caption, MAX_CAPTION_LENGTH)
    
    # 验证和清理文件名
    filename = normalized.get('filename') or ''
    if filename:
        if not isinstance(filename, str):
            filename = str(filename)
        # 移除路径分隔符（防止路径注入）
        filename = filename.replace('/', '_').replace('\\', '_')
        # 移除控制字符
        filename = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', filename)
        normalized['filename'] = clean_text(filename, MAX_FILENAME_LENGTH)
    else:
        normalized['filename'] = ''
    
    # 验证内容类型
    content_type = normalized.get('content_type', 'text')
    if not isinstance(content_type, str):
        content_type = str(content_type) if content_type else 'text'
    valid_types = ['text', 'media', 'document', 'mixed']
    if content_type not in valid_types:
        warnings.append(f"无效的内容类型: {content_type}，已设置为 'text'")
        normalized['content_type'] = 'text'
    else:
        normalized['content_type'] = content_type
    
    # 验证发布时间
    publish_time = normalized.get('publish_time')
    if not publish_time:
        normalized['publish_time'] = datetime.now()
        warnings.append("发布时间缺失，已设置为当前时间")
    elif isinstance(publish_time, datetime):
        # 已经是 datetime 对象，直接使用
        normalized['publish_time'] = publish_time
    else:
        try:
            if isinstance(publish_time, (int, float)):
                normalized['publish_time'] = datetime.fromtimestamp(float(publish_time))
            elif isinstance(publish_time, str):
                # 尝试解析字符串格式的时间
                try:
                    normalized['publish_time'] = datetime.fromisoformat(publish_time.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    normalized['publish_time'] = datetime.now()
                    warnings.append("发布时间字符串格式无效，已设置为当前时间")
            else:
                normalized['publish_time'] = datetime.now()
                warnings.append("发布时间格式无效，已设置为当前时间")
        except (ValueError, OSError, OverflowError) as e:
            normalized['publish_time'] = datetime.now()
            warnings.append(f"发布时间解析失败: {e}，已设置为当前时间")
    
    # 验证用户信息
    user_id = normalized.get('user_id')
    if not user_id:
        normalized['user_id'] = 0
    else:
        try:
            normalized['user_id'] = int(user_id)
        except (ValueError, TypeError):
            warnings.append(f"用户ID格式无效: {user_id}，已设置为 0")
            normalized['user_id'] = 0
    
    username = normalized.get('username')
    if not username or not isinstance(username, str):
        normalized['username'] = 'channel'
    else:
        normalized['username'] = str(username).strip() or 'channel'
    
    # 记录警告
    if warnings:
        logger.warning(f"消息 {normalized['message_id']} 数据不规范: {'; '.join(warnings)}")
    
    return normalized


async def extract_message_info(message):
    """
    从消息对象中提取信息（容错处理，支持不规范消息）
    
    Args:
        message: Telegram 消息对象
        
    Returns:
        dict: 包含提取信息的字典
    """
    try:
        # 安全获取消息ID
        message_id = getattr(message, 'message_id', None)
        if not message_id:
            raise ValueError("消息ID缺失")
        
        # 安全获取文本内容
        caption = getattr(message, 'caption', None) or ''
        text = getattr(message, 'text', None) or ''
        full_text = caption or text or ''
        
        # 安全获取日期
        date = getattr(message, 'date', None)
        publish_time = date if date else datetime.now()
        
        info = {
            'message_id': message_id,
            'media_list': [],
            'doc_list': [],
            'content_type': 'text',
            'caption': full_text,
            'tags': '',
            'title': '',
            'link': '',
            'note': '',
            'filename': '',
            'publish_time': publish_time,
            'user_id': 0,  # 频道消息没有用户ID
            'username': 'channel',  # 频道消息默认用户名
            'related_message_ids': []  # 用于媒体组
        }
        
        # 提取文本信息
        if full_text and isinstance(full_text, str):
            info['tags'] = extract_tags_from_text(full_text)
            info['link'] = extract_link_from_text(full_text)
            # note 就是完整的 caption（去掉标签和链接后）
            note_text = re.sub(r'#\w+', '', full_text)
            note_text = re.sub(r'https?://[^\s]+', '', note_text)
            note_text = note_text.strip()
            info['note'] = note_text if note_text else ''
        else:
            info['tags'] = ''
            info['link'] = ''
            info['note'] = ''
        
        # 提取媒体信息（容错处理）
        filename_candidates = []
        
        try:
            if hasattr(message, 'photo') and message.photo:
                # 获取最大尺寸的照片
                photo = message.photo[-1]
                file_id = getattr(photo, 'file_id', None)
                if file_id:
                    info['media_list'].append(f"photo:{file_id}")
                    info['content_type'] = 'media'
        except Exception as e:
            logger.warning(f"提取照片信息失败: {e}")
        
        try:
            if hasattr(message, 'video') and message.video:
                file_id = getattr(message.video, 'file_id', None)
                if file_id:
                    info['media_list'].append(f"video:{file_id}")
                    info['content_type'] = 'media'
                file_name = getattr(message.video, 'file_name', None)
                if file_name:
                    filename_candidates.append(file_name)
        except Exception as e:
            logger.warning(f"提取视频信息失败: {e}")
        
        try:
            if hasattr(message, 'animation') and message.animation:
                file_id = getattr(message.animation, 'file_id', None)
                if file_id:
                    info['media_list'].append(f"animation:{file_id}")
                    info['content_type'] = 'media'
        except Exception as e:
            logger.warning(f"提取动画信息失败: {e}")
        
        try:
            if hasattr(message, 'audio') and message.audio:
                file_id = getattr(message.audio, 'file_id', None)
                if file_id:
                    info['media_list'].append(f"audio:{file_id}")
                    info['content_type'] = 'media'
                file_name = getattr(message.audio, 'file_name', None)
                if file_name:
                    filename_candidates.append(file_name)
                # 音频可能还有标题和表演者
                title = getattr(message.audio, 'title', None)
                performer = getattr(message.audio, 'performer', None)
                if title and not info['title']:
                    info['title'] = title
                    if performer:
                        info['title'] = f"{performer} - {title}"
        except Exception as e:
            logger.warning(f"提取音频信息失败: {e}")
        
        try:
            if hasattr(message, 'document') and message.document:
                file_id = getattr(message.document, 'file_id', None)
                file_name = getattr(message.document, 'file_name', None) or '未知文件'
                if file_id:
                    info['doc_list'].append(f"document:{file_id}:{file_name}")
                    info['content_type'] = 'document'
                    filename_candidates.append(file_name)
        except Exception as e:
            logger.warning(f"提取文档信息失败: {e}")
        
        # 处理文件名（优先使用第一个找到的）
        if filename_candidates and not info['filename']:
            info['filename'] = filename_candidates[0]
        
        # 如果没有文本但有媒体，尝试从文件名提取标题
        if not info['title'] and info['filename']:
            info['title'] = extract_title_from_text('', info['filename'])
        
        # 如果还是没有标题，使用默认值（稍后在验证时处理）
        if not info['title'] and full_text:
            info['title'] = extract_title_from_text(full_text, info['filename'])
        
        # 处理混合类型
        if info['media_list'] and info['doc_list']:
            info['content_type'] = 'mixed'
        elif not info['media_list'] and not info['doc_list'] and full_text:
            info['content_type'] = 'text'
        
        # 处理媒体组（media_group_id）
        try:
            if hasattr(message, 'media_group_id') and message.media_group_id:
                info['related_message_ids'] = [message_id]
        except Exception as e:
            logger.warning(f"提取媒体组信息失败: {e}")
        
        # 处理转发消息
        try:
            if hasattr(message, 'forward_from_chat') and message.forward_from_chat:
                # 转发自其他频道/群组
                chat_title = getattr(message.forward_from_chat, 'title', None) or ''
                if chat_title:
                    info['note'] = f"转发自: {chat_title}\n{info['note']}"
            elif hasattr(message, 'forward_from') and message.forward_from:
                # 转发自用户
                username = getattr(message.forward_from, 'username', None)
                user_id = getattr(message.forward_from, 'id', None)
                if username:
                    info['note'] = f"转发自: @{username}\n{info['note']}"
                elif user_id:
                    info['note'] = f"转发自: user{user_id}\n{info['note']}"
        except Exception as e:
            logger.warning(f"提取转发信息失败: {e}")
        
        return info
        
    except Exception as e:
        logger.error(f"提取消息信息时发生错误: {e}", exc_info=True)
        # 返回最小可用信息
        return {
            'message_id': getattr(message, 'message_id', 0),
            'media_list': [],
            'doc_list': [],
            'content_type': 'text',
            'caption': '',
            'tags': '',
            'title': f"消息 {getattr(message, 'message_id', 'unknown')}",
            'link': '',
            'note': '',
            'filename': '',
            'publish_time': datetime.now(),
            'user_id': 0,
            'username': 'channel',
            'related_message_ids': []
        }


async def save_channel_message(message_info: dict):
    """
    保存频道消息到数据库和搜索索引
    
    Args:
        message_info: 消息信息字典
        
    Returns:
        bool: 是否成功保存
    """
    message_id = None
    post_id = None
    
    try:
        # 验证必要字段
        message_id = message_info.get('message_id')
        if not message_id:
            logger.error("消息ID缺失，无法保存")
            return False
        
        # 准备数据（使用安全的默认值）
        media_list = message_info.get('media_list', [])
        doc_list = message_info.get('doc_list', [])
        content_type = message_info.get('content_type', 'text')
        
        # 验证列表类型
        if not isinstance(media_list, list):
            logger.warning(f"media_list 不是列表类型: {type(media_list)}，使用空列表")
            media_list = []
        if not isinstance(doc_list, list):
            logger.warning(f"doc_list 不是列表类型: {type(doc_list)}，使用空列表")
            doc_list = []
        
        # 安全地序列化 JSON
        try:
            file_ids_data = media_list if media_list else doc_list
            file_ids = json.dumps(file_ids_data, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            logger.warning(f"序列化 file_ids 失败: {e}，使用空列表")
            file_ids = '[]'
        except Exception as e:
            logger.error(f"序列化 file_ids 时发生意外错误: {e}，使用空列表")
            file_ids = '[]'
        
        tags = message_info.get('tags', '')
        title = message_info.get('title', '')
        link = message_info.get('link', '')
        note = message_info.get('note', '')
        caption = message_info.get('caption', '')
        filename = message_info.get('filename', '')
        publish_time = message_info.get('publish_time', datetime.now())
        user_id = message_info.get('user_id', 0)
        username = message_info.get('username', 'channel') or 'channel'
        related_ids_json = None
        
        # 处理相关消息ID
        related_ids = message_info.get('related_message_ids', [])
        if related_ids:
            try:
                related_ids_json = json.dumps(related_ids)
            except (TypeError, ValueError) as e:
                logger.warning(f"序列化 related_message_ids 失败: {e}")
                related_ids_json = None
        
        # 转换发布时间为时间戳
        if isinstance(publish_time, datetime):
            publish_timestamp = publish_time.timestamp()
        elif isinstance(publish_time, (int, float)):
            publish_timestamp = float(publish_time)
        else:
            logger.warning(f"发布时间格式无效: {publish_time}，使用当前时间")
            publish_timestamp = datetime.now().timestamp()
        
        # 在单个事务中完成检查和插入
        try:
            async with get_db() as conn:
                cursor = await conn.cursor()
                
                # 检查是否已存在（使用 SELECT FOR UPDATE 防止并发问题）
                await cursor.execute(
                    "SELECT message_id FROM published_posts WHERE message_id = ?",
                    (message_id,)
                )
                if await cursor.fetchone():
                    logger.debug(f"消息 {message_id} 已存在，跳过")
                    return False
                
                # 插入新记录（使用参数化查询防止 SQL 注入）
                try:
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
                        publish_timestamp,
                        datetime.now().timestamp(),
                        related_ids_json
                    ))
                    post_id = cursor.lastrowid
                    # 注意：get_db() 上下文管理器会自动 commit，不需要手动 commit
                    logger.info(f"已保存频道消息 {message_id} (post_id: {post_id}) 到数据库")
                except Exception as db_error:
                    logger.error(f"插入数据库记录失败 (message_id: {message_id}): {db_error}", exc_info=True)
                    # 尝试插入最小记录（只包含必要字段）
                    try:
                        await cursor.execute("""
                            INSERT INTO published_posts 
                            (message_id, user_id, username, title, publish_time, last_update)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            message_id,
                            user_id,
                            username,
                            f"消息 {message_id}",
                            publish_timestamp,
                            datetime.now().timestamp()
                        ))
                        post_id = cursor.lastrowid
                        logger.warning(f"已保存频道消息 {message_id} 的最小记录 (post_id: {post_id})")
                    except Exception as fallback_error:
                        logger.error(f"保存最小记录也失败 (message_id: {message_id}): {fallback_error}", exc_info=True)
                        return False
        except Exception as conn_error:
            logger.error(f"数据库连接错误 (message_id: {message_id}): {conn_error}", exc_info=True)
            return False
        
        # 添加到搜索索引（独立处理，失败不影响数据库保存）
        try:
            search_engine = get_search_engine()
            
            # 先删除索引中可能存在的旧记录（确保索引和数据库一致）
            # 这可以处理以下情况：
            # 1. 删除操作标记为已删除，但索引删除失败
            # 2. 频道消息仍然存在，频道监听器重新保存到数据库
            # 3. 需要确保索引中不存在旧记录，避免重复或冲突
            try:
                search_engine.delete_post(int(message_id))
                logger.debug(f"已清理索引中可能存在的旧记录: {message_id}")
            except Exception as delete_err:
                # 删除失败可能是正常的（记录不存在），只记录调试信息
                logger.debug(f"清理索引旧记录时未找到记录（正常）: {message_id}, {delete_err}")
            
            # 确保 publish_time 是 datetime 对象
            if isinstance(publish_time, datetime):
                publish_dt = publish_time
            elif isinstance(publish_time, (int, float)):
                publish_dt = datetime.fromtimestamp(float(publish_time))
            else:
                publish_dt = datetime.now()
            
            post_doc = PostDocument(
                message_id=message_id,
                post_id=post_id,
                title=title,
                description=note,
                tags=tags,
                filename=filename,
                link=link,
                user_id=user_id,
                username=username,
                publish_time=publish_dt,
                views=0,
                heat_score=0
            )
            
            search_engine.add_post(post_doc)
            logger.info(f"已添加频道消息 {message_id} (post_id: {post_id}) 到搜索索引")
            
        except Exception as e:
            # 索引失败不影响整体流程，只记录错误
            logger.error(f"添加到搜索索引失败（消息已保存到数据库）: {e}", exc_info=True)
        
        return True
            
    except Exception as e:
        logger.error(f"保存频道消息失败 (message_id: {message_id}): {e}", exc_info=True)
        return False


async def delete_channel_post_from_db(message_id: int, context: CallbackContext = None):
    """
    标记频道消息为已删除（保留历史数据）并从搜索索引中删除
    
    Args:
        message_id: 频道消息ID
        context: 回调上下文（可选，用于获取 bot 实例）
        
    Returns:
        bool: 是否成功标记为已删除
    """
    try:
        from database.db_manager import get_db
        from utils.search_engine import get_search_engine
        
        async with get_db() as conn:
            cursor = await conn.cursor()
            
            # 根据 message_id 获取帖子信息
            await cursor.execute(
                "SELECT rowid AS post_id, message_id, related_message_ids FROM published_posts WHERE message_id=?",
                (int(message_id),)
            )
            post_row = await cursor.fetchone()
            
            if not post_row:
                logger.debug(f"消息 {message_id} 不在数据库中，无需删除")
                return False
            
            post_id = post_row['post_id']
            related_ids_json = post_row['related_message_ids']
            
            # 从搜索索引中删除
            try:
                search_engine = get_search_engine()
                if search_engine:
                    search_engine.delete_post(int(message_id))
                    logger.info(f"已从搜索索引删除帖子: {message_id}")
                    
                    # 如果有关联消息，也从索引删除
                    if related_ids_json:
                        try:
                            related_ids = json.loads(related_ids_json)
                            for related_id in related_ids:
                                search_engine.delete_post(related_id)
                            logger.info(f"已从索引删除 {len(related_ids)} 个关联消息")
                        except json.JSONDecodeError:
                            logger.warning(f"解析关联消息ID失败: {related_ids_json}")
            except Exception as e:
                logger.error(f"从搜索索引删除失败: {e}")
                # 继续执行，不因索引删除失败而中断
            
            # 标记为已删除而不是直接删除记录（保留历史数据）
            await cursor.execute("UPDATE published_posts SET is_deleted = 1 WHERE rowid=?", (post_id,))
            await conn.commit()
            logger.info(f"已标记帖子为已删除: ID={post_id}, message_id={message_id}")
            
            return True
            
    except Exception as e:
        logger.error(f"删除频道消息失败 (message_id: {message_id}): {e}", exc_info=True)
        return False


async def check_and_handle_deleted_message(message_id: int, context: CallbackContext):
    """
    检查消息是否被删除，如果被删除则标记为已删除（保留历史数据）
    
    通过尝试转发消息来检查消息是否存在。如果消息不存在（被删除），
    会抛出异常，此时标记为已删除（is_deleted = 1），保留历史数据。
    
    Args:
        message_id: 频道消息ID
        context: 回调上下文
        
    Returns:
        bool: 如果消息被删除并成功标记为已删除，返回 True；否则返回 False
    """
    try:
        from config.settings import CHANNEL_ID, OWNER_ID
        from telegram.error import BadRequest, TelegramError

        # 转发目标不能是机器人自己：Telegram 禁止 bot 向 bot（包括自身）发消息，
        # 必然 Forbidden，导致本检查成为永远失败的空转。
        # 改为转发到 OWNER 的私聊（不可见噪音最小）；未配置 OWNER 时无法检查。
        if not OWNER_ID:
            logger.debug("未配置 OWNER_ID，跳过消息存在性检查")
            return False

        # 尝试通过转发消息来检查消息是否存在
        # 如果消息不存在（被删除），会抛出 BadRequest 异常
        try:
            forwarded_msg = await context.bot.forward_message(
                chat_id=OWNER_ID,
                from_chat_id=CHANNEL_ID,
                message_id=message_id
            )

            # 如果成功转发，说明消息存在，立即删除转发的消息以减少副作用
            try:
                await context.bot.delete_message(
                    chat_id=OWNER_ID,
                    message_id=forwarded_msg.message_id
                )
            except Exception as e:
                # 删除失败不影响检查结果，只记录警告
                logger.debug(f"删除检查用的转发消息失败: {e}")

            # 消息存在，返回 False
            return False

        except BadRequest as e:
            error_msg = str(e).lower()

            # 只有明确表明"消息不存在"的错误才能判定为已删除。
            # 注意排除 "chat not found"（OWNER_ID 错误/未启动机器人）
            # 与 "bot was blocked" 等环境问题——这些情况下绝不能批量标记删除。
            message_missing = (
                'message to forward not found' in error_msg
                or ('message not found' in error_msg and 'chat not found' not in error_msg)
                or 'message id invalid' in error_msg
            )
            if message_missing:
                logger.info(f"检测到频道消息 {message_id} 已被删除，开始标记为已删除")
                return await delete_channel_post_from_db(message_id, context)
            else:
                # 其他 BadRequest 错误（可能是权限问题等），记录但不删除
                logger.debug(f"检查消息 {message_id} 时出错（可能是权限问题）: {error_msg}")
                return False

        except TelegramError as e:
            # 其他 Telegram 错误（网络问题等），记录但不删除
            logger.debug(f"检查消息 {message_id} 时出错（可能是网络问题）: {e}")
            return False

    except Exception as e:
        logger.error(f"检查删除消息时出错 (message_id: {message_id}): {e}", exc_info=True)
        return False


async def check_deleted_messages_periodic(context: CallbackContext):
    """
    定期检查数据库中的消息是否仍然存在于频道中
    如果消息已被删除，则标记为已删除（保留历史数据）并从搜索索引中删除
    
    Args:
        context: 回调上下文
    """
    try:
        from database.db_manager import get_db
        
        logger.info("开始定期检查已删除的频道消息...")
        
        async with get_db() as conn:
            cursor = await conn.cursor()
            
            # 获取所有未删除的消息ID（限制检查数量，避免一次性检查太多）
            await cursor.execute(
                "SELECT message_id FROM published_posts WHERE is_deleted = 0 ORDER BY rowid DESC LIMIT 100"
            )
            rows = await cursor.fetchall()
            
            if not rows:
                logger.debug("没有需要检查的消息")
                return
            
            logger.info(f"检查 {len(rows)} 条消息是否存在...")
            deleted_count = 0
            
            for row in rows:
                message_id = row['message_id']
                try:
                    # 检查消息是否被删除
                    deleted = await check_and_handle_deleted_message(message_id, context)
                    if deleted:
                        deleted_count += 1
                    
                    # 添加小延迟，避免请求过快
                    await asyncio.sleep(0.5)
                    
                except Exception as e:
                    logger.warning(f"检查消息 {message_id} 时出错: {e}")
                    continue
            
            if deleted_count > 0:
                logger.info(f"定期检查完成：发现并标记了 {deleted_count} 条已删除的消息")
            else:
                logger.debug("定期检查完成：未发现已删除的消息")
                
    except Exception as e:
        logger.error(f"定期检查已删除消息时出错: {e}", exc_info=True)


async def handle_channel_message(update: Update, context: CallbackContext):
    """
    处理频道消息
    
    Args:
        update: Telegram 更新对象
        context: 回调上下文
    """
    message = None
    message_id = None
    
    try:
        # 记录所有接收到的更新（用于调试）
        logger.debug(f"handle_channel_message 被调用: update_id={update.update_id}, has_channel_post={update.channel_post is not None}, has_edited_channel_post={update.edited_channel_post is not None}")
        
        # 只处理频道消息
        if not update.channel_post and not update.edited_channel_post:
            logger.debug(f"更新 {update.update_id} 不是频道消息，跳过")
            return
        
        message = update.channel_post or update.edited_channel_post
        logger.info(f"📨 开始处理频道消息: update_id={update.update_id}")
        
        # 安全获取消息ID
        if not message:
            logger.warning("消息对象为空")
            return
        
        message_id = getattr(message, 'message_id', None)
        if not message_id:
            logger.warning("无法获取消息ID")
            return
        
        # 并发控制：检查是否正在处理
        async with _processing_lock:
            if message_id in _processing_messages:
                logger.debug(f"消息 {message_id} 正在处理中，跳过")
                return
            _processing_messages.add(message_id)
        
        try:
            # 验证消息来源（通过 chat_id 或 username）
            is_valid = False
            try:
                if CHANNEL_ID.startswith('@'):
                    # 对于 @username 格式，通过 chat.username 验证
                    if hasattr(message, 'chat') and message.chat:
                        chat_username = getattr(message.chat, 'username', None)
                        if chat_username:
                            is_valid = chat_username.lower() == CHANNEL_ID.lstrip('@').lower()
                        else:
                            # 如果没有 username，尝试通过 chat.id 验证（需要先获取频道信息）
                            try:
                                chat = await context.bot.get_chat(CHANNEL_ID)
                                is_valid = message.chat.id == chat.id
                            except Exception as e:
                                logger.warning(f"无法验证频道ID: {e}")
                                # 如果无法验证，默认接受（由 filters.Chat 过滤）
                                is_valid = True
                    else:
                        logger.warning("消息缺少 chat 对象")
                        is_valid = False
                else:
                    # 对于数字ID格式，直接比较
                    try:
                        channel_id_int = int(CHANNEL_ID)
                        if hasattr(message, 'chat') and message.chat:
                            is_valid = message.chat.id == channel_id_int
                        else:
                            is_valid = False
                    except ValueError:
                        logger.warning(f"无法解析 CHANNEL_ID: {CHANNEL_ID}")
                        # 如果无法解析，默认接受（由 filters.Chat 过滤）
                        is_valid = True
            except Exception as e:
                logger.error(f"验证频道来源时出错: {e}", exc_info=True)
                is_valid = False
            
            if not is_valid:
                logger.debug(f"消息来自其他频道，跳过: {getattr(message.chat, 'id', 'unknown') if hasattr(message, 'chat') else 'unknown'}")
                return
            
            logger.info(f"收到频道消息: {message_id}")
            
            # 如果是编辑消息，先检查消息是否仍然存在（防止消息被删除后仍收到编辑事件）
            if update.edited_channel_post:
                try:
                    # 检查消息是否被删除
                    deleted = await check_and_handle_deleted_message(message_id, context)
                    if deleted:
                        logger.info(f"编辑消息 {message_id} 已被删除，已从数据库清除")
                        return
                except Exception as e:
                    logger.warning(f"检查编辑消息 {message_id} 是否被删除时出错: {e}")
                    # 继续处理，不因检查失败而中断
            
            # 提取消息信息
            message_info = await extract_message_info(message)
            
            # 验证 message_info 不为空
            if not message_info or not isinstance(message_info, dict):
                logger.error(f"提取的消息信息无效: {message_info}")
                return
            
            # 验证和规范化消息信息（处理不规范数据）
            try:
                message_info = validate_and_normalize_message_info(message_info)
            except Exception as e:
                logger.error(f"验证消息信息失败: {e}", exc_info=True)
                # 即使验证失败，也尝试保存（使用默认值）
                logger.warning(f"使用默认值保存消息 {message_info.get('message_id', 'unknown')}")
                # 确保 message_id 存在
                if not message_info.get('message_id'):
                    message_info['message_id'] = message_id
            
            # 保存到数据库
            success = await save_channel_message(message_info)
            
            if success:
                logger.info(f"频道消息 {message_id} 处理完成")
            else:
                logger.warning(f"频道消息 {message_id} 处理失败或已存在")
        
        finally:
            # 移除处理标记
            async with _processing_lock:
                _processing_messages.discard(message_id)
        
    except Exception as e:
        logger.error(f"处理频道消息失败 (message_id: {message_id}): {e}", exc_info=True)
        # 确保清理处理标记
        if message_id:
            async with _processing_lock:
                _processing_messages.discard(message_id)

