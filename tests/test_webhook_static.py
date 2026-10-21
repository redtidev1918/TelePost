"""Mini App static hosting: /app/* served from webapp/dist when present."""
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from utils.webhook_server import WebhookServer


@pytest.mark.asyncio
async def test_miniapp_static_serves_index(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("console.log('x')", encoding="utf-8")
    monkeypatch.setenv("MINIAPP_DIST_DIR", str(dist))

    server = WebhookServer(MagicMock(), 0, "/webhook", "secret")
    server.web_app = web.Application()
    server._mount_miniapp_static()

    assert any("/app/" in r.resource.canonical for r in server.web_app.router.routes())
    runner = web.AppRunner(server.web_app)
    await runner.setup()
    client = TestClient(TestServer(server.web_app))
    await client.start_server()
    try:
        resp = await client.get("/app/")
        assert resp.status == 200
        assert "app" in await resp.text()
        asset = await client.get("/app/assets/app.js")
        assert asset.status == 200
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_miniapp_static_absent_without_dist(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIAPP_DIST_DIR", str(tmp_path / "missing"))
    server = WebhookServer(MagicMock(), 0, "/webhook", "secret")
    server.web_app = web.Application()
    server._mount_miniapp_static()
    routes = list(server.web_app.router.routes())
    assert all("/app/" not in r.resource.canonical for r in routes)


@pytest.mark.asyncio
async def test_spa_fallback_serves_index_for_unknown_path(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    monkeypatch.setenv("MINIAPP_DIST_DIR", str(dist))

    server = WebhookServer(MagicMock(), 0, "/webhook", "secret")
    server.web_app = web.Application()
    server._mount_miniapp_static()

    runner = web.AppRunner(server.web_app)
    await runner.setup()
    client = TestClient(TestServer(server.web_app))
    await client.start_server()
    try:
        resp = await client.get("/app/review/12")
        assert resp.status == 200
        assert "app" in await resp.text()
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_traversal_and_sibling_prefix_cannot_escape_dist(tmp_path, monkeypatch):
    from yarl import URL

    webapp = tmp_path / "webapp"
    dist = webapp / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    # A sibling directory whose name extends the dist prefix: a plain string
    # startswith(".../dist") guard would serve files out of it.
    sibling = webapp / "dist-private"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("sibling-secret", encoding="utf-8")
    monkeypatch.setenv("MINIAPP_DIST_DIR", str(dist))

    server = WebhookServer(MagicMock(), 0, "/webhook", "secret")
    server.web_app = web.Application()
    server._mount_miniapp_static()

    runner = web.AppRunner(server.web_app)
    await runner.setup()
    client = TestClient(TestServer(server.web_app))
    await client.start_server()
    try:
        # encoded=True keeps yarl from normalizing the dot segments away, so
        # the handler receives the decoded ../ path like a raw client would.
        resp = await client.get(
            URL("/app/%2e%2e/dist-private/secret.txt", encoded=True))
        assert resp.status == 200
        body = await resp.text()
        assert "app" in body  # SPA fallback, not the sibling file
        assert "sibling-secret" not in body
    finally:
        await client.close()
        await runner.cleanup()
