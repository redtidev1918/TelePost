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


async def test_setup_bot_commands_menu_button_state(monkeypatch):
    """Regression: setup_bot_commands must run to completion and set the chat
    menu button to a WebApp URL whenever the Mini App is configured, so that
    ``t.me/<bot>?startapp=`` Deep Links keep their Main Mini App association.

    It previously aborted on `from utils.blacklist import ADMIN_IDS` (ADMIN_IDS
    lives in config.settings), so set_chat_menu_button was never reached. When
    later reached with a hard-coded MenuButtonDefault, it stripped the platform
    WebApp association, breaking ?startapp and WebView launch-param injection.

    Two states must hold:
      * MINIAPP_ENABLED=True + public URL  -> MenuButtonWebApp (restores Mini App)
      * MINIAPP_ENABLED=False               -> MenuButtonDefault"""
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

        # State 1: Mini App enabled -> MenuButtonWebApp with bot=bot1 URL
        # (default TELEPOST_BOT_INDEX is "1").
        mp.setattr("config.settings.MINIAPP_ENABLED", True)
        mp.setattr("config.settings.MINIAPP_PUBLIC_URL",
                   "https://telesubmit.test/app/")

        await main.setup_bot_commands(application)

    assert set_my_commands.await_count >= 2  # default scope + at least 1 admin chat
    set_chat_menu_button.assert_awaited_once()
    kwargs = set_chat_menu_button.call_args.kwargs
    btn = kwargs.get("menu_button")
    assert isinstance(btn, main.MenuButtonWebApp)
    assert btn.web_app.url == "https://telesubmit.test/app?bot=bot1"

    # State 2: Mini App disabled -> plain default command menu.
    set_chat_menu_button.reset_mock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("config.settings.OWNER_ID", 123456789)
        mp.setattr("config.settings.ADMIN_IDS", [123456789, 987654321])
        mp.setattr("config.settings.MINIAPP_ENABLED", False)
        mp.setattr("config.settings.MINIAPP_PUBLIC_URL",
                   "https://telesubmit.test/app/")
        await main.setup_bot_commands(application)
    set_chat_menu_button.assert_awaited_once()
    assert isinstance(set_chat_menu_button.call_args.kwargs.get("menu_button"),
                     main.MenuButtonDefault)
