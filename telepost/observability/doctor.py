"""Read-only health self-inspection for TelePost (``telepost doctor``).

The doctor answers one question: **is this deployment healthy right now?**
It never repairs anything: every database is opened with ``mode=ro`` and the
module only issues ``PRAGMA``/``SELECT`` statements — no migrations, no writes,
no background loops.

Exit codes mirror the ``reviews inspect`` discipline:

* ``0`` — ``HEALTHY``;
* ``1`` — at least one ``CRIT`` check failed;
* ``2`` — the state cannot be verified (database missing / unreadable, or
  ``PRAGMA integrity_check`` is not ``ok``).

The whole report is secret-free: it only carries codes, counts, row ids and
ages, never message text, tokens or audit payloads.
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import time
from glob import glob
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

try:  # the canonical refetch state names live here
    from telepost.domain import refetch_state
except ImportError:  # pragma: no cover - defensive: doctor must never crash
    class _UnavailableRefetchState:
        """Placeholder when ``telepost.domain.refetch_state`` is not shipped.

        The refetch checks then degrade to ``SKIP`` instead of turning a
        read-only diagnostic into an import traceback.
        """

        ACTIVE_STATES: Tuple[str, ...] = ()
        LEGACY_NAMES: Tuple[str, ...] = ()

    refetch_state = _UnavailableRefetchState()

OK = "OK"
WARN = "WARN"
CRIT = "CRIT"
SKIP = "SKIP"

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
FAILED = "FAILED"

#: Stable check codes, in report order.
CHECK_CODES: Tuple[str, ...] = (
    "db_integrity",
    "refetch_stuck",
    "refetch_jobs",
    "refetch_active_invariant",
    "review_queue_orphans",
    "review_queue_publishing",
    "review_queue_counts",
    "delivery_outbox",
    "audit_events_recent",
)

# ---- thresholds -------------------------------------------------------------

#: An active refetch attempt older than this is worth a look.
REFETCH_WARN_SECONDS = 15 * 60
#: An active refetch attempt older than this is a failure (SLA breach).
REFETCH_CRIT_SECONDS = 30 * 60

#: Poll cadence of the refetch job heartbeat loop (mirrors the handler knob).
REFETCH_POLL_INTERVAL_SECONDS = 30
#: Our own heartbeat older than this means the poller stopped touching the row.
REFETCH_HEARTBEAT_STALE_SECONDS = 20 * 60
#: Absolute per-attempt ceiling; a running row older than this is over budget.
REFETCH_HARD_TIMEOUT_SECONDS = 90 * 60
#: The failure_code an older TelePost wrote for a broken correlation (legacy).
LEGACY_REFETCH_FAILURE_CODES = ("legacy_refetch_correlation_broken",)
#: Opt-in epoch: rows created at/after it count as job-model rows even when the
#: heartbeat columns are empty. 0 keeps the discriminator column-based only.
REFETCH_JOB_MODEL_SINCE = float(
    os.environ.get("TELEPOST_REFETCH_JOB_MODEL_SINCE", "0") or 0
)

#: A pending review whose control card was never posted: warn / fail window.
ORPHAN_WARN_SECONDS = 15 * 60
ORPHAN_CRIT_SECONDS = 60 * 60

#: Oldest pending review before the queue itself is considered suspicious.
OLDEST_PENDING_WARN_SECONDS = 7 * 24 * 60 * 60

#: Delivery ledger rows that are not a confirmed publication.
FAILED_LEDGER_STATUSES = ("partial", "uncertain", "failed", "error")
#: Age of the oldest unconfirmed delivery row before it is worth a warning.
DELIVERY_WARN_SECONDS = 30 * 60

#: Window for the informational audit-event count.
AUDIT_RECENT_SECONDS = 24 * 60 * 60
#: An active refetch attempt with no newer refetch audit event than this is a
#: warning (the attempt is running but the audit trail went quiet).
AUDIT_REFETCH_STALE_SECONDS = 2 * 60 * 60

#: ``services.review_service.PUBLISHING_STALE_SECONDS`` default (SSOT mirror).
_PUBLISHING_STALE_DEFAULT = 300.0

_MAX_LISTED = 5


def _now(now: Optional[float]) -> float:
    return time.time() if now is None else float(now)


def _age_seconds(now: float, created_at: Any) -> Optional[float]:
    """Return a non-negative age in seconds, or ``None`` when unusable."""
    if created_at is None:
        return None
    try:
        value = float(created_at)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    return max(0.0, now - value)


def _minutes(seconds: Optional[float]) -> Optional[float]:
    return None if seconds is None else round(seconds / 60.0, 1)


def _is_empty_reference(value: Any) -> bool:
    """Treat NULL / 0 / '' as "no control message was ever posted"."""
    if value is None:
        return True
    if isinstance(value, (int, float)):
        return int(value) == 0
    return str(value).strip() in ("", "0")


def _connect_ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> Set[str]:
    """Columns of ``table``; empty set when the table does not exist."""
    try:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    except sqlite3.Error:
        return set()
    return {str(row["name"]) for row in rows}


def _table_names(conn: sqlite3.Connection) -> Set[str]:
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    except sqlite3.Error:
        return set()
    return {str(row["name"]) for row in rows}


def _is_active(state: Any) -> bool:
    """True when ``state`` (canonical or legacy) is an active refetch state.

    ``refetch_state`` is the SSOT; the fallback only relies on the documented
    ``ACTIVE_STATES`` / ``normalize`` API so a rename inside that module can
    never turn this read-only diagnostic into a traceback.
    """
    predicate = getattr(refetch_state, "is_active", None)
    if callable(predicate):
        try:
            return bool(predicate(state))
        except Exception:
            pass
    canonical = _canonical_state(state)
    return canonical in tuple(getattr(refetch_state, "ACTIVE_STATES", ()))


def _canonical_state(state: Any) -> Any:
    """Normalize a possibly-legacy state name through ``refetch_state``."""
    normalize = getattr(refetch_state, "normalize", None)
    if callable(normalize):
        try:
            return normalize(state)
        except Exception:
            pass
    return state


def _active_state_names() -> Tuple[str, ...]:
    """Canonical *and* legacy spellings of every active refetch state.

    Older databases still store ``requested`` / ``admitted``; both are active.
    """
    names: List[str] = [str(name) for name in getattr(refetch_state, "ACTIVE_STATES", ())]
    for name in getattr(refetch_state, "LEGACY_NAMES", ()):
        if name not in names and _is_active(name):
            names.append(str(name))
    return tuple(names)


def _sql_in_list(values: Sequence[str]) -> str:
    return ", ".join("'" + str(value).replace("'", "''") + "'" for value in values)


def _publishing_stale_seconds() -> Tuple[float, str]:
    """Return ``(seconds, source)`` for the publishing-stale window.

    The authoritative constant lives in ``services.review_service``; its import
    chain pulls ``config.settings`` (which raises when ``TOKEN`` is unset), so a
    standalone read-only CLI has to tolerate that. The fallback mirrors the SSOT
    definition character for character — ``max(60.0, float(os.getenv(
    "PUBLISHING_STALE_SECONDS", "300")))`` — and the check reports which source
    was used.
    """
    for module_name in (
        "telepost.services.review_service",
        "services.review_service",
    ):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        value = getattr(module, "PUBLISHING_STALE_SECONDS", None)
        if value is not None:
            try:
                return float(value), module_name
            except (TypeError, ValueError):
                continue
    return (
        max(60.0, float(os.getenv("PUBLISHING_STALE_SECONDS", "300"))),
        "fallback",
    )


class _Collector:
    """Accumulates checks plus the per-database summary."""

    def __init__(self, now: float):
        self.now = now
        self.checks: List[Dict[str, Any]] = []
        self.databases: List[Dict[str, Any]] = []
        self.has_crit = False
        self.has_warn = False
        self.unverifiable = False

    def add(
        self,
        code: str,
        level: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        check: Dict[str, Any] = {"code": code, "level": level, "message": message}
        if details:
            check["details"] = details
        self.checks.append(check)
        if level == CRIT:
            self.has_crit = True
        elif level == WARN:
            self.has_warn = True


def _prefix(db_path: str, message: str) -> str:
    return f"[{db_path}] {message}"


# ---- check 1: database integrity -------------------------------------------


def _check_db_integrity(
    db_path: str, conn: sqlite3.Connection, collector: _Collector
) -> None:
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as exc:
        collector.unverifiable = True
        collector.add(
            "db_integrity",
            CRIT,
            _prefix(db_path, f"无法执行 PRAGMA integrity_check：{exc}"),
            {"path": db_path},
        )
        return
    values = [str(row[0]) for row in rows] or ["<no result>"]
    ok = len(values) == 1 and values[0].lower() == "ok"
    if ok:
        collector.add(
            "db_integrity",
            OK,
            _prefix(db_path, "PRAGMA integrity_check = ok"),
            {"path": db_path, "integrity_check": "ok"},
        )
        return
    collector.unverifiable = True
    collector.add(
        "db_integrity",
        CRIT,
        _prefix(
            db_path,
            "PRAGMA integrity_check 未通过（数据库无法验证）："
            + "; ".join(values[:_MAX_LISTED]),
        ),
        {"path": db_path, "integrity_check": values[:_MAX_LISTED]},
    )


# ---- check 2: stuck refetch attempts ---------------------------------------


def _check_refetch_stuck(
    db_path: str, conn: sqlite3.Connection, collector: _Collector
) -> bool:
    """Report active refetch attempts and return whether any exists."""
    columns = _table_columns(conn, "refetch_attempts")
    if not columns:
        collector.add(
            "refetch_stuck", SKIP, _prefix(db_path, "refetch_attempts 表不存在，跳过")
        )
        return False
    if "state" not in columns:
        collector.add(
            "refetch_stuck", SKIP, _prefix(db_path, "refetch_attempts.state 列不存在，跳过")
        )
        return False
    time_column = "created_at" if "created_at" in columns else (
        "started_at" if "started_at" in columns else None
    )
    if time_column is None:
        collector.add(
            "refetch_stuck",
            SKIP,
            _prefix(db_path, "refetch_attempts 缺少 created_at/started_at 列，跳过"),
        )
        return False

    selected = ["state", time_column]
    for name in ("request_id", "source_review_id"):
        if name in columns:
            selected.append(name)
    if not _active_state_names():
        collector.add(
            "refetch_stuck",
            SKIP,
            _prefix(db_path, "无法解析活跃重抓状态（refetch_state 不可用），跳过"),
        )
        return False
    rows = conn.execute(
        f"SELECT {', '.join(selected)} FROM refetch_attempts"
    ).fetchall()

    attempts: List[Dict[str, Any]] = []
    for row in rows:
        raw_state = row["state"]
        if not _is_active(raw_state):
            continue
        age = _age_seconds(collector.now, row[time_column])
        entry: Dict[str, Any] = {
            "request_id": row["request_id"] if "request_id" in columns else None,
            "state": raw_state,
            "canonical_state": _canonical_state(raw_state),
            "source_review_id": (
                row["source_review_id"] if "source_review_id" in columns else None
            ),
            "age_minutes": _minutes(age),
        }
        attempts.append(entry)

    if not attempts:
        collector.add(
            "refetch_stuck",
            OK,
            _prefix(db_path, "没有活跃的重抓 attempt"),
            {"path": db_path, "count": 0, "attempts": []},
        )
        return False

    attempts.sort(key=lambda item: item["age_minutes"] or 0.0, reverse=True)
    worst = attempts[0]["age_minutes"] or 0.0
    worst_seconds = worst * 60.0
    if worst_seconds > REFETCH_CRIT_SECONDS:
        level = CRIT
    elif worst_seconds > REFETCH_WARN_SECONDS:
        level = WARN
    else:
        level = OK
    collector.add(
        "refetch_stuck",
        level,
        _prefix(
            db_path,
            f"有 {len(attempts)} 个活跃重抓 attempt，最久已等待 {worst:.1f} 分钟"
            f"（WARN>{REFETCH_WARN_SECONDS // 60} 分钟，"
            f"CRIT>{REFETCH_CRIT_SECONDS // 60} 分钟）",
        ),
        {
            "path": db_path,
            "count": len(attempts),
            "warn_seconds": REFETCH_WARN_SECONDS,
            "crit_seconds": REFETCH_CRIT_SECONDS,
            "attempts": attempts,
        },
    )
    return True


# ---- check 2b: refetch job lifecycle --------------------------------------


def _refetch_is_job_model_row(row: Dict[str, Any], columns: Set[str]) -> bool:
    """True when the new heartbeat job model actually observed this row.

    Production carried 12 terminal rows written by an older TelePost: all of
    them have ``terminal_reason = ''`` and ``notify_count = 0``, and backfilling
    or re-notifying them is explicitly forbidden. The discriminator is therefore
    behavioural, not temporal: the job model always writes at least one heartbeat
    column before a row can go terminal, so a row with every heartbeat column
    empty predates the job model and its missing reason is LEGACY (reported,
    never a critical). ``TELEPOST_REFETCH_JOB_MODEL_SINCE`` can additionally
    mark rows created at/after a known deploy epoch.
    """
    for name in ("heartbeat_at", "next_poll_at", "remote_state_at",
                 "remote_heartbeat_at"):
        if name in columns and row[name] is not None:
            return True
    for name in ("heartbeat_count", "poll_failures"):
        if name in columns and int(row[name] or 0) > 0:
            return True
    if REFETCH_JOB_MODEL_SINCE > 0:
        created = row["created_at"] if "created_at" in columns else None
        if created is not None and float(created or 0) >= REFETCH_JOB_MODEL_SINCE:
            return True
    return False


def _check_refetch_jobs(
    db_path: str, conn: sqlite3.Connection, collector: _Collector
) -> None:
    """Refetch job-lifecycle section (§6): running / stuck / over-budget / failed."""
    columns = _table_columns(conn, "refetch_attempts")
    if not columns:
        collector.add(
            "refetch_jobs", SKIP, _prefix(db_path, "refetch_attempts 表不存在，跳过")
        )
        return
    if "state" not in columns:
        collector.add(
            "refetch_jobs", SKIP, _prefix(db_path, "refetch_attempts.state 列不存在，跳过")
        )
        return
    if not _active_state_names():
        collector.add(
            "refetch_jobs",
            SKIP,
            _prefix(db_path, "无法解析重抓状态（refetch_state 不可用），跳过"),
        )
        return

    selected = sorted(columns)
    rows = [dict(r) for r in conn.execute(
        f"SELECT {', '.join(selected)} FROM refetch_attempts"
    ).fetchall()]

    def _column(row: Dict[str, Any], name: str, default=None):
        return row[name] if name in columns else default

    def _time_of(row: Dict[str, Any]) -> Optional[float]:
        for name in ("created_at", "started_at"):
            value = float(_column(row, name) or 0)
            if value:
                return value
        return None

    def _finished_of(row: Dict[str, Any]) -> Optional[float]:
        for name in ("finished_at", "updated_at", "created_at"):
            value = float(_column(row, name) or 0)
            if value:
                return value
        return None

    running = stuck = over_budget = retrying = 0
    failed_24h = 0
    missing_reason = 0
    legacy_missing_reason = 0
    failure_breakdown: Dict[str, int] = {}
    for row in rows:
        raw_state = row["state"]
        created = _time_of(row)
        if _is_active(raw_state):
            running += 1
            last_touch = float(_column(row, "heartbeat_at") or 0) or created or 0.0
            if last_touch and (collector.now - last_touch) > REFETCH_HEARTBEAT_STALE_SECONDS:
                stuck += 1
            if created and (collector.now - created) > REFETCH_HARD_TIMEOUT_SECONDS:
                over_budget += 1
            if int(_column(row, "poll_failures", 0) or 0) > 0:
                retrying += 1
            continue
        finished = _finished_of(row)
        canonical = _canonical_state(raw_state)
        legacy_code = str(_column(row, "failure_code", "") or "")
        # ``failed(last24h)`` is attributed by the TERMINAL STATE: both TIMEOUT
        # and FAILED count, whether the attempt ran out of a budget or the remote
        # reported a search failure.
        if canonical in ("failed", "timeout") and finished is not None:
            if (collector.now - finished) <= 24 * 3600:
                failed_24h += 1
                code = legacy_code or str(
                    _column(row, "terminal_reason", "") or "unknown")
                failure_breakdown[code] = failure_breakdown.get(code, 0) + 1
        if not str(_column(row, "terminal_reason", "") or "").strip():
            # The invariant only binds rows the job model owns; legacy rows are
            # reported separately so a deployed doctor still exits 0.
            if legacy_code in LEGACY_REFETCH_FAILURE_CODES:
                legacy_missing_reason += 1
            elif _refetch_is_job_model_row(row, columns):
                missing_reason += 1
            else:
                legacy_missing_reason += 1

    # Durable fallback queue for terminal notices whose direct send failed.
    outbox_pending = outbox_failed = 0
    if _table_columns(conn, "submitter_notifications"):
        try:
            outbox_pending = int(conn.execute(
                "SELECT COUNT(*) FROM submitter_notifications "
                "WHERE kind = 'refetch_terminal' AND state = 'pending'"
            ).fetchone()[0])
            outbox_failed = int(conn.execute(
                "SELECT COUNT(*) FROM submitter_notifications "
                "WHERE kind = 'refetch_terminal' AND COALESCE(attempts, 0) > 0 "
                "AND state = 'pending'"
            ).fetchone()[0])
        except sqlite3.Error:
            outbox_pending = outbox_failed = 0

    summary = f"Refetch: running: {running} stuck: {stuck} failed(last24h): {failed_24h}"
    details: Dict[str, Any] = {
        "path": db_path,
        "running": running,
        "stuck": stuck,
        "over_budget": over_budget,
        "retrying": retrying,
        "failed_24h": failed_24h,
        "terminal_missing_reason": missing_reason,
        "legacy_missing_reason": legacy_missing_reason,
        "terminal_notify_pending": outbox_pending,
        "terminal_notify_failed": outbox_failed,
        "failure_breakdown": dict(sorted(failure_breakdown.items())),
        "summary": summary,
        "heartbeat_stale_seconds": REFETCH_HEARTBEAT_STALE_SECONDS,
        "hard_timeout_seconds": REFETCH_HARD_TIMEOUT_SECONDS,
    }
    # 「卡住」必须一眼可见并让 doctor 报红：an ACTIVE attempt whose own heartbeat
    # stopped (the poller is dead) or that outlived the absolute ceiling means the
    # job model itself is broken. Legacy rows are all terminal, so they can never
    # trip this (parent constraint: a deployed doctor still exits 0).
    if stuck or over_budget:
        level = CRIT
    elif missing_reason or outbox_failed:
        level = WARN
    else:
        level = OK
    breakdown_text = "、".join(
        f"{code}×{count}" for code, count in sorted(failure_breakdown.items())
    ) or "-"
    message = (
        f"{summary}｜超预算 {over_budget}｜轮询重试中 {retrying}"
        f"｜终态缺原因（新代码路径）{missing_reason}"
        f"｜终态缺原因（legacy 历史行，不计入不变量）{legacy_missing_reason}"
        f"｜24h 失败构成 {breakdown_text}"
        f"｜终态通知待补发 {outbox_pending}（失败 {outbox_failed}）"
    )
    collector.add("refetch_jobs", level, _prefix(db_path, message), details)


# ---- check 3: one-active-attempt-per-chain invariant -----------------------


def _check_refetch_active_invariant(
    db_path: str, conn: sqlite3.Connection, collector: _Collector
) -> None:
    columns = _table_columns(conn, "refetch_attempts")
    if not columns:
        collector.add(
            "refetch_active_invariant",
            SKIP,
            _prefix(db_path, "refetch_attempts 表不存在，跳过"),
        )
        return
    if "state" not in columns or "review_chain_id" not in columns:
        collector.add(
            "refetch_active_invariant",
            SKIP,
            _prefix(db_path, "refetch_attempts 缺少 state/review_chain_id 列，跳过"),
        )
        return

    index_row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='idx_refetch_one_active'"
    ).fetchone()
    index_unique: Optional[bool] = None
    if index_row is not None:
        try:
            for listed in conn.execute("PRAGMA index_list(refetch_attempts)"):
                if str(listed["name"]) == "idx_refetch_one_active":
                    index_unique = bool(listed["unique"])
                    break
        except sqlite3.Error:
            index_unique = None

    active_names = _active_state_names()
    if not active_names:
        collector.add(
            "refetch_active_invariant",
            SKIP,
            _prefix(db_path, "无法解析活跃重抓状态（refetch_state 不可用），跳过"),
        )
        return
    rows = conn.execute(
        "SELECT review_chain_id, state FROM refetch_attempts "
        f"WHERE state IN ({_sql_in_list(active_names)})"
    ).fetchall()
    per_chain: Dict[str, int] = {}
    for row in rows:
        if not _is_active(row["state"]):
            continue
        chain = str(row["review_chain_id"] or "")
        per_chain[chain] = per_chain.get(chain, 0) + 1
    duplicated = sorted(
        (chain, count) for chain, count in per_chain.items() if count > 1
    )

    problems: List[str] = []
    if index_row is None:
        problems.append("缺少 partial UNIQUE 索引 idx_refetch_one_active")
    elif index_unique is False:
        problems.append("idx_refetch_one_active 存在但不是 UNIQUE 索引")
    if duplicated:
        listed = ", ".join(f"{chain or '<empty>'}: {count}" for chain, count in duplicated[:_MAX_LISTED])
        problems.append(f"{len(duplicated)} 条审核链同时存在多个活跃 attempt（{listed}）")

    details: Dict[str, Any] = {
        "path": db_path,
        "index_exists": index_row is not None,
        "index_unique": index_unique,
        "active_chains": len(per_chain),
        "chains_with_multiple_active": len(duplicated),
        "duplicated_chains": [
            {"review_chain_id": chain, "active_attempts": count}
            for chain, count in duplicated[:_MAX_LISTED]
        ],
    }
    if problems:
        collector.add(
            "refetch_active_invariant",
            CRIT,
            _prefix(db_path, "；".join(problems)),
            details,
        )
        return
    collector.add(
        "refetch_active_invariant",
        OK,
        _prefix(db_path, "idx_refetch_one_active 存在，且每条审核链至多一个活跃 attempt"),
        details,
    )


# ---- check 4: review queue orphans -----------------------------------------


def _check_review_queue_orphans(
    db_path: str, conn: sqlite3.Connection, collector: _Collector
) -> None:
    columns = _table_columns(conn, "pending_reviews")
    required = {"status", "control_message_id", "created_at", "id"}
    if not columns:
        collector.add(
            "review_queue_orphans", SKIP, _prefix(db_path, "pending_reviews 表不存在，跳过")
        )
        return
    if not required.issubset(columns):
        missing = ", ".join(sorted(required - columns))
        collector.add(
            "review_queue_orphans",
            SKIP,
            _prefix(db_path, f"pending_reviews 缺少列（{missing}），跳过"),
        )
        return

    rows = conn.execute(
        "SELECT id, created_at, control_message_id FROM pending_reviews "
        "WHERE status='pending'"
    ).fetchall()
    orphans: List[Dict[str, Any]] = []
    for row in rows:
        if not _is_empty_reference(row["control_message_id"]):
            continue
        age = _age_seconds(collector.now, row["created_at"])
        orphans.append({
            "id": row["id"],
            "age_minutes": _minutes(age),
            "age_seconds": None if age is None else round(age, 1),
        })

    if not orphans:
        collector.add(
            "review_queue_orphans",
            OK,
            _prefix(db_path, "没有缺失控制消息的 pending 审核"),
            {"path": db_path, "count": 0, "review_ids": []},
        )
        return

    orphans.sort(key=lambda item: item["age_seconds"] or 0.0, reverse=True)
    worst = orphans[0]["age_seconds"] or 0.0
    if worst > ORPHAN_CRIT_SECONDS:
        level = CRIT
    elif worst > ORPHAN_WARN_SECONDS:
        level = WARN
    else:
        level = OK
    collector.add(
        "review_queue_orphans",
        level,
        _prefix(
            db_path,
            f"有 {len(orphans)} 条 pending 审核没有控制消息，最久 {worst / 60.0:.1f} 分钟"
            f"（WARN>{ORPHAN_WARN_SECONDS // 60} 分钟，"
            f"CRIT>{ORPHAN_CRIT_SECONDS // 60} 分钟）",
        ),
        {
            "path": db_path,
            "count": len(orphans),
            "warn_seconds": ORPHAN_WARN_SECONDS,
            "crit_seconds": ORPHAN_CRIT_SECONDS,
            "review_ids": [item["id"] for item in orphans[:_MAX_LISTED]],
            "oldest": orphans[0],
        },
    )


# ---- check 5: reviews stuck in publishing ----------------------------------


def _check_review_queue_publishing(
    db_path: str, conn: sqlite3.Connection, collector: _Collector
) -> None:
    columns = _table_columns(conn, "pending_reviews")
    if not columns:
        collector.add(
            "review_queue_publishing",
            SKIP,
            _prefix(db_path, "pending_reviews 表不存在，跳过"),
        )
        return
    if not {"status", "created_at", "id"}.issubset(columns):
        collector.add(
            "review_queue_publishing",
            SKIP,
            _prefix(db_path, "pending_reviews 缺少 status/created_at 列，跳过"),
        )
        return

    stale_seconds, stale_source = _publishing_stale_seconds()
    rows = conn.execute(
        "SELECT id, created_at FROM pending_reviews WHERE status='publishing'"
    ).fetchall()
    stuck: List[Dict[str, Any]] = []
    for row in rows:
        age = _age_seconds(collector.now, row["created_at"])
        if age is None or age > stale_seconds:
            stuck.append({
                "id": row["id"],
                "age_minutes": _minutes(age),
                "age_seconds": None if age is None else round(age, 1),
            })

    details: Dict[str, Any] = {
        "path": db_path,
        "publishing_rows": len(rows),
        "stale_seconds": stale_seconds,
        "stale_source": stale_source,
        "stuck_count": len(stuck),
        "review_ids": [item["id"] for item in stuck[:_MAX_LISTED]],
    }
    if stuck:
        stuck.sort(key=lambda item: item["age_seconds"] or 0.0, reverse=True)
        collector.add(
            "review_queue_publishing",
            CRIT,
            _prefix(
                db_path,
                f"{len(stuck)} 条审核停留在 publishing 超过 "
                f"{stale_seconds:.0f} 秒未被回收（最久 {stuck[0]['age_minutes']} 分钟）",
            ),
            details,
        )
        return
    collector.add(
        "review_queue_publishing",
        OK,
        _prefix(
            db_path,
            f"没有超时未回收的 publishing 行（当前 {len(rows)} 条，"
            f"窗口 {stale_seconds:.0f} 秒）",
        ),
        details,
    )


# ---- check 6: review queue counts (informational) --------------------------


def _check_review_queue_counts(
    db_path: str, conn: sqlite3.Connection, collector: _Collector
) -> None:
    columns = _table_columns(conn, "pending_reviews")
    if not columns or "status" not in columns:
        collector.add(
            "review_queue_counts", SKIP, _prefix(db_path, "pending_reviews 表不存在，跳过")
        )
        return
    counts = {
        str(row["status"]): int(row["count"])
        for row in conn.execute(
            "SELECT status, COUNT(*) AS count FROM pending_reviews GROUP BY status"
        ).fetchall()
    }
    oldest_age: Optional[float] = None
    if "created_at" in columns:
        row = conn.execute(
            "SELECT MIN(created_at) AS oldest FROM pending_reviews "
            "WHERE status='pending'"
        ).fetchone()
        if row is not None and row["oldest"] is not None:
            oldest_age = _age_seconds(collector.now, row["oldest"])

    level = (
        WARN
        if oldest_age is not None and oldest_age > OLDEST_PENDING_WARN_SECONDS
        else OK
    )
    summary = ", ".join(f"{key}={counts[key]}" for key in sorted(counts)) or "空"
    collector.add(
        "review_queue_counts",
        level,
        _prefix(
            db_path,
            f"审核队列按状态计数：{summary}"
            + (
                f"；最久 pending 已 {oldest_age / 86400.0:.1f} 天"
                if oldest_age is not None
                else ""
            ),
        ),
        {
            "path": db_path,
            "counts": counts,
            "oldest_pending_age_seconds": (
                None if oldest_age is None else round(oldest_age, 1)
            ),
            "warn_seconds": OLDEST_PENDING_WARN_SECONDS,
        },
    )


# ---- check 7: delivery ledger / outbox -------------------------------------


def _check_delivery_outbox(
    db_path: str, conn: sqlite3.Connection, collector: _Collector
) -> None:
    ledger_columns = _table_columns(conn, "delivery_ledger")
    outbox_columns = _table_columns(conn, "delivery_outbox")
    if not ledger_columns and not outbox_columns:
        collector.add(
            "delivery_outbox",
            SKIP,
            _prefix(db_path, "没有 delivery_ledger / delivery_outbox 表，跳过"),
        )
        return

    details: Dict[str, Any] = {"path": db_path}
    problems: List[str] = []

    if ledger_columns:
        total = int(
            conn.execute("SELECT COUNT(*) AS n FROM delivery_ledger").fetchone()["n"]
        )
        details["ledger_total"] = total
        if "status" in ledger_columns:
            failed = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM delivery_ledger "
                    f"WHERE status IN ({_sql_in_list(FAILED_LEDGER_STATUSES)})"
                ).fetchone()["n"]
            )
            details["ledger_failed"] = failed
            if failed:
                problems.append(f"delivery_ledger 有 {failed} 条未确认发布")
        if "created_at" in ledger_columns and "status" in ledger_columns:
            # ``delivery_ledger`` is a *confirmed-post* idempotency ledger: an
            # ordinary row is a historical success record, so the age of the
            # oldest row says nothing about health (it only ever grows). Only
            # rows that never reached a terminal success still need attention.
            row = conn.execute(
                "SELECT MIN(created_at) AS oldest FROM delivery_ledger "
                f"WHERE status IN ({_sql_in_list(FAILED_LEDGER_STATUSES)})"
            ).fetchone()
            oldest = None if row is None else row["oldest"]
            age = None if oldest is None else _age_seconds(collector.now, oldest)
            details["ledger_oldest_unresolved_age_seconds"] = (
                None if age is None else round(age, 1)
            )
            if age is not None and age > DELIVERY_WARN_SECONDS:
                problems.append(
                    f"delivery_ledger 最旧未确认记录已 {age / 60.0:.1f} 分钟"
                )

    if outbox_columns:
        total = int(
            conn.execute("SELECT COUNT(*) AS n FROM delivery_outbox").fetchone()["n"]
        )
        details["outbox_total"] = total
        if "status" in outbox_columns:
            failed = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM delivery_outbox "
                    f"WHERE status IN ({_sql_in_list(FAILED_LEDGER_STATUSES)})"
                ).fetchone()["n"]
            )
            details["outbox_failed"] = failed
            if failed:
                problems.append(f"delivery_outbox 有 {failed} 条失败投递")
        if "created_at" in outbox_columns:
            row = conn.execute(
                "SELECT MIN(created_at) AS oldest FROM delivery_outbox"
            ).fetchone()
            if row is not None and row["oldest"] is not None:
                age = _age_seconds(collector.now, row["oldest"])
                details["outbox_oldest_age_seconds"] = (
                    None if age is None else round(age, 1)
                )
                if age is not None and age > DELIVERY_WARN_SECONDS:
                    problems.append(f"delivery_outbox 最旧记录已 {age / 60.0:.1f} 分钟")

    details["warn_seconds"] = DELIVERY_WARN_SECONDS
    if problems:
        collector.add(
            "delivery_outbox",
            WARN,
            _prefix(db_path, "；".join(problems)),
            details,
        )
        return
    collector.add(
        "delivery_outbox",
        OK,
        _prefix(db_path, "投递账本没有未确认发布，且没有陈旧积压"),
        details,
    )


# ---- check 8: audit event activity (informational) -------------------------


def _check_audit_events_recent(
    db_path: str,
    conn: sqlite3.Connection,
    collector: _Collector,
    has_active_attempt: bool,
) -> None:
    columns = _table_columns(conn, "audit_events")
    if not columns or "ts" not in columns:
        collector.add(
            "audit_events_recent", SKIP, _prefix(db_path, "audit_events 表不存在，跳过")
        )
        return
    recent = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM audit_events WHERE ts >= ?",
            (collector.now - AUDIT_RECENT_SECONDS,),
        ).fetchone()["n"]
    )
    newest_refetch_age: Optional[float] = None
    if "event" in columns:
        row = conn.execute(
            "SELECT MAX(ts) AS newest FROM audit_events "
            "WHERE event LIKE 'review.refetch%'"
        ).fetchone()
        if row is not None and row["newest"] is not None:
            newest_refetch_age = _age_seconds(collector.now, row["newest"])

    level = OK
    message = f"最近 24 小时 {recent} 条审计事件"
    if newest_refetch_age is None:
        if has_active_attempt:
            level = WARN
            message += "；有活跃重抓 attempt 但没有任何 review.refetch_* 审计事件"
        else:
            message += "；没有 review.refetch_* 事件"
    else:
        message += f"；最新 review.refetch_* 事件 {newest_refetch_age / 60.0:.1f} 分钟前"
        if has_active_attempt and newest_refetch_age > AUDIT_REFETCH_STALE_SECONDS:
            level = WARN
            message += "（有活跃 attempt 但审计已静默）"

    collector.add(
        "audit_events_recent",
        level,
        _prefix(db_path, message),
        {
            "path": db_path,
            "events_last_24h": recent,
            "newest_refetch_event_age_seconds": (
                None if newest_refetch_age is None else round(newest_refetch_age, 1)
            ),
            "active_attempt": has_active_attempt,
        },
    )


# ---- per-database driver ---------------------------------------------------


def _check_database(db_path: str, collector: _Collector) -> None:
    entry: Dict[str, Any] = {
        "path": db_path,
        "resolved_path": os.path.abspath(db_path),
        "exists": False,
        "size_bytes": None,
        "integrity_check": None,
        "error": None,
    }
    if not os.path.isfile(db_path):
        entry["error"] = "missing"
        collector.unverifiable = True
        collector.add(
            "db_integrity",
            CRIT,
            _prefix(db_path, "数据库文件不存在或不是普通文件（无法验证）"),
            {"path": db_path},
        )
        collector.databases.append(entry)
        return
    try:
        entry["size_bytes"] = os.path.getsize(db_path)
    except OSError:
        entry["size_bytes"] = None
    entry["exists"] = True

    try:
        conn = _connect_ro(db_path)
    except sqlite3.Error as exc:
        entry["error"] = "unreadable"
        collector.unverifiable = True
        collector.add(
            "db_integrity",
            CRIT,
            _prefix(db_path, f"数据库无法以只读方式打开（无法验证）：{exc}"),
            {"path": db_path},
        )
        collector.databases.append(entry)
        return

    try:
        _check_db_integrity(db_path, conn, collector)
        last = collector.checks[-1]
        entry["integrity_check"] = (last.get("details") or {}).get("integrity_check")
        if last["level"] == CRIT:
            entry["error"] = "integrity"
            collector.databases.append(entry)
            return
        has_active = _check_refetch_stuck(db_path, conn, collector)
        _check_refetch_jobs(db_path, conn, collector)
        _check_refetch_active_invariant(db_path, conn, collector)
        _check_review_queue_orphans(db_path, conn, collector)
        _check_review_queue_publishing(db_path, conn, collector)
        _check_review_queue_counts(db_path, conn, collector)
        _check_delivery_outbox(db_path, conn, collector)
        _check_audit_events_recent(db_path, conn, collector, has_active)
    except sqlite3.Error as exc:
        # A malformed/partial schema must degrade to a report, never a traceback.
        collector.unverifiable = True
        entry["error"] = f"sqlite error: {exc}"
        collector.add(
            "db_integrity",
            CRIT,
            _prefix(db_path, f"读取数据库结构失败（无法验证）：{exc}"),
            {"path": db_path},
        )
    finally:
        conn.close()
    collector.databases.append(entry)


def run_doctor(
    *, db_paths: List[str], now: Optional[float] = None
) -> Tuple[int, Dict[str, Any]]:
    """Inspect ``db_paths`` and return ``(exit_code, report)``.

    Pure function: no argparse, no printing, no writes. ``now`` pins the
    reference clock so age thresholds are deterministic in tests.
    """
    timestamp = _now(now)
    collector = _Collector(timestamp)
    for db_path in db_paths:
        _check_database(db_path, collector)

    if collector.unverifiable or collector.has_crit:
        status = FAILED
    elif collector.has_warn:
        status = DEGRADED
    else:
        status = HEALTHY

    if collector.unverifiable:
        exit_code = 2
    elif collector.has_crit:
        exit_code = 1
    else:
        exit_code = 0

    report: Dict[str, Any] = {
        "status": status,
        "checks": collector.checks,
        "databases": collector.databases,
        "generated_at": timestamp,
    }
    return exit_code, report


def resolve_paths(
    bots: Optional[Sequence[int]], all_bots: bool, resolve
) -> List[str]:
    """Expand ``--bot N`` / ``--all-bots`` into an ordered, deduped path list.

    ``--all-bots`` scans ``data/bot*/submissions.db``. When nothing is selected
    at all (no flags, or ``--all-bots`` with no bot directory) the default
    database (``resolve(None)``) is inspected, exactly like ``reviews inspect``.
    """
    paths: List[str] = []
    seen: Set[str] = set()

    def push(path: str) -> None:
        key = os.path.abspath(path)
        if key not in seen:
            seen.add(key)
            paths.append(path)

    if all_bots:
        for path in sorted(glob(os.path.join("data", "bot*", "submissions.db"))):
            push(path)
    for bot in bots or ():
        push(resolve(bot))
    if not paths:
        push(resolve(None))
    return paths


def render_human(report: Dict[str, Any]) -> str:
    """Render the human report: one line per check, then the summary block."""
    lines = [
        f"[{check['level']}] {check['code']} {check['message']}"
        for check in report["checks"]
    ]
    counts = {OK: 0, WARN: 0, CRIT: 0, SKIP: 0}
    for check in report["checks"]:
        counts[check["level"]] = counts.get(check["level"], 0) + 1
    lines.append(f"{report['status']}")
    lines.append(
        f"  检查 {len(report['checks'])} 项：{counts[OK]} OK / {counts[WARN]} WARN / "
        f"{counts[CRIT]} CRIT / {counts[SKIP]} SKIP；数据库 {len(report['databases'])} 个"
    )
    return "\n".join(lines)
