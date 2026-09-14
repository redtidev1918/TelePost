"""Schedule terminal outcome notifications (TelePost receiver)."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from database import db_manager
from utils import api_server

pytestmark = pytest.mark.security

TOKEN_ROW = {"id": 1, "telegram_user_id": 5073758941, "name": "pixivflow",
             "created_at": 1.0}


async def _db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "sched.db"))
    await db_manager.init_db()


def _make_app(monkeypatch, *, review_chat=-100123):
    monkeypatch.setattr(api_server, "authenticate",
                        AsyncMock(return_value=TOKEN_ROW))
    monkeypatch.setattr(api_server, "REVIEW_CHAT_ID", review_chat)
    application = MagicMock()
    application.bot = AsyncMock()
    application.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    app = web.Application()
    api_server.add_api_routes(app, application)
    return app, application


async def _client(app):
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


PAYLOAD = {
    "schedule_id": "bot1-daily",
    "slot_id": "bot1-daily@2026-09-14T2200",
    "occurrence_at": "2026-09-14T14:00:00Z",
    "status": "partial",
    "duration_ms": 1_680_000,
    "cells": {"total": 2, "submitted": 1, "no_match": 1},
    "targets": [
        {"target_id": "bot1-illust-botefuku", "work_type": "illustration",
         "status": "no_candidate", "work_id": None,
         "error_code": "duplicate_exhausted"},
        {"target_id": "bot1-novel-botefuku", "work_type": "novel",
         "status": "submitted", "work_id": "29118637"},
    ],
}


@pytest.mark.asyncio
async def test_requires_service_token(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    app, _ = _make_app(monkeypatch)
    monkeypatch.setattr(api_server, "authenticate", AsyncMock(return_value=None))
    client = await _client(app)
    try:
        resp = await client.post("/api/v1/schedule/outcomes", json=PAYLOAD)
        assert resp.status == 401
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_validates_payload(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    app, _ = _make_app(monkeypatch)
    client = await _client(app)
    headers = {"Authorization": "Bearer tp_service"}
    try:
        for bad, code in (
            ({"slot_id": "x"}, "missing_slot"),
            ({**PAYLOAD, "status": "maybe"}, "invalid_status"),
            ({**PAYLOAD, "targets": "nope"}, "invalid_targets"),
        ):
            resp = await client.post("/api/v1/schedule/outcomes", json=bad,
                                     headers=headers)
            assert resp.status == 400
            assert (await resp.json())["error"]["code"] == code
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_partial_notifies_once_and_is_idempotent(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    app, application = _make_app(monkeypatch)
    client = await _client(app)
    headers = {"Authorization": "Bearer tp_service"}
    try:
        resp = await client.post("/api/v1/schedule/outcomes", json=PAYLOAD,
                                 headers=headers)
        assert resp.status == 200
        body = await resp.json()
        assert body["data"]["delivered"] is True

        text = application.bot.send_message.await_args.kwargs["text"]
        assert "部分完成" in text
        assert "插画：❌ 未找到合适作品" in text
        assert "小说：✅ 已提交" in text
        assert "不再重试" in text
        # No mention-capable content and no internal jargon.
        for forbidden in ("@", "tg://", "cell", "slot ", "outbox"):
            assert forbidden not in text

        # A replayed delivery (or duplicate clock) never notifies twice.
        again = await client.post("/api/v1/schedule/outcomes", json=PAYLOAD,
                                  headers=headers)
        assert again.status == 200
        assert (await again.json())["data"]["replayed"] is True
        assert application.bot.send_message.await_count == 1
    finally:
        await client.close()

    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM api_notifications WHERE idempotency_key=? AND status='sent'",
            (f"schedule-outcome:{PAYLOAD['slot_id']}",),
        )
        assert (await cur.fetchone())[0] == 1
        cur = await conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event='schedule.outcome_notified'"
        )
        assert (await cur.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_success_and_failure_also_notify(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    app, application = _make_app(monkeypatch)
    client = await _client(app)
    headers = {"Authorization": "Bearer tp_service"}
    try:
        ok = {**PAYLOAD, "slot_id": "bot1-daily@2026-09-15T1000", "status": "success",
              "targets": [
                  {"target_id": "bot1-illust-botefuku", "work_type": "illustration",
                   "status": "submitted", "work_id": "1"},
                  {"target_id": "bot1-novel-botefuku", "work_type": "novel",
                   "status": "submitted", "work_id": "2"},
              ]}
        resp = await client.post("/api/v1/schedule/outcomes", json=ok, headers=headers)
        assert resp.status == 200
        success_text = application.bot.send_message.await_args.kwargs["text"]
        assert "执行完成" in success_text
        assert "插画：✅ 已提交" in success_text

        bad = {**PAYLOAD, "slot_id": "bot1-daily@2026-09-15T2200", "status": "failed",
               "targets": [
                   {"target_id": "bot1-illust-botefuku", "work_type": "illustration",
                    "status": "failed", "error_code": "pixiv_unavailable"},
               ]}
        resp = await client.post("/api/v1/schedule/outcomes", json=bad, headers=headers)
        assert resp.status == 200
        failed_text = application.bot.send_message.await_args.kwargs["text"]
        assert "执行失败" in failed_text
        assert "插画：❌ 执行失败" in failed_text
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_notification_survives_missing_review_chat(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    app, application = _make_app(monkeypatch, review_chat=None)
    client = await _client(app)
    try:
        resp = await client.post("/api/v1/schedule/outcomes", json=PAYLOAD,
                                 headers={"Authorization": "Bearer tp_service"})
        assert resp.status == 503
        assert (await resp.json())["error"]["code"] == "review_chat_not_configured"
        assert application.bot.send_message.await_count == 0
    finally:
        await client.close()

@pytest.mark.asyncio
async def test_failed_send_retries_and_pending_claim_is_not_ack(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    app, application = _make_app(monkeypatch)
    client = await _client(app)
    headers = {"Authorization": "Bearer tp_service"}
    key = f"schedule-outcome:{PAYLOAD['slot_id']}"
    try:
        await db_manager.claim_api_notification(0, key)
        pending = await client.post("/api/v1/schedule/outcomes", json=PAYLOAD, headers=headers)
        assert pending.status == 503
        assert application.bot.send_message.await_count == 0
        await db_manager.release_api_notification(0, key)
        application.bot.send_message.side_effect = RuntimeError("temporary")
        failed = await client.post("/api/v1/schedule/outcomes", json=PAYLOAD, headers=headers)
        assert failed.status == 502
        application.bot.send_message.side_effect = None
        done = await client.post("/api/v1/schedule/outcomes", json=PAYLOAD, headers=headers)
        assert done.status == 200
        assert (await done.json())["data"]["delivered"] is True
        # New app / new request still sees the durable sent receipt.
        again = await client.post("/api/v1/schedule/outcomes", json=PAYLOAD, headers=headers)
        assert (await again.json())["data"]["replayed"] is True
        assert application.bot.send_message.await_count == 2
    finally:
        await client.close()
