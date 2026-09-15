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

import time
from dataclasses import dataclass
from typing import Optional

from database.db_manager import get_db


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
                     now: Optional[float] = None) -> None:
        """Record the terminal enrichment outcome for this publication.

        A terminal row is authoritative: a later retry of the same publication
        reuses it (including a failure) instead of calling the provider again.
        """
        key = preview_key(publication_key)
        now = time.time() if now is None else now
        async with get_db() as conn:
            await conn.execute(
                """
                INSERT INTO publication_previews (
                  publication_key, provider, status, url, title,
                  created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(publication_key) DO UPDATE SET
                  provider=excluded.provider,
                  status=excluded.status,
                  url=excluded.url,
                  title=excluded.title,
                  updated_at=excluded.updated_at
                """,
                (key, str(provider or ""), str(status or ""), str(url or ""),
                 str(title or ""), now, now),
            )
