"""Manual recovery tests (§manual-recovery, §tests).

Contract: recovery requests are durable + idempotent per callback key, one
active attempt per target, only the chosen preset is ever sent (never raw
parameters), and the button handler gives a business-friendly answer and
removes the stale buttons.
"""
import os
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


def _fake_remote(monkeypatch, *, status=202, payload=None):
    """Patch urllib urlopen with a fake 202 'accepted' response."""
    from telepost.application import recovery as recovery_mod

    class FakeResponse:
        def __init__(self):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            body = {"status": "accepted", "slotId": "s",
                    "requestId": "x"} if payload is None else payload
            import json
            return json.dumps(body).encode()

    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        return FakeResponse()

    monkeypatch.setattr(recovery_mod, "urlopen", fake_urlopen)
    return calls


async def _init(monkeypatch, tmp_path):
    db = _test_db(monkeypatch, tmp_path)
    await db.init_db()
    return db


async def test_recovery_request_is_idempotent_per_button_click(monkeypatch,
                                                               tmp_path,
                                                               pixivflow_env):
    await _init(monkeypatch, tmp_path)
    calls = _fake_remote(monkeypatch)

    from telepost.application.recovery import request_target_recovery

    first = await request_target_recovery(
        "bot1-illust-botefuku", retry_mode="relaxed",
        callback_key="cb-123", schedule_id="bot1-daily")
    assert first["state"] == "accepted"
    assert first["retry_mode"] == "relaxed"
    assert first["target_id"] == "bot1-illust-botefuku"

    # The SAME button click redelivered converges onto the SAME attempt.
    replay = await request_target_recovery(
        "bot1-illust-botefuku", retry_mode="relaxed",
        callback_key="cb-123", schedule_id="bot1-daily")
    assert replay["replayed"] is True
    assert replay["request_id"] == first["request_id"]

    # Only ONE remote submission happened.
    assert len(calls) == 1
    body = calls[0].data.decode()
    assert '"retryMode": "relaxed"' in body
    assert "candidateScanLimit" not in body
    assert "lookbackDays" not in body


async def test_one_active_recovery_per_target(monkeypatch, tmp_path,
                                              pixivflow_env):
    await _init(monkeypatch, tmp_path)
    _fake_remote(monkeypatch)

    from telepost.application.recovery import (
        RecoveryAlreadyRunningError,
        request_target_recovery,
    )

    await request_target_recovery(
        "bot1-illust-botefuku", retry_mode="normal", callback_key="cb-1")

    # A NEW intentional click while an attempt is still active is refused, not
    # silently duplicated.
    with pytest.raises(RecoveryAlreadyRunningError):
        await request_target_recovery(
            "bot1-illust-botefuku", retry_mode="relaxed", callback_key="cb-2")


async def test_recovery_defaults_to_normal_and_never_sends_raw_parameters(
        monkeypatch, tmp_path, pixivflow_env):
    await _init(monkeypatch, tmp_path)
    calls = _fake_remote(monkeypatch)

    from telepost.application.recovery import request_target_recovery

    result = await request_target_recovery(
        "bot2-novel-marunomi", callback_key=f"cb-{uuid.uuid4().hex}")
    assert result["retry_mode"] == "normal"
    body = calls[0].data.decode()
    assert '"retryMode": "normal"' in body


async def test_schedule_recover_button_handler(monkeypatch, tmp_path,
                                               pixivflow_env):
    await _init(monkeypatch, tmp_path)
    _fake_remote(monkeypatch)

    from handlers.recovery import schedule_recover

    query = SimpleNamespace(
        id="cb-callback-1",
        data="sched_recover|bot1-illust-botefuku|relaxed",
        answer=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query)
    await schedule_recover(update, MagicMock())

    answer = query.answer.await_args.kwargs.get("text") or ""
    assert "系统会自动继续处理" in answer
    assert "放宽条件重试" in answer
    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)


@pytest.mark.asyncio
async def test_recovery_not_configured_is_a_clear_error(monkeypatch, tmp_path):
    await _init(monkeypatch, tmp_path)
    from telepost.application.recovery import (
        RecoveryNotConfiguredError,
        request_target_recovery,
    )
    with pytest.raises(RecoveryNotConfiguredError):
        await request_target_recovery("bot1-illust-botefuku", callback_key="cb-x")