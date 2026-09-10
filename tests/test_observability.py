"""Observability layer: redaction, error classification, durable audit."""

import pytest
from unittest.mock import AsyncMock, patch

from database import db_manager
from telepost.observability import audit as audit_mod
from telepost.observability.errors import classify
from telepost.observability.redact import (
    redact_headers,
    redact_text,
    sanitize_detail,
)


# ---- redaction -------------------------------------------------------------

@pytest.mark.unit
def test_redact_text_masks_bot_token_and_bearer():
    shaped = "123456789:AAEhBOweykLoeXAMPLE-TOKEN_abcdef1234567"
    bearer = "tp_" + "x" * 40
    text = (
        f"calling https://api.telegram.org/bot{shaped}/sendMessage "
        f"Authorization: Bearer {bearer} "
        f"see ?token=secretvalue&q=ok password=hunter2"
    )
    out = redact_text(text)
    assert shaped not in out
    assert bearer not in out
    assert "secretvalue" not in out
    assert "hunter2" not in out
    assert "q=ok" in out  # non-secret params survive


@pytest.mark.unit
def test_redact_headers_and_sanitize_detail():
    token = "123456789:AAEhBOweykLoeXAMPLE-TOKEN_abcdef1234567"
    headers = {
        "Authorization": f"Bearer {token}",
        "Cookie": "session=abc",
        "X-Telegram-Bot-Api-Secret-Token": "webhooksecret",
        "X-Custom-Token": token,
        "Content-Type": "application/json",
    }
    masked = redact_headers(headers)
    assert all(v == "***" for k, v in masked.items() if k != "Content-Type")

    clean = sanitize_detail({
        "url": f"https://x/bot{token}/y",
        "nested": [{"api_token": token}, {"ok": True}],
        "headers": headers,
        "plain": "kept",
    })
    rendered = str(clean)
    assert token not in rendered
    assert clean["plain"] == "kept"
    assert clean["nested"][0]["api_token"] == "***"
    assert clean["headers"]["Content-Type"] == "application/json"


# ---- classification --------------------------------------------------------

@pytest.mark.unit
def test_classify_exception_and_status_mapping():
    from telegram.error import BadRequest, RetryAfter, TimedOut

    assert classify(TimedOut()) == "network_timeout"
    assert classify(RetryAfter(1)) == "rate_limited"
    assert classify(BadRequest("bad")) == "telegram_send_failed"
    import aiosqlite
    assert classify(aiosqlite.Error("x")) == "database_failed"
    assert classify(None, 502) == "remote_5xx"
    assert classify(None, 400) == "remote_4xx"
    assert classify(None, 409) == "idempotency_conflict"
    assert classify(None, 429) == "rate_limited"
    assert classify(None) == "internal_error"
    # True class survives an upstream 502 mapping.
    assert classify(TimedOut(), 502) == "network_timeout"


# ---- durable audit ---------------------------------------------------------

@pytest.fixture
async def audit_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "audit.db"))
    await db_manager.init_db()
    return db_manager


@pytest.mark.asyncio
async def test_record_and_list_events(audit_db):
    await audit_mod.record_event(
        "review.created", review_id=7, pixiv_id="123", work_type="illust",
        target_id="daily", idempotency_key="api:1:k",
        execution_id="exec-9", actor="telegram_user:1",
        detail={"items": [{"decision": "photo_passthrough"}],
                "token": "123456789:AAEhBOweykLoeXAMPLE-TOKEN_abcdef1234567"},
    )
    events = await audit_mod.list_events(review_id=7)
    assert len(events) == 1
    event = events[0]
    assert event["event"] == "review.created"
    assert event["execution_id"] == "exec-9"
    assert isinstance(event["detail"], dict)
    assert event["detail"]["items"][0]["decision"] == "photo_passthrough"
    # Secrets in detail are redacted before persistence.
    assert event["detail"]["token"] == "***"

    by_exec = await audit_mod.list_events(execution_id="exec-9")
    assert [e["id"] for e in by_exec] == [event["id"]]

    # limit + newest-first
    for index in range(3):
        await audit_mod.record_event("x", detail={"i": index})
    assert len(await audit_mod.list_events(limit=2)) == 2


@pytest.mark.asyncio
async def test_record_event_swallows_db_failure(audit_db, caplog):
    import logging
    broken = AsyncMock(side_effect=RuntimeError("disk full"))
    with patch.object(db_manager, "get_db", broken):
        with caplog.at_level(logging.WARNING):
            # Must never raise.
            await audit_mod.record_event("review.created", review_id=1)
    assert any("not persisted" in record.message for record in caplog.records)


def test_execution_id_from_source_ref():
    assert audit_mod.execution_id_from_ref(
        '{"executionId": "abc-1"}'
    ) == "abc-1"
    assert audit_mod.execution_id_from_ref("bare-ref") == "bare-ref"
    assert audit_mod.execution_id_from_ref("") is None
    assert audit_mod.execution_id_from_ref('{"other": 1}') is None
