"""SQLite persistence for confirmed channel posts (``published_posts``).

The row is the canonical "this message exists in the channel" record used by
heat statistics and search. Search indexing itself stays an adapter concern
(see ``telepost.application.posts.record_published_post``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from database import db_manager


@dataclass
class PublishedPostRecord:
    message_id: int
    user_id: int
    username: str
    title: str
    tags: str
    link: str
    note: str
    content_type: str  # media | document | mixed
    file_ids: str
    caption: str
    filename: str
    related_message_ids: Optional[List[int]] = None
    publish_time: Optional[datetime] = None


class PublishedPostRepository:
    async def insert(self, record: PublishedPostRecord) -> Optional[int]:
        published_at = record.publish_time or datetime.now()
        related_json = None
        if record.related_message_ids and len(record.related_message_ids) > 1:
            others = [mid for mid in record.related_message_ids
                      if mid != record.message_id]
            if others:
                related_json = json.dumps(others)
        try:
            async with db_manager.get_db() as conn:
                cursor = await conn.execute(
                    """
                    INSERT INTO published_posts
                    (message_id, user_id, username, title, tags, link, note,
                     content_type, file_ids, caption, filename, publish_time,
                     last_update, related_message_ids)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.message_id, record.user_id, record.username,
                        record.title, record.tags, record.link, record.note,
                        record.content_type, record.file_ids, record.caption,
                        record.filename, published_at.timestamp(),
                        published_at.timestamp(), related_json,
                    ),
                )
                return cursor.lastrowid
        except Exception as exc:
            # Post recording must never fail the confirmed channel delivery.
            import logging
            logging.getLogger(__name__).error(
                "保存帖子信息到数据库失败: %s", exc
            )
            return None
