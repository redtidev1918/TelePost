"""Webhook secrets must never be written to application logs.

The webhook secret is also compared with a constant-time comparison so
that a wrong/missing/prefix token is rejected with 401 without leaking
the token via timing differences.
"""

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from utils.webhook_server import WebhookServer, setup_webhook


def test_webhook_server_does_not_log_configured_secret(caplog):
    secret = "configured-super-secret"

    with caplog.at_level(logging.INFO, logger="utils.webhook_server"):
        WebhookServer(
            application=SimpleNamespace(),
            port=8080,
            path="/webhook",
            secret_token=secret,
        )

    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_setup_webhook_does_not_log_secret(caplog):
    secret = "registered-super-secret"
    bot = SimpleNamespace(
        delete_webhook=AsyncMock(return_value=True),
        set_webhook=AsyncMock(return_value=True),
        get_webhook_info=AsyncMock(
            return_value=SimpleNamespace(
                url="https://example.test/webhook",
                pending_update_count=0,
                allowed_updates=["channel_post", "edited_channel_post"],
            )
        ),
    )

    with caplog.at_level(logging.INFO, logger="utils.webhook_server"):
        assert await setup_webhook(
            application=SimpleNamespace(bot=bot),
            webhook_url="https://example.test",
            webhook_path="/webhook",
            secret_token=secret,
        )

    assert secret not in caplog.text
    bot.delete_webhook.assert_not_awaited()
    bot.set_webhook.assert_awaited_once_with(
        url="https://example.test/webhook",
        secret_token=secret,
        allowed_updates=[
            "message",
            "edited_message",
            "channel_post",
            "edited_channel_post",
            "callback_query",
            "inline_query",
        ],
        drop_pending_updates=False,
    )


def _make_server(secret: str) -> WebhookServer:
    bot = SimpleNamespace()
    queue: asyncio.Queue = asyncio.Queue()
    app = SimpleNamespace(bot=bot, update_queue=queue)
    server = WebhookServer(application=app, port=8080, path="/webhook", secret_token=secret)
    return server


def _make_request(token=None):
    headers = {}
    if token is not None:
        headers["X-Telegram-Bot-Api-Secret-Token"] = token
    # Minimal valid Telegram update; body is only read when the secret passes.
    body = json.dumps({"update_id": 1})
    return SimpleNamespace(
        headers=headers,
        json=AsyncMock(return_value=json.loads(body)),
    )


@pytest.mark.asyncio
async def test_webhook_rejects_wrong_secret_with_401():
    server = _make_server("the-real-secret")
    resp = await server.webhook_handler(_make_request(token="a-wrong-secret"))
    assert resp.status == 401


@pytest.mark.asyncio
async def test_webhook_rejects_missing_secret_header_with_401():
    server = _make_server("the-real-secret")
    resp = await server.webhook_handler(_make_request(token=None))
    assert resp.status == 401


@pytest.mark.asyncio
async def test_webhook_rejects_prefix_lookalike_secret_with_401():
    # A token sharing a long prefix must still differ (constant-time check
    # rejects length/content mismatches rather than doing a short-circuit).
    secret = "the-real-secret-value-123456"
    server = _make_server(secret)
    resp = await server.webhook_handler(_make_request(token=secret[:-1] + "X"))
    assert resp.status == 401


@pytest.mark.asyncio
async def test_webhook_accepts_correct_secret():
    secret = "the-real-secret"
    server = _make_server(secret)
    resp = await server.webhook_handler(_make_request(token=secret))
    assert resp.status == 200
    # The accepted update was enqueued for processing.
    assert server.application.update_queue.qsize() == 1
