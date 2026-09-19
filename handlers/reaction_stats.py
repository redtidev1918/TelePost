"""Telegram 频道反应热度统计（message_reaction_count）。

标准 Bot API 不提供频道帖子的浏览量 / 转发量，这两个字段永远是 0。真实可用的
互动数据是 ``message_reaction_count`` 更新：在频道归 Bot 所有时，Telegram 会对
每条帖子的反应变化推送一次计数（不是单条 reaction 事件），每条媒体消息独立计数。

本模块把每个 ``(message_id, total_count)`` 存入 ``message_reaction_counts``，
并在每次收到更新后重新聚合所属 ``published_posts`` 行（主帖 + 相册 related ids）
的 reaction 总数与热度分，同时刷新 Whoosh 索引里的 heat_score，让 /hot 等
排序立即反映真实互动。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Sequence, Set

from telegram import Update

from database.db_manager import get_db

logger = logging.getLogger(__name__)


async def handle_message_reaction_count(update: Update) -> int:
    """Handle one Telegram ``message_reaction_count`` update.

    Returns the number of ``published_posts`` rows refreshed. A reaction on an
    album member refreshes the owning post once, not once per member.
    """
    mrc = getattr(update, "message_reaction_count", None)
    if mrc is None:
        return 0
    message_id = int(mrc.message_id or 0)
    if not message_id:
        return 0
    total = sum(int(getattr(r, "total_count", 0) or 0)
                for r in (mrc.reactions or []))
    now = time.time()

    async with get_db() as conn:
        cursor = await conn.cursor()
        await cursor.execute(
            """
            INSERT INTO message_reaction_counts (message_id, total_count, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                total_count=excluded.total_count,
                updated_at=excluded.updated_at
            """,
            (message_id, total, now),
        )

        affected: List[int] = []
        rows = await _post_rows_containing(conn, message_id)
        for row in rows:
            post_message_id = int(row["message_id"] or 0)
            if not post_message_id:
                continue
            await _refresh_post_row(conn, row, now=now)
            affected.append(post_message_id)

    if affected:
        await _refresh_search_indexes(affected)
    return len(affected)


async def _post_rows_containing(conn, message_id: int) -> List[Any]:
    """Find published_posts rows whose album contains ``message_id``.

    ``published_posts.related_message_ids`` is a JSON array of integers; a
    plain SQL ``LIKE`` would be brittle, so we scan surviving rows and parse in
    Python. Post counts are small (channel history), so this is fine.
    """
    cursor = await conn.cursor()
    rows: List[Any] = []
    seen: Set[int] = set()

    await cursor.execute(
        "SELECT * FROM published_posts WHERE message_id = ? AND is_deleted = 0",
        (message_id,),
    )
    row = await cursor.fetchone()
    if row is not None:
        rows.append(row)
        seen.add(int(row["message_id"] or 0))

    await cursor.execute(
        "SELECT * FROM published_posts "
        "WHERE is_deleted = 0 AND related_message_ids IS NOT NULL "
        "AND related_message_ids <> ''"
    )
    for row in await cursor.fetchall():
        mid = int(row["message_id"] or 0)
        if not mid or mid in seen:
            continue
        try:
            related = json.loads(row["related_message_ids"])
        except (TypeError, ValueError):
            continue
        if any(int(i) == int(message_id) for i in (related or []) if str(i).isdigit()):
            rows.append(row)
            seen.add(mid)
    return rows


async def _refresh_post_row(conn, row: Any, *, now: float) -> None:
    cursor = await conn.cursor()
    post_message_id = int(row["message_id"] or 0)
    related = _parse_related(
        row["related_message_ids"] if "related_message_ids" in row.keys() else None
    )
    all_ids: List[int] = [post_message_id] + [int(i) for i in related if int(i) > 0]
    totals = await _reaction_totals(cursor, all_ids)
    total_reactions = int(sum(totals.values()))

    main_count = int(totals.get(post_message_id, 0))
    related_stats: List[Dict[str, int]] = [
        {"views": 0, "forwards": 0, "reactions": int(c)}
        for mid, c in totals.items() if mid != post_message_id
    ]
    publish_time = row["publish_time"] or now
    try:
        heat = _compute_heat(main_count, related_stats, float(publish_time))
    except Exception:
        logger.exception("热度计算失败 message_id=%s", post_message_id)
        heat = 0.0

    await cursor.execute(
        "UPDATE published_posts SET reactions = ?, heat_score = ?, last_update = ? "
        "WHERE message_id = ? AND is_deleted = 0",
        (total_reactions, float(heat or 0.0), now, post_message_id),
    )


def _parse_related(raw: Any) -> List[int]:
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    out: List[int] = []
    for item in data or []:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


async def _reaction_totals(cursor, message_ids: Sequence[int]) -> Dict[int, int]:
    ids = [int(i) for i in message_ids if int(i) > 0]
    result: Dict[int, int] = {}
    if not ids:
        return result
    marks = ",".join("?" * len(ids))
    await cursor.execute(
        f"SELECT message_id, total_count FROM message_reaction_counts "
        f"WHERE message_id IN ({marks})",
        list(ids),
    )
    for r in await cursor.fetchall():
        result[int(r["message_id"])] = int(r["total_count"] or 0)
    return result


def _compute_heat(main_reactions: int, related_stats: List[Dict[str, int]],
                  publish_time: float) -> float:
    """Reaction-only heat score with the existing time-decay formula.

    Views / forwards are not available to a Bot API client, so they contribute
    nothing; reactions are the only real interaction signal.
    """
    from utils.heat_calculator import calculate_multi_message_heat

    detail = calculate_multi_message_heat(
        {"views": 0, "forwards": 0, "reactions": int(main_reactions)},
        related_stats,
        float(publish_time),
    )
    return float(detail["heat_score"])


async def _refresh_search_indexes(message_ids: List[int]) -> None:
    """Re-add each affected post to the Whoosh index with its fresh heat_score."""
    from datetime import datetime

    from utils.search_engine import PostDocument, get_search_engine

    search_engine = get_search_engine()
    if search_engine is None:
        return
    async with get_db() as conn:
        cursor = await conn.cursor()
        for message_id in message_ids:
            await cursor.execute(
                "SELECT * FROM published_posts WHERE message_id = ? AND is_deleted = 0",
                (message_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                continue
            publish_time = row["publish_time"] or time.time()
            try:
                publish_dt = datetime.fromtimestamp(float(publish_time))
            except (TypeError, ValueError, OverflowError):
                publish_dt = datetime.now()
            try:
                search_engine.delete_post(int(message_id))
                doc = PostDocument(
                    message_id=int(message_id),
                    post_id=int(message_id),
                    user_id=int(row["user_id"] or 0),
                    username=str(row["username"] or ""),
                    title=str(row["title"] or ""),
                    description=str(row["caption"] or ""),
                    tags=str(row["tags"] or ""),
                    filename=str(row["filename"] or ""),
                    link=str(row["link"] or ""),
                    publish_time=publish_dt,
                    views=int(row["views"] or 0),
                    heat_score=float(row["heat_score"] or 0.0),
                )
                search_engine.add_post(doc)
            except Exception:
                logger.exception("刷新搜索索引失败 message_id=%s", message_id)
