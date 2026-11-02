"""Durable, publication-scoped novel preview records (§telepress-preview).

One row per PUBLICATION (keyed by the publication's stable idempotency key),
not per submission and not per review: the enrichment belongs to an immutable
publication intent. The row is what makes ``Telegraph preview generation is
publication-idempotent`` true — a publication retry (Telegram transient
failure, process restart, outbox replay) reuses the recorded URL instead of
creating a second Telegraph page.

TelePress's own content cache is NOT this guarantee: even when the provider
would return the same page for the same text, TelePost still has to know that
*this publication* already has a preview URL, so the provider is not called
again at all.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from database.db_manager import get_db


def process_identity(pid=None):
    """Linux process generation, resistant to PID reuse and machine restarts."""
    pid = os.getpid() if pid is None else int(pid)
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    start = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    return f"{boot}:{pid}:{start}"


def attempt_alive(attempt):
    """Unknown platforms/legacy identities fail closed."""
    parts = str(attempt).split(":")
    if len(parts) != 4:
        return True
    try:
        # Missing /proc is not evidence that an owner exited (macOS/Windows).
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return True
    if boot != parts[0]:
        return True  # A different machine/boot cannot prove remote work stopped.
    try:
        return process_identity(parts[1]) == ":".join(parts[:3])
    except FileNotFoundError:
        # Only a vanished PID on this boot proves this local owner is gone.
        return False
    except (OSError, ValueError, IndexError):
        return True


def preview_key(publication_key: str) -> str:
    """Stable enrichment identity of one publication."""
    return f"publication:{str(publication_key).strip()}:novel-preview"[:240]


@dataclass(frozen=True)
class PreviewRecord:
    publication_key: str
    provider: str
    status: str
    url: str
    title: str
    created_at: float
    updated_at: float
    reason: str = ""
    stage: str = ""
    attempt_id: str = ""
    attempts: int = 0

    @classmethod
    def from_row(cls, row) -> "PreviewRecord":
        return cls(
            publication_key=row["publication_key"],
            provider=row["provider"] or "",
            status=row["status"],
            url=row["url"] or "",
            title=row["title"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            reason=row["reason"], stage=row["stage"],
            attempt_id=row["attempt_id"], attempts=row["attempts"],
        )


class PublicationPreviewRepository:
    async def find(self, publication_key: str) -> Optional[PreviewRecord]:
        key = preview_key(publication_key)
        if not publication_key:
            return None
        async with get_db() as conn:
            cursor = await conn.execute(
                "SELECT * FROM publication_previews WHERE publication_key=?",
                (key,),
            )
            row = await cursor.fetchone()
        return PreviewRecord.from_row(row) if row else None

    async def upsert(self, publication_key: str, *, provider: str, status: str,
                     url: str = "", title: str = "",
                     reason: str = "", stage: str = "",
                     now: Optional[float] = None) -> None:
        """Import an outcome; successful URLs are immutable. Live work uses claim/finish."""
        key = preview_key(publication_key)
        now = time.time() if now is None else now
        async with get_db() as conn:
            await conn.execute(
                """
                INSERT INTO publication_previews (
                  publication_key, provider, status, url, title,
                  created_at, updated_at, reason, stage
                ) VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(publication_key) DO UPDATE SET
                  provider=excluded.provider,
                  status=excluded.status,
                  url=excluded.url,
                  title=excluded.title,
                  updated_at=excluded.updated_at,
                  reason=excluded.reason, stage=excluded.stage
                WHERE publication_previews.status != 'succeeded'
                """,
                (key, str(provider or ""), str(status or ""), str(url or ""),
                 str(title or ""), now, now, reason, stage),
            )

    async def claim(self, publication_key: str, *, retry: bool = False,
                    expected_attempt: Optional[str] = None) -> str:
        """Single writer across processes. No time-based reclaim of remote work."""
        import uuid
        try:
            owner = process_identity()
        except (OSError, ValueError, IndexError):
            owner = "unknown"
        attempt = owner + ":" + uuid.uuid4().hex
        now = time.time()
        async with get_db() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO publication_previews "
                "(publication_key, status, created_at, updated_at) VALUES (?, '', ?, ?)",
                (preview_key(publication_key), now, now),
            )
            states = "('', 'failed', 'timeout')" if retry else "('')"
            cursor = await conn.execute(
                "UPDATE publication_previews SET status='generating', attempt_id=?, "
                "attempts=attempts+1, updated_at=?, reason='', stage='txt_read' "
                f"WHERE publication_key=? AND status IN {states} AND url='' "
                "AND (? IS NULL OR attempt_id=?)",
                (attempt, now, preview_key(publication_key), expected_attempt, expected_attempt),
            )
        return attempt if cursor.rowcount == 1 else ""

    async def finish(self, publication_key: str, attempt: str, *, status: str,
                     url: str = "", reason: str = "", stage: str = "",
                     title: str = "") -> None:
        async with get_db() as conn:
            await conn.execute(
                "UPDATE publication_previews SET provider='telepress', status=?, url=?, "
                "reason=?, stage=?, title=?, updated_at=? "
                "WHERE publication_key=? AND attempt_id=? "
                "AND status IN ('generating', 'timeout')",
                (status, url, reason, stage, title, time.time(),
                 preview_key(publication_key), attempt),
            )

    async def adopt(self, publication_key: str, url: str) -> bool:
        """Attach an operator-reconciled existing page; never overwrite a live attempt."""
        record = await self.find(publication_key)
        if (record and record.status == "timeout" and record.attempt_id
                and attempt_alive(record.attempt_id)):
            return False
        if record and record.status == "generating":
            if not await self.release_abandoned(publication_key, record.attempt_id):
                return False
        async with get_db() as conn:
            cursor = await conn.execute(
                "UPDATE publication_previews SET status='succeeded', url=?, "
                "stage='reconciled', reason='', updated_at=? "
                "WHERE publication_key=? AND status IN ('failed', 'timeout') AND url='' "
                "AND attempt_id=?",
                (url, time.time(), preview_key(publication_key),
                 record.attempt_id if record else ""),
            )
        return cursor.rowcount == 1

    async def release_abandoned(self, publication_key: str, attempt: str) -> bool:
        """Only after explicit remote reconciliation AND proof the owner exited."""
        if attempt_alive(attempt):
            return False
        async with get_db() as conn:
            cursor = await conn.execute(
                "UPDATE publication_previews SET status='failed', stage='reconciled', "
                "reason='abandoned_owner', updated_at=? "
                "WHERE publication_key=? AND attempt_id=? AND status='generating'",
                (time.time(), preview_key(publication_key), attempt),
            )
        return cursor.rowcount == 1


    async def set_stage(self, publication_key: str, attempt: str, stage: str) -> None:
        async with get_db() as conn:
            await conn.execute(
                "UPDATE publication_previews SET stage=?, updated_at=? "
                "WHERE publication_key=? AND attempt_id=? AND status='generating'",
                (stage, time.time(), preview_key(publication_key), attempt),
            )
