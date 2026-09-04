"""
工具函数模块
"""
import re
import json
import html as _html
import unicodedata
import asyncio
import logging
from functools import lru_cache, wraps
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ConversationHandler, CallbackContext

from config.settings import ALLOWED_TAGS, NET_TIMEOUT, SHOW_SUBMITTER
from database.db_manager import get_db

logger = logging.getLogger(__name__)

# 标签分割正则表达式：逗号/空白/中文逗号，以及会让 Telegram hashtag 失效的斜杠
TAG_SPLIT_PATTERN = re.compile(r'[,，\s/／]+')
# Telegram hashtag 只接受 Unicode 字母/组合标记/数字/下划线；用 Unicode
# category 判定，不能用一个宽泛的非 ASCII 范围（那会误收 emoji 与全角标点）。

# 配置常量
CONFIG = {
    "VERSION": "2.10.39",
    "MAX_MEDIA_COUNT": 10,
    "MAX_DOCUMENT_COUNT": 10,
    "NET_TIMEOUT": 30,  # 网络超时时间（秒）
    "MAX_RETRIES": 3,   # 最大重试次数
    "RETRY_DELAY": 2,   # 重试延迟（秒）
}

@lru_cache(maxsize=128)
def process_tags(raw_tags: str) -> tuple:
    """
    处理标签字符串

    Telegram 的 hashtag 规则：# 后只能跟字母、数字、下划线和非 ASCII 字母
    （中文/日文假名/韩文/西里尔文等）。包含连字符、斜杠、括号等字符的
    标签不会被客户端解析（#r-18 只会显示为 #r）。这里统一做：

    1. 按逗号/空白/斜杠拆分（pixivflow 的 workTags 用逗号传入）；
    2. 去掉开头的 # 与所有非法字符；
    3. 常见 NSFW 标记归一（r-18/r18→r18，r-18g→r18g）；
    4. 去重保序，统一补回 #。

    Args:
        raw_tags: 原始标签字符串

    Returns:
        tuple: (成功标志, 处理后的标签字符串)
    """
    try:
        # 先归一常见 NSFW 标记；必须在按空白分词前做，才能覆盖 "R 18"。
        normalized = re.sub(
            r'(?<!\w)r[\s_-]*18(g?)(?!\w)',
            lambda match: 'r18g' if match.group(1) else 'r18',
            str(raw_tags),
            flags=re.IGNORECASE,
        )
        tags = [
            t.strip().casefold()
            for t in TAG_SPLIT_PATTERN.split(normalized)
            if t.strip()
        ]

        def sanitize(part: str) -> str:
            part = part.lstrip('#')
            return ''.join(
                char
                for char in part
                if char == '_' or unicodedata.category(char)[0] in {'L', 'M', 'N'}
            )

        processed = []
        seen = set()
        for tag in tags:
            clean_tag = sanitize(tag)
            if clean_tag and clean_tag not in seen:  # 去重并保留来源顺序
                seen.add(clean_tag)
                processed.append(f"#{clean_tag}")
                if len(processed) >= ALLOWED_TAGS:
                    break

        # 单个标签长度上限 30 字符（保留原有约束）
        processed = [tag[:31] for tag in processed]

        # 使用空格拼接标签，得到正确的格式
        return True, ' '.join(processed)
    except Exception as e:
        logger.error(f"标签处理错误: {e}")
        return False, ""

def escape_markdown(text: str) -> str:
    """
    转义 MarkdownV2 中的特殊字符。
    注意：这与 HTML 转义无关；频道 caption 使用 parse_mode=HTML，
    用户输入进入 caption 前应使用 html.escape（见 build_caption）。
    
    Args:
        text: 需要转义的文本
        
    Returns:
        str: 转义后的文本
    """
    escape_chars = r'\_*[]()~>#+-=|{}.!'
    return ''.join(f"\\{c}" if c in escape_chars else c for c in text)

# 截断 HTML 文本时避免把转义实体（如 &amp;）从中间切断
def _trim_html_entities(text: str, limit: int) -> str:
    """截断到 limit 长度；若末尾留下未闭合的转义实体则回退到实体边界"""
    if len(text) <= limit:
        return text
    text = text[:limit]
    amp = text.rfind("&")
    semi = text.rfind(";")
    if amp > semi:  # 存在未闭合的 &...（其后没有分号）
        text = text[:amp]
    return text


def build_caption(data) -> str:
    """
    构建媒体说明文本。
    所有用户输入字段都会做 HTML 转义（caption 以 parse_mode="HTML" 发送），
    否则包含 <、>、& 的投稿会导致 Telegram 解析失败，投稿无法发布。
    
    Args:
        data: 包含投稿信息的数据对象
        
    Returns:
        str: 格式化的说明文本（已转义，长度不超过 Telegram 上限）
    """
    MAX_CAPTION_LENGTH = 1024  # Telegram 的最大 caption 长度

    def esc(value) -> str:
        """转义并保证输入为字符串；异常数据退化为空串，确保 caption 总能构建"""
        try:
            return _html.escape(str(value))
        except Exception:
            return ""

    def get_link_part(link: str) -> str:
        return f"🔗 链接： {esc(link)}" if link else ""
    
    def get_title_part(title: str) -> str:
        return f"🔖 标题： \n【{esc(title)}】" if title else ""
    
    def get_note_part(note: str) -> str:
        # "简介"部分要求第一行为标签，后面跟内容
        return f"📝 简介：\n{esc(note)}" if note else ""
    
    def get_tags_part(tags: str) -> str:
        return f"🏷 Tags: {esc(tags)}" if tags else ""
    
    def get_spoiler_part(spoiler: str) -> str:
        return "⚠️点击查看⚠️" if spoiler.lower() == "true" else ""
    
    def get_submitter_part(user_id: int) -> str:
        if not SHOW_SUBMITTER:
            return ""

        # 匿名投稿：频道内不展示投稿人
        try:
            if "anonymous" in data.keys() and (data["anonymous"] or "false") == "true":
                return ""
        except (KeyError, TypeError, IndexError):
            pass

        # 获取保存的用户名，如果存在的话
        # 注意：对 sqlite3.Row 使用 "col" in data 判断的是"值"是否相等（几乎恒为 False），
        # 必须用 data.keys() 判断列是否存在
        try:
            username = data["username"] if "username" in data.keys() else f"user{user_id}"
            if not username:
                username = f"user{user_id}"
        except (KeyError, TypeError, IndexError):
            username = f"user{user_id}"
        
        # 构建用户链接，可以通过点击访问用户资料（用户名需转义）
        safe_username = esc(str(username)) if username else f"user{user_id}"
        return f"\n\n投稿人：<a href=\"tg://user?id={user_id}\">@{safe_username}</a>"

    # 收集各部分，只有内容不为空时才添加，避免产生多余的换行
    parts = []
    
    # 初始化变量，防止后续引用时未定义
    link = ""
    title = ""
    note = ""
    tags = ""
    
    # 安全获取属性，防止访问不存在的键
    try:
        link = get_link_part(data["link"] if data["link"] else "")
        if link:
            parts.append(link)
    except (KeyError, TypeError):
        link = ""

    try:
        title = get_title_part(data["title"] if data["title"] else "")
        if title:
            parts.append(title)
    except (KeyError, TypeError):
        title = ""

    try:
        note = get_note_part(data["note"] if data["note"] else "")
        if note:
            parts.append(note)
    except (KeyError, TypeError):
        note = ""

    try:
        tags = get_tags_part(data["tags"] if data["tags"] else "")
        if tags:
            parts.append(tags)
    except (KeyError, TypeError):
        tags = ""
    
    # 将各部分按换行符连接，避免空值带来多余换行
    caption_body = "\n".join(parts)
    
    try:
        spoiler = get_spoiler_part(data["spoiler"] if data["spoiler"] else "false")
    except (KeyError, TypeError):
        spoiler = ""
    
    # 添加投稿人信息（如果启用）
    try:
        submitter = get_submitter_part(data["user_id"])
    except (KeyError, TypeError):
        submitter = ""
    
    # 如果存在正文内容且有剧透提示，则剧透提示单独占一行
    if caption_body:
        full_caption = f"{spoiler}\n{caption_body}{submitter}" if spoiler else f"{caption_body}{submitter}"
    else:
        full_caption = f"{spoiler}{submitter}" if submitter else spoiler

    # 如果整体长度在允许范围内，则直接返回
    if len(full_caption) <= MAX_CAPTION_LENGTH:
        return full_caption

    # 超长情况：保留投稿人信息，尝试截断 note 部分（其他部分保持不变）
    fixed_parts = []
    if link:
        fixed_parts.append(link)
    if title:
        fixed_parts.append(title)
    if tags:
        fixed_parts.append(tags)
    fixed_text = "\n".join(fixed_parts)
    
    # 预留剧透提示、投稿人信息和固定部分所占长度以及连接换行符
    prefix = f"{spoiler}\n" if spoiler and fixed_text else spoiler
    # 计算可用长度（要为投稿人信息预留空间）
    connector = "\n" if fixed_text and note else ""
    available_length = MAX_CAPTION_LENGTH - len(prefix) - len(fixed_text) - len(connector) - len(submitter)
    
    truncated_note_part = ""
    try:
        raw_note = data["note"] if (available_length > 0 and data["note"]) else ""
        if raw_note:
            # 先按可用长度截原始文本，再转义；若转义后超限则逐步收缩
            candidate = raw_note[:available_length]
            escaped_candidate = esc(candidate + "...") if len(raw_note) > available_length else esc(candidate)
            while len(escaped_candidate) > max(0, available_length - 5):
                candidate = candidate[:-max(1, len(escaped_candidate) // 4)]
                if not candidate:
                    escaped_candidate = ""
                    break
                escaped_candidate = esc(candidate + "...")
            truncated_note = candidate + ("..." if len(raw_note) > len(candidate) and candidate else "")
            truncated_note_part = get_note_part(truncated_note)
    except (KeyError, TypeError):
        truncated_note_part = ""
    
    # 重新组装各部分
    parts = []
    if link:
        parts.append(link)
    if title:
        parts.append(title)
    if truncated_note_part:
        parts.append(truncated_note_part)
    if tags:
        parts.append(tags)
    caption_body = "\n".join(parts)
    full_caption = f"{spoiler}\n{caption_body}{submitter}" if spoiler and caption_body else f"{spoiler or caption_body}{submitter}"

    # 硬上限兜底：使用实体安全截断，避免切坏 &amp; 之类的转义序列
    return _trim_html_entities(full_caption, MAX_CAPTION_LENGTH)

def validate_state(expected_state: int):
    """
    验证会话状态装饰器
    
    Args:
        expected_state: 期望的状态值
        
    Returns:
        装饰器函数
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: CallbackContext):
            user_id = update.effective_user.id
            try:
                async with get_db() as conn:
                    c = await conn.cursor()
                    await c.execute("SELECT timestamp FROM submissions WHERE user_id=?", (user_id,))
                    result = await c.fetchone()
                    if not result:
                        # 埋点：会话外触发受保护 handler 是"无响应/已过期"高发路径，
                        # 记录来源函数，避免靠猜。
                        logger.info(
                            "会话外触发 %s(user_id=%s)，无 submissions 行 -> 已过期",
                            getattr(func, "__name__", "?"), user_id,
                        )
                        await update.message.reply_text("❌ 会话已过期，请重新发送 /start")
                        return ConversationHandler.END
            except Exception as e:
                logger.error(f"状态验证错误: {e}")
                await update.message.reply_text("❌ 内部错误，请稍后再试")
                return ConversationHandler.END
            return await func(update, context)
        return wrapper
    return decorator

async def end_conversation_with_message(update: Update, message: str, clear_keyboard: bool = True) -> int:
    """
    统一的会话终止函数，发送消息并清理键盘
    
    Args:
        update: Telegram 更新对象
        message: 要发送的消息
        clear_keyboard: 是否清除键盘（默认True）
        
    Returns:
        int: ConversationHandler.END
    """
    try:
        if clear_keyboard:
            await update.message.reply_text(message, reply_markup=ReplyKeyboardRemove())
        else:
            await update.message.reply_text(message)
    except Exception as e:
        logger.error(f"发送终止消息失败: {e}")
    logger.info(
        "会话终止 user_id=%s: %s",
        getattr(getattr(update, "effective_user", None), "id", "?"),
        message,
    )
    return ConversationHandler.END


async def handle_conversation_error(update: Update, error_message: str = "❌ 内部错误，请稍后再试") -> int:
    """
    统一的会话错误处理函数
    
    Args:
        update: Telegram 更新对象
        error_message: 错误消息
        
    Returns:
        int: ConversationHandler.END
    """
    logger.error(f"会话错误: {error_message}")
    return await end_conversation_with_message(update, error_message, clear_keyboard=True)


def get_submission_mode(row) -> str:
    """
    从数据库行中提取投稿模式
    
    Args:
        row: 数据库查询结果行（sqlite3.Row 对象）
        
    Returns:
        str: 投稿模式 ('media', 'document', 'mixed')
    """
    if not row:
        return "mixed"
    
    # 处理 sqlite3.Row 对象
    if hasattr(row, 'keys'):
        mode = row["mode"] if "mode" in row.keys() else "mixed"
    else:
        mode = row.get("mode", "mixed")
    
    return mode.lower() if mode else "mixed"


def parse_json_list(raw_data: str) -> list:
    """
    安全解析JSON列表数据
    
    Args:
        raw_data: JSON字符串
        
    Returns:
        list: 解析后的列表，失败返回空列表
    """
    if not raw_data:
        return []
    
    try:
        parsed = json.loads(raw_data)
        # 只接受列表类型，其他类型返回空列表
        if isinstance(parsed, list):
            return parsed
        else:
            logger.debug(f"JSON解析结果不是列表: {type(parsed)}")
            return []
    except (json.JSONDecodeError, TypeError) as e:
        logger.debug(f"JSON解析失败: {e}")
        return []


async def safe_send(send_func, *args, **kwargs):
    """
    安全发送函数，包含重试逻辑
    
    Args:
        send_func: 发送函数
        args: 位置参数
        kwargs: 关键字参数
        
    Returns:
        发送结果或None（如果失败）
    """
    max_retries = 2  # 最多重试次数
    current_attempt = 0
    last_error = None
    
    while current_attempt <= max_retries:
        try:
            return await asyncio.wait_for(send_func(*args, **kwargs), timeout=NET_TIMEOUT)
        except asyncio.TimeoutError:
            current_attempt += 1
            last_error = f"网络请求超时 (尝试 {current_attempt}/{max_retries + 1})"
            # 只有在最后一次尝试失败时才记录
            if current_attempt > max_retries:
                logger.warning(last_error)
            else:
                # 非最后一次尝试，只打印调试信息
                logger.debug(f"发送超时，正在重试 ({current_attempt}/{max_retries + 1})...")
                await asyncio.sleep(2)  # 等待2秒后重试
        except Exception as e:
            # 其他错误直接记录
            last_error = str(e)
            logger.error(f"发送失败: {e}")
            return None
    
    # 所有重试都失败，但我们不想中断流程，所以返回 None
    return None

# 增强型安全发送函数
async def enhanced_safe_send(send_func, *args, **kwargs):
    """
    增强型安全发送函数，提供更全面的错误处理和重试逻辑
    
    Args:
        send_func: 发送函数
        args: 位置参数
        kwargs: 关键字参数
        
    Returns:
        发送结果或None（如果失败）
    """
    max_retries = CONFIG["MAX_RETRIES"]
    base_delay = CONFIG["RETRY_DELAY"]
    current_attempt = 0
    last_error = None
    
    while current_attempt <= max_retries:
        try:
            return await asyncio.wait_for(send_func(*args, **kwargs), timeout=NET_TIMEOUT)
        except asyncio.TimeoutError:
            current_attempt += 1
            last_error = f"网络请求超时 (尝试 {current_attempt}/{max_retries + 1})"
            
            if current_attempt <= max_retries:
                # 使用指数退避算法
                delay = base_delay * (2 ** (current_attempt - 1))
                logger.info(f"发送超时，将在 {delay} 秒后重试 ({current_attempt}/{max_retries})...")
                await asyncio.sleep(delay)
            else:
                logger.warning(f"发送失败: {last_error}")
        
        except Exception as e:
            error_text = str(e).lower()
            
            # 处理Markdown/HTML解析错误
            if "parse" in error_text and ("entities" in error_text or "html" in error_text):
                logger.warning(f"格式解析错误，尝试无格式发送: {e}")
                try:
                    # 移除解析模式参数
                    if 'parse_mode' in kwargs:
                        kwargs_copy = kwargs.copy()
                        kwargs_copy.pop('parse_mode')
                        return await asyncio.wait_for(send_func(*args, **kwargs_copy), timeout=NET_TIMEOUT)
                except Exception as e2:
                    logger.error(f"无格式发送也失败: {e2}")
            
            # 处理网络相关错误
            elif any(kw in error_text for kw in ["network", "connection", "timeout"]):
                current_attempt += 1
                if current_attempt <= max_retries:
                    # 使用指数退避算法
                    delay = base_delay * (2 ** (current_attempt - 1))
                    logger.info(f"网络错误，将在 {delay} 秒后重试 ({current_attempt}/{max_retries}): {e}")
                    await asyncio.sleep(delay)
                    continue
            
            # 处理权限错误
            elif any(kw in error_text for kw in ["forbidden", "permission", "not enough rights", "blocked"]):
                logger.error(f"权限错误，无法发送消息: {e}")
                return None
            
            # 处理请求错误
            elif "bad request" in error_text:
                logger.error(f"无效请求错误: {e}")
                # 检查是否需要重试
                if "retry after" in error_text:
                    # 提取需要等待的秒数
                    import re
                    retry_seconds = 1
                    match = re.search(r"retry after (\d+)", error_text)
                    if match:
                        retry_seconds = int(match.group(1))
                    
                    logger.info(f"请求过于频繁，等待 {retry_seconds} 秒后重试")
                    await asyncio.sleep(retry_seconds)
                    continue
                return None
            
            # 其他错误
            else:
                logger.error(f"发送失败，未知错误: {e}")
            
            return None
    
    # 所有重试都失败
    logger.error(f"发送失败，已达到最大重试次数: {last_error}")
    return None
