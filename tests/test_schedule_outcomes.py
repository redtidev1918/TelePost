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
         "error_code": "duplicate_exhausted",
         "terminal_reason_code": "duplicate_exhausted",
         "reason": "候选作品均已投稿过", "stage": "acquisition",
         "retryable": True, "operator_hint": "可等待新作品或放宽条件重试。"},
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
        # First-level cause in business language (§failure-observability).
        assert "插画：❌ 没找到合适的新作品" in text
        assert "原因：候选作品均已投稿过" in text
        assert "后续处理：可重试" in text
        assert "建议：可等待新作品或放宽条件重试。" in text
        assert "小说：✅ 已提交" in text
        # The misleading "recovery exhausted" line is gone: the root cause IS
        # the message now.
        assert "不再重试" not in text
        # Operator recovery buttons for the failed target only.
        reply_markup = application.bot.send_message.await_args.kwargs.get("reply_markup")
        assert reply_markup is not None
        callback_data = [
            button.callback_data
            for row in reply_markup.inline_keyboard
            for button in row
        ]
        assert "sched_recover|bot1-illust-botefuku|normal" in callback_data
        assert "sched_recover|bot1-illust-botefuku|relaxed" in callback_data
        assert not any("bot1-novel" in data for data in callback_data)
        # No mention-capable content and no internal jargon.
        for forbidden in ("@", "tg://", "cell", "slot ", "outbox",
                          "terminal_reason_code", "duplicate_exhausted"):
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
async def test_recovery_outcome_renders_recovered_style(monkeypatch, tmp_path):
    """A MANUAL RECOVERY outcome is rendered as 已恢复, never as the daily
    summary, and the auto-run history wording stays untouched (§manual-recovery)."""
    await _db(monkeypatch, tmp_path)
    app, application = _make_app(monkeypatch)
    client = await _client(app)
    headers = {"Authorization": "Bearer tp_service"}
    try:
        rec = {
            "schedule_id": "bot1-daily",
            "slot_id": "bot1-daily@recover-abc123",
            "status": "success",
            "recovery": {"mode": "relaxed", "requestId": "abc123"},
            "targets": [
                {"target_id": "bot1-illust-botefuku", "work_type": "illustration",
                 "status": "submitted", "work_id": "777"},
            ],
        }
        resp = await client.post("/api/v1/schedule/outcomes", json=rec, headers=headers)
        assert resp.status == 200
        text = application.bot.send_message.await_args.kwargs["text"]
        assert "已恢复（放宽条件重试）" in text
        assert "插画：✅ 已提交" in text
        assert "部分完成" not in text
        # No daily-summary recovery buttons on a recovery outcome.
        assert application.bot.send_message.await_args.kwargs.get("reply_markup") is None
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


@pytest.mark.asyncio
async def test_candidate_report_and_target_buttons(monkeypatch, tmp_path):
    """Phase 1/3: operator sees the supply funnel and per-target buttons."""
    await _db(monkeypatch, tmp_path)
    app, application = _make_app(monkeypatch)
    client = await _client(app)
    headers = {"Authorization": "Bearer tp_service"}
    payload = {
        **PAYLOAD,
        "targets": [
            {
                **PAYLOAD["targets"][0],
                "candidate_report": {
                    "fetched": 59,
                    "selected": 0,
                    "rejected": 59,
                    "reasons": [
                        {"code": "duplicate", "count": 30},
                        {"code": "ai_filtered", "count": 20},
                        {"code": "language_filter", "count": 9},
                    ],
                },
            },
            PAYLOAD["targets"][1],
        ],
    }
    try:
        resp = await client.post("/api/v1/schedule/outcomes", json=payload,
                                 headers=headers)
        assert resp.status == 200
        text = application.bot.send_message.await_args.kwargs["text"]
        assert "候选扫描：59" in text
        assert "重复：30" in text
        assert "最终候选：0" in text
        assert "判断：candidate_supply_low" in text

        reply_markup = application.bot.send_message.await_args.kwargs["reply_markup"]
        first_row = list(reply_markup.inline_keyboard[0])
        assert first_row[0].text == "重试·插画"
        assert first_row[1].text == "放宽·插画"
        assert first_row[0].callback_data == "sched_recover|bot1-illust-botefuku|normal"
        assert first_row[1].callback_data == "sched_recover|bot1-illust-botefuku|relaxed"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_candidate_report_renders_inventory_reserve(monkeypatch, tmp_path):
    """Phase 5: empty result shows the durable 待发池 reserve."""
    import copy
    await _db(monkeypatch, tmp_path)
    app, application = _make_app(monkeypatch)
    client = await _client(app)
    headers = {"Authorization": "Bearer tp_service"}
    payload = copy.deepcopy(PAYLOAD)
    payload["targets"][0]["candidate_report"] = {
        "fetched": 120,
        "selected": 0,
        "rejected": 118,
        "reasons": [{"code": "duplicate", "count": 118}],
        "inventory": {
            "pendingCount": 6,
            "reserveSize": 20,
            "maxAgeDays": 30,
            "oldestSeenDate": "2026-09-03",
        },
    }
    try:
        resp = await client.post("/api/v1/schedule/outcomes", json=payload,
                                 headers=headers)
        assert resp.status == 200
        text = application.bot.send_message.await_args.kwargs["text"]
        assert "候选扫描：120" in text
        assert "重复：118" in text
        assert "待发池：6 条（预计可用到 2026-10-03）" in text
    finally:
        await client.close()

