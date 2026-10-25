"""Answer-first manual recovery (§manual-recovery, 2026-10-25 repair).

The recovery callback runs inside the strictly-serial Durable Inbox worker.
Before this repair it answered the user only AFTER a remote HTTP round-trip
(PixivFlow cold start ~8s+, worst case a 120s timeout): the answer blew
Telegram's ~10s callback window ("no response") and head-of-line blocked
every later update of the bot. These tests pin the repaired contract:

* the handler returns (and answers) without waiting on the remote;
* the remote submission happens in the background and settles the row;
* a crash-or-transient 'requested' row is re-submitted by the periodic sweep
  with the SAME requestId (idempotent), and terminalizes past its age cap;
* a replayed click never resurrects a terminal row.
"""
import asyncio
import threading
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from database import db_manager


def _test_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "recovery.db"))
    return db_manager


@pytest.fixture
def pixivflow_env(monkeypatch):
    monkeypatch.setenv("PIXIVFLOW_REFETCH_BASE_URL", "http://pixivflow.test:8090")
    monkeypatch.setenv("PIXIVFLOW_REFETCH_TOKEN", "recover-secret")
    yield
    monkeypatch.delenv("PIXIVFLOW_REFETCH_BASE_URL", raising=False)
    monkeypatch.delenv("PIXIVFLOW_REFETCH_TOKEN", raising=False)


async def _init(monkeypatch, tmp_path):
    db = _test_db(monkeypatch, tmp_path)
    await db.init_db()
    return db


def _query(qid="cb-1", target="bot1-illust-test", mode="normal", chat=-100777):
    return SimpleNamespace(
        id=qid,
        data=f"sched_recover|{target}|{mode}",
        answer=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
        message=SimpleNamespace(chat_id=chat) if chat is not None else None,
    )


def _context():
    context = MagicMock()
    context.bot = AsyncMock()
    context.bot_data = {}
    return context


async def _row(request_id: str):
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT * FROM recovery_attempts WHERE request_id=?", (request_id,)
        )
        return await cur.fetchone()


# ---- T-05: the click must not wait on the remote --------------------------
@pytest.mark.asyncio
async def test_click_answers_while_remote_is_slow(monkeypatch, tmp_path,
                                                  pixivflow_env):
    """HOL injection: the remote blocks; the handler still answers in time."""
    await _init(monkeypatch, tmp_path)
    from telepost.application import recovery as recovery_mod
    from handlers.recovery import schedule_recover

    release = threading.Event()
    calls = []

    def slow_urlopen(request, timeout=None):
        calls.append(request)
        release.wait(timeout=30)  # blocks the to_thread worker thread
        import json as _json

        class FakeResponse:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return _json.dumps(
                    {"status": "accepted", "slotId": "s"}).encode()

        return FakeResponse()

    monkeypatch.setattr(recovery_mod, "urlopen", slow_urlopen)

    query = _query()
    context = _context()
    # RED on the pre-fix code: the handler awaited the submit → TimeoutError.
    await asyncio.wait_for(
        schedule_recover(SimpleNamespace(callback_query=query), context),
        timeout=2.0,
    )
    answer = query.answer.await_args.kwargs.get("text") or ""
    assert "系统会自动继续处理" in answer, "the user must be answered first"

    release.set()
    for _ in range(300):
        if not context.bot_data.get("_recovery_submit_tasks"):
            break
        await asyncio.sleep(0.01)
    async with db_manager.get_db() as conn:
        row = await (await conn.execute(
            "SELECT * FROM recovery_attempts"
        )).fetchone()
    assert row is not None and row["state"] == "accepted", \
        "the background submit must settle the attempt"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_replayed_click_does_not_resubmit(monkeypatch, tmp_path,
                                                pixivflow_env):
    """Webhook re-delivery of the same click answers again but never starts a
    second submit or resurrects a terminal row."""
    await _init(monkeypatch, tmp_path)
    from telepost.application import recovery as recovery_mod
    from handlers.recovery import schedule_recover

    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        import json as _json

        class FakeResponse:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return _json.dumps({"status": "accepted", "slotId": "s"}).encode()

        return FakeResponse()

    monkeypatch.setattr(recovery_mod, "urlopen", fake_urlopen)

    first_context = _context()
    await schedule_recover(
        SimpleNamespace(callback_query=_query(qid="cb-same")), first_context
    )
    for _ in range(300):
        if not first_context.bot_data.get("_recovery_submit_tasks"):
            break
        await asyncio.sleep(0.01)

    # Same callback id = same durable key: replay, not a new attempt.
    replay_query = _query(qid="cb-same")
    replay_context = _context()
    await schedule_recover(
        SimpleNamespace(callback_query=replay_query), replay_context
    )
    answer = replay_query.answer.await_args.kwargs.get("text") or ""
    assert "已提交" in answer or "处理中" in answer

    async with db_manager.get_db() as conn:
        n = await (await conn.execute(
            "SELECT COUNT(*) AS n FROM recovery_attempts")).fetchone()
    assert n["n"] == 1, "a replayed click must not create a second attempt"
    # The replay path did not spawn another submit (row already accepted).
    assert not replay_context.bot_data.get("_recovery_submit_tasks")
    assert len(calls) == 1


# ---- sweep: crash / transient safety net -----------------------------------
@pytest.mark.asyncio
async def test_sweep_resubmits_stale_requested_row(monkeypatch, tmp_path,
                                                   pixivflow_env):
    await _init(monkeypatch, tmp_path)
    from telepost.application import recovery as recovery_mod
    from telepost.storage.sqlite.recovery import RecoveryRepository

    request_id = str(uuid.uuid4())
    async with db_manager.get_db() as conn:
        await RecoveryRepository(conn).create_attempt(
            target_id="bot1-illust-sweep", retry_mode="normal",
            callback_key=f"cb-{request_id[:8]}",
        )
        # Force a specific request_id + age.
        await conn.execute(
            "UPDATE recovery_attempts SET request_id=?, created_at=? "
            "WHERE callback_key=?",
            (request_id, time.time() - 120, f"cb-{request_id[:8]}"),
        )

    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        import json as _json

        class FakeResponse:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return _json.dumps({"status": "accepted", "slotId": "sw"}).encode()

        return FakeResponse()

    monkeypatch.setattr(recovery_mod, "urlopen", fake_urlopen)

    from telepost.application.recovery import resubmit_pending_recoveries
    n = await resubmit_pending_recoveries()
    assert n == 1
    row = await _row(request_id)
    assert row["state"] == "accepted"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_sweep_terminalizes_past_max_age(monkeypatch, tmp_path,
                                               pixivflow_env):
    await _init(monkeypatch, tmp_path)
    from telepost.application import recovery as recovery_mod
    from telepost.storage.sqlite.recovery import RecoveryRepository

    request_id = str(uuid.uuid4())
    key = f"cb-old-{request_id[:8]}"
    async with db_manager.get_db() as conn:
        await RecoveryRepository(conn).create_attempt(
            target_id="bot1-illust-old", retry_mode="normal", callback_key=key,
        )
        await conn.execute(
            "UPDATE recovery_attempts SET request_id=?, created_at=? "
            "WHERE callback_key=?",
            (request_id, time.time() - 3600, key),
        )

    calls = []

    def no_call(request, timeout=None):  # pragma: no cover - must not run
        calls.append(request)
        raise AssertionError("expired attempt must not be re-submitted")

    monkeypatch.setattr(recovery_mod, "urlopen", no_call)

    from telepost.application.recovery import resubmit_pending_recoveries
    await resubmit_pending_recoveries()
    row = await _row(request_id)
    assert row["state"] == "failed"
    assert row["failure_code"] == "submit_retry_exhausted"
    assert calls == []


@pytest.mark.asyncio
async def test_sweep_ignores_fresh_requested_rows(monkeypatch, tmp_path,
                                                  pixivflow_env):
    """A row younger than the sweep floor belongs to its background task."""
    await _init(monkeypatch, tmp_path)
    from telepost.storage.sqlite.recovery import RecoveryRepository

    async with db_manager.get_db() as conn:
        await RecoveryRepository(conn).create_attempt(
            target_id="bot1-illust-fresh", retry_mode="normal",
            callback_key="cb-fresh",
        )

    def no_call(request, timeout=None):  # pragma: no cover - must not run
        raise AssertionError("fresh attempt must not be swept")

    from telepost.application import recovery as recovery_mod
    monkeypatch.setattr(recovery_mod, "urlopen", no_call)

    from telepost.application.recovery import resubmit_pending_recoveries
    assert await resubmit_pending_recoveries() == 0
