"""
HTTP API（/api/v1）测试：鉴权、校验、投稿流转、路由转发
"""
import os
import asyncio
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
    application.bot.send_message.return_value = MagicMock(message_id=321)
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
        monkeypatch.chdir(tmp_path)
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
            await asyncio.sleep(0)
            assert not os.path.exists(files[0]["path"])
            assert list((tmp_path / "data" / "api_uploads").iterdir()) == []
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
    async def test_invalid_link(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
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
            await asyncio.sleep(0)
            assert list((tmp_path / "data" / "api_uploads").iterdir()) == []
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_failed_publish_cleans_upload_directory(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        app, publish_mock = _make_app(monkeypatch, _TOKEN_ROW)
        publish_mock.side_effect = RuntimeError("telegram unavailable")
        client = await _client(app)
        try:
            form = __import__("aiohttp").FormData()
            form.add_field(
                "files",
                b"temporary-image",
                filename="photo.jpg",
                content_type="image/jpeg",
            )
            form.add_field("tags", "Pixiv")
            resp = await client.post(
                "/api/v1/submissions",
                data=form,
                headers={"Authorization": "Bearer tp_ok"},
            )
            assert resp.status == 502
            uploaded_path = publish_mock.call_args.args[1][0]["path"]
            await asyncio.sleep(0)
            assert not os.path.exists(uploaded_path)
            assert list((tmp_path / "data" / "api_uploads").iterdir()) == []
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_rejects_files_over_aggregate_limit_and_cleans_uploads(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(api_server, "MAX_FILE_BYTES", 20)
        monkeypatch.setattr(api_server, "MAX_TOTAL_FILE_BYTES", 10)
        app, publish_mock = _make_app(monkeypatch, _TOKEN_ROW)
        client = await _client(app)
        try:
            form = __import__("aiohttp").FormData()
            form.add_field("files", b"123456", filename="one.jpg", content_type="image/jpeg")
            form.add_field("files", b"abcdef", filename="two.jpg", content_type="image/jpeg")
            form.add_field("tags", "Pixiv")
            resp = await client.post(
                "/api/v1/submissions",
                data=form,
                headers={"Authorization": "Bearer tp_ok"},
            )
            assert resp.status == 413
            assert (await resp.json())["error"]["code"] == "request_too_large"
            await asyncio.sleep(0)
            assert list((tmp_path / "data" / "api_uploads").iterdir()) == []
            publish_mock.assert_not_called()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_review_mode_queues_without_publishing(self, monkeypatch):
        monkeypatch.setattr(api_server, "API_REVIEW_REQUIRED", True)
        queue_mock = AsyncMock(return_value={
            "status": "pending_review", "review_id": 42,
            "media_count": 1, "document_count": 0,
        })
        monkeypatch.setattr("handlers.review.queue_review_from_files", queue_mock)
        app, publish_mock = _make_app(monkeypatch, _TOKEN_ROW)
        client = await _client(app)
        try:
            form = __import__("aiohttp").FormData()
            form.add_field("files", b"fake-image", filename="photo.jpg", content_type="image/jpeg")
            form.add_field("tags", "Pixiv")
            form.add_field("idempotency_key", "pixiv:123")
            resp = await client.post(
                "/api/v1/submissions", data=form,
                headers={"Authorization": "Bearer tp_ok"},
            )
            assert resp.status == 201
            data = await resp.json()
            assert data["data"]["status"] == "pending_review"
            assert data["data"]["review_id"] == 42
            queue_mock.assert_awaited_once()
            assert queue_mock.call_args.kwargs["idempotency_key"] == "pixiv:123"
            publish_mock.assert_not_called()
        finally:
            await client.close()


class TestNotification:
    @pytest.mark.asyncio
    async def test_sends_idempotent_review_chat_notice(self, monkeypatch, tmp_path):
        from database import db_manager
        monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "notifications.db"))
        await db_manager.init_db()
        monkeypatch.setattr(api_server, "REVIEW_CHAT_ID", -100123)
        app, _ = _make_app(monkeypatch, _TOKEN_ROW)
        client = await _client(app)
        try:
            headers = {"Authorization": "Bearer tp_ok"}
            payload = {"text": "PixivFlow 没有候选", "idempotency_key": "empty:today"}
            first = await client.post("/api/v1/notifications", headers=headers, json=payload)
            second = await client.post("/api/v1/notifications", headers=headers, json=payload)
            assert first.status == 201
            assert (await first.json())["data"]["status"] == "notified"
            assert second.status == 201
            assert (await second.json())["data"]["status"] == "duplicate"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_notification_requires_auth(self, monkeypatch):
        monkeypatch.setattr(api_server, "REVIEW_CHAT_ID", -100123)
        app, _ = _make_app(monkeypatch, None)
        client = await _client(app)
        try:
            assert (await client.post("/api/v1/notifications", json={"text": "x"})).status == 401
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_notification_state_failure_is_structured(self, monkeypatch):
        monkeypatch.setattr(api_server, "REVIEW_CHAT_ID", -100123)
        monkeypatch.setattr(
            api_server,
            "claim_api_notification",
            AsyncMock(side_effect=RuntimeError("database busy")),
        )
        app, _ = _make_app(monkeypatch, _TOKEN_ROW)
        client = await _client(app)
        try:
            response = await client.post(
                "/api/v1/notifications",
                headers={"Authorization": "Bearer tp_ok"},
                json={"text": "x", "idempotency_key": "busy"},
            )
            assert response.status == 503
            assert (await response.json())["error"]["code"] == "notification_state_failed"
        finally:
            await client.close()


class TestFileIdDirect:
    """file_id 直投（JSON body）分支"""

    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch):
        file_id_mock = AsyncMock(return_value={
            "status": "published", "message_id": 777,
            "link": "https://t.me/c/1/777", "media_count": 2, "document_count": 0,
        })
        monkeypatch.setattr("handlers.publish.publish_from_file_ids", file_id_mock)
        app, _ = _make_app(monkeypatch, _TOKEN_ROW)
        client = await _client(app)
        try:
            resp = await client.post(
                "/api/v1/submissions",
                headers={"Authorization": "Bearer tp_ok"},
                json={
                    "media": [
                        {"type": "photo", "file_id": "AAA"},
                        {"type": "video", "file_id": "BBB"},
                    ],
                    "tags": "测试",
                    "title": "直投标题",
                    "anonymous": True,
                },
            )
            assert resp.status == 201
            data = await resp.json()
            assert data["data"]["message_id"] == 777

            file_id_mock.assert_called_once()
            kwargs = file_id_mock.call_args.kwargs
            assert kwargs["anonymous"] is True
            assert file_id_mock.call_args.args[1][0]["file_id"] == "AAA"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_invalid_media_type(self, monkeypatch):
        app, _ = _make_app(monkeypatch, _TOKEN_ROW)
        client = await _client(app)
        try:
            resp = await client.post(
                "/api/v1/submissions",
                headers={"Authorization": "Bearer tp_ok"},
                json={"media": [{"type": "sticker", "file_id": "AAA"}]},
            )
            assert resp.status == 400
            assert (await resp.json())["error"]["code"] == "invalid_media"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_missing_media_and_documents(self, monkeypatch):
        app, _ = _make_app(monkeypatch, _TOKEN_ROW)
        client = await _client(app)
        try:
            resp = await client.post(
                "/api/v1/submissions",
                headers={"Authorization": "Bearer tp_ok"},
                json={"tags": "t"},
            )
            assert resp.status == 400
            assert (await resp.json())["error"]["code"] == "missing_media"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_documents_only(self, monkeypatch):
        file_id_mock = AsyncMock(return_value={
            "status": "published", "message_id": 55,
            "link": "https://t.me/c/1/55", "media_count": 0, "document_count": 1,
        })
        monkeypatch.setattr("handlers.publish.publish_from_file_ids", file_id_mock)
        app, _ = _make_app(monkeypatch, _TOKEN_ROW)
        client = await _client(app)
        try:
            resp = await client.post(
                "/api/v1/submissions",
                headers={"Authorization": "Bearer tp_ok"},
                json={"documents": [{"file_id": "DDD", "filename": "archive.zip"}], "tags": "t"},
            )
            assert resp.status == 201
            kwargs = file_id_mock.call_args.kwargs
            assert file_id_mock.call_args.args[2][0]["filename"] == "archive.zip"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_review_mode_queues_file_ids(self, monkeypatch):
        monkeypatch.setattr(api_server, "API_REVIEW_REQUIRED", True)
        queue_mock = AsyncMock(return_value={
            "status": "pending_review", "review_id": 7,
            "media_count": 1, "document_count": 0,
        })
        monkeypatch.setattr("handlers.review.queue_review_from_file_ids", queue_mock)
        app, publish_mock = _make_app(monkeypatch, _TOKEN_ROW)
        client = await _client(app)
        try:
            resp = await client.post(
                "/api/v1/submissions",
                headers={"Authorization": "Bearer tp_ok"},
                json={
                    "media": [{"type": "photo", "file_id": "AAA"}],
                    "tags": "Pixiv",
                    "idempotency_key": "pixiv:456",
                },
            )
            assert resp.status == 201
            assert (await resp.json())["data"]["status"] == "pending_review"
            queue_mock.assert_awaited_once()
            assert queue_mock.call_args.kwargs["idempotency_key"] == "pixiv:456"
            publish_mock.assert_not_called()
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
