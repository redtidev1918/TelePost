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
    """新建会话（覆盖旧会话）。mode 保留 'media'/'document'/'mixed' 语义。

    epoch 递增标记投稿代数（审计用）；媒体去重日志跨代匹配同一 update_id，
    旧代 Update 的重放不会把媒体追加进新投稿。
    """
    now = datetime.now().timestamp()
    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute(
            "SELECT COALESCE(MAX(epoch), 0) AS epoch FROM submissions WHERE user_id=?",
            (user_id,))
        row = await c.fetchone()
        epoch = (row["epoch"] or 0) + 1 if row else 1
        await c.execute("DELETE FROM submissions WHERE user_id=?", (user_id,))
        await c.execute(
            "INSERT INTO submissions "
            "(user_id, timestamp, mode, image_id, document_id, tags, link, title, note, spoiler, anonymous, username, epoch) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, now, mode, "[]", "[]", "", "", "", "", "false", "false", username, epoch),
        )


async def append_entry(user_id: int, entry: str,
                       update_id: Optional[int] = None) -> int:
    """按条目类型追加（document -> document_id，否则 image_id），返回该列长度。

    带 update_id 时按（user_id, update_id）跨代去重：同一 Update 的重放
    （崩溃恢复）不重复追加；日志与追加同事务提交，中途崩溃两边一起回滚。
    用户主动重发相同图片是新的 update_id，照常追加。
    """
    column = "document_id" if entry_kind(entry) == "document" else "image_id"
    now = datetime.now().timestamp()
    async with get_db() as conn:
        c = await conn.cursor()
        if update_id is not None:
            await c.execute(
                "SELECT epoch FROM submissions WHERE user_id=?", (user_id,))
            row = await c.fetchone()
            epoch = (row["epoch"] or 0) if row else 0
            await c.execute(
                "SELECT 1 FROM submission_media_log "
                "WHERE user_id=? AND update_id=?", (user_id, update_id))
            if await c.fetchone():
                # 该 Update 已应用过（任何一代）——重放，返回现有数量。
                await c.execute(
                    f"SELECT {column} FROM submissions WHERE user_id=?", (user_id,))
                existing = await c.fetchone()
                if not existing:
                    return 0
                return len(parse_json_list(existing[column]))
            await c.execute(
                "INSERT INTO submission_media_log "
                "(user_id, update_id, epoch, entry, applied_at) VALUES (?,?,?,?,?)",
                (user_id, update_id, epoch, entry, now))
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
