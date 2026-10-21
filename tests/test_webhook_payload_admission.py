"""Webhook payload admission: malformed payloads must be rejected with 400
(never silently acked with 200, never retried forever via 500), and webhook
deletion must keep Telegram-side pending updates by default."""
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from utils.webhook_server import WebhookServer, delete_webhook

SECRET = "admission-test-secret"


@pytest.fixture
async def client():
    application = MagicMock()
    application.update_queue.put = AsyncMock()
    server = WebhookServer(application, 0, "/webhook/bot1", SECRET)
    server.web_app = web.Application()
    server.web_app.router.add_post("/webhook/bot1", server.webhook_handler)

    runner = web.AppRunner(server.web_app)
    await runner.setup()
    tc = TestClient(TestServer(server.web_app))
    await tc.start_server()
    try:
        yield tc, application
    finally:
        await tc.close()
        await runner.cleanup()


def _headers(token=SECRET, content_type=None):
    headers = {"X-Telegram-Bot-Api-Secret-Token": token}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


@pytest.mark.asyncio
async def test_valid_update_is_queued_and_acknowledged(client):
    tc, application = client
    payload = {"update_id": 11, "message": {
        "message_id": 5, "date": 0, "chat": {"id": 42, "type": "private"}}}
    resp = await tc.post("/webhook/bot1", json=payload, headers=_headers())
    assert resp.status == 200
    application.update_queue.put.assert_awaited_once()
    (update,) = application.update_queue.put.await_args.args
    assert update.update_id == 11


@pytest.mark.asyncio
async def test_object_without_update_id_is_rejected(client):
    tc, application = client
    resp = await tc.post("/webhook/bot1", json={"foo": "bar"}, headers=_headers())
    assert resp.status == 400
    application.update_queue.put.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["{}", "[1, 2]", '"text"', "null"])
async def test_non_update_payloads_are_rejected(client, body):
    tc, application = client
    resp = await tc.post(
        "/webhook/bot1", data=body, headers=_headers(content_type="application/json"))
    assert resp.status == 400, body
    application.update_queue.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_json_body_is_rejected(client):
    tc, application = client
    resp = await tc.post(
        "/webhook/bot1", data=b"not-json{{{",
        headers=_headers(content_type="application/json"))
    assert resp.status == 400
    application.update_queue.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_payload_is_not_echoed_in_logs(client, caplog):
    tc, _application = client
    marker = "do-not-log-me-12345"
    with caplog.at_level(logging.WARNING, logger="utils.webhook_server"):
        resp = await tc.post(
            "/webhook/bot1", json={"secret_marker": marker}, headers=_headers())
        assert resp.status == 400
        resp = await tc.post(
            "/webhook/bot1", data=b"\xff\xfe" + marker.encode(),
            headers=_headers(content_type="application/json"))
        assert resp.status == 400
    assert marker not in caplog.text


@pytest.mark.asyncio
async def test_wrong_secret_token_still_rejected(client):
    tc, application = client
    resp = await tc.post(
        "/webhook/bot1", json={"update_id": 1}, headers=_headers(token="wrong"))
    assert resp.status == 401
    application.update_queue.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_webhook_keeps_pending_updates_by_default():
    bot = MagicMock()
    bot.delete_webhook = AsyncMock(return_value=True)
    assert await delete_webhook(MagicMock(bot=bot)) is True
    bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)


@pytest.mark.asyncio
async def test_delete_webhook_drop_requires_explicit_opt_in():
    bot = MagicMock()
    bot.delete_webhook = AsyncMock(return_value=True)
    await delete_webhook(MagicMock(bot=bot), drop_pending_updates=True)
    bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=True)
