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
    # Review lineage (populated by the replacement path, empty otherwise).
    review_chain_id: str = ""
    generation: int = 0
    supersedes_review_id: Optional[int] = None
    # Request UUID of the refetch attempt that produced this review.
    refetch_request_id: str = ""
    # Identity / provenance (§identity): verified human owner (NULL for
    # service/automatic submissions) + request actor. These are orthogonal to
    # the legacy user_id/username (request identity and display only).
    submitter_user_id: Optional[int] = None
    submitter_username: str = ""
    actor_kind: str = "user"  # 'user' | 'service'
    actor_subject: str = ""


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
        async with db_manager.get_db() as conn:
            return await self.insert_into(conn, review)

    async def insert_into(self, conn, review: NewReview) -> int:
        """Insert on the caller's connection (for atomic replacement linking).

        Keeps the INSERT and the refetch-replacement UPDATEs in one SQLite
        transaction, so the "new review exists" proof of success and the
        "old review superseded" effect can never split across a crash.
        """
        now = time.time()
        cursor = await conn.execute(
            """
            INSERT INTO pending_reviews (
                idempotency_key, source, status, user_id, username, title,
                tags, note, link, anonymous, spoiler, media_json,
                documents_json, review_chat_id, review_message_ids,
                target_id, source_label, source_ref, scheduled_at,
                pixiv_id, work_type, delivery_target,
                review_chain_id, generation, supersedes_review_id,
                refetch_request_id,
                submitter_user_id, submitter_username, actor_kind,
                actor_subject,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?)
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
                review.delivery_target,
                review.review_chain_id, int(review.generation),
                review.supersedes_review_id,
                review.refetch_request_id,
                review.submitter_user_id, review.submitter_username,
                review.actor_kind, review.actor_subject,
                now, now,
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
                               ready: bool = True,
                               refetch_request_id: str = "") -> bool:
        async with db_manager.get_db() as conn:
            if refetch_request_id and ready:
                from telepost.storage.sqlite.refetch import RefetchRepository
                refetch_repo = RefetchRepository()
                cur = await conn.execute(
                    "SELECT * FROM pending_reviews WHERE id=? AND status='preparing' "
                    "AND refetch_request_id=?",
                    (int(review_id), refetch_request_id),
                )
                new_review = await cur.fetchone()
                if new_review is None:
                    return False
                attempt, source = await refetch_repo.resolve_replacement(
                    conn, refetch_request_id
                )
                if attempt is None:
                    return False
                if source is None or source["status"] != "pending":
                    await conn.execute(
                        "UPDATE refetch_attempts SET state='obsolete', finished_at=? "
                        "WHERE request_id=? AND state IN ('requested','admitted')",
                        (time.time(), refetch_request_id),
                    )
                    return False
                chain_id, _ = await refetch_repo.chain_of_review(conn, source)
                cur = await conn.execute(
                    "UPDATE pending_reviews SET control_message_id=?, status='pending', "
                    "updated_at=?, error='' WHERE id=? AND status='preparing'",
                    (int(message_id), time.time(), int(review_id)),
                )
                if cur.rowcount != 1:
                    return False
                state = await refetch_repo.finalize_replacement(
                    conn, attempt=attempt, new_review_id=int(review_id),
                    new_candidate_id=new_review["pixiv_id"] or "",
                    source_review_id=source["id"], review_chain_id=chain_id,
                    generation=int(attempt["generation"] or 0),
                )
                if state != "replaced":
                    await conn.execute(
                        "UPDATE pending_reviews SET status='failed' WHERE id=?",
                        (int(review_id),),
                    )
                return state == "replaced"
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

    async def list_old_superseded(self, *, cutoff: float, limit: int = 100) -> list:
        """Old chain versions (status='superseded') past their retention cut-off.

        Used by the Telegram-side sweep: rows are returned so the caller can
        delete their preview/control messages first, then delete the row.
        Lineage (refetch_attempts / refetch_seen_candidates) is never touched
        by this method — the audit trail survives the card cleanup.
        """
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM pending_reviews WHERE status='superseded' "
                "AND updated_at <= ? ORDER BY updated_at ASC LIMIT ?",
                (cutoff, max(1, min(int(limit), 500))),
            )
            return list(await cur.fetchall())

    async def touch_preparing(self, review_id: int) -> bool:
        """Renew the liveness marker of an IN-FLIGHT preparation.

        A request that is still staging its previews and a request that died
        mid-staging are indistinguishable in this table: both leave
        ``status='preparing'`` with ``control_message_id IS NULL``. That is
        exactly the predicate ``list_incomplete`` uses to decide what the
        reconciliation sweep may repair, so a submission whose staging outlives
        the staleness window was treated as crash leftovers and destroyed by
        the sweeper while its own handler was still running. A live handler
        renews ``updated_at`` here, so ``updated_at <= cutoff`` really means
        "nobody has signalled liveness for N seconds".

        Scoped to ``status='preparing'`` so a heartbeat can never resurrect a
        row another writer already moved on (staged/pending/failed/published),
        and returns whether the row was still ours to renew.
        """
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "UPDATE pending_reviews SET updated_at=? "
                "WHERE id=? AND status='preparing'",
                (time.time(), int(review_id)),
            )
            return cur.rowcount == 1

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

    async def list_by_user(self, user_id: int, *, limit: int,
                           created_cursor: Optional[float] = None,
                           id_cursor: Optional[int] = None) -> list:
        """Own-submission history: strictly user-scoped, keyset paged.

        Returns every review row the user submitted, newest first; a user can
        never query another user's rows (§25 privacy boundary enforced here).
        """
        async with db_manager.get_db() as conn:
            if created_cursor is None:
                cur = await conn.execute(
                    "SELECT * FROM pending_reviews WHERE user_id=? "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (int(user_id), limit),
                )
            else:
                cur = await conn.execute(
                    "SELECT * FROM pending_reviews WHERE user_id=? "
                    "AND (created_at, id) < (?, ?) "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (int(user_id), created_cursor, id_cursor, limit),
                )
            return list(await cur.fetchall())

    async def list_by_submitter(self, submitter_user_id: int, *, limit: int,
                                created_cursor: Optional[float] = None,
                                id_cursor: Optional[int] = None) -> list:
        """Own-submission history by the VERIFIED human submitter (§identity).

        Only rows with explicit human attribution (submitter_user_id) are ever
        returned; service/automatic submissions (submitter NULL) never appear,
        even when an API token bound to this user created them.
        """
        async with db_manager.get_db() as conn:
            if created_cursor is None:
                cur = await conn.execute(
                    "SELECT * FROM pending_reviews WHERE submitter_user_id=? "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (int(submitter_user_id), limit),
                )
            else:
                cur = await conn.execute(
                    "SELECT * FROM pending_reviews WHERE submitter_user_id=? "
                    "AND (created_at, id) < (?, ?) "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (int(submitter_user_id), created_cursor, id_cursor, limit),
                )
            return list(await cur.fetchall())

    async def list_logical_submissions(
        self, submitter_user_id: int, *, limit: int,
        updated_cursor: Optional[float] = None,
        id_cursor: Optional[int] = None,
    ) -> list:
        """One row per review CHAIN for the verified human submitter (§mine).

        A refetch replacement is a new generation of the SAME logical
        submission, so the list must never show A/B/C as three items. The head
        of each chain (max id, i.e. the current, not-superseded generation) is
        returned with chain aggregates, keyset-paged on the head's
        ``updated_at`` so pagination stays stable while generations change.

        Aggregates returned per head row:
          * ``generation_count``  — generations in the chain (1 for a fresh post)
          * ``chain_created_at``  — when the logical submission was first made
        """
        sql_head = """
            SELECT h.*,
                   (SELECT COUNT(*) FROM pending_reviews c
                     WHERE c.review_chain_id = h.review_chain_id)
                       AS generation_count,
                   (SELECT MIN(c.created_at) FROM pending_reviews c
                     WHERE c.review_chain_id = h.review_chain_id)
                       AS chain_created_at
              FROM pending_reviews h
             WHERE h.submitter_user_id = ?
               AND h.id = (SELECT MAX(c2.id) FROM pending_reviews c2
                            WHERE c2.review_chain_id = h.review_chain_id)
        """
        async with db_manager.get_db() as conn:
            if updated_cursor is None:
                cur = await conn.execute(
                    sql_head + " ORDER BY h.updated_at DESC, h.id DESC LIMIT ?",
                    (int(submitter_user_id), limit),
                )
            else:
                cur = await conn.execute(
                    sql_head
                    + " AND (h.updated_at, h.id) < (?, ?)"
                    + " ORDER BY h.updated_at DESC, h.id DESC LIMIT ?",
                    (int(submitter_user_id), updated_cursor, id_cursor, limit),
                )
            return list(await cur.fetchall())

    async def head_of_chain(self, review_chain_id: str):
        """The current (latest) generation of one chain, with chain aggregates."""
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                """
                SELECT h.*,
                       (SELECT COUNT(*) FROM pending_reviews c
                         WHERE c.review_chain_id = h.review_chain_id)
                           AS generation_count,
                       (SELECT MIN(c.created_at) FROM pending_reviews c
                         WHERE c.review_chain_id = h.review_chain_id)
                           AS chain_created_at
                  FROM pending_reviews h
                 WHERE h.review_chain_id = ?
                 ORDER BY h.id DESC LIMIT 1
                """,
                (str(review_chain_id),),
            )
            return await cur.fetchone()

    async def chain_rows(self, review_chain_id: str) -> list:
        """All generations of one chain, oldest first (detail/history view)."""
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM pending_reviews WHERE review_chain_id=? "
                "ORDER BY id ASC",
                (str(review_chain_id),),
            )
            return list(await cur.fetchall())

    async def _select_in_conn(self, conn, review_id: int):
        cur = await conn.execute(
            "SELECT * FROM pending_reviews WHERE id=?", (review_id,)
        )
        return await cur.fetchone()
