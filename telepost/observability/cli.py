"""Read-only observability CLI: ``reviews inspect <id> [--bot N]``.

Resolves the SQLite DB the same way the migration/cleanup scripts do:
``config.settings.DB_PATH`` (which honors ``DB_PATH``/``BOTN_DB_PATH`` env) with
the multi-bot default ``data/botN/submissions.db``. Never writes.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Optional


def resolve_db_path(bot: Optional[int]) -> str:
    if bot is not None:
        # Mirror run.build_bot_env: BOT{n}_DB_PATH overrides the per-bot
        # default; an explicit --bot selects the per-bot data directory.
        return os.getenv(f"BOT{bot}_DB_PATH", f"data/bot{bot}/submissions.db")
    if os.getenv("DB_PATH"):
        return os.environ["DB_PATH"]
    try:
        from config.settings import DB_PATH
        return DB_PATH
    except Exception:
        return "data/submissions.db"


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _print_row(row: Optional[sqlite3.Row]) -> None:
    if row is None:
        print("review: <not found>")
        return
    print("review:")
    for key in row.keys():
        print(f"  {key}: {row[key]}")


def inspect_review(db_path: str, review_id: int) -> int:
    if not os.path.exists(db_path):
        print(f"database not found: {db_path}", file=sys.stderr)
        return 2
    with _connect(db_path) as conn:
        review = conn.execute(
            "SELECT * FROM pending_reviews WHERE id=?", (review_id,)
        ).fetchone()
        _print_row(review)

        print("\naudit_events (newest first):")
        try:
            events = conn.execute(
                "SELECT * FROM audit_events WHERE review_id=? "
                "ORDER BY ts DESC, id DESC",
                (review_id,),
            ).fetchall()
        except sqlite3.Error as exc:
            print(f"  <audit_events unavailable: {exc}>")
            events = []
        if not events:
            print("  <none>")
        for event in events:
            detail = event["detail"]
            try:
                detail = json.dumps(json.loads(detail), ensure_ascii=False)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            print(
                f"  [{event['ts']}] {event['event']} actor={event['actor']} "
                f"error_class={event['error_class']} detail={detail}"
            )

        print("\ndelivery_ledger:")
        ledger = None
        if review is not None and review["idempotency_key"]:
            ledger = conn.execute(
                "SELECT * FROM delivery_ledger WHERE idempotency_key=?",
                (f"review:{review_id}:{review['idempotency_key']}",),
            ).fetchone()
        if ledger is None:
            print("  <none>")
        else:
            for key in ledger.keys():
                print(f"  {key}: {ledger[key]}")
    return 0 if review is not None else 1


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="telepost.observability.cli")
    sub = parser.add_subparsers(dest="resource", required=True)
    reviews = sub.add_parser("reviews")
    review_sub = reviews.add_subparsers(dest="action", required=True)
    inspect_parser = review_sub.add_parser("inspect")
    inspect_parser.add_argument("review_id", type=int)
    inspect_parser.add_argument("--bot", type=int, default=None)

    args = parser.parse_args(argv)
    if args.resource == "reviews" and args.action == "inspect":
        return inspect_review(resolve_db_path(args.bot), args.review_id)
    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
