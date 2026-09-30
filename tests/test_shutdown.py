import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import main


@pytest.mark.asyncio
async def test_shutdown_completes_without_stopping_event_loop():
    webhook_server = SimpleNamespace(stop=AsyncMock())
    application = SimpleNamespace(
        bot=SimpleNamespace(delete_webhook=AsyncMock()),
        updater=SimpleNamespace(is_connected=False, stop=AsyncMock()),
        stop=AsyncMock(),
        shutdown=AsyncMock(),
    )

    await main.shutdown(application, signal.SIGTERM, webhook_server)

    webhook_server.stop.assert_awaited_once()
    application.bot.delete_webhook.assert_not_awaited()
    application.stop.assert_awaited_once()
    application.shutdown.assert_awaited_once()


async def test_setup_bot_commands_sets_default_menu_button():
    """Regression: setup_bot_commands must run to completion and set the
    default (non-Mini App) chat menu button. It previously aborted on
    `from utils.blacklist import ADMIN_IDS` (ADMIN_IDS lives in
    config.settings), so set_chat_menu_button was never reached and the old
    Mini App web_app button persisted in production."""
    set_my_commands = AsyncMock()
    set_chat_menu_button = AsyncMock()
    application = SimpleNamespace(
        bot=SimpleNamespace(
            set_my_commands=set_my_commands,
            set_chat_menu_button=set_chat_menu_button,
        )
    )

    with pytest.MonkeyPatch.context() as mp:
        # Non-empty admin list so the per-chat admin scope loop runs.
        mp.setattr("config.settings.OWNER_ID", 123456789)
        mp.setattr("config.settings.ADMIN_IDS", [123456789, 987654321])

        await main.setup_bot_commands(application)

    assert set_my_commands.await_count >= 2  # default scope + at least 1 admin chat
    set_chat_menu_button.assert_awaited_once()
    kwargs = set_chat_menu_button.call_args.kwargs
    assert isinstance(kwargs.get("menu_button"), main.MenuButtonDefault)
