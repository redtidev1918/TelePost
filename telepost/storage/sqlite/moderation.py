"""SQLite moderation block repository (§moderation).

One row per blocked subject (user or API token). Blocks are append-only deny
list entries that never mutate submissions history; they only stop NEW
submissions at the entry points. subjects use canonical actor identities
(user:<id> | api:<id>), never display strings.
"""
from __future__ import annotations

import time
from typing import Optional

from database import db_manager


def canonical_actor_subject(actor_subject: str) -> str:
    """Map an actor_subject / token id into the canonical moderation subject."""
    value = str(actor_subject or "").strip()
    if value.startswith("api_token:"):
        return f"api:{value.split(':', 1)[1].strip()}"
    if value.startswith("api:"):
        return f"api:{value.split(':', 1)[1].strip()}"
    if value.startswith("telegram_user:"):
        return f"user:{value.split(':', 1)[1].strip()}"
    if value.startswith("telegram:"):
        return f"user:{value.split(':', 1)[1].strip()}"
    if value.startswith("user:"):
        return value
    return value


def user_subject(user_id) -> str:
    return f"user:{int(user_id)}"


def api_subject(token_id) -> str:
    return f"api:{int(token_id)}"


class ModerationRepository:
    async def add_block(self, subject: str, *, reason: str = "",
                        created_by: int) -> int:
        """Insert a block (idempotent on subject). Returns the row id."""
        subject = canonical_actor_subject(subject)
        now = time.time()
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "INSERT OR IGNORE INTO moderation_blocks "
                "(subject, reason, created_by, created_at) VALUES (?,?,?,?)",
                (subject, str(reason or "")[:500], int(created_by), now),
            )
            await conn.commit()
            if cur.lastrowid:
                return int(cur.lastrowid)
            cur = await conn.execute(
                "SELECT id FROM moderation_blocks WHERE subject=?",
                (subject,),
            )
            row = await cur.fetchone()
            return int(row["id"]) if row else 0

    async def is_blocked(self, subject: str) -> bool:
        subject = canonical_actor_subject(subject)
        if not subject:
            return False
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT 1 FROM moderation_blocks WHERE subject=? LIMIT 1",
                (subject,),
            )
            return await cur.fetchone() is not None

    async def find(self, subject: str):
        subject = canonical_actor_subject(subject)
        if not subject:
            return None
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM moderation_blocks WHERE subject=? LIMIT 1",
                (subject,),
            )
            return await cur.fetchone()
