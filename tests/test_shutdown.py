import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import main


@pytest.mark.asyncio
async def test_shutdown_completes_without_stopping_event_loop():
    application = SimpleNamespace(
        updater=SimpleNamespace(is_connected=False, stop=AsyncMock()),
        stop=AsyncMock(),
        shutdown=AsyncMock(),
    )

    await main.shutdown(application, signal.SIGTERM)

    application.stop.assert_awaited_once()
    application.shutdown.assert_awaited_once()
