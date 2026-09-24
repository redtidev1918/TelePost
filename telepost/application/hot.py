"""Hot ranking application service shared by Bot and Mini App.

This is the ONE place that turns ``published_posts`` into the canonical hot
list. ``telepost.domain.hot`` owns query/week semantics; this module owns
database access and safe presentation rows. Bot rendering and Mini App HTTP
serialization stay thin adapters.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from ..domain.hot import HotQuery, week_start_utc

logger = logging.getLogger(__name__)

HOT_LIMIT_MAX = 50
HOT_LIMIT_DEFAULT = 10


@dataclass(frozen=True)
class HotPost:
    """Safe hot row. It intentionally has no submitter identity."""

    message_id: int
    title: str
    tags: str
    link: str
    note: str
    content_type: str
    publish_time: float
    heat_score: float
    reactions: int
    file_ids: str = ""


@dataclass(frozen=True)
class HotPage:
    items: List[HotPost]
    next_cursor: Optional[str] = None
    total_count: int = 0
    pages: int = 1
    page: int = 1


def _clamp_limit(raw: Optional[object]) -> int:
    try:
        value = int(raw or HOT_LIMIT_DEFAULT)
    except (TypeError, ValueError):
        value = HOT_LIMIT_DEFAULT
    return max(1, min(value, HOT_LIMIT_MAX))


def _scope_of(value: Optional[object]) -> str:
    return "week" if str(value or "all").strip().lower() in {"week", "本周"} else "all"


def parse_public_tags(raw: object) -> List[str]:
    """Normalize stored tag presentation without exposing internal identity."""
    if raw is None:
        return []
    if isinstance(raw, list):
        values = raw
    else:
        text = str(raw).strip()
        if not text:
            return []
        try:
            decoded = json.loads(text)
            values = decoded if isinstance(decoded, list) else [decoded]
        except (json.JSONDecodeError, TypeError, ValueError):
            values = text.split()
    tags: List[str] = []
    for value in values:
        tag = str(value).strip().lstrip("#")
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _encode_cursor(score: float, published_at: float, message_id: int) -> str:
    payload = json.dumps(
        [round(float(score), 10), float(published_at), int(message_id)],
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: Optional[str]) -> Optional[tuple[float, float, int]]:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        values = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        score, published_at, message_id = values
        return float(score), float(published_at), int(message_id)
    except Exception:
        return None


class HotRepository:
    """Read-only SQLite access for canonical hot rows."""

    _COLUMNS = (
        "message_id, title, tags, link, note, content_type, file_ids, "
        "publish_time, heat_score, reactions"
    )

    def _row_to_post(self, row) -> HotPost:
        data = dict(row)
        return HotPost(
            message_id=int(data.get("message_id") or 0),
            title=str(data.get("title") or ""),
            tags=str(data.get("tags") or ""),
            link=str(data.get("link") or ""),
            note=str(data.get("note") or ""),
            content_type=str(data.get("content_type") or ""),
            publish_time=float(data.get("publish_time") or 0),
            heat_score=float(data.get("heat_score") or 0),
            reactions=int(data.get("reactions") or 0),
            file_ids=str(data.get("file_ids") or ""),
        )

    async def _rows(self, sql: str, params: Sequence[object]) -> List[HotPost]:
        from database.db_manager import get_db
        async with get_db() as conn:
            cursor = await conn.cursor()
            await cursor.execute(sql, tuple(params))
            rows = await cursor.fetchall()
        return [self._row_to_post(row) for row in rows]

    async def page(self, query: HotQuery, page: int = 1) -> HotPage:
        cutoff = week_start_utc() if query.scope == "week" else None
        where = "is_deleted = 0"
        params: list[object] = []
        if cutoff is not None:
            where += " AND publish_time > ?"
            params.append(cutoff)
        params_count = list(params)
        params.append(query.limit)
        params.append((max(1, int(page)) - 1) * query.limit)
        rows = await self._rows(
            f"SELECT {self._COLUMNS} FROM published_posts WHERE {where} "
            "ORDER BY heat_score DESC, publish_time DESC, message_id DESC "
            "LIMIT ? OFFSET ?",
            params,
        )
        from database.db_manager import get_db
        async with get_db() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                f"SELECT COUNT(*) AS c FROM published_posts WHERE {where}",
                params_count,
            )
            row = await cursor.fetchone()
        try:
            total_count = int(row["c"]) if row else 0
        except (TypeError, ValueError):
            total_count = 0
        return HotPage(
            items=rows,
            total_count=total_count,
            pages=max(1, (total_count + query.limit - 1) // query.limit),
            page=max(1, int(page)),
        )

    async def cursor_page(
        self, scope: str = "all", limit: int = HOT_LIMIT_DEFAULT,
        cursor: Optional[str] = None,
    ) -> HotPage:
        where = "is_deleted = 0"
        params: list[object] = []
        if _scope_of(scope) == "week":
            where += " AND publish_time > ?"
            params.append(week_start_utc())
        decoded = _decode_cursor(cursor)
        if decoded is not None:
            score, published_at, message_id = decoded
            where += (
                " AND (COALESCE(heat_score, 0) < ? OR "
                "(COALESCE(heat_score, 0) = ? AND "
                "(COALESCE(publish_time, 0) < ? OR "
                "(COALESCE(publish_time, 0) = ? AND message_id < ?))))"
            )
            params.extend([score, score, published_at, published_at, message_id])
        params.append(_clamp_limit(limit) + 1)
        rows = await self._rows(
            f"SELECT {self._COLUMNS} FROM published_posts WHERE {where} "
            "ORDER BY COALESCE(heat_score, 0) DESC, "
            "COALESCE(publish_time, 0) DESC, message_id DESC LIMIT ?",
            params,
        )
        has_more = len(rows) > _clamp_limit(limit)
        rows = rows[:_clamp_limit(limit)]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = _encode_cursor(
                last.heat_score, last.publish_time, last.message_id
            )
        return HotPage(items=rows, next_cursor=next_cursor)

    async def get(self, message_id: int) -> Optional[HotPost]:
        rows = await self._rows(
            f"SELECT {self._COLUMNS} FROM published_posts "
            "WHERE message_id = ? AND is_deleted = 0",
            [message_id],
        )
        return rows[0] if rows else None


class HotService:
    def __init__(self, repository: Optional[HotRepository] = None):
        self.repository = repository or HotRepository()

    async def page(self, query: HotQuery, page: int = 1) -> HotPage:
        return await self.repository.page(query, page)

    async def cursor_page(
        self, scope: str = "all", limit: int = HOT_LIMIT_DEFAULT,
        cursor: Optional[str] = None,
    ) -> HotPage:
        return await self.repository.cursor_page(scope, limit, cursor)

    async def get(self, message_id: int) -> Optional[HotPost]:
        return await self.repository.get(message_id)


def hot_post_payload(post: HotPost, *, include_note: bool = False) -> dict:
    """Stable Mini App DTO. Never expose file_ids or submitter identity."""
    try:
        media = json.loads(post.file_ids or "[]")
        if not isinstance(media, list):
            media = []
    except (json.JSONDecodeError, TypeError, ValueError):
        media = []
    payload = {
        "message_id": post.message_id,
        "title": post.title or "无标题",
        "tags": parse_public_tags(post.tags),
        "link": post.link,
        "publish_time": post.publish_time,
        "heat_score": post.heat_score,
        "reactions": post.reactions,
        "content_type": post.content_type,
        "media_count": len(media),
    }
    if include_note:
        payload["note"] = post.note
    return payload
