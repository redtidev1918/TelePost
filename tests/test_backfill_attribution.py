"""Backfill of the identity/provenance model on legacy rows (§identity)."""
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from database import db_manager


async def _mkdb(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "bf.db"))
    await db_manager.init_db()


async def _row(*, source="api", user_id=5073758941, username="pixivflow",
               target_id="", source_ref="", source_label="",
               idempotency_key="", refetch_request_id="",
               supersedes_review_id=None, submitter_user_id=None,
               submitter_username="", actor_kind="user", actor_subject=""):
    now = time.time()
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            """
            INSERT INTO pending_reviews (
                idempotency_key, source, status, user_id, username,
                review_chat_id, media_json, documents_json,
                target_id, source_label, source_ref,
                review_chain_id, supersedes_review_id, refetch_request_id,
                submitter_user_id, submitter_username, actor_kind, actor_subject,
                created_at, updated_at
            ) VALUES (?, ?, 'pending', ?, ?, -1001, '[]', '[]',
                      ?, ?, ?, 'chain-x', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (idempotency_key or f"k{int(now*1000)}-{user_id}-{refetch_request_id or '' or supersedes_review_id or int(now*1e6)}", source,
             user_id, username, target_id, source_label, source_ref,
             supersedes_review_id, refetch_request_id or "",
             submitter_user_id, submitter_username, actor_kind, actor_subject,
             now, now),
        )
        return cur.lastrowid


def _run_script(db_path, apply=False):
    import subprocess, sys
    cmd = [sys.executable, "scripts/backfill_submission_attribution.py",
           "--db", db_path, "--json"]
    if apply:
        cmd.append("--apply")
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=".")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


async def _get(row_id):
    async with db_manager.get_db() as conn:
        cur = await conn.execute("SELECT * FROM pending_reviews WHERE id=?",
                                 (row_id,))
        return await cur.fetchone()


@pytest.mark.asyncio
async def test_backfill_classifies_and_is_idempotent(monkeypatch, tmp_path):
    await _mkdb(monkeypatch, tmp_path)
    svc = await _row(target_id="bot1-illust-botefuku", source_ref="exec-1",
                     source_label="PixivFlow · bot1-daily")
    chat = await _row(source="chat", user_id=7, username="alice")
    unknown = await _row(source="api")  # no markers
    # replacement of a human chat chain (delivered by the service actor)
    repl = await _row(refetch_request_id="u-u-i-d",
                      supersedes_review_id=chat)

    report = _run_script(str(tmp_path / "bf.db"), apply=True)
    assert report["scanned"] == 4
    assert report["classified_service"] == 2  # service row + its refetch replacement
    assert report["classified_human_chat"] == 1
    assert report["legacy_unknown"] == 1
    assert report["changed"] == 4  # all four rows updated once

    s = await _get(svc)
    assert s["submitter_user_id"] is None
    assert s["actor_kind"] == "service"
    assert s["actor_subject"] == "pixivflow_delivery"

    c = await _get(chat)
    assert c["submitter_user_id"] == 7
    assert c["submitter_username"] == "alice"
    assert c["actor_kind"] == "user"

    u = await _get(unknown)
    assert u["submitter_user_id"] is None
    assert u["actor_kind"] == "unknown"  # never invented as human

    r = await _get(repl)
    assert r["submitter_user_id"] == 7          # chain owner inherited
    assert r["submitter_username"] == "alice"
    assert r["actor_kind"] == "service"         # actor is the deliverer

    # audit rows: one backfill per changed row + one unknown marker
    async with db_manager.get_db() as conn:
        n_backfill = (await (await conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event='submission.attribution_backfill'"
        )).fetchone())[0]
        n_unknown = (await (await conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event='submission.attribution_unknown'"
        )).fetchone())[0]
    assert n_backfill == 4
    assert n_unknown == 1

    # Idempotent: re-running changes nothing.
    report2 = _run_script(str(tmp_path / "bf.db"), apply=True)
    assert report2["changed"] == 0
    async with db_manager.get_db() as conn:
        n2 = (await (await conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event='submission.attribution_backfill'"
        )).fetchone())[0]
    assert n2 == 4


@pytest.mark.asyncio
async def test_backfill_service_chain_replacement_stays_unowned(monkeypatch, tmp_path):
    await _mkdb(monkeypatch, tmp_path)
    root = await _row(target_id="bot2-illust-marunomi", source_ref="e-2")
    repl = await _row(refetch_request_id="u2", supersedes_review_id=root)
    _run_script(str(tmp_path / "bf.db"), apply=True)
    assert (await _get(root))["submitter_user_id"] is None
    assert (await _get(repl))["submitter_user_id"] is None