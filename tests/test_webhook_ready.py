"""WebhookServer exposes liveness independent of PTB and readiness gated on
Application.running, so Fly's /ready check never routes to a half-warmed bot."""
from types import SimpleNamespace

import pytest

from utils.webhook_server import WebhookServer


@pytest.mark.asyncio
@pytest.mark.parametrize("running,expected", [(False, 503), (True, 200)])
async def test_webhook_live_and_ready(running, expected, unused_tcp_port):
    from aiohttp.test_utils import TestClient, TestServer

    server = WebhookServer(
        application=SimpleNamespace(running=running, bot=object()),
        port=unused_tcp_port,
        path="/webhook/bot1",
        secret_token="sec",
    )
    try:
        await server.start()
        app = server.web_app
        async with TestClient(TestServer(app)) as client:
            assert (await client.get("/live")).status == 200
            ready = await client.get("/ready")
            assert ready.status == expected
            assert (await ready.json())["ready"] is running
    finally:
        await server.stop()
