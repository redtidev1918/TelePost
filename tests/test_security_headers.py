"""Security response headers (§71): CSP for the Mini App, hardening elsewhere.

The middleware is attached by ``utils.webhook_server.build_web_app`` /
``utils.polling_server.build_polling_app``, so these tests exercise the same
application wiring production uses.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from utils.security_headers import MINIAPP_CSP
from utils.webhook_server import WebhookServer, build_web_app

pytestmark = pytest.mark.security


def _write_dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("console.log('x')", encoding="utf-8")
    return dist


async def _client(app):
    runner = web.AppRunner(app)
    await runner.setup()
    client = TestClient(TestServer(app))
    await client.start_server()
    return runner, client


@pytest.mark.asyncio
async def test_miniapp_pages_carry_the_documented_csp(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIAPP_DIST_DIR", str(_write_dist(tmp_path)))
    server = WebhookServer(MagicMock(), 0, "/webhook", "secret")
    server.web_app = build_web_app()
    server._mount_miniapp_static()

    runner, client = await _client(server.web_app)
    try:
        for path in ("/app/", "/app/assets/app.js", "/app/review/12"):
            res = await client.get(path)
            assert res.status == 200
            csp = res.headers["Content-Security-Policy"]
            for directive in (
                "default-src 'self'",
                "script-src 'self'",
                "style-src 'self' 'unsafe-inline'",
                "img-src 'self' data: blob:",
                "media-src 'self' blob:",
                "connect-src 'self'",
                "object-src 'none'",
                "base-uri 'none'",
                "frame-ancestors 'self' https://*.telegram.org",
            ):
                assert directive in csp, (path, directive)
            # Least privilege: no wildcard script source and no eval.
            assert "script-src *" not in csp
            assert "unsafe-eval" not in csp
            assert res.headers["X-Content-Type-Options"] == "nosniff"
            assert res.headers["Referrer-Policy"] == "no-referrer"
            assert "camera=(self)" in res.headers["Permissions-Policy"]
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_csp_keeps_blob_for_local_media_previews(tmp_path, monkeypatch):
    """Preview media uses URL.createObjectURL — blob: must stay allowed."""
    monkeypatch.setenv("MINIAPP_DIST_DIR", str(_write_dist(tmp_path)))
    server = WebhookServer(MagicMock(), 0, "/webhook", "secret")
    server.web_app = build_web_app()
    server._mount_miniapp_static()

    runner, client = await _client(server.web_app)
    try:
        res = await client.get("/app/")
        csp = res.headers["Content-Security-Policy"]
        img = [d for d in csp.split(";") if d.strip().startswith("img-src")][0]
        media = [d for d in csp.split(";") if d.strip().startswith("media-src")][0]
        assert "blob:" in img
        assert "blob:" in media
        assert csp == MINIAPP_CSP
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_handler_specific_csp_is_not_overwritten():
    """The media download handlers pin CSP: sandbox; middleware must not replace it."""

    async def sandbox(_request):
        return web.Response(
            text="bytes",
            headers={"Content-Security-Policy": "sandbox"},
        )

    app = build_web_app()
    app.router.add_get("/download", sandbox)

    runner, client = await _client(app)
    try:
        res = await client.get("/download")
        assert res.headers["Content-Security-Policy"] == "sandbox"
        assert res.headers["X-Content-Type-Options"] == "nosniff"
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_api_and_health_responses_are_hardened_without_page_csp():
    from utils.api_server import add_api_routes

    app = build_web_app()
    add_api_routes(app, MagicMock(bot=AsyncMock()))
    app.router.add_get("/health", lambda _r: web.json_response({"status": "ok"}))

    runner, client = await _client(app)
    try:
        for path in ("/health", "/api/v1/health"):
            res = await client.get(path)
            assert res.status == 200
            assert res.headers["X-Content-Type-Options"] == "nosniff"
            assert res.headers["Referrer-Policy"] == "no-referrer"
            # JSON/health responses are not documents: no page CSP.
            assert "Content-Security-Policy" not in res.headers
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_polling_surface_is_hardened():
    from utils.polling_server import build_polling_app

    app = build_polling_app(SimpleNamespace(bot=object()))
    runner, client = await _client(app)
    try:
        res = await client.get("/health")
        assert res.status == 200
        assert res.headers["X-Content-Type-Options"] == "nosniff"
        assert "Content-Security-Policy" not in res.headers
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_miniapp_errors_keep_security_headers():
    app = build_web_app()
    runner, client = await _client(app)
    try:
        response = await client.get("/app/missing")
        assert response.status == 404
        assert response.headers["Content-Security-Policy"] == MINIAPP_CSP
        assert response.headers["X-Content-Type-Options"] == "nosniff"
    finally:
        await client.close()
        await runner.cleanup()
