from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from utils.webhook_server import WEBHOOK_ALLOWED_UPDATES, verify_webhook


@pytest.mark.asyncio
async def test_registration_drift_repairs_without_dropping_updates():
    expected = SimpleNamespace(url='https://example.test/webhook/bot1', allowed_updates=WEBHOOK_ALLOWED_UPDATES)
    app = SimpleNamespace(bot_data={}, bot=SimpleNamespace(
        get_webhook_info=AsyncMock(side_effect=[SimpleNamespace(url='', allowed_updates=[]), expected]),
        set_webhook=AsyncMock(return_value=True)))
    assert await verify_webhook(app, 'https://example.test', '/webhook/bot1', 'test', now=100)
    assert app.bot.set_webhook.await_args.kwargs['drop_pending_updates'] is False
    assert app.bot_data['webhook_registration']['registered'] is True


@pytest.mark.asyncio
async def test_persistent_drift_has_bounded_repair_and_remains_unready():
    app = SimpleNamespace(bot_data={}, bot=SimpleNamespace(
        get_webhook_info=AsyncMock(return_value=SimpleNamespace(url='', allowed_updates=[])),
        set_webhook=AsyncMock(return_value=True)))
    for now in (100, 101, 102, 220, 221, 460, 461, 462):
        assert not await verify_webhook(app, 'https://example.test', '/webhook/bot1', 'test', now=now)
    assert app.bot.set_webhook.await_count == 3
    assert app.bot_data['webhook_registration']['next_repair_at'] == 1360
