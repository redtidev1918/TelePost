"""Durable, append-only audit events backed by ``audit_events``.

Every call is best-effort: a database failure is logged and swallowed so
auditing can never break the submission/publish paths. The review row in
``pending_reviews`` stays the source of truth; this table is evidence only.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from database import db_manager
from .redact import sanitize_detail

logger = logging.getLogger(__name__)

_DETAIL_MAX_CHARS = 4000
_COLUMNS = (
    "ts", "component", "event", "review_id", "pixiv_id", "work_type",
    "target_id", "idempotency_key", "execution_id", "actor",
    "error_class", "detail",
)


def detail_value(raw: Any) -> Any:
    """Parse a stored JSON detail; pass through already-decoded values."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return raw
    return raw


def execution_id_from_ref(source_ref: Optional[str]) -> Optional[str]:
    """Extract a PixivFlow executionId from a source_ref (JSON or bare value)."""
    if not source_ref:
        return None
    text = str(source_ref).strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return text[:240]
    if isinstance(parsed, dict):
        value = parsed.get("executionId") or parsed.get("execution_id")
        return str(value)[:240] if value else None
    return text[:240]


def _encode_detail(detail: Any) -> Optional[str]:
    if detail is None:
        return None
    try:
        text = json.dumps(
            sanitize_detail(detail), ensure_ascii=False,
            separators=(",", ":"), default=str,
        )
    except (TypeError, ValueError):
        return None
    return text[:_DETAIL_MAX_CHARS]


async def record_event(event: str, **fields: Any) -> None:
    """Append one audit event. Never raises (logs a warning on failure)."""
    values = {name: None for name in _COLUMNS}
    values["component"] = "telepost"
    values["ts"] = float(fields.pop("ts", None) or time.time())
    values["event"] = str(event)
    for name in _COLUMNS:
        if name in fields:
            values[name] = fields[name]
    values["detail"] = _encode_detail(fields.get("detail") if "detail" in fields
                                      else values["detail"])
    try:
        async with db_manager.get_db() as conn:
            await conn.execute(
                f"INSERT INTO audit_events ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                tuple(values[name] for name in _COLUMNS),
            )
    except Exception as exc:  # auditing must never break the business path
        logger.warning("audit event not persisted: event=%s error=%s", event, exc)


async def list_events(*, review_id: Optional[int] = None,
                      execution_id: Optional[str] = None,
                      limit: int = 100) -> list[dict]:
    """Return audit events newest-first, optionally filtered by review/execution."""
    clauses, params = [], []
    if review_id is not None:
        clauses.append("review_id=?")
        params.append(int(review_id))
    if execution_id is not None:
        clauses.append("execution_id=?")
        params.append(str(execution_id))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(max(1, min(int(limit), 1000)))
    try:
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                f"SELECT * FROM audit_events{where} "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                tuple(params),
            )
            rows = await cur.fetchall()
    except Exception as exc:
        logger.warning("audit events not readable: %s", exc)
        return []
    events = []
    for row in rows:
        item = dict(row)
        raw = item.get("detail")
        if raw:
            try:
                item["detail"] = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        events.append(item)
    return events
