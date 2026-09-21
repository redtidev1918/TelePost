"""Operator-driven reaction history backfill.

The Bot API only pushes ``message_reaction_count`` for changes after the
handler ships; posts reacted to before that stay at heat 0. A user MTProto
client (Pyrogram) can read historical reaction totals, so the backfill script
hands the raw per-message counts to :func:`project_counts`, which reuses the
exact projection/aggregation logic as live reaction ingestion. This keeps one
source of truth for how a ``(message_id, total_count)`` fact becomes
``message_reaction_counts`` + ``published_posts.reactions/heat_score``.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Iterable, Set, Tuple

from database.db_manager import get_db
from handlers import reaction_stats
from telepost.observability.audit import record_event

logger = logging.getLogger(__name__)


def normalize_counts(raw: Iterable[Tuple[int, int]]) -> Dict[int, int]:
    """Collapse scraped per-message totals into stable ints.

    Negative/zero totals are kept so an explicit ``0`` overwrites stale data;
    non-numeric rows are dropped loudly.
    """
    out: Dict[int, int] = {}
    for message_id, total in raw:
        try:
            mid = int(message_id)
            count = int(total)
        except (TypeError, ValueError):
            logger.warning("reaction backfill dropped non-numeric row: %r=%r",
                           message_id, total)
            continue
        if mid <= 0:
            logger.warning("reaction backfill dropped non-positive message_id: %r",
                           message_id)
            continue
        out[mid] = count
    return out


async def project_counts(
    counts: Dict[int, int],
    *,
    dry_run: bool = False,
    actor: str = "operator:reaction-backfill",
) -> Dict[str, object]:
    """Persist scraped reaction facts and recompute owning post heat.

    ``dry_run`` performs the same discovery (which messages map to which posts)
    but writes nothing; the returned counters let an operator preview the blast
    radius before applying.
    """
    now = time.time()
    affected_posts: Set[int] = set()
    seen_messages: Set[int] = set()

    async with get_db() as conn:
        cursor = await conn.cursor()
        for message_id, total in sorted(counts.items()):
            seen_messages.add(message_id)
            if not dry_run:
                await cursor.execute(
                    """
                    INSERT INTO message_reaction_counts (message_id, total_count, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(message_id) DO UPDATE SET
                        total_count = excluded.total_count,
                        updated_at = excluded.updated_at
                    """,
                    (message_id, total, now),
                )
            for row in await reaction_stats._post_rows_containing(conn, message_id):
                post_message_id = int(row["message_id"] or 0)
                if not post_message_id:
                    continue
                affected_posts.add(post_message_id)
                if not dry_run:
                    await reaction_stats._refresh_post_row(conn, row, now=now)

    if not dry_run and affected_posts:
        await reaction_stats._refresh_search_indexes(list(affected_posts))
        await record_event(
            "reaction.backfilled",
            actor=actor,
            detail={
                "messages": len(counts),
                "posts": len(affected_posts),
                "updated_at": now,
            },
        )
        logger.info("reaction backfill applied: messages=%s posts=%s",
                    len(counts), len(affected_posts))

    return {
        "dry_run": bool(dry_run),
        "messages": len(seen_messages),
        "posts": len(affected_posts),
    }
