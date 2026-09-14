#!/usr/bin/env python3
"""Reconcile legacy refetch attempts whose correlation was broken by the
PixivFlow 2.20.0 delivery-template bug (``refetch_request_id`` delivered as the
literal ``{{refetchRequestId}}``).

Background (2026-09-14 production incident):

* TelePost admitted a refetch attempt and POSTed it to PixivFlow.
* PixivFlow found a candidate and delivered a replacement submission, but the
  submission carried the literal template instead of the request UUID, so
  TelePost could not correlate it back to the attempt.
* The replacement landed as an independent review and the attempt stayed
  ``admitted`` until the local stale watchdog failed it with
  ``failure_code='stale_timeout'`` - a crash-fallback code that mislabels the
  real cause.

What this script does (idempotent, bounded, audit-preserving):

* selects ONLY attempts with ``state='failed' AND failure_code='stale_timeout'``
  (the code is no longer produced by the fixed watchdog, so the set is closed);
* rewrites ``failure_code`` to ``legacy_refetch_correlation_broken`` and appends
  one audit event per attempt (``review.refetch_legacy_reconciled``);
* NEVER touches review rows, reviewer decisions, or PixivFlow state.

It deliberately does not "restore lineage": the durable PixivFlow delivery
ledger proves which candidate each attempt delivered, but the delivered reviews
were independently approved/rejected by reviewers afterwards, so rewriting that
lineage would change reviewer-visible history.

Usage::

    python3 scripts/reconcile-legacy-refetch-attempts.py --db /path/submissions.db            # dry run
    python3 scripts/reconcile-legacy-refetch-attempts.py --db /path/submissions.db --apply    # mutate

Run it through the deploy repository against bot1 and bot2 databases.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from typing import Any, Dict, List


LEGACY_CODE = "stale_timeout"
NEW_CODE = "legacy_refetch_correlation_broken"
EVENT = "review.refetch_legacy_reconciled"

SELECT_SQL = """
SELECT a.id, a.request_id, a.review_chain_id, a.generation, a.source_review_id,
       a.slot_id, a.state, a.failure_code, a.scanned, a.result_candidate_id,
       r.status AS review_status, r.refetch_request_id AS review_request_id
  FROM refetch_attempts a
  LEFT JOIN pending_reviews r ON r.id = a.source_review_id
 WHERE a.state = 'failed' AND a.failure_code = ?
 ORDER BY a.id
"""


def audit_columns(conn: sqlite3.Connection) -> List[str]:
    return [row[1] for row in conn.execute("PRAGMA table_info(audit_events)")]


def collect(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(SELECT_SQL, (LEGACY_CODE,))]


def already_reconciled(conn: sqlite3.Connection, request_id: str) -> bool:
    for row in conn.execute(
        "SELECT detail FROM audit_events WHERE event = ? AND execution_id = ?",
        (EVENT, request_id),
    ):
        return True
    return False


def evidence(row: Dict[str, Any]) -> Dict[str, Any]:
    """Durable evidence recorded with each reconciliation (no guessing)."""
    return {
        "request_id": row["request_id"],
        "slot_id": row["slot_id"],
        "review_chain_id": row["review_chain_id"],
        "source_review_id": row["source_review_id"],
        "source_review_status": row["review_status"],
        "review_refetch_request_id": row["review_request_id"],
        "legacy_failure_code": LEGACY_CODE,
        "cause": (
            "refetch_request_id delivered as literal template by PixivFlow 2.20.0; "
            "replacement landed as an uncorrelated review and the attempt was only "
            "terminated by the local stale watchdog"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="TelePost submissions.db path")
    parser.add_argument("--apply", action="store_true", help="perform the mutation")
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            print(f"REFUSING: integrity_check = {integrity}", file=sys.stderr)
            return 2

        rows = collect(conn)
        report: Dict[str, Any] = {
            "db": args.db,
            "mode": "apply" if args.apply else "dry-run",
            "legacy_attempts": len(rows),
            "reconciled": [],
            "already_reconciled": [],
        }

        cols = audit_columns(conn)
        now = time.time()
        for row in rows:
            detail = evidence(row)
            if already_reconciled(conn, row["request_id"]):
                report["already_reconciled"].append(detail)
                continue
            report["reconciled"].append(detail)
            if not args.apply:
                continue
            conn.execute(
                "UPDATE refetch_attempts SET failure_code = ? "
                "WHERE id = ? AND state = 'failed' AND failure_code = ?",
                (NEW_CODE, row["id"], LEGACY_CODE),
            )
            values = {
                "ts": now,
                "component": "telepost",
                "event": EVENT,
                "review_id": row["source_review_id"],
                "pixiv_id": None,
                "work_type": None,
                "target_id": None,
                "idempotency_key": None,
                "execution_id": row["request_id"],
                "actor": "operator:reconcile-legacy-refetch",
                "error_class": "legacy_refetch_correlation_broken",
                "detail": json.dumps(detail, ensure_ascii=False),
            }
            keys = [k for k in values if k in cols]
            conn.execute(
                f"INSERT INTO audit_events ({', '.join(keys)}) "
                f"VALUES ({', '.join('?' for _ in keys)})",
                [values[k] for k in keys],
            )
        if args.apply:
            conn.commit()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"{report['mode']}: {len(rows)} legacy attempt(s) in {args.db}")
            for item in report["reconciled"]:
                print(
                    f"  attempt {item['request_id']} chain={item['review_chain_id']} "
                    f"review=#{item['source_review_id']} status={item['source_review_status']} "
                    f"slot={item['slot_id']}"
                )
            for item in report["already_reconciled"]:
                print(f"  already reconciled: {item['request_id']}")
            if not args.apply and rows:
                print("dry run: no rows written (pass --apply)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
