"""Per-bot release version diagnostics (dual-bot version consistency).

Each botN subprocess serves ``/api/v1/version`` (publicly ``/api/botN/v1/version``
through the run.py relay) reporting ITS OWN bot identity plus the REAL release
identity from ``telepost.build_info.release_info()`` — never the legacy
``hf.CONFIG["VERSION"]`` placeholder. Dual-bot production must be able to prove
which release each bot process actually runs (§version-matrix).
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from utils import api_server

_FAKE_RELEASE = {
    "service": "telepost",
    "version": "9.9.9",
    "commit": "abc123def",
    "build_date": "2026-01-01T00:00:00Z",
}


def _make_app():
    application = MagicMock()
    application.bot = AsyncMock()
    app = web.Application()
    api_server.add_api_routes(app, application)
    return app


async def _client(app) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _patch_release(monkeypatch):
    monkeypatch.setattr(
        "telepost.build_info.release_info", lambda: dict(_FAKE_RELEASE)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("index", ["1", "2"])
async def test_version_reports_own_bot_and_real_release(monkeypatch, index):
    """botN's own API reports botN — never the other bot, never a default."""
    monkeypatch.setenv("TELEPOST_BOT_INDEX", index)
    _patch_release(monkeypatch)
    client = await _client(_make_app())
    try:
        resp = await client.get("/api/v1/version")
        assert resp.status == 200
        payload = await resp.json()
        assert payload["ok"] is True
        data = payload["data"]
        assert data["bot"] == f"bot{index}"
        assert data["service"] == "telepost"
        assert data["version"] == _FAKE_RELEASE["version"]
        assert data["commit"] == _FAKE_RELEASE["commit"]
        assert data["build_date"] == _FAKE_RELEASE["build_date"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_health_bot_version_is_real_release(monkeypatch):
    """health's bot_version must track the real release, not legacy config."""
    _patch_release(monkeypatch)
    client = await _client(_make_app())
    try:
        resp = await client.get("/api/v1/health")
        assert resp.status == 200
        data = (await resp.json())["data"]
        assert data["bot_version"] == _FAKE_RELEASE["version"]
    finally:
        await client.close()
