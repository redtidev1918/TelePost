"""Post-publish archival: persist the confirmed channel post + search index.

This is the single post-publish path shared by chat, review and HTTP API
publication. Persistence is canonical; search indexing is a best-effort
adapter that never fails a confirmed delivery.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from utils.helper_functions import build_caption

from ..storage.sqlite.posts import PublishedPostRecord, PublishedPostRepository

logger = logging.getLogger(__name__)


@dataclass
class PublishedPostInput:
    """Everything needed to archive a confirmed publication."""

    user_id: int
    username: str
    main_message_id: int
    title: str = ""
    tags: str = ""
    note: str = ""
    link: str = ""
    media_compact: Optional[List[str]] = None     # legacy "kind:file_id" strings
    document_compact: Optional[List[str]] = None  # legacy "document:file_id:name"
    all_message_ids: Optional[List[int]] = None


def content_type_of(media_compact, document_compact) -> str:
    has_media = bool(media_compact)
    has_docs = bool(document_compact)
    if has_media and has_docs:
        return "mixed"
    return "media" if has_media else "document"


def filenames_of(document_compact) -> str:
    names = []
    for entry in document_compact or []:
        parts = str(entry).split(":", 2)
        if len(parts) >= 3:
            names.append(parts[2])
        elif len(parts) == 2:
            names.append("未知文件")
    return " | ".join(names)


async def record_published_post(post: PublishedPostInput) -> Optional[int]:
    media = post.media_compact or []
    docs = document_compact = post.document_compact or []
    data = {
        "tags": post.tags,
        "title": post.title,
        "note": post.note,
        "link": post.link,
        "username": post.username or f"user{post.user_id}",
        "anonymous": "false",
        "spoiler": "false",
        "user_id": post.user_id,
    }
    caption = build_caption(data)
    record = PublishedPostRecord(
        message_id=post.main_message_id,
        user_id=post.user_id,
        username=data["username"],
        title=post.title or "",
        tags=post.tags or "",
        link=post.link or "",
        note=post.note or "",
        content_type=content_type_of(media, docs),
        file_ids=json.dumps(media if media else docs),
        caption=caption,
        filename=filenames_of(docs),
        related_message_ids=post.all_message_ids or [],
    )
    repo = PublishedPostRepository()
    post_id = await repo.insert(record)
    if post_id is not None:
        _index_search(post, post_id, record.filename)
    return post_id


def _index_search(post: PublishedPostInput, post_id: int, filename: str) -> None:
    try:
        from config.settings import SEARCH_ENABLED
        if not SEARCH_ENABLED:
            return
        from utils.search_engine import get_search_engine, PostDocument

        engine = get_search_engine()
        engine.add_post(PostDocument(
            message_id=post.main_message_id,
            post_id=post_id,
            title=post.title or "",
            description=post.note or "",
            tags=post.tags or "",
            filename=filename,
            link=post.link or "",
            user_id=post.user_id,
            username=post.username or f"user{post.user_id}",
            publish_time=datetime.now(),
            views=0,
            heat_score=0,
        ))
    except Exception as exc:  # indexing must never affect publication
        logger.error("添加到搜索索引失败: %s", exc, exc_info=True)
