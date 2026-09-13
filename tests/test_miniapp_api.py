"""Mini App HTTP API tests: session endpoint, my-submissions privacy, RBAC.

Uses the real aiohttp test client with stubbed services/repositories, so the
endpoint contract (auth → principal → service) is covered without a database.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from utils import api_server

pytestmark = pytest.mark.security

_SUBMITTER = {"id": 1, "telegram_user_id": 5073758941, "name": "user1",
              "created_at": 1.0}
_REVIEWER = {"id": 2, "telegram_user_id": 200, "name": "reviewer",
             "created_at": 1.0}


def _make_app(monkeypatch, authenticate_return=None, *, reviewer=False):
    """App with API routes; Mini App sessions work without API tokens."""
    monkeypatch.setattr(
        api_server, "authenticate",
        AsyncMock(return_value=authenticate_return),
    )
    publish_mock = AsyncMock(return_value={
        "status": "published", "message_id": 123,
        "link": "https://t.me/c/1/123", "media_count": 1, "document_count": 0,
    })
    monkeypatch.setattr("handlers.publish.publish_from_files", publish_mock)
    monkeypatch.setattr(api_server, "_rate_cache",
                        api_server.TTLCache(default_ttl=3600, max_size=4096))

    # Reviewer-identity source of truth (ADMIN_IDS/OWNER_ID from settings).
    monkeypatch.setenv("OWNER_ID", "100" if reviewer else "99999")
    monkeypatch.setenv("ADMIN_IDS", "100,200" if reviewer else "99999")

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


def _session_token(uid: int, roles, *, secret="s" * 40):
    from telepost.miniapp.session import issue_session
    os.environ["MINIAPP_SESSION_SECRET"] = secret
    return issue_session(uid, roles, username=f"user{uid}")


class TestMiniAppSession:
    @pytest.fixture(autouse=True)
    def _auth_token(self, monkeypatch):
        # auth.BOT_TOKEN is captured at import from config.settings (conftest
        # fake). Point it at the token the fixtures sign with.
        from telepost.miniapp import auth as miniapp_auth
        monkeypatch.setattr(
            miniapp_auth,
            "BOT_TOKEN",
            "123456:TESTTOKENabcdefghijklmnopqrstuvwxyz012345",
        )
        monkeypatch.setenv("MINIAPP_SESSION_SECRET", "s" * 40)

    @pytest.mark.asyncio
    async def test_valid_session_endpoint(self, monkeypatch):
        from init_data_py.signing import sign
        app, _ = _make_app(monkeypatch, _SUBMITTER)
        client = await _client(app)
        try:
            raw = sign({"user": '{"id":5073758941,"first_name":"A","username":"user1"}'},
                       "123456:TESTTOKENabcdefghijklmnopqrstuvwxyz012345")
            resp = await client.post("/api/v1/miniapp/session",
                                     json={"initData": raw})
            assert resp.status == 200
            data = (await resp.json())["data"]
            assert data["token"].startswith("ma_v1.")
            assert data["user"]["telegram_user_id"] == 5073758941
            assert "submitter" in data["user"]["roles"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_tampered_init_data_rejected(self, monkeypatch):
        app, _ = _make_app(monkeypatch, _SUBMITTER)
        client = await _client(app)
        try:
            import time as _t
            resp = await client.post(
                "/api/v1/miniapp/session",
                json={"initData":
                      f"user=%7B%22id%22%3A5073758941%2C%22first_name%22%3A%22A%22%7D"
                      f"&auth_date={int(_t.time())}&hash={'0'*64}"}
            )
            assert resp.status == 401
            body = await resp.json()
            assert body["error"]["code"] == "invalid_init_data_signature"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_missing_init_data_rejected(self, monkeypatch):
        app, _ = _make_app(monkeypatch, _SUBMITTER)
        client = await _client(app)
        try:
            resp = await client.post("/api/v1/miniapp/session", json={})
            assert resp.status == 401
            assert (await resp.json())["error"]["code"] == "missing_init_data"
        finally:
            await client.close()


class TestMiniAppPrincipal:
    @pytest.mark.asyncio
    async def test_me_with_session(self, monkeypatch):
        from telepost.miniapp.session import issue_session
        app, _ = _make_app(monkeypatch, _SUBMITTER)
        monkeypatch.setenv("MINIAPP_SESSION_SECRET", "s" * 40)
        token = issue_session(5073758941, ["submitter"], username="user1")
        client = await _client(app)
        try:
            resp = await client.get("/api/v1/me",
                                    headers={"Authorization": f"Bearer {token}"})
            assert resp.status == 200
            data = (await resp.json())["data"]
            assert data["telegram_user_id"] == 5073758941
            assert data["surface"] == "mini_app"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_me_with_expired_session(self, monkeypatch):
        from telepost.miniapp.session import issue_session, verify_session
        app, _ = _make_app(monkeypatch, _SUBMITTER)
        monkeypatch.setenv("MINIAPP_SESSION_SECRET", "s" * 40)
        # Mint an already-expired token deterministically (negative TTL).
        token = issue_session(5073758941, ["submitter"], ttl=-3600)
        assert verify_session(token) is None  # sanity: expired immediately
        client = await _client(app)
        try:
            resp = await client.get("/api/v1/me",
                                    headers={"Authorization": f"Bearer {token}"})
            assert resp.status == 401
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_api_token_still_works(self, monkeypatch):
        app, _ = _make_app(monkeypatch, _SUBMITTER)
        client = await _client(app)
        try:
            resp = await client.get("/api/v1/me",
                                    headers={"Authorization": "Bearer tp_ok"})
            assert resp.status == 200
            assert (await resp.json())["data"]["surface"] == "api"
        finally:
            await client.close()


class TestMySubmissions:
    @pytest.mark.asyncio
    async def test_returns_own_rows(self, monkeypatch):
        from telepost.storage.sqlite import reviews as reviews_mod

        rows = [
            {"id": 11, "status": "pending", "title": "T", "tags": "#a #b",
             "media_json": "[]", "documents_json": "[]", "spoiler": 0,
             "created_at": 100.0, "source": "api", "user_id": 5073758941,
             "username": "user1", "note": "", "link": "",
             "anonymous": 0, "review_chat_id": "1", "review_message_ids": "[]",
             "updated_at": 100.0, "decided_at": None, "decided_by": None,
             "published_message_id": None, "target_id": "",
             "source_label": "", "source_ref": "", "scheduled_at": "",
             "error": "", "idempotency_key": "k", "pixiv_id": "",
             "work_type": "", "delivery_target": ""},
        ]
        repo_mock = MagicMock()
        repo_mock.list_by_user = AsyncMock(return_value=rows)
        monkeypatch.setattr(reviews_mod.ReviewRepository, "list_by_user",
                            repo_mock.list_by_user)
        app, _ = _make_app(monkeypatch, _SUBMITTER)
        monkeypatch.setenv("MINIAPP_SESSION_SECRET", "s" * 40)
        token = _session_token(5073758941, ["submitter"])
        client = await _client(app)
        try:
            resp = await client.get("/api/v1/me/submissions",
                                    headers={"Authorization": f"Bearer {token}"})
            assert resp.status == 200
            data = (await resp.json())["data"]
            assert data["items"][0]["review_id"] == 11
            assert data["items"][0]["status"] == "pending_review"
            assert data["items"][0]["tags"] == ["#a", "#b"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_requires_auth(self, monkeypatch):
        app, _ = _make_app(monkeypatch, None)
        client = await _client(app)
        try:
            resp = await client.get("/api/v1/me/submissions")
            assert resp.status == 401
        finally:
            await client.close()


class TestReviewRBAC:
    @pytest.mark.asyncio
    async def test_submitter_cannot_list_queue(self, monkeypatch):
        app, _ = _make_app(monkeypatch, _SUBMITTER, reviewer=False)
        monkeypatch.setenv("MINIAPP_SESSION_SECRET", "s" * 40)
        token = _session_token(5073758941, ["submitter"])
        client = await _client(app)
        try:
            resp = await client.get("/api/v1/reviews",
                                    headers={"Authorization": f"Bearer {token}"})
            assert resp.status == 403
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_reviewer_can_list_queue(self, monkeypatch):
        from services import review_service as review_service_mod

        app, _ = _make_app(monkeypatch, _REVIEWER, reviewer=True)
        monkeypatch.setenv("MINIAPP_SESSION_SECRET", "s" * 40)
        token = _session_token(200, ["submitter", "reviewer"])
        client = await _client(app)
        try:
            with patch.object(
                review_service_mod.ReviewService,
                "list_pending",
                AsyncMock(return_value={"items": [], "next_cursor": None}),
            ):
                resp = await client.get("/api/v1/reviews",
                                        headers={"Authorization": f"Bearer {token}"})
                assert resp.status == 200
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_submitter_cannot_approve(self, monkeypatch):
        app, _ = _make_app(monkeypatch, _SUBMITTER, reviewer=False)
        monkeypatch.setenv("MINIAPP_SESSION_SECRET", "s" * 40)
        token = _session_token(5073758941, ["submitter"])
        client = await _client(app)
        try:
            resp = await client.post(
                "/api/v1/reviews/1/approve",
                json={"spoiler": False},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status == 403
        finally:
            await client.close()

class TestMiniAppReadonlyExemption:
    @pytest.mark.asyncio
    async def test_miniapp_reviewer_can_approve_even_in_readonly_mode(self, monkeypatch):
        """A human Mini App review action is not gated by the MCP/API read-only
        automation switch (TELEPOST_REVIEW_API_MODE=readonly)."""
        from services import review_service as review_service_mod

        monkeypatch.setenv("TELEPOST_REVIEW_API_MODE", "readonly")
        app, _ = _make_app(monkeypatch, _REVIEWER, reviewer=True)
        monkeypatch.setenv("MINIAPP_SESSION_SECRET", "s" * 40)
        token = _session_token(100, ["submitter", "reviewer", "admin"])
        client = await _client(app)
        try:
            with patch.object(
                review_service_mod.ReviewService,
                "approve",
                AsyncMock(return_value=review_service_mod.ActionResult(
                    7, "published", False, 1, "https://t.me/c/1/1",
                )),
            ):
                resp = await client.post(
                    "/api/v1/reviews/7/approve",
                    json={"spoiler": False},
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status == 200
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_api_token_write_still_blocked_in_readonly_mode(self, monkeypatch):
        """API-token (automation) writes remain blocked when the mode is readonly."""
        monkeypatch.setenv("TELEPOST_REVIEW_API_MODE", "readonly")
        monkeypatch.setenv("OWNER_ID", "5073758941")
        app, _ = _make_app(monkeypatch, _SUBMITTER, reviewer=False)
        token_row = {"id": 1, "telegram_user_id": 5073758941, "name": "owner-token"}
        monkeypatch.setattr(api_server, "authenticate",
                            AsyncMock(return_value=token_row))
        client = await _client(app)
        try:
            resp = await client.post(
                "/api/v1/reviews/7/approve",
                json={"spoiler": False},
                headers={"Authorization": "Bearer tp_ownertoken"},
            )
            assert resp.status == 403
            body = await resp.json()
            assert body["error"]["code"] == "permission_denied"
        finally:
            await client.close()
