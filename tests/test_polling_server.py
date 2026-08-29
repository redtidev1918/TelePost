from types import SimpleNamespace

import pytest

from utils.polling_server import build_polling_app


@pytest.mark.asyncio
async def test_polling_mode_exposes_health_and_api(monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    monkeypatch.setenv("API_ENABLED", "true")
    app = build_polling_app(SimpleNamespace(bot=object()))

    async with TestClient(TestServer(app)) as client:
        health = await client.get("/health")
        assert health.status == 200
        assert (await health.json())["mode"] == "polling"

        api_health = await client.get("/api/v1/health")
        assert api_health.status == 200
        payload = await api_health.json()
        assert payload["ok"] is True
        assert payload["data"]["service"] == "telepost-api"


@pytest.mark.asyncio
async def test_polling_mode_can_disable_api(monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    monkeypatch.setenv("API_ENABLED", "false")
    app = build_polling_app(SimpleNamespace(bot=object()))

    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/health")).status == 200
        assert (await client.get("/api/v1/health")).status == 404
