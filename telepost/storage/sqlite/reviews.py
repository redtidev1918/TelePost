"""SQLite repository for the review queue (``pending_reviews``).

This is the *only* place allowed to mutate ``pending_reviews.status``. Every
state-changing UPDATE is conditional so concurrent administrators / API calls
can never double-publish:

* ``claim_for_publishing`` uses one atomic UPDATE whose WHERE clause accepts
  pending/failed rows or stale ``publishing`` zombies (crash recovery);
* terminal updates carry ``AND status='publishing'`` guards, so a late writer
  can never clobber a row a stale reclaim already moved again;
* expiry claims rows with ``BEGIN IMMEDIATE`` before any Telegram I/O.

Rows are returned as ``aiosqlite.Row`` to stay compatible with the application
service DTO mapper.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, List, Optional

import aiosqlite

from database import db_manager


@dataclass
class NewReview:
    idempotency_key: str
    source: str
    user_id: int
    username: str
    title: str
    tags: str
    note: str
    link: str
    anonymous: bool
    spoiler: bool
    media: list
    documents: list
    review_chat_id: str
    review_message_ids: List[int]
    target_id: str = ""
    source_label: str = ""
    source_ref: str = ""
    scheduled_at: str = ""
    pixiv_id: str = ""
    work_type: str = ""
    delivery_target: str = ""
    status: str = "pending"


class ReviewRepository:
    async def get(self, review_id: int):
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM pending_reviews WHERE id=?", (int(review_id),)
            )
            return await cur.fetchone()

    async def find_active_by_key(self, idempotency_key: str,
                                 dedup_window_seconds: int):
        """Pending/failed with the same key, or a recently published one."""
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM pending_reviews "
                "WHERE idempotency_key=? AND ("
                "  status IN ('preparing', 'pending', 'failed')"
                "  OR (status='published' AND decided_at >= ?)"
                ")",
                (idempotency_key, time.time() - dedup_window_seconds),
            )
            return await cur.fetchone()

    async def find_published_work(self, target_id: str, work_type: str,
                                  pixiv_id: str, window_seconds: int):
        """Cross-intent duplicate: same downstream work, different key."""
        if not pixiv_id:
            return None
        async with db_manager.get_db() as conn:
            sql = (
                "SELECT * FROM pending_reviews "
                "WHERE status='published' AND work_type=? AND pixiv_id=? "
                "AND decided_at >= ?"
            )
            params: list = [work_type, pixiv_id, time.time() - window_seconds]
            if target_id:
                sql += " AND target_id=?"
                params.append(target_id)
            sql += " ORDER BY decided_at DESC LIMIT 1"
            cur = await conn.execute(sql, params)
            return await cur.fetchone()

    async def insert(self, review: NewReview) -> int:
        now = time.time()
        async with db_manager.get_db() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO pending_reviews (
                    idempotency_key, source, status, user_id, username, title,
                    tags, note, link, anonymous, spoiler, media_json,
                    documents_json, review_chat_id, review_message_ids,
                    target_id, source_label, source_ref, scheduled_at,
                    pixiv_id, work_type, delivery_target,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review.idempotency_key, review.source, review.status,
                    review.user_id,
                    review.username, review.title, review.tags, review.note,
                    review.link, int(review.anonymous), int(review.spoiler),
                    json.dumps(review.media), json.dumps(review.documents),
                    str(review.review_chat_id),
                    json.dumps(review.review_message_ids),
                    review.target_id, review.source_label, review.source_ref,
                    review.scheduled_at, review.pixiv_id, review.work_type,
                    review.delivery_target, now, now,
                ),
            )
            return cursor.lastrowid

    async def update_staged(self, review_id: int, *, media: list,
                            documents: list, preview_message_ids: List[int]) -> bool:
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "UPDATE pending_reviews SET media_json=?, documents_json=?, "
                "review_message_ids=?, updated_at=?, error='' "
                "WHERE id=? AND status='preparing'",
                (json.dumps(media), json.dumps(documents),
                 json.dumps(preview_message_ids), time.time(), int(review_id)),
            )
            return cur.rowcount == 1

    async def finalize_control(self, review_id: int, message_id: int, *,
                               ready: bool = True) -> bool:
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "UPDATE pending_reviews SET control_message_id=?, status=?, "
                "updated_at=?, error=CASE WHEN ? THEN '' ELSE error END "
                "WHERE id=? AND status IN ('preparing', 'failed')",
                (int(message_id), "pending" if ready else "failed",
                 time.time(), 1 if ready else 0, int(review_id)),
            )
            return cur.rowcount == 1

    async def mark_preparation_failed(self, review_id: int, error: str, *,
                                      clear_previews: bool = False) -> bool:
        previews = ", review_message_ids='[]'" if clear_previews else ""
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "UPDATE pending_reviews SET status='failed', updated_at=?, "
                f"error=?{previews} WHERE id=? AND status='preparing'",
                (time.time(), str(error)[:500], int(review_id)),
            )
            return cur.rowcount == 1

    async def list_incomplete(self, *, cutoff: float, limit: int = 100) -> list:
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM pending_reviews WHERE control_message_id IS NULL "
                "AND status IN ('preparing', 'failed') AND updated_at <= ? "
                "ORDER BY updated_at ASC LIMIT ?",
                (cutoff, max(1, min(int(limit), 500))),
            )
            return list(await cur.fetchall())

    async def delete(self, review_id: int) -> None:
        async with db_manager.get_db() as conn:
            await conn.execute("DELETE FROM pending_reviews WHERE id=?",
                               (int(review_id),))

    async def set_control_message(self, review_id: int, message_id: int) -> None:
        async with db_manager.get_db() as conn:
            await conn.execute(
                "UPDATE pending_reviews SET control_message_id=?, updated_at=? "
                "WHERE id=?",
                (message_id, time.time(), int(review_id)),
            )

    async def backfill_target(self, review_id: int, target_id: str) -> None:
        async with db_manager.get_db() as conn:
            await conn.execute(
                "UPDATE pending_reviews SET target_id=?, updated_at=? "
                "WHERE id=? AND COALESCE(target_id, '')=''",
                (target_id, time.time(), int(review_id)),
            )

    async def claim_for_publishing(self, review_id: int, *,
                                   stale_seconds: float,
                                   spoiler: Optional[bool] = None):
        """Atomically move pending/failed (or stale publishing) → publishing.

        Returns ``(claimed, row)``. A published row is returned unclaimed so
        the service can short-circuit as an idempotent success; a fresh
        ``publishing`` row is returned unclaimed for the busy error.
        """
        review_id = int(review_id)
        now = time.time()
        spoiler_set = "" if spoiler is None else ", spoiler=?"
        params: List[Any] = [now]
        if spoiler is not None:
            params.append(1 if spoiler else 0)
        params.extend([review_id, now, stale_seconds])
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                f"""
                UPDATE pending_reviews
                SET status='publishing', updated_at=?, error=''{spoiler_set}
                WHERE id=? AND (
                    status IN ('pending', 'failed')
                    OR (status='publishing' AND ? - updated_at > ?)
                )
                """,
                tuple(params),
            )
            claimed = cur.rowcount == 1
            row = await self._select_in_conn(conn, review_id)
        return claimed, row

    async def mark_published(self, review_id: int, *, actor: Any,
                             message_id: int) -> bool:
        """Guarded terminal transition; True only if this writer owned it."""
        now = time.time()
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                """
                UPDATE pending_reviews
                SET status='published', updated_at=?, decided_at=?,
                    decided_by=?, published_message_id=?, error=''
                WHERE id=? AND status='publishing'
                """,
                (now, now, actor if isinstance(actor, int) else None,
                 message_id, int(review_id)),
            )
            return cur.rowcount == 1

    async def mark_failed(self, review_id: int, error: str) -> bool:
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "UPDATE pending_reviews SET status='failed', updated_at=?, "
                "error=? WHERE id=? AND status='publishing'",
                (time.time(), str(error)[:500], int(review_id)),
            )
            return cur.rowcount == 1

    async def reject(self, review_id: int, *, actor: Any) -> tuple:
        now = time.time()
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "UPDATE pending_reviews SET status='rejected', updated_at=?, "
                "decided_at=?, decided_by=?, error='' "
                "WHERE id=? AND status IN ('pending', 'failed')",
                (now, now, actor if isinstance(actor, int) else None,
                 int(review_id)),
            )
            changed = cur.rowcount == 1
            row = await self._select_in_conn(conn, int(review_id))
        return changed, row

    async def set_spoiler(self, review_id: int, spoiler: bool) -> tuple:
        now = time.time()
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "UPDATE pending_reviews SET spoiler=?, updated_at=? "
                "WHERE id=? AND status IN ('pending', 'failed')",
                (1 if spoiler else 0, now, int(review_id)),
            )
            changed = cur.rowcount == 1
            row = await self._select_in_conn(conn, int(review_id))
        return changed, row

    async def toggle_spoiler(self, review_id: int) -> tuple:
        now = time.time()
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "UPDATE pending_reviews SET spoiler=1-COALESCE(spoiler,0), "
                "updated_at=? WHERE id=? AND status IN ('pending', 'failed')",
                (now, int(review_id)),
            )
            changed = cur.rowcount == 1
            row = await self._select_in_conn(conn, int(review_id))
        return changed, row

    async def expire_pending(self, *, cutoff: float, now: float,
                             batch_size: int, error: str) -> list:
        """Atomically claim an oldest-first batch of stale pending rows."""
        rows = []
        async with db_manager.get_db() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "SELECT * FROM pending_reviews "
                "WHERE status='pending' AND created_at < ? "
                "ORDER BY created_at ASC LIMIT ?",
                (cutoff, batch_size),
            )
            candidates = await cur.fetchall()
            for row in candidates:
                cur = await conn.execute(
                    "UPDATE pending_reviews SET status='expired', "
                    "updated_at=?, decided_at=?, error=? "
                    "WHERE id=? AND status='pending'",
                    (now, now, error, row["id"]),
                )
                if cur.rowcount == 1:
                    rows.append(row)
        return rows

    async def list_pending(self, *, limit: int,
                           created_cursor: Optional[float] = None,
                           id_cursor: Optional[int] = None) -> list:
        async with db_manager.get_db() as conn:
            if created_cursor is None:
                cur = await conn.execute(
                    "SELECT * FROM pending_reviews WHERE status='pending' "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (limit,),
                )
            else:
                cur = await conn.execute(
                    "SELECT * FROM pending_reviews WHERE status='pending' "
                    "AND (created_at, id) < (?, ?) "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (created_cursor, id_cursor, limit),
                )
            return list(await cur.fetchall())

    async def _select_in_conn(self, conn, review_id: int):
        cur = await conn.execute(
            "SELECT * FROM pending_reviews WHERE id=?", (review_id,)
        )
        return await cur.fetchone()
