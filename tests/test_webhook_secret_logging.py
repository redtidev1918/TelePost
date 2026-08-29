"""Webhook secrets must never be written to application logs."""

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
    bot.set_webhook.assert_awaited_once()
