"""SQLite storage for Workflow Protocol v1 Events received by TelePost the consumer.

TelePost is the consumer in the PixivFlow ↔ TelePost protocol relationship. Two
channels can deliver the same Event to us — a producer callback
(``POST /api/botN/v1/jobs/events``) and the reconcile pull that makes the
stream self-healing. Both must converge to the SAME durable state with the SAME
business effect, so this module is the single write path for persisted protocol
events.

Dedupe guarantee: the ``protocol_job_events.event_id`` column is UNIQUE. A
producer that delivers at least once therefore maps to a no-op here on every
replay — :meth:`ProtocolEventRepository.persist` returns ``inserted=False`` for
an event_id we already hold, and no business handling is repeated.

The per-job ack cursor (``protocol_job_cursors``) is the monotonic event_id of
the newest event we have durably persisted (and acknowledged). It is both the
``?after=`` value for the next reconcile pull and the ``ack_through`` value for
the ack, so a re-pull never re-persists the same events and an ack is idempotent
(an older/unknown cursor is a producer-side no-op, not an error).

The raw Event document is stored verbatim in ``raw``; business handling always
decodes from it, so a future protocol/projection change never needs a data
migration to be correct.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from database import db_manager

#: Protocol timestamps are epoch seconds; guard against millisecond payloads.
_EPOCH_MS_FLOOR = 1e11


def _to_epoch(value: Any) -> Optional[float]:
    """Coerce a protocol ``at`` timestamp to epoch seconds (None when unusable).

    Protocol event timestamps are epoch seconds, but producers have shipped
    millisecond values (fixtures carry 13-digit ``at``), so a value above the
    millisecond floor (year ≥ 1973) is scaled down.
    """
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if number > _EPOCH_MS_FLOOR:
        number = number / 1000.0
    return number


class ProtocolEventRepository:
    """Durable storage for received protocol Events + per-job ack cursors."""

    # ---- events ---------------------------------------------------------
    async def persist(self, event: Dict[str, Any], *, received_at=None) -> Dict[str, Any]:
        """Persist one Event, deduping on ``event_id``.

        Returns ``{"inserted": bool, "row": dict-or-None}``. ``inserted`` is
        True only when THIS call created the row; a replay (same ``event_id``)
        is a no-op that changes nothing and never re-fires business handling.
        """
        return (await self.persist_many([event], received_at=received_at))[0]

    async def persist_many(self, events, *, received_at=None) -> list:
        """Persist a batch of Events; each dedupes independently on ``event_id``.

        Returns one result dict per input event, in order:
        ``{"inserted": bool, "row": dict-or-None}``. An event already held is a
        no-op (inserted=False); newly-inserted rows keep their persisted shape.
        """
        results: list = []
        async with db_manager.get_db() as conn:
            for event in events:
                if not isinstance(event, dict):
                    results.append({"inserted": False, "row": None})
                    continue
                event_id = str(event.get("event_id") or "").strip()
                if not event_id:
                    results.append({"inserted": False, "row": None})
                    continue
                raw = json.dumps(event, ensure_ascii=False)
                cur = await conn.execute(
                    "INSERT INTO protocol_job_events "
                    "(event_id, job_id, event_type, correlation_id, at, raw, received_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(event_id) DO NOTHING",
                    (
                        event_id,
                        str(event.get("job_id") or ""),
                        str(event.get("type") or ""),
                        str(event.get("correlation_id") or ""),
                        _to_epoch(event.get("at")),
                        raw,
                        float(received_at if received_at is not None else time.time()),
                    ),
                )
                if cur.rowcount == 0:
                    results.append({"inserted": False, "row": None})
                    continue
                results.append({
                    "inserted": True,
                    "row": await self._row(conn, event_id),
                })
            await conn.commit()
        return results

    async def find(self, event_id: str) -> Optional[Dict[str, Any]]:
        async with db_manager.get_db() as conn:
            return await self._row(conn, str(event_id))

    @staticmethod
    async def _row(conn, event_id: str) -> Optional[Dict[str, Any]]:
        cur = await conn.execute(
            "SELECT * FROM protocol_job_events WHERE event_id = ?", (event_id,),
        )
        return await cur.fetchone()

    async def decode(self, row) -> Optional[Dict[str, Any]]:
        """The raw Event document stored on ``row`` (or None when unreadable)."""
        if row is None:
            return None
        try:
            decoded = json.loads(row["raw"])
        except (KeyError, TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, dict) else None

    # ---- cursor / ack ----------------------------------------------------
    async def ack_through(self, job_id: str) -> str:
        """The newest event_id we durably hold for ``job_id`` (acked cursor)."""
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT ack_through FROM protocol_job_cursors WHERE job_id = ?",
                (str(job_id),),
            )
            row = await cur.fetchone()
            return str(row["ack_through"] or "") if row else ""

    async def newest_event_id(self, job_id: str) -> str:
        """The max event_id (by insertion order id) we hold for ``job_id``.

        This is the durable ``ack_through`` when a reconcile run persists new
        events and wants to advance the cursor by the newest one.
        """
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT event_id FROM protocol_job_events "
                "WHERE job_id = ? ORDER BY id DESC LIMIT 1",
                (str(job_id),),
            )
            row = await cur.fetchone()
            return str(row["event_id"] or "") if row else ""

    async def set_ack_through(self, job_id: str, event_id: str) -> None:
        """Advance (or set) the per-job ack cursor monotonically.

        Only ever moves forward: a cursor older than one already recorded is
        ignored. ``event_id`` is the newest event we have durably persisted.
        """
        job_id = str(job_id or "")
        event_id = str(event_id or "")
        if not job_id or not event_id:
            return
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT ack_through FROM protocol_job_cursors WHERE job_id = ?",
                (job_id,),
            )
            row = await cur.fetchone()
            current = str(row["ack_through"] or "") if row else ""
            if current == event_id:
                return
            if row is None:
                await conn.execute(
                    "INSERT INTO protocol_job_cursors (job_id, ack_through, updated_at) "
                    "VALUES (?, ?, ?)",
                    (job_id, event_id, time.time()),
                )
            else:
                await conn.execute(
                    "UPDATE protocol_job_cursors SET ack_through=?, updated_at=? "
                    "WHERE job_id=?",
                    (event_id, time.time(), job_id),
                )
            await conn.commit()