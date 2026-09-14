"""Admin surface (Mini App /admin/*): RBAC, audit, no secret exposure."""
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from utils import api_server
from telepost.application import admin_ops

pytestmark = pytest.mark.security

ADMIN_UID = 100  # OWNER_ID in _make_app
REVIEWER_UID = 200
SUBMITTER_UID = 5073758941


def _make_app(monkeypatch, roles_uid=None):
    monkeypatch.setattr(
        api_server, "authenticate", AsyncMock(return_value=None),
    )
    monkeypatch.setattr(api_server, "_rate_cache",
                        api_server.TTLCache(default_ttl=3600, max_size=64))
    monkeypatch.setenv("OWNER_ID", str(ADMIN_UID))
    monkeypatch.setenv("ADMIN_IDS", str(REVIEWER_UID))
    monkeypatch.setenv("MINIAPP_SESSION_SECRET", "s" * 40)
    application = MagicMock()
    application.bot = AsyncMock()
    app = web.Application()
    api_server.add_api_routes(app, application)
    return app


def _session_token(uid: int, roles):
    import os as _os
    from telepost.miniapp.session import issue_session
    _os.environ["MINIAPP_SESSION_SECRET"] = "s" * 40
    return issue_session(uid, roles, username=f"user{uid}")


async def _client(app):
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_admin_status_requires_admin(monkeypatch):
    from telepost.application import admin_ops
    monkeypatch.setattr(admin_ops, "status_snapshot",
                        AsyncMock(return_value={"queue": {}}))
    app = _make_app(monkeypatch)

    for uid, roles, expected in (
        (SUBMITTER_UID, ["submitter"], 403),
        (REVIEWER_UID, ["submitter", "reviewer"], 403),
        (ADMIN_UID, ["submitter", "reviewer", "admin"], 200),
    ):
        client = await _client(app)
        try:
            token = _session_token(uid, roles)
            resp = await client.get(
                "/api/v1/admin/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status == expected, f"uid={uid} roles={roles}"
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_admin_policy_and_blacklist_audit(monkeypatch):
    snapshot = AsyncMock(return_value={"queue": {}, "policy": {}})
    monkeypatch.setattr(admin_ops, "status_snapshot", snapshot)
    monkeypatch.setattr(admin_ops, "update_policy", AsyncMock(return_value={
        "applied": {"CHAT_REVIEW_REQUIRED": "true"}, "reload_scheduled": False,
    }))
    monkeypatch.setattr(admin_ops, "blacklist_entries", AsyncMock(return_value=[]))
    monkeypatch.setattr(admin_ops, "blacklist_add", AsyncMock(return_value={
        "user_id": 9, "reason": "spam", "added": True,
    }))
    monkeypatch.setattr(admin_ops, "blacklist_remove", AsyncMock(return_value={
        "user_id": 9, "removed": True,
    }))

    app = _make_app(monkeypatch)
    token = _session_token(ADMIN_UID, ["submitter", "reviewer", "admin"])
    client = await _client(app)
    try:
        headers = {"Authorization": f"Bearer {token}"}
        status = await client.get("/api/v1/admin/status", headers=headers)
        assert status.status == 200

        policy = await client.patch(
            "/api/v1/admin/policy",
            json={"chat_review": "on"},
            headers=headers,
        )
        assert policy.status == 200
        assert (await policy.json())["data"]["applied"]["CHAT_REVIEW_REQUIRED"] == "true"

        listed = await client.get("/api/v1/admin/blacklist", headers=headers)
        assert listed.status == 200

        added = await client.post(
            "/api/v1/admin/blacklist",
            json={"user_id": 9, "reason": "spam"},
            headers=headers,
        )
        assert added.status == 201

        removed = await client.delete(
            "/api/v1/admin/blacklist/9", headers=headers,
        )
        assert removed.status == 200
        assert (await removed.json())["data"]["removed"] is True
    finally:
        await client.close()

    admin_ops.update_policy.assert_awaited_once()
    admin_ops.blacklist_add.assert_awaited_once()
    admin_ops.blacklist_remove.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_mutation_rejects_service_principal(monkeypatch):
    """An API token (service principal) can never reach admin mutations."""
    token_row = {"id": 1, "telegram_user_id": ADMIN_UID, "name": "script",
                 "created_at": 1.0}
    app = _make_app(monkeypatch)
    monkeypatch.setattr(
        api_server, "authenticate",
        AsyncMock(return_value=token_row),
    )
    client = await _client(app)
    try:
        resp = await client.get(
            "/api/v1/admin/status",
            headers={"Authorization": "Bearer tp_fakeservice"},
        )
        assert resp.status == 403
        body = await resp.json()
        assert body["error"]["code"] == "permission_denied"
    finally:
        await client.close()