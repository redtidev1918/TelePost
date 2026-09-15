"""Durable manual-recovery attempts (§manual-recovery).

Exactly-once and one-active-per-target are enforced here; the application
service owns the business flow. The table is a sibling of ``refetch_attempts``:
a recovery re-runs a FAILED schedule target under a policy preset, while a
refetch replaces a specific review generation.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional


class RecoveryRepository:
    def __init__(self, conn):
        self._conn = conn

    async def find_by_callback_key(self, callback_key: str) -> Optional[dict]:
        cur = await self._conn.execute(
            "SELECT * FROM recovery_attempts WHERE callback_key = ?",
            (callback_key,),
        )
        return await cur.fetchone()

    async def create_attempt(self, *, target_id: str, retry_mode: str,
                             callback_key: str, schedule_id: str = ""):
        """INSERT one attempt; returns (attempt, None) or (None, 'already_running').

        One active recovery per target is enforced by the partial UNIQUE index
        on (target_id) WHERE state IN ('requested','accepted').
        """
        request_id = uuid.uuid4().hex
        try:
            await self._conn.execute(
                "INSERT INTO recovery_attempts "
                "(request_id, slot_id, schedule_id, target_id, retry_mode, "
                " state, callback_key, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'requested', ?, ?)",
                (request_id, "", schedule_id or "", target_id, retry_mode,
                 callback_key, time.time()),
            )
        except Exception as exc:
            # The ONLY expected constraint here is the partial UNIQUE index
            # (one active recovery per target) or a replayed callback_key.
            if "UNIQUE constraint failed" not in str(exc):
                raise
            active = await self._conn.execute(
                "SELECT * FROM recovery_attempts WHERE target_id = ? "
                "AND state IN ('requested','accepted') LIMIT 1",
                (target_id,),
            )
            row = await active.fetchone()
            if row is not None and row["callback_key"] != callback_key:
                return None, "already_running"
            return None, "already_running"
        cur = await self._conn.execute(
            "SELECT * FROM recovery_attempts WHERE request_id = ?", (request_id,)
        )
        return await cur.fetchone(), None

    async def mark_accepted(self, request_id: str, slot_id: str) -> None:
        await self._conn.execute(
            "UPDATE recovery_attempts SET state='accepted', slot_id=? WHERE request_id=?",
            (slot_id or "", request_id),
        )

    async def mark_failed(self, request_id: str, failure_code: str) -> None:
        await self._conn.execute(
            "UPDATE recovery_attempts SET state='failed', failure_code=?, "
            "finished_at=CURRENT_TIMESTAMP WHERE request_id=? AND state IN ('requested','accepted')",
            (failure_code or "", request_id),
        )

    async def find_active_by_target(self, target_id: str) -> Optional[dict]:
        cur = await self._conn.execute(
            "SELECT * FROM recovery_attempts WHERE target_id = ? "
            "AND state IN ('requested','accepted') ORDER BY id DESC LIMIT 1",
            (target_id,),
        )
        return await cur.fetchone()