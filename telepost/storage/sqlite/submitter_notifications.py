"""Durable, idempotent submitter publication notifications (§notify-submitter).

IMPORTANT INVARIANT: the notification trigger is PUBLICATION SUCCESS, never
review approval. Approval is an intermediate moderation event; the only
user-visible terminal shared by every submission path is ``published``.

One durable row per (publication message id, submitter) — the idempotency key
``publication:<message_id>:submitter-notification`` — so a replay of the same
publication (direct, reviewed, or editorial) can never produce a second DM.
The row carries a publication CONTEXT payload; the message text is formatted at
delivery time by the application layer. Telegram failure only retries the ROW:
the publication itself is never rolled back.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from database.db_manager import get_db


def notification_key(publication_id: int) -> str:
    return f"publication:{int(publication_id)}:submitter-notification"


class SubmitterNotificationRepository:
    async def enqueue(
        self,
        publication_id: int,
        telegram_user_id: int,
        payload: Dict[str, Any],
        now: Optional[float] = None,
    ) -> bool:
        now = time.time() if now is None else now
        key = notification_key(publication_id)
        import json as _json

        async with get_db() as conn:
            cur = await conn.execute(
                """
                INSERT OR IGNORE INTO submitter_notifications (
                  review_id, revision_id, telegram_user_id, idempotency_key,
                  kind, state, payload, created_at, updated_at
                ) VALUES (?,?,?,?,?, 'pending', ?, ?, ?)
                """,
                (
                    int(payload.get("review_id") or 0) or None,
                    int(payload.get("revision_id") or 0) or None,
                    int(telegram_user_id),
                    key,
                    str(payload.get("source") or "publication"),
                    _json.dumps(payload, ensure_ascii=False),
                    now, now,
                ),
            )
            return cur.rowcount == 1

    async def pending(self, limit: int = 20) -> List[Dict[str, Any]]:
        async with get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM submitter_notifications WHERE state = 'pending' "
                "ORDER BY created_at ASC LIMIT ?", (max(1, min(int(limit), 50)),),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def mark_sent(self, notification_id: int, message_id: int,
                        now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        async with get_db() as conn:
            cur = await conn.execute(
                "UPDATE submitter_notifications SET state = 'sent', message_id = ?, "
                "sent_at = ?, updated_at = ? WHERE id = ? AND state = 'pending'",
                (int(message_id), now, now, int(notification_id)),
            )
            return cur.rowcount == 1

    async def record_error(self, notification_id: int, error: str,
                           now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        async with get_db() as conn:
            await conn.execute(
                "UPDATE submitter_notifications SET attempts = attempts + 1, "
                "last_error = ?, updated_at = ? WHERE id = ?",
                (str(error)[:400], now, int(notification_id)),
            )

    async def get_by_key(self, publication_id: int) -> Optional[Dict[str, Any]]:
        key = notification_key(publication_id)
        async with get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM submitter_notifications WHERE idempotency_key = ?", (key,)
            )
            row = await cur.fetchone()
            return dict(row) if row else None
