"""
投稿会话单一数据源：媒体/文档归类 + submissions 行读写。

所有聊天投稿 handler 只通过这里读写 submissions（替代散落在
media/document/preview/submit_handlers 里的重复 SQL），媒体归类只此一份。
"""
from datetime import datetime
import json
from typing import Optional

from database.db_manager import get_db
from utils.helper_functions import parse_json_list


# ---- 归类（唯一实现）----

def classify_message(message) -> Optional[str]:
    """把 Telegram 消息归类为条目字符串。

    - photo/video/animation/audio -> "type:file_id"
    - document(image/gif)           -> "animation:file_id"
    - document(audio/*)             -> "audio:file_id"
    - 其它 document                  -> "document:file_id:filename"
    - 非媒体/文档                    -> None
    """
    if getattr(message, "photo", None):
        return f"photo:{message.photo[-1].file_id}"
    if getattr(message, "video", None):
        return f"video:{message.video.file_id}"
    if getattr(message, "animation", None):
        return f"animation:{message.animation.file_id}"
    if getattr(message, "audio", None):
        return f"audio:{message.audio.file_id}"
    doc = getattr(message, "document", None)
    if doc:
        mime = (doc.mime_type or "").lower()
        if mime == "image/gif":
            return f"animation:{doc.file_id}"
        if mime.startswith("audio/"):
            return f"audio:{doc.file_id}"
        return f"document:{doc.file_id}:{doc.file_name or '未命名文件'}"
    return None


def entry_kind(entry: str) -> str:
    return entry.split(":", 1)[0]


# ---- 会话读写 ----

async def get_session(user_id: int):
    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute("SELECT * FROM submissions WHERE user_id=?", (user_id,))
        return await c.fetchone()


async def create_session(user_id: int, username: str, mode: str = "mixed") -> None:
    """新建会话（覆盖旧会话）。mode 保留 'media'/'document'/'mixed' 语义。"""
    now = datetime.now().timestamp()
    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute("DELETE FROM submissions WHERE user_id=?", (user_id,))
        await c.execute(
            "INSERT INTO submissions "
            "(user_id, timestamp, mode, image_id, document_id, tags, link, title, note, spoiler, anonymous, username) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, now, mode, "[]", "[]", "", "", "", "", "false", "false", username),
        )


async def append_entry(user_id: int, entry: str) -> int:
    """按条目类型追加（document -> document_id，否则 image_id），返回该列长度。"""
    column = "document_id" if entry_kind(entry) == "document" else "image_id"
    now = datetime.now().timestamp()
    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute(f"SELECT {column} FROM submissions WHERE user_id=?", (user_id,))
        row = await c.fetchone()
        if not row:
            return 0
        items = parse_json_list(row[column])
        items.append(entry)
        await c.execute(
            f"UPDATE submissions SET {column}=?, timestamp=? WHERE user_id=?",
            (json.dumps(items), now, user_id),
        )
    return len(items)


async def update_fields(user_id: int, **fields) -> None:
    """更新可选字段（tags/link/title/note/anonymous/spoiler），刷新时间戳。"""
    fields = {k: v for k, v in fields.items() if k in {
        "tags", "link", "title", "note", "spoiler", "anonymous",
    }}
    if not fields:
        return
    assignments = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [datetime.now().timestamp(), user_id]
    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute(
            f"UPDATE submissions SET {assignments}, timestamp=? WHERE user_id=?",
            values,
        )
