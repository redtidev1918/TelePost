"""Review HTTP facade and dependency-free MCP sidecar client tests."""

import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from database import db_manager
from mcp_server import review_client
from mcp_server import server as mcp_server_module
from services import review_service as service_module
from utils import api_server

_TOKEN_ROW = {
    "id": 1,
    "telegram_user_id": 5073758941,
    "name": "owner",
    "created_at": 1.0,
}


async def _client_for_reviews(monkeypatch, tmp_path, *, token_row=_TOKEN_ROW, mode=None, review_token=""):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "reviews.db"))
    monkeypatch.setenv("TELEPOST_REVIEW_TOKEN", review_token)
    if mode:
        monkeypatch.setenv("TELEPOST_REVIEW_API_MODE", mode)
    elif "TELEPOST_REVIEW_API_MODE" in os.environ:
        monkeypatch.delenv("TELEPOST_REVIEW_API_MODE", raising=False)
    monkeypatch.setattr(api_server, "OWNER_ID", 5073758941)
    monkeypatch.setattr(
        api_server,
        "authenticate",
        AsyncMock(return_value=token_row),
    )
    application = MagicMock()
    application.bot = AsyncMock()
    app = web.Application()
    api_server.add_api_routes(app, application)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, application


@pytest.mark.asyncio
async def test_review_http_read_endpoints_and_missing_review(monkeypatch, tmp_path):
    client, _app = await _client_for_reviews(monkeypatch, tmp_path)
    await db_manager.init_db()
    try:
        empty = await client.get(
            "/api/v1/reviews",
            headers={"Authorization": "Bearer tp_owner"},
        )
        assert empty.status == 200
        assert (await empty.json())["data"] == {"items": [], "next_cursor": None}

        missing = await client.get(
            "/api/v1/reviews/404",
            headers={"Authorization": "Bearer tp_owner"},
        )
        body = await missing.json()
        assert missing.status == 404
        assert body["error"]["code"] == "review_not_found"
        assert "Traceback" not in json.dumps(body)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_review_http_unauthorized_and_readonly_blocks_writes(monkeypatch, tmp_path):
    client, _app = await _client_for_reviews(
        monkeypatch, tmp_path, token_row=None, mode="readonly"
    )
    await db_manager.init_db()
    try:
        denied = await client.get("/api/v1/reviews")
        assert denied.status == 401
        assert (await denied.json())["error"]["code"] == "invalid_token"

        # Valid reviewer identity, but the whole review API is explicitly read-only.
        monkeypatch.setattr(api_server, "authenticate", AsyncMock(return_value=_TOKEN_ROW))
        blocked = await client.post(
            "/api/v1/reviews/1/reject",
            json={},
            headers={"Authorization": "Bearer tp_owner"},
        )
        assert blocked.status == 403
        assert (await blocked.json())["error"]["code"] == "permission_denied"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_review_http_owner_token_writes_but_regular_token_does_not(monkeypatch, tmp_path):
    client, _app = await _client_for_reviews(monkeypatch, tmp_path)
    await db_manager.init_db()
    monkeypatch.setattr(api_server, "OWNER_ID", 123456789)
    monkeypatch.setattr(
        api_server,
        "authenticate",
        AsyncMock(return_value={**_TOKEN_ROW, "telegram_user_id": 999}),
    )
    try:
        forbidden = await client.post(
            "/api/v1/reviews/1/reject",
            json={},
            headers={"Authorization": "Bearer tp_submitter"},
        )
        assert forbidden.status == 403
        assert (await forbidden.json())["error"]["code"] == "permission_denied"

        monkeypatch.setattr(
            api_server,
            "authenticate",
            AsyncMock(return_value={**_TOKEN_ROW, "telegram_user_id": 123456789}),
        )
        missing = await client.post(
            "/api/v1/reviews/999/reject",
            json={},
            headers={"Authorization": "Bearer tp_owner"},
        )
        assert missing.status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_review_http_dedicated_token_marks_mcp_source(monkeypatch, tmp_path):
    client, application = await _client_for_reviews(
        monkeypatch, tmp_path, token_row=None, review_token="secret-token"
    )
    await db_manager.init_db()
    captured = {}

    async def fake_reject(self, bot, review_id, **kwargs):
        captured.update({"review_id": review_id, "kwargs": kwargs, "bot": bot})
        return service_module.ActionResult(review_id, "rejected", False)

    monkeypatch.setattr(service_module.ReviewService, "reject", fake_reject)
    try:
        bad = await client.post(
            "/api/v1/reviews/8/reject",
            json={"reason": "no"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert bad.status == 401

        ok = await client.post(
            "/api/v1/reviews/8/reject",
            json={"reason": "no"},
            headers={
                "Authorization": "Bearer secret-token",
                "X-TelePost-Source": "mcp",
            },
        )
        assert ok.status == 200
        assert captured["review_id"] == 8
        assert captured["kwargs"]["source"] == "mcp"
        assert captured["kwargs"]["actor"] == "mcp"
        assert application.bot is captured["bot"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_review_http_media_returns_image_bytes_and_rejects_index(monkeypatch, tmp_path):
    client, application = await _client_for_reviews(monkeypatch, tmp_path)
    await db_manager.init_db()
    async def fake_media(self, bot, review_id, index, variant):
        if index != 0:
            raise service_module.MediaNotFoundError("媒体不存在")
        return service_module.MediaResult(
            review_id=review_id,
            index=index,
            kind="image",
            variant=variant,
            mime_type="image/jpeg",
            filename="preview.jpg",
            size=3,
            data=b"jpg",
            original_kind="photo",
        )

    monkeypatch.setattr(service_module.ReviewService, "get_media", fake_media)
    try:
        ok = await client.get(
            "/api/v1/reviews/2/media/0?variant=preview",
            headers={"Authorization": "Bearer tp_owner"},
        )
        assert ok.status == 200
        assert ok.headers["Content-Type"] == "image/jpeg"
        assert await ok.read() == b"jpg"
        assert json.loads(ok.headers["X-Review-Media"])["original_kind"] == "photo"

        bad = await client.get(
            "/api/v1/reviews/2/media/9",
            headers={"Authorization": "Bearer tp_owner"},
        )
        assert bad.status == 404
        assert (await bad.json())["error"]["code"] == "media_not_found"
    finally:
        await client.close()


def test_mcp_client_rejects_missing_token():
    with pytest.raises(review_client.ReviewApiError) as exc:
        review_client.ReviewApiClient(base_url="http://x", token="")
    assert exc.value.code == "configuration_error"


def test_mcp_client_uses_bearer_mcp_header_and_error_envelope():
    requests = []

    class FakeResponse:
        def __init__(self, body, status=200, headers=None):
            self._body = body
            self.status = status
            self.headers = headers or {}
        def read(self):
            return self._body
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        if request.full_url.endswith("/reviews/1/approve"):
            return FakeResponse(json.dumps({
                "ok": False,
                "error": {"code": "review_busy", "message": "busy"},
            }).encode(), 409)
        return FakeResponse(json.dumps({"ok": True, "data": {"items": []}}).encode())

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(review_client.urllib.request, "urlopen", fake_urlopen)
    try:
        client = review_client.ReviewApiClient(
            "http://telepost.test/api/v1", "tok", timeout=3
        )
        assert client.list_pending(1) == {"items": []}
        with pytest.raises(review_client.ReviewApiError) as exc:
            client.approve(1)
        assert exc.value.code == "review_busy"

        request = requests[0][0]
        assert request.headers["Authorization"] == "Bearer tok"
        assert request.headers["X-telepost-source"] == "mcp"
        assert requests[0][1] == 3
    finally:
        monkeypatch.undo()


def test_mcp_client_has_no_file_path_parameter():
    import inspect

    signature = inspect.signature(review_client.ReviewApiClient.get_media)
    assert "path" not in signature.parameters
    assert set(signature.parameters) == {"self", "review_id", "index", "variant"}


@pytest.mark.asyncio
async def test_mcp_readonly_blocks_every_write_tool_without_http(monkeypatch):
    monkeypatch.setattr(mcp_server_module, "READ_ONLY", True)

    def fail_client():
        raise AssertionError("read-only mode must not call TelePost API")

    monkeypatch.setattr(mcp_server_module, "_client", fail_client)

    approve = await mcp_server_module.approve_review(1)
    reject = await mcp_server_module.reject_review(1, "x")
    spoiler = await mcp_server_module.set_review_spoiler(1, True)

    assert approve["error"]["code"] == "permission_denied"
    assert reject["error"]["code"] == "permission_denied"
    assert spoiler["error"]["code"] == "permission_denied"


def test_review_prompt_never_mentions_automatic_write():
    prompt = mcp_server_module.review_submission_prompt("184")
    assert "未执行审核动作" in prompt or "不调用 approve_review" in prompt
    assert "自动发布" not in prompt
