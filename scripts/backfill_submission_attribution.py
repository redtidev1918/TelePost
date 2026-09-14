#!/usr/bin/env python3
"""Backfill the identity/provenance model on existing TelePost review rows.

Why
---
Before the identity split, ``pending_reviews.user_id`` doubled as "request
actor" and "submission owner", so every automatic PixivFlow/API submission
created with an API token bound to an admin Telegram id looked like that
admin's own submission and appeared under 我的投稿.

What it does (deterministic, idempotent, audited)
-------------------------------------------------
1. Classifies each existing review row from durable evidence only:

   * ``service``  — the row carries PixivFlow/automatic provenance
     (``target_id`` non-empty, ``source_ref`` non-empty,
     ``source_label`` like ``PixivFlow%``, or ``idempotency_key`` like
     ``pixivflow:%``). Sets ``submitter_user_id=NULL`` /
     ``submitter_username=''`` and ``actor_kind='service'`` with
     ``actor_subject='pixivflow_delivery'``.
   * ``human_chat`` — ``source='chat'``: a private-chat submission, which is
     always an explicit human submission. Sets the submitter to the row's
     ``user_id``/``username`` and ``actor_kind='user'``.
   * ``unknown`` — API rows with no service marker and no chat source. The
     submitter stays NULL (no human attribution is invented) and an
     ``submission.attribution_unknown`` audit event is written so the row can be
     revisited if evidence appears.

2. Propagates chain ownership: every refetch replacement inherits the
   submitter of its review-chain root (walking ``supersedes_review_id``).

Guarantees
----------
* never changes review ``status`` / reviewer decisions / refetch attempts;
* never deletes rows;
* dry-run by default; ``--apply`` writes and appends an audit event per changed
  row (``submission.attribution_backfill``);
* re-running is a no-op once the classification is stable.

Usage::

    python3 scripts/backfill_submission_attribution.py --db data/bot1/submissions.db
    python3 scripts/backfill_submission_attribution.py --db data/bot1/submissions.db --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional

SERVICE_MARKERS_SQL = """
    (COALESCE(target_id, '') <> ''
     OR COALESCE(source_ref, '') <> ''
     OR COALESCE(source_label, '') LIKE 'PixivFlow%'
     OR COALESCE(idempotency_key, '') LIKE 'pixivflow:%')
"""

SELECT_SQL = f"""
SELECT id, status, source, user_id, username, target_id, source_ref,
       source_label, idempotency_key, review_chain_id, supersedes_review_id,
       refetch_request_id, submitter_user_id, submitter_username, actor_kind,
       actor_subject
  FROM pending_reviews
 ORDER BY id
"""


def classify(row: Dict[str, Any]) -> str:
    if (row["target_id"] or "").strip() \
            or (row["source_ref"] or "").strip() \
            or (row["source_label"] or "").startswith("PixivFlow") \
            or (row["idempotency_key"] or "").startswith("pixivflow:") \
            or (row["refetch_request_id"] or "").strip():
        # Automatic delivery provenance: PixivFlow/scheduler submissions and
        # refetch replacements are created by the service actor, never by a
        # human, regardless of which API token carried them.
        return "service"
    if (row["source"] or "") == "chat":
        return "human_chat"
    return "unknown"


def chain_root(rows_by_id: Dict[int, Dict[str, Any]], row: Dict[str, Any]) -> Dict[str, Any]:
    seen = set()
    current = row
    while current["supersedes_review_id"] and current["supersedes_review_id"] in rows_by_id:
        if current["id"] in seen:
            break
        seen.add(current["id"])
        current = rows_by_id[current["supersedes_review_id"]]
    return current


def audit_columns(conn: sqlite3.Connection) -> List[str]:
    return [r[1] for r in conn.execute("PRAGMA table_info(audit_events)")]


def write_audit(conn: sqlite3.Connection, cols: List[str], event: str,
                row: Dict[str, Any], detail: Dict[str, Any],
                error_class: Optional[str] = None) -> None:
    values = {
        "ts": time.time(),
        "component": "telepost",
        "event": event,
        "review_id": row["id"],
        "execution_id": row.get("refetch_request_id") or None,
        "actor": "operator:backfill-attribution",
        "error_class": error_class,
        "detail": json.dumps(detail, ensure_ascii=False),
    }
    keys = [k for k in values if k in cols]
    conn.execute(
        f"INSERT INTO audit_events ({', '.join(keys)}) "
        f"VALUES ({', '.join('?' for _ in keys)})",
        [values[k] for k in keys],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            print(f"REFUSING: integrity_check = {integrity}", file=sys.stderr)
            return 2
        columns = {r[1] for r in conn.execute("PRAGMA table_info(pending_reviews)")}
        if "submitter_user_id" not in columns:
            print("REFUSING: pending_reviews has no submitter_user_id column; "
                  "deploy the identity release first so init_db can migrate",
                  file=sys.stderr)
            return 2

        rows = [dict(r) for r in conn.execute(SELECT_SQL)]
        by_id = {r["id"]: r for r in rows}
        report: Dict[str, Any] = {
            "db": args.db,
            "mode": "apply" if args.apply else "dry-run",
            "scanned": len(rows),
            "classified_service": 0,
            "classified_human_chat": 0,
            "legacy_unknown": 0,
            "changed": 0,
            "unchanged": 0,
            "chain_propagated": 0,
            "details": [],
        }
        audit_cols = audit_columns(conn)

        for row in rows:
            kind = classify(row)
            root = chain_root(by_id, row)
            root_kind = classify(root)
            is_replacement = bool((row["refetch_request_id"] or "").strip()) \
                or row["supersedes_review_id"] is not None

            if is_replacement:
                # Chain ownership dominates for a replacement: the delivering
                # actor is always the service, but the OWNER is the chain root's
                # owner (human chain stays human, service chain stays unowned).
                actor_kind, actor_subject = "service", "pixivflow_delivery"
                if root_kind == "human_chat":
                    submitter_id = root["user_id"]
                    submitter_name = root["username"] or ""
                    report["chain_propagated"] += 1
                else:
                    submitter_id, submitter_name = None, ""
            elif kind == "service":
                submitter_id, submitter_name = None, ""
                actor_kind, actor_subject = "service", "pixivflow_delivery"
            elif kind == "human_chat":
                submitter_id, submitter_name = row["user_id"], row["username"] or ""
                actor_kind, actor_subject = "user", f"telegram:{row['user_id']}"
            else:
                # Genuinely undecidable: no human attribution is invented and
                # the actor stays explicitly unknown (never "user").
                submitter_id, submitter_name = None, ""
                actor_kind = "unknown"
                actor_subject = "unknown"

            if kind == "service":
                report["classified_service"] += 1
            elif kind == "human_chat":
                report["classified_human_chat"] += 1
            else:
                report["legacy_unknown"] += 1

            changed = (
                (row["submitter_user_id"] or None) != submitter_id
                or (row["submitter_username"] or "") != submitter_name
                or (row["actor_kind"] or "") != actor_kind
                or (row["actor_subject"] or "") != actor_subject
            )
            if not changed:
                report["unchanged"] += 1
                continue
            report["changed"] += 1
            report["details"].append({
                "review_id": row["id"],
                "classification": kind,
                "chain_root": root["id"],
                "submitter_user_id": submitter_id,
                "actor_kind": actor_kind,
                "previous_actor": row["actor_kind"] or "",
            })
            if not args.apply:
                continue
            conn.execute(
                "UPDATE pending_reviews SET submitter_user_id=?, "
                "submitter_username=?, actor_kind=?, actor_subject=? WHERE id=?",
                (submitter_id, submitter_name, actor_kind, actor_subject, row["id"]),
            )
            write_audit(
                conn, audit_cols,
                "submission.attribution_backfill", row,
                {
                    "classification": kind,
                    "chain_root_id": root["id"],
                    "submitter_user_id": submitter_id,
                    "actor_kind": actor_kind,
                    "actor_subject": actor_subject,
                },
                error_class=None if kind != "unknown" else "attribution_unknown",
            )
            if kind == "unknown":
                write_audit(
                    conn, audit_cols, "submission.attribution_unknown", row,
                    {"reason": "no deterministic human attribution evidence"},
                    error_class="attribution_unknown",
                )

        if args.apply:
            conn.commit()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(
                f"{report['mode']}: scanned={report['scanned']} "
                f"service={report['classified_service']} "
                f"human_chat={report['classified_human_chat']} "
                f"unknown={report['legacy_unknown']} "
                f"changed={report['changed']} unchanged={report['unchanged']} "
                f"chain_propagated={report['chain_propagated']}"
            )
            for item in report["details"][:20]:
                print(
                    f"  review #{item['review_id']}: {item['classification']} "
                    f"-> submitter={item['submitter_user_id']} "
                    f"actor={item['actor_kind']}"
                )
            if not args.apply and report["changed"]:
                print("dry run: no rows written (pass --apply)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
