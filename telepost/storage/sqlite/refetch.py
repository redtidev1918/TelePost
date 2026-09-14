"""SQLite repository for the review ``refetch`` lineage.

Owns ``refetch_attempts`` and ``refetch_seen_candidates``. The `pending_reviews`
row stays the source of truth for review state; these tables record the
attempt lifecycle and which candidates a review chain has already shown.

Invariants (enforced here, data-level where possible):

* one active attempt per review chain — partial UNIQUE index
  ``idx_refetch_one_active`` rejects the second row at the database level;
* request idempotency — the same ``callback_key`` (Telegram callback id) always
  resolves to the same attempt and the same ``request_id``, and the same
  ``request_id`` sent to PixivFlow maps to the same durable slot;
* a new generation is only created by a NEW user click (new callback id) after
  the previous attempt is terminal;
* replacement is commit-after-success: the new review row is inserted first and
  the old review is superseded only in the same transaction that marks the
  attempt ``replaced``;
* stale asynchronous results never overwrite a terminal/superseded review
  (``apply_outcome`` and ``apply_replacement`` re-check that the source review
  is still ``pending`` before writing anything).
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional, Tuple

from database import db_manager

# External business states; internal sub-states stay private to this module.
ACTIVE_STATES = ("requested", "admitted")
TERMINAL_STATES = ("replaced", "no_alternative", "failed", "obsolete")


class RefetchRepository:
    # ---- reads ---------------------------------------------------------
    async def find_by_callback_key(self, callback_key: str):
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM refetch_attempts WHERE callback_key = ?",
                (callback_key,),
            )
            return await cur.fetchone()

    async def find_by_request_id(self, request_id: str):
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM refetch_attempts WHERE request_id = ?",
                (request_id,),
            )
            return await cur.fetchone()

    async def find_active_by_chain(self, review_chain_id: str):
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM refetch_attempts "
                "WHERE review_chain_id = ? AND state IN ('requested', 'admitted') "
                "ORDER BY created_at ASC LIMIT 1",
                (review_chain_id,),
            )
            return await cur.fetchone()

    # ---- attempt creation (idempotent per callback key) -----------------
    async def create_attempt(
        self,
        *,
        callback_key: str,
        review_chain_id: str,
        generation: int,
        source_review_id: int,
        source_candidate_id: str,
        request_id: Optional[str] = None,
    ) -> Tuple[Optional[Any], Optional[str]]:
        """Insert a new attempt or return the existing one.

        Returns ``(row, None)`` on success/replay and ``(None, reason)`` when
        the data layer refused a second ACTIVE attempt for this chain (the
        caller should answer "正在重抓，请稍候").
        """
        now = time.time()
        rid = request_id or str(uuid.uuid4())
        async with db_manager.get_db() as conn:
            try:
                cur = await conn.execute(
                    """
                    INSERT INTO refetch_attempts (
                        callback_key, request_id, review_chain_id, generation,
                        source_review_id, source_candidate_id, state,
                        created_at, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'requested', ?, ?)
                    """,
                    (callback_key, rid, review_chain_id, generation,
                     source_review_id, source_candidate_id, now, now),
                )
                attempt_id = cur.lastrowid
            except Exception as exc:
                import aiosqlite
                if not isinstance(exc, aiosqlite.IntegrityError):
                    raise
                # Either the same callback (idempotent replay) or a different
                # user click while an attempt is already active.
                existing = await self.find_by_callback_key(callback_key)
                if existing is not None:
                    return existing, None
                active = await self.find_active_by_chain(review_chain_id)
                return None, "already_running"
            # Read on the SAME (uncommitted) connection: the outer transaction
            # has not committed yet, so a second connection cannot see the row.
            cur = await conn.execute(
                "SELECT * FROM refetch_attempts WHERE id = ?", (attempt_id,)
            )
            return await cur.fetchone(), None

    # ---- state transitions ----------------------------------------------
    async def mark_admitted(self, request_id: str, slot_id: str) -> bool:
        """PixivFlow accepted the request (202): durable slot exists there."""
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "UPDATE refetch_attempts SET state='admitted', slot_id=?, started_at=? "
                "WHERE request_id=? AND state IN ('requested', 'admitted')",
                (slot_id, time.time(), request_id),
            )
            return cur.rowcount == 1

    async def mark_failed(self, request_id: str, failure_code: str) -> bool:
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "UPDATE refetch_attempts SET state='failed', failure_code=?, finished_at=? "
                "WHERE request_id=? AND state IN ('requested', 'admitted')",
                (failure_code, time.time(), request_id),
            )
            return cur.rowcount == 1

    async def mark_replaced(self, request_id: str,
                            result_candidate_id: str) -> bool:
        """Terminal 'replaced' (replacement delivered and linked).

        The normal path runs inside the submission transaction
        (``finalize_replacement``); this is the explicit transition for
        callers/tests that already know the replacement landed.
        """
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "UPDATE refetch_attempts SET state='replaced', "
                "result_candidate_id=?, finished_at=? "
                "WHERE request_id=? AND state IN ('requested', 'admitted')",
                (result_candidate_id, time.time(), request_id),
            )
            return cur.rowcount == 1

    async def active_since(self, cutoff_remind: float, cutoff_fail: float) -> list:
        """Active attempts (requested/admitted) that have been waiting too long.

        Used by the progress watchdog: rows older than ``cutoff_remind`` deserve
        a "still running" reminder; rows older than ``cutoff_fail`` must be
        failed (no terminal outcome ever arrived). Bounded batch, oldest first.
        """
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM refetch_attempts "
                "WHERE state IN ('requested', 'admitted') AND created_at <= ? "
                "ORDER BY created_at ASC LIMIT 50",
                (cutoff_remind,),
            )
            out = []
            for row in await cur.fetchall():
                out.append((row, "stale" if row["created_at"] <= cutoff_fail else "remind"))
            return out

    async def bump_progress_notified(self, request_id: str, at: float) -> bool:
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "UPDATE refetch_attempts SET last_progress_notified_at=? "
                "WHERE request_id=?",
                (at, request_id),
            )
            return cur.rowcount == 1

    async def resolve_replacement(self, conn, request_id: str):
        """Resolve a submission's refetch correlation inside an open connection.

        Returns the attempt row plus its source review when the attempt is
        still ACTIVE; ``(None, None)`` when it is unknown or already terminal.
        The caller must still re-check the source review's status within the
        same transaction before applying anything.
        """
        cur = await conn.execute(
            "SELECT * FROM refetch_attempts WHERE request_id = ?", (request_id,)
        )
        attempt = await cur.fetchone()
        if attempt is None or attempt["state"] not in ACTIVE_STATES:
            return None, None
        cur = await conn.execute(
            "SELECT * FROM pending_reviews WHERE id = ?",
            (attempt["source_review_id"],),
        )
        source = await cur.fetchone()
        return attempt, source

    async def insert_seen(
        self, conn, *, review_chain_id: str, candidate_id: str,
        source: str, generation: int, created_at: Optional[float] = None,
    ) -> None:
        await conn.execute(
            "INSERT OR IGNORE INTO refetch_seen_candidates "
            "(review_chain_id, candidate_id, source, generation, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (review_chain_id, candidate_id, source, generation,
             created_at or time.time()),
        )

    async def finalize_replacement(
        self,
        conn,
        *,
        attempt,
        new_review_id: int,
        new_candidate_id: str,
        source_review_id: int,
        review_chain_id: str,
        generation: int,
    ) -> str:
        """COMMIT the replacement inside the caller's transaction.

        Returns the resulting attempt state: ``'replaced'`` when the source was
        still pending and the chain advanced, ``'obsolete'`` when the source
        left pending (approve/reject/expire raced the result) — in which case
        nothing is linked and nothing is superseded.
        """
        cur = await conn.execute(
            "UPDATE pending_reviews SET status='superseded', updated_at=? "
            "WHERE id=? AND status='pending'",
            (time.time(), source_review_id),
        )
        if cur.rowcount != 1:
            # The reviewer already decided; a late replacement must not
            # overwrite that decision or create a phantom superseded row.
            await conn.execute(
                "UPDATE refetch_attempts SET state='obsolete', finished_at=? "
                "WHERE request_id=?",
                (time.time(), attempt["request_id"]),
            )
            return "obsolete"

        # Link the NEW review into the chain and the attempt's result.
        await conn.execute(
            "UPDATE pending_reviews SET review_chain_id=?, generation=?, "
            "supersedes_review_id=?, updated_at=? WHERE id=?",
            (review_chain_id, generation, source_review_id, time.time(),
             new_review_id),
        )
        await conn.execute(
            "UPDATE refetch_attempts SET state='replaced', "
            "result_candidate_id=?, finished_at=? WHERE request_id=?",
            (new_candidate_id, time.time(), attempt["request_id"]),
        )
        await self.insert_seen(
            conn, review_chain_id=review_chain_id, candidate_id=new_candidate_id,
            source="replacement", generation=generation,
        )
        return "replaced"

    async def apply_outcome(
        self,
        request_id: str,
        disposition: str,
        *,
        reason: str = "",
        scanned: int = 0,
        skipped_duplicate: int = 0,
        skipped_invalid: int = 0,
        skipped_unavailable: int = 0,
    ) -> Tuple[Optional[Any], str, bool]:
        """Apply a PixivFlow terminal verdict (no_alternative | failed).

        Returns ``(attempt_row, applied_state, changed)`` where ``applied_state``
        is the attempt's state after the call and ``changed`` is True only when
        THIS call moved the attempt (an already-terminal replay returns
        ``changed=False`` so the caller does not re-notify). Never touches a
        review the reviewer already decided.
        """
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM refetch_attempts WHERE request_id = ?",
                (request_id,),
            )
            attempt = await cur.fetchone()
            if attempt is None:
                return None, "not_found", False
            if attempt["state"] not in ACTIVE_STATES:
                return attempt, attempt["state"], False
            cur = await conn.execute(
                "SELECT * FROM pending_reviews WHERE id = ?",
                (attempt["source_review_id"],),
            )
            source = await cur.fetchone()
            if source is None or source["status"] != "pending":
                await conn.execute(
                    "UPDATE refetch_attempts SET state='obsolete', finished_at=? "
                    "WHERE request_id=?",
                    (time.time(), request_id),
                )
                return None, "obsolete", True

            state = "no_alternative" if disposition == "no_alternative" else "failed"
            await conn.execute(
                "UPDATE refetch_attempts SET state=?, failure_code=?, "
                "scanned=?, skipped_duplicate=?, skipped_invalid=?, "
                "skipped_unavailable=?, finished_at=? WHERE request_id=?",
                (state, (reason or "")[:400], int(scanned),
                 int(skipped_duplicate), int(skipped_invalid),
                 int(skipped_unavailable), time.time(), request_id),
            )
            cur = await conn.execute(
                "SELECT * FROM refetch_attempts WHERE request_id = ?",
                (request_id,),
            )
            return await cur.fetchone(), state, True

    async def chain_of_review(self, conn, review_row) -> Tuple[str, int]:
        """Stable (chain_id, generation) for a review; bootstraps if missing.

        Runs on the caller's connection so the bootstrap commit is atomic with
        whatever the caller is doing (e.g. attempt creation).
        """
        chain_id = review_row["review_chain_id"] or ""
        if not chain_id:
            chain_id = f"chain-{review_row['id']}"
            await conn.execute(
                "UPDATE pending_reviews SET review_chain_id=?, updated_at=? "
                "WHERE id=?",
                (chain_id, time.time(), review_row["id"]),
            )
        return chain_id, int(review_row["generation"] or 0)

    async def next_generation(self, review_chain_id: str) -> int:
        """Generation for the NEXT attempt on a chain.

        Every intentional click increments the chain's generation (spec §12), so
        a no_alternative attempt followed by a fresh click on the SAME review is
        a NEW generation, and A→B→C replacements number 1,2,3.
        """
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT COALESCE(MAX(generation), 0) AS max_gen "
                "FROM refetch_attempts WHERE review_chain_id = ?",
                (review_chain_id,),
            )
            row = await cur.fetchone()
            return int(row["max_gen"] or 0) + 1

    async def seed_original_seen(self, conn, review_row) -> None:
        """Ensure the CURRENT candidate of every review is in seen-history.

        Keeps the spec invariant "the current candidate is always in the seen
        set" true for reviews created after the migration bootstrap.
        """
        if not review_row["pixiv_id"]:
            return
        chain_id, generation = await self.chain_of_review(conn, review_row)
        await self.insert_seen(
            conn, review_chain_id=chain_id,
            candidate_id=review_row["pixiv_id"],
            source="original", generation=int(review_row["generation"] or 0),
        )