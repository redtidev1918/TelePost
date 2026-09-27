"""``telepost doctor`` — read-only health self-inspection.

The databases are built by hand (only the tables/columns the doctor reads) so
the tests never touch the migration runner and never depend on a production
schema. ``run_doctor`` is called directly with a pinned ``--now`` reference
clock, which is exactly the contract the CLI exposes.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from telepost.observability import doctor
from telepost.observability.doctor import CHECK_CODES, run_doctor

ROOT = Path(__file__).resolve().parent.parent
#: Deterministic reference clock (all inserted rows are relative to it).
NOW = 1_800_000_000.0

# Minimal, hand-copied subsets of database/db_manager.py DDL (never imported).
_PENDING_REVIEWS_DDL = """
CREATE TABLE pending_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL DEFAULT 'api',
    status TEXT NOT NULL DEFAULT 'pending',
    user_id INTEGER NOT NULL DEFAULT 1,
    review_chat_id TEXT NOT NULL DEFAULT '',
    control_message_id INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
"""

_REFETCH_ATTEMPTS_DDL = """
CREATE TABLE refetch_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    callback_key TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL UNIQUE,
    review_chain_id TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 0,
    source_review_id INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'requested',
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
)
"""

_REFETCH_INDEX_DDL = (
    "CREATE UNIQUE INDEX idx_refetch_one_active "
    "ON refetch_attempts(review_chain_id) WHERE state IN ('requested','admitted')"
)

_AUDIT_EVENTS_DDL = """
CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    component TEXT DEFAULT 'telepost',
    event TEXT NOT NULL,
    review_id INTEGER,
    actor TEXT,
    error_class TEXT,
    detail TEXT
)
"""

_DELIVERY_LEDGER_DDL = """
CREATE TABLE delivery_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'published',
    message_id INTEGER,
    user_id INTEGER,
    created_at REAL NOT NULL
)
"""

_DDL = {
    "pending_reviews": _PENDING_REVIEWS_DDL,
    "refetch_attempts": _REFETCH_ATTEMPTS_DDL,
    "audit_events": _AUDIT_EVENTS_DDL,
    "delivery_ledger": _DELIVERY_LEDGER_DDL,
}


def _db_path(tmp_path, name="submissions.db") -> str:
    return str(tmp_path / name)


def _make_db(path, tables=tuple(_DDL), refetch_index=True) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    for table in tables:
        conn.execute(_DDL[table])
    if refetch_index and "refetch_attempts" in tables:
        conn.execute(_REFETCH_INDEX_DDL)
    conn.commit()
    return conn


def _insert_attempt(conn, *, chain, state, age_seconds, request_id, review_id=1,
                    created_column="created_at"):
    conn.execute(
        f"INSERT INTO refetch_attempts (callback_key, request_id, "
        f"review_chain_id, source_review_id, state, {created_column}) "
        f"VALUES (?, ?, ?, ?, ?, ?)",
        (f"cb-{request_id}", request_id, chain, review_id, state, NOW - age_seconds),
    )


def _insert_review(conn, *, status="pending", age_seconds=0.0, control_message_id=42):
    conn.execute(
        "INSERT INTO pending_reviews (idempotency_key, source, status, user_id, "
        "review_chat_id, control_message_id, created_at, updated_at) "
        "VALUES (?, 'api', ?, 1, 'chat', ?, ?, ?)",
        (f"api:1:{status}:{age_seconds}:{control_message_id}", status,
         control_message_id, NOW - age_seconds, NOW - age_seconds),
    )


def _by_code(report, code):
    matches = [check for check in report["checks"] if check["code"] == code]
    assert matches, f"缺失检查 {code}: {[c['code'] for c in report['checks']]}"
    return matches[0]


# ---- clean baseline --------------------------------------------------------


def test_clean_database_is_healthy(tmp_path):
    path = _db_path(tmp_path)
    _make_db(path).close()

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 0
    assert report["status"] == "HEALTHY"
    assert report["generated_at"] == NOW
    assert [check["code"] for check in report["checks"]] == list(CHECK_CODES)
    assert not [c for c in report["checks"] if c["level"] in {"WARN", "CRIT"}]
    assert report["databases"][0]["exists"] is True
    assert report["databases"][0]["integrity_check"] == "ok"
    json.dumps(report)  # details must stay JSON-serializable


def test_all_checks_are_json_serializable_with_findings(tmp_path):
    path = _db_path(tmp_path)
    conn = _make_db(path, refetch_index=False)
    _insert_attempt(conn, chain="c1", state="searching", age_seconds=40 * 60,
                    request_id="req-a")
    _insert_attempt(conn, chain="c1", state="candidate_found", age_seconds=5 * 60,
                    request_id="req-b")
    _insert_review(conn, status="publishing", age_seconds=10_000.0)
    conn.commit()
    conn.close()

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 1
    assert json.dumps(report)
    # No secrets or submission payloads may leak into the report.
    assert "token" not in json.dumps(report).lower()


def test_read_only_doctor_never_writes_to_the_database(tmp_path):
    path = _db_path(tmp_path)
    _make_db(path).close()
    with open(path, "rb") as handle:
        before = hashlib.sha256(handle.read()).hexdigest()

    os.chmod(path, 0o444)
    try:
        exit_code, report = run_doctor(db_paths=[path], now=NOW)
    finally:
        os.chmod(path, 0o644)

    with open(path, "rb") as handle:
        after = hashlib.sha256(handle.read()).hexdigest()

    assert exit_code == 0
    assert report["status"] == "HEALTHY"
    assert before == after


# ---- check 2: stuck refetch attempts ---------------------------------------


def test_active_attempt_20_minutes_is_warn_and_degraded(tmp_path):
    path = _db_path(tmp_path)
    conn = _make_db(path)
    _insert_attempt(conn, chain="chain-1", state="admitted", age_seconds=20 * 60,
                    request_id="req-1", review_id=5)
    conn.commit()
    conn.close()

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 0
    assert report["status"] == "DEGRADED"
    check = _by_code(report, "refetch_stuck")
    assert check["level"] == "WARN"
    assert check["details"]["count"] == 1
    attempt = check["details"]["attempts"][0]
    assert attempt["request_id"] == "req-1"
    assert attempt["source_review_id"] == 5
    assert attempt["state"] == "admitted"
    assert attempt["age_minutes"] == pytest.approx(20.0)


def test_active_attempt_40_minutes_is_crit_and_exit_1(tmp_path):
    path = _db_path(tmp_path)
    conn = _make_db(path)
    _insert_attempt(conn, chain="chain-1", state="admitted", age_seconds=40 * 60,
                    request_id="req-2")
    conn.commit()
    conn.close()

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 1
    assert report["status"] == "FAILED"
    check = _by_code(report, "refetch_stuck")
    assert check["level"] == "CRIT"
    assert check["details"]["attempts"][0]["age_minutes"] == pytest.approx(40.0)


def test_legacy_admitted_state_is_normalized_to_active(tmp_path):
    """`admitted` is the pre-2.69 spelling of the canonical `searching`."""
    path = _db_path(tmp_path)
    conn = _make_db(path)
    _insert_attempt(conn, chain="chain-legacy", state="admitted", age_seconds=60.0,
                    request_id="req-legacy")
    conn.commit()
    conn.close()

    _exit_code, report = run_doctor(db_paths=[path], now=NOW)

    check = _by_code(report, "refetch_stuck")
    assert check["level"] == "OK"
    assert check["details"]["count"] == 1
    assert check["details"]["attempts"][0]["canonical_state"] == "searching"


def test_terminal_attempts_are_not_reported_as_active(tmp_path):
    path = _db_path(tmp_path)
    conn = _make_db(path)
    for state in ("replaced", "failed", "no_alternative", "obsolete", "timeout"):
        _insert_attempt(conn, chain=f"chain-{state}", state=state,
                        age_seconds=90 * 60, request_id=f"req-{state}")
    conn.commit()
    conn.close()

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 0
    assert report["status"] == "HEALTHY"
    assert _by_code(report, "refetch_stuck")["details"]["count"] == 0


def test_unresolvable_refetch_states_degrade_to_skip(tmp_path, monkeypatch):
    """A drifted ``refetch_state`` API must never turn doctor into a traceback."""
    class _Stub:  # no ACTIVE_STATES / normalize / is_active
        pass

    path = _db_path(tmp_path)
    conn = _make_db(path)
    _insert_attempt(conn, chain="chain-1", state="searching", age_seconds=60.0,
                    request_id="req-1")
    conn.commit()
    conn.close()
    monkeypatch.setattr(doctor, "refetch_state", _Stub())

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 0
    assert _by_code(report, "refetch_stuck")["level"] == "SKIP"
    assert _by_code(report, "refetch_active_invariant")["level"] == "SKIP"


def test_refetch_tables_absent_degrades_to_skip(tmp_path):
    path = _db_path(tmp_path)
    _make_db(path, tables=("pending_reviews", "audit_events")).close()

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 0
    assert report["status"] == "HEALTHY"
    for code in ("refetch_stuck", "refetch_active_invariant", "delivery_outbox"):
        assert _by_code(report, code)["level"] == "SKIP"


# ---- check 3: one active attempt per chain ---------------------------------


def test_two_active_attempts_on_one_chain_is_crit(tmp_path):
    path = _db_path(tmp_path)
    conn = _make_db(path, refetch_index=False)
    _insert_attempt(conn, chain="chain-dup", state="searching", age_seconds=60.0,
                    request_id="req-dup-1", review_id=1)
    _insert_attempt(conn, chain="chain-dup", state="requested", age_seconds=30.0,
                    request_id="req-dup-2", review_id=2)
    conn.commit()
    conn.close()

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 1
    check = _by_code(report, "refetch_active_invariant")
    assert check["level"] == "CRIT"
    assert check["details"]["index_exists"] is False
    assert check["details"]["chains_with_multiple_active"] == 1
    assert check["details"]["duplicated_chains"] == [
        {"review_chain_id": "chain-dup", "active_attempts": 2}
    ]


def test_index_present_and_one_active_per_chain_is_ok(tmp_path):
    path = _db_path(tmp_path)
    conn = _make_db(path)
    _insert_attempt(conn, chain="chain-a", state="requested", age_seconds=10.0,
                    request_id="req-a")
    _insert_attempt(conn, chain="chain-b", state="searching", age_seconds=10.0,
                    request_id="req-b")
    conn.commit()
    conn.close()

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 0
    check = _by_code(report, "refetch_active_invariant")
    assert check["level"] == "OK"
    assert check["details"]["index_exists"] is True
    assert check["details"]["index_unique"] is True
    assert check["details"]["chains_with_multiple_active"] == 0


# ---- check 4: pending reviews without a control card -----------------------


@pytest.mark.parametrize(
    "age_seconds, expected",
    [(20 * 60, "WARN"), (70 * 60, "CRIT")],
)
def test_orphan_pending_review(tmp_path, age_seconds, expected):
    path = _db_path(tmp_path)
    conn = _make_db(path)
    _insert_review(conn, status="pending", age_seconds=age_seconds,
                   control_message_id=None)
    conn.commit()
    conn.close()

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    check = _by_code(report, "review_queue_orphans")
    assert check["level"] == expected
    assert check["details"]["count"] == 1
    assert len(check["details"]["review_ids"]) == 1
    if expected == "CRIT":
        assert exit_code == 1
    else:
        assert exit_code == 0
        assert report["status"] == "DEGRADED"


# ---- check 5: publishing rows past the stale window ------------------------


def test_publishing_row_older_than_stale_window_is_crit(tmp_path):
    stale_seconds, _source = doctor._publishing_stale_seconds()
    path = _db_path(tmp_path)
    conn = _make_db(path)
    _insert_review(conn, status="publishing",
                   age_seconds=stale_seconds + 120.0, control_message_id=7)
    conn.commit()
    conn.close()

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 1
    assert report["status"] == "FAILED"
    check = _by_code(report, "review_queue_publishing")
    assert check["level"] == "CRIT"
    assert check["details"]["stale_seconds"] == pytest.approx(stale_seconds)
    assert check["details"]["stuck_count"] == 1


def test_publishing_row_inside_stale_window_is_ok(tmp_path):
    stale_seconds, _source = doctor._publishing_stale_seconds()
    path = _db_path(tmp_path)
    conn = _make_db(path)
    _insert_review(conn, status="publishing",
                   age_seconds=max(1.0, stale_seconds / 2.0), control_message_id=7)
    conn.commit()
    conn.close()

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 0
    assert _by_code(report, "review_queue_publishing")["level"] == "OK"


# ---- check 6: queue counts -------------------------------------------------


def test_review_queue_counts_warn_when_oldest_pending_is_stale(tmp_path):
    path = _db_path(tmp_path)
    conn = _make_db(path)
    _insert_review(conn, status="pending", age_seconds=8 * 86_400.0)
    _insert_review(conn, status="published", age_seconds=60.0)
    conn.commit()
    conn.close()

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 0
    assert report["status"] == "DEGRADED"
    check = _by_code(report, "review_queue_counts")
    assert check["level"] == "WARN"
    assert check["details"]["counts"] == {"pending": 1, "published": 1}
    assert check["details"]["oldest_pending_age_seconds"] == pytest.approx(
        8 * 86_400.0
    )


# ---- check 7: outbox absent / present --------------------------------------


def test_absent_outbox_table_is_skip_without_crash(tmp_path):
    path = _db_path(tmp_path)
    _make_db(path, tables=("pending_reviews", "refetch_attempts", "audit_events")).close()

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 0
    assert report["status"] == "HEALTHY"
    assert _by_code(report, "delivery_outbox")["level"] == "SKIP"


def test_failed_delivery_ledger_rows_are_warn(tmp_path):
    path = _db_path(tmp_path)
    conn = _make_db(path)
    conn.execute(
        "INSERT INTO delivery_ledger (idempotency_key, status, created_at) "
        "VALUES ('review:1:k', 'partial', ?)",
        (NOW - 60.0,),
    )
    conn.commit()
    conn.close()

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 0
    assert report["status"] == "DEGRADED"
    check = _by_code(report, "delivery_outbox")
    assert check["level"] == "WARN"
    assert check["details"]["ledger_failed"] == 1


# ---- check 8: audit activity ----------------------------------------------


def test_active_attempt_without_refetch_audit_events_is_warn(tmp_path):
    path = _db_path(tmp_path)
    conn = _make_db(path)
    _insert_attempt(conn, chain="chain-1", state="searching", age_seconds=60.0,
                    request_id="req-1")
    conn.execute(
        "INSERT INTO audit_events (ts, event, review_id) VALUES (?, 'review.approved', 1)",
        (NOW - 120.0,),
    )
    conn.commit()
    conn.close()

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    check = _by_code(report, "audit_events_recent")
    assert check["level"] == "WARN"
    assert check["details"]["events_last_24h"] == 1
    assert check["details"]["newest_refetch_event_age_seconds"] is None
    assert exit_code == 0


def test_recent_refetch_audit_event_is_ok(tmp_path):
    path = _db_path(tmp_path)
    conn = _make_db(path)
    _insert_attempt(conn, chain="chain-1", state="searching", age_seconds=60.0,
                    request_id="req-1")
    conn.execute(
        "INSERT INTO audit_events (ts, event, detail) VALUES (?, 'review.refetch.requested', ?)",
        (NOW - 30.0, json.dumps({"detail": "secret-token-value"})),
    )
    conn.commit()
    conn.close()

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 0
    check = _by_code(report, "audit_events_recent")
    assert check["level"] == "OK"
    assert "secret-token-value" not in json.dumps(report, ensure_ascii=False)


# ---- cannot verify ---------------------------------------------------------


def test_missing_database_file_is_exit_2(tmp_path):
    path = _db_path(tmp_path, "missing.db")

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 2
    assert report["status"] == "FAILED"
    assert _by_code(report, "db_integrity")["level"] == "CRIT"
    assert report["databases"][0]["exists"] is False


def test_not_a_database_file_is_exit_2(tmp_path):
    path = _db_path(tmp_path, "garbage.db")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("this is not a sqlite database")

    exit_code, report = run_doctor(db_paths=[path], now=NOW)

    assert exit_code == 2
    assert _by_code(report, "db_integrity")["level"] == "CRIT"


def test_multiple_databases_are_all_reported(tmp_path):
    good = _db_path(tmp_path, "good.db")
    _make_db(good).close()
    bad = _db_path(tmp_path, "bad.db")

    exit_code, report = run_doctor(db_paths=[good, bad], now=NOW)

    assert exit_code == 2
    assert [entry["path"] for entry in report["databases"]] == [good, bad]
    assert report["databases"][0]["exists"] is True
    assert report["databases"][1]["exists"] is False


# ---- human rendering + CLI wrapper ----------------------------------------


def test_human_output_lines_and_summary(tmp_path, capsys, monkeypatch):
    path = _db_path(tmp_path)
    _make_db(path).close()
    monkeypatch.setenv("DB_PATH", path)
    from telepost.observability import cli

    exit_code = cli.main(["doctor", "--now", str(NOW)])
    out = capsys.readouterr().out

    assert exit_code == 0
    lines = out.strip().splitlines()
    assert lines[0].startswith("[OK] db_integrity ")
    assert all(
        line.startswith(("[OK] ", "[WARN] ", "[CRIT] ", "[SKIP] "))
        for line in lines[:-2]
    )
    assert lines[-2] == "HEALTHY"
    assert "检查" in lines[-1]


def test_cli_json_output_parses(tmp_path, capsys, monkeypatch):
    path = _db_path(tmp_path)
    conn = _make_db(path)
    _insert_attempt(conn, chain="chain-1", state="admitted", age_seconds=20 * 60,
                    request_id="req-1")
    conn.commit()
    conn.close()
    monkeypatch.setenv("DB_PATH", path)
    from telepost.observability import cli

    exit_code = cli.main(["doctor", "--json", "--now", str(NOW)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "DEGRADED"
    assert payload["generated_at"] == NOW
    assert [check["code"] for check in payload["checks"]] == list(CHECK_CODES)
    assert payload["databases"][0]["exists"] is True


def test_cli_missing_db_is_exit_2_without_traceback(tmp_path):
    missing = _db_path(tmp_path, "nope.db")
    result = subprocess.run(
        [sys.executable, "-m", "telepost.observability.cli",
         "doctor", "--json", "--bot", "9"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env={**os.environ, "BOT9_DB_PATH": missing},
    )

    assert result.returncode == 2, result.stderr
    assert "Traceback" not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "FAILED"
    assert payload["databases"][0]["path"] == missing


def test_cli_repeated_bot_flag_collects_every_database(tmp_path, capsys, monkeypatch):
    first = _db_path(tmp_path, "bot1.db")
    second = _db_path(tmp_path, "bot2.db")
    _make_db(first).close()
    _make_db(second).close()
    monkeypatch.setenv("BOT1_DB_PATH", first)
    monkeypatch.setenv("BOT2_DB_PATH", second)
    from telepost.observability import cli

    exit_code = cli.main(
        ["doctor", "--bot", "1", "--bot", "2", "--json", "--now", str(NOW)]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert [entry["path"] for entry in payload["databases"]] == [first, second]


def test_cli_all_bots_scans_data_directory(tmp_path, capsys, monkeypatch):
    bot_dir = tmp_path / "data" / "bot3"
    bot_dir.mkdir(parents=True)
    _make_db(str(bot_dir / "submissions.db")).close()
    monkeypatch.chdir(tmp_path)
    from telepost.observability import cli

    exit_code = cli.main(["doctor", "--all-bots", "--json", "--now", str(NOW)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert [entry["path"] for entry in payload["databases"]] == [
        os.path.join("data", "bot3", "submissions.db")
    ]


def test_reviews_inspect_still_works(tmp_path, capsys, monkeypatch):
    """The doctor must not change the existing ``reviews inspect`` behaviour."""
    path = _db_path(tmp_path)
    conn = _make_db(path)
    conn.execute(
        "INSERT INTO pending_reviews (idempotency_key, source, status, user_id, "
        "review_chat_id, created_at, updated_at) VALUES ('api:1:k','api','pending',1,'c',1.0,1.0)"
    )
    conn.execute(
        "INSERT INTO audit_events (ts, event, review_id, actor) "
        "VALUES (1.0, 'review.created', 1, 'api')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("DB_PATH", path)
    from telepost.observability import cli

    exit_code = cli.main(["reviews", "inspect", "1"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "review.created" in out
    assert "idempotency_key" in out
