"""SQLite-backed delivery idempotency ledger (``delivery_ledger``).

One row per *confirmed* channel post on the direct-publish path, keyed by the
caller's idempotency key. A second, cross-intent lookup by
(target_id, work_type, pixiv_id) catches duplicate works arriving under a
different key.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

from database import db_manager


@dataclass(frozen=True)
class LedgerEntry:
    idempotency_key: str
    target_id: str
    pixiv_id: str
    work_type: str
    status: str
    message_id: Optional[int]
    related_message_ids: list
    user_id: Optional[int]
    created_at: float

    @classmethod
    def from_row(cls, row) -> "LedgerEntry":
        try:
            related = json.loads(row["related_message_ids"] or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            related = []
        return cls(
            idempotency_key=row["idempotency_key"],
            target_id=row["target_id"] or "",
            pixiv_id=row["pixiv_id"] or "",
            work_type=row["work_type"] or "",
            status=row["status"],
            message_id=row["message_id"],
            related_message_ids=related,
            user_id=row["user_id"],
            created_at=row["created_at"],
        )


class DeliveryLedgerRepository:
    async def find_by_key(self, idempotency_key: str) -> Optional[LedgerEntry]:
        if not idempotency_key:
            return None
        async with db_manager.get_db() as conn:
            cursor = await conn.execute(
                "SELECT * FROM delivery_ledger WHERE idempotency_key=?",
                (idempotency_key,),
            )
            row = await cursor.fetchone()
        return LedgerEntry.from_row(row) if row else None

    async def find_work(self, target_id: str, work_type: str, pixiv_id: str,
                        window_seconds: int) -> Optional[LedgerEntry]:
        if not pixiv_id:
            return None
        async with db_manager.get_db() as conn:
            sql = (
                "SELECT * FROM delivery_ledger WHERE pixiv_id=? AND work_type=? "
                "AND status='published' AND created_at >= ?"
            )
            params: list = [pixiv_id, work_type, time.time() - window_seconds]
            if target_id:
                sql += " AND target_id=?"
                params.append(target_id)
            sql += " ORDER BY created_at DESC LIMIT 1"
            cursor = await conn.execute(sql, params)
            row = await cursor.fetchone()
        return LedgerEntry.from_row(row) if row else None

    async def record_published(self, idempotency_key: str, *, target_id: str,
                               pixiv_id: str, work_type: str,
                               message_id: int, related_message_ids: list,
                               user_id: int) -> bool:
        """Persist a confirmed publication. False on a UNIQUE replay race."""
        if not idempotency_key:
            return False
        async with db_manager.get_db() as conn:
            try:
                await conn.execute(
                    "INSERT INTO delivery_ledger "
                    "(idempotency_key, target_id, pixiv_id, work_type, status, "
                    "message_id, related_message_ids, user_id, created_at) "
                    "VALUES (?, ?, ?, ?, 'published', ?, ?, ?, ?)",
                    (
                        idempotency_key, target_id or "", pixiv_id or "",
                        work_type or "", message_id,
                        json.dumps(related_message_ids or []), user_id, time.time(),
                    ),
                )
                return True
            except Exception:
                # UNIQUE race / replay: the other attempt owns the channel post.
                return False
