"""Durable webhook inbox storage (§durable-inbox).

One row per Telegram Update, committed BEFORE the HTTP 200 so a process
crash can never silently drop an acknowledged update. Each bot process owns
its own SQLite database, so ``update_id`` alone is the (bot, update_id)
identity.

Durability contract: the receive path writes through a dedicated connection
with ``synchronous=FULL`` — the insert reaches the WAL file before the ack.
State transitions (claims, done marks) use the shared WAL connection with
``synchronous=NORMAL``: still process-crash safe, which is the threat model
here (Telegram redelivery only compensates for failures before the 200).
"""
from __future__ import annotations

import time
from typing import Optional

import aiosqlite

from database import db_manager
from database.db_manager import get_db

# transient processing failures back off geometrically, capped at one hour
BACKOFF_BASE_SECONDS = 30.0
BACKOFF_CAP_SECONDS = 3600.0


class InboxRepository:
    async def receive(self, update_id: int, payload_json: str, *,
                      now: Optional[float] = None) -> bool:
        """Persist a freshly received Update. True iff this delivery created
        the row (a duplicate delivery of a known update never requeues)."""
        now = time.time() if now is None else now
        # Read at call time so per-bot/test DB_PATH overrides apply.
        db_path = db_manager.DB_PATH
        conn = await aiosqlite.connect(db_path)
        try:
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("PRAGMA synchronous=FULL")
            cur = await conn.execute(
                "INSERT OR IGNORE INTO webhook_inbox "
                "(update_id, payload, state, received_at, updated_at, next_attempt_at) "
                "VALUES (?,?, 'pending', ?,?, 0)",
                (update_id, payload_json, now, now),
            )
            inserted = cur.rowcount == 1
            await conn.commit()
            return inserted
        finally:
            await conn.close()

    async def claim_next_due(self, *, now: float,
                             lease_seconds: float) -> Optional[dict]:
        """Claim the head of the inbox for serial processing.

        Strictly ordered: only the LOWEST unfinished update_id is ever
        claimable, and only once its retry backoff has expired. A backed-off
        head row therefore blocks everything behind it (bounded by
        max_attempts → dead) — that is what keeps replayed updates from
        landing in a newer conversation state."""
        async with get_db() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "SELECT update_id, next_attempt_at FROM webhook_inbox "
                "WHERE state='pending' ORDER BY update_id ASC LIMIT 1",
            )
            row = await cur.fetchone()
            if row is None:
                return None
            if float(row["next_attempt_at"]) > now:
                return None  # head not due yet: nothing later may pass it
            cur = await conn.execute(
                "UPDATE webhook_inbox SET state='processing', lease_until=?, "
                "updated_at=? WHERE update_id=? AND state='pending'",
                (now + lease_seconds, now, row["update_id"]),
            )
            if cur.rowcount != 1:
                return None  # lost the claim; next drain picks another row
            cur = await conn.execute(
                "SELECT * FROM webhook_inbox WHERE update_id=?", (row["update_id"],)
            )
            claimed = await cur.fetchone()
            return dict(claimed) if claimed else None

    async def mark_done(self, update_id: int, *, now: float) -> None:
        async with get_db() as conn:
            await conn.execute(
                "UPDATE webhook_inbox SET state='done', done_at=?, updated_at=?, "
                "lease_until=NULL, last_error='' WHERE update_id=?",
                (now, now, update_id),
            )

    async def mark_failed(self, update_id: int, *, now: float, error: str,
                          max_attempts: int) -> None:
        """Requeue with geometric backoff; past max_attempts the row becomes
        a visible, non-retrying `dead` state for operator inspection."""
        async with get_db() as conn:
            await conn.execute(
                "UPDATE webhook_inbox SET attempts=attempts+1, last_error=?, "
                "updated_at=? WHERE update_id=?",
                (error, now, update_id),
            )
            cur = await conn.execute(
                "SELECT attempts FROM webhook_inbox WHERE update_id=?",
                (update_id,),
            )
            row = await cur.fetchone()
            attempts = row["attempts"] if row else max_attempts
            if attempts >= max_attempts:
                await conn.execute(
                    "UPDATE webhook_inbox SET state='dead', lease_until=NULL, "
                    "updated_at=? WHERE update_id=?", (now, update_id),
                )
            else:
                backoff = min(
                    BACKOFF_CAP_SECONDS,
                    BACKOFF_BASE_SECONDS * (2 ** min(attempts, 7)),
                )
                await conn.execute(
                    "UPDATE webhook_inbox SET state='pending', lease_until=NULL, "
                    "next_attempt_at=?, updated_at=? WHERE update_id=?",
                    (now + backoff, now, update_id),
                )

    async def mark_dead(self, update_id: int, *, now: float, error: str) -> None:
        """Permanent reject without retry (payload unparsable at processing)."""
        async with get_db() as conn:
            await conn.execute(
                "UPDATE webhook_inbox SET state='dead', last_error=?, "
                "lease_until=NULL, updated_at=? WHERE update_id=?",
                (error, now, update_id),
            )

    async def recover_stale(self, *, now: float) -> int:
        """Startup recovery: every in-flight 'processing' row goes back to
        pending. The previous holder is dead by construction (this process is
        the single worker per bot database), so the lease is reclaimed rather
        than honoured."""
        async with get_db() as conn:
            cur = await conn.execute(
                "UPDATE webhook_inbox SET state='pending', lease_until=NULL, "
                "next_attempt_at=0, updated_at=? WHERE state='processing'",
                (now,),
            )
            return cur.rowcount

    async def counts(self) -> dict:
        async with get_db() as conn:
            cur = await conn.execute(
                "SELECT state, count(*) AS n, min(updated_at) AS oldest "
                "FROM webhook_inbox GROUP BY state"
            )
            return {row["state"]: {"n": row["n"], "oldest": row["oldest"]}
                    for row in await cur.fetchall()}
