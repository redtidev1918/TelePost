"""
HTTP API（/api/v1）测试：鉴权、校验、投稿流转、路由转发
"""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from utils import api_server
from utils.cache import TTLCache
_TOKEN_ROW = {"id": 1, "telegram_user_id": 5073758941, "name": "script", "created_at": 1.0}


def _make_app(monkeypatch, authenticate_return):
    """构建挂载 API 路由的应用；authenticate 按测试需要打桩"""
    token_row = {"id": 1, "telegram_user_id": 5073758941, "name": "script", "created_at": 1.0}
    monkeypatch.setattr(
        api_server, "authenticate",
        AsyncMock(return_value=authenticate_return),
    )
    publish_mock = AsyncMock(return_value={
        "status": "published", "message_id": 123,
        "link": "https://t.me/c/1/123", "media_count": 1, "document_count": 0,
    })
    monkeypatch.setattr("handlers.publish.publish_from_files", publish_mock)
    monkeypatch.setattr(api_server, "_rate_cache", TTLCache(default_ttl=3600, max_size=4096))

    application = MagicMock()
    application.bot = AsyncMock()
    app = web.Application()
    api_server.add_api_routes(app, application)
    return app, publish_mock


async def _client(app) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_public(self, monkeypatch):
        app, _ = _make_app(monkeypatch, _TOKEN_ROW)
        client = await _client(app)
        try:
            resp = await client.get("/api/v1/health")
            assert resp.status == 200
            data = await resp.json()
            assert data["ok"] is True
            assert data["data"]["api_version"] == api_server.API_VERSION
        finally:
            await client.close()


class TestAuth:
    @pytest.mark.asyncio
    async def test_endpoints_require_token(self, monkeypatch):
        app, _ = _make_app(monkeypatch, None)  # authenticate → None
        client = await _client(app)
        try:
            assert (await client.get("/api/v1/me")).status == 401
            assert (await client.post("/api/v1/submissions", data={"tags": "x"})).status == 401
            bad = await client.get("/api/v1/me", headers={"Authorization": "Bearer tp_bad"})
            assert bad.status == 401
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_me_returns_quota(self, monkeypatch):
        app, _ = _make_app(monkeypatch, _TOKEN_ROW)
        client = await _client(app)
        try:
            resp = await client.get("/api/v1/me", headers={"Authorization": "Bearer tp_ok"})
            assert resp.status == 200
            data = await resp.json()
            assert data["data"]["telegram_user_id"] == 5073758941
            assert data["data"]["rate_limit_per_hour"] == api_server.SUBMIT_LIMIT_PER_HOUR
        finally:
            await client.close()


class TestSubmission:
    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch, tmp_path):
        app, publish_mock = _make_app(monkeypatch, _TOKEN_ROW)
        client = await _client(app)
        try:
            form = __import__("aiohttp").FormData()
            form.add_field("files", b"fake-image-bytes", filename="photo.jpg", content_type="image/jpeg")
            form.add_field("tags", "测试, API")
            form.add_field("title", "标题")
            form.add_field("anonymous", "true")
            resp = await client.post(
                "/api/v1/submissions", data=form,
                headers={"Authorization": "Bearer tp_ok"},
            )
            assert resp.status == 201
            data = await resp.json()
            assert data["ok"] is True
            assert data["data"]["status"] == "published"
            assert data["data"]["message_id"] == 123

            assert publish_mock.call_count == 1
            bot_arg, files = publish_mock.call_args.args
            kwargs = publish_mock.call_args.kwargs
            assert os.path.exists(files[0]["path"])
            assert files[0]["kind"] == "photo"
            assert kwargs["tags"].startswith("#")
            assert kwargs["title"] == "标题"
            assert kwargs["anonymous"] is True
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_requires_tags(self, monkeypatch):
        app, publish_mock = _make_app(monkeypatch, _TOKEN_ROW)
        client = await _client(app)
        try:
            resp = await client.post(
                "/api/v1/submissions",
                headers={"Authorization": "Bearer tp_ok"},
                data={"files": b"x", "tags": ""},
            )
            assert resp.status == 400
            assert (await resp.json())["error"]["code"] == "invalid_tags"
            publish_mock.assert_not_called()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_rate_limit(self, monkeypatch):
        monkeypatch.setattr(api_server, "SUBMIT_LIMIT_PER_HOUR", 1)
        app, publish_mock = _make_app(monkeypatch, _TOKEN_ROW)
        client = await _client(app)
        try:
            headers = {"Authorization": "Bearer tp_ok"}
            r1 = await client.post("/api/v1/submissions", headers=headers,
                                   data={"files": b"x", "tags": "t"})
            r2 = await client.post("/api/v1/submissions", headers=headers,
                                   data={"files": b"y", "tags": "t"})
            assert r1.status == 201
            assert r2.status == 429
            assert (await r2.json())["error"]["code"] == "rate_limited"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_invalid_link(self, monkeypatch):
        app, publish_mock = _make_app(monkeypatch, _TOKEN_ROW)
        client = await _client(app)
        try:
            resp = await client.post(
                "/api/v1/submissions",
                headers={"Authorization": "Bearer tp_ok"},
                data={"files": b"x", "tags": "t", "link": "ftp://bad"},
            )
            assert resp.status == 400
            assert (await resp.json())["error"]["code"] == "invalid_link"
        finally:
            await client.close()


class TestKindDetection:
    def test_photo(self):
        assert api_server.detect_kind("a.jpg", "image/jpeg") == "photo"

    def test_gif_is_animation(self):
        assert api_server.detect_kind("a.gif", "image/gif") == "animation"

    def test_video(self):
        assert api_server.detect_kind("a.mp4", "video/mp4") == "video"

    def test_audio(self):
        assert api_server.detect_kind("a.mp3", "audio/mpeg") == "audio"

    def test_unknown_is_document(self):
        assert api_server.detect_kind("a.zip", "application/zip") == "document"
        assert api_server.detect_kind("a.bin", "") == "document"


class TestRouterApiRelay:
    @pytest.mark.asyncio
    async def test_api_prefix_stripped(self, monkeypatch):
        import run as run_mod
        from aiohttp import web

        received = {}

        async def fake_v1_me(request):
            received["path"] = request.path
            return web.json_response({"ok": True})

        monkeypatch.setattr(run_mod, "bot_webhook_port", lambda i: 18091 if i == 1 else 18092)

        bot_app = web.Application()
        bot_app.router.add_get("/api/v1/me", fake_v1_me)
        bot_runner = web.AppRunner(bot_app)
        await bot_runner.setup()
        bot_site = web.TCPSite(bot_runner, "127.0.0.1", 18091)
        await bot_site.start()

        router_app = run_mod.build_router_app([1])
        router_runner = web.AppRunner(router_app)
        await router_runner.setup()
        router_site = web.TCPSite(router_runner, "127.0.0.1", 18090)
        await router_site.start()

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:18090/api/bot1/v1/me") as resp:
                    assert resp.status == 200
                    assert (await resp.json())["ok"] is True
            assert received["path"] == "/api/v1/me"
        finally:
            await router_runner.cleanup()
            await bot_runner.cleanup()
