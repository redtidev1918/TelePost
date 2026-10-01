"""Reviewer review history (§history): storage query + API contract.

The Mini App reviewer history is a read-only projection of the SAME
ReviewService / persisted review state as the pending queue — terminal states
only, keyset-paged, reviewer RBAC, and never a second store.
"""
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from database import db_manager
from telepost.storage.sqlite.reviews import ReviewRepository
from utils import api_server


async def _db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "history.db"))
    await db_manager.init_db()


async def _row(*, status="pending", title="", created=None, updated=None,
               user_id=7, username="alice", submitter=None,
               chain="chain-1", source="api"):
    now = created if created is not None else time.time()
    upd = updated if updated is not None else now
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            """
            INSERT INTO pending_reviews (
                idempotency_key, source, status, user_id, username,
                review_chat_id, media_json, documents_json, target_id,
                review_chain_id, generation,
                submitter_user_id, submitter_username,
                created_at, updated_at, title
            ) VALUES (?, ?, ?, ?, ?, -1001, '[]', '[]', '', ?, 0, ?, ?, ?, ?, ?)
            """,
            (f"k-{chain}-{status}-{int(now*1000)}-{int(upd*1000)}", source,
             status, user_id, username, chain, submitter, username,
             now, upd, title),
        )
        return cur.lastrowid


@pytest.mark.asyncio
async def test_list_terminal_only_returns_terminal_states(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    base = time.time() - 1000
    await _row(status="pending", title="queue", created=base + 0, updated=base + 0)
    await _row(status="published", title="done", created=base + 1, updated=base + 1)
    await _row(status="rejected", title="no", created=base + 2, updated=base + 2)
    await _row(status="failed", title="fail", created=base + 3, updated=base + 3)
    await _row(status="expired", title="old", created=base + 4, updated=base + 4)
    await _row(status="superseded", title="replaced", created=base + 5, updated=base + 5)

    rows = await ReviewRepository().list_terminal(limit=10)
    titles = [r["title"] for r in rows]
    assert titles == ["replaced", "old", "fail", "no", "done"]
    assert "queue" not in titles


@pytest.mark.asyncio
async def test_list_terminal_paginates_without_duplicates(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    base = time.time() - 1000
    titles = []
    for index in range(5):
        title = f"row-{index}"
        titles.append(title)
        await _row(status="published", title=title,
                   created=base + index, updated=base + index)

    repo = ReviewRepository()
    page1 = await repo.list_terminal(limit=2)
    assert [r["title"] for r in page1] == ["row-4", "row-3"]
    cursor = (page1[-1]["updated_at"], page1[-1]["id"])
    page2 = await repo.list_terminal(limit=2, updated_cursor=cursor[0],
                                     id_cursor=cursor[1])
    cursor2 = (page2[-1]["updated_at"], page2[-1]["id"])
    page3 = await repo.list_terminal(limit=2, updated_cursor=cursor2[0],
                                     id_cursor=cursor2[1])
    seen = [r["title"] for r in page1 + page2 + page3]
    assert seen == ["row-4", "row-3", "row-2", "row-1", "row-0"]
    assert len(seen) == len(set(seen))


# ---- API contract ----------------------------------------------------------

REVIEWER_UID = 200
SUBMITTER_UID = 5073758941


def _make_app(monkeypatch):
    monkeypatch.setattr(
        api_server, "authenticate", AsyncMock(return_value=None),
    )
    monkeypatch.setattr(api_server, "_rate_cache",
                        api_server.TTLCache(default_ttl=3600, max_size=64))
    monkeypatch.setenv("OWNER_ID", "100")
    monkeypatch.setenv("ADMIN_IDS", str(REVIEWER_UID))
    monkeypatch.setenv("MINIAPP_SESSION_SECRET", "s" * 40)
    from telepost.miniapp import rbac as _rbac
    monkeypatch.setattr(_rbac, "OWNER_ID", 100)
    monkeypatch.setattr(_rbac, "ADMIN_IDS", [REVIEWER_UID])
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
async def test_history_requires_authentication(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    app = _make_app(monkeypatch)
    client = await _client(app)
    try:
        resp = await client.get("/api/v1/reviews/history")
        assert resp.status == 401
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_history_requires_reviewer_role(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    app = _make_app(monkeypatch)
    token = _session_token(SUBMITTER_UID, ["submitter"])
    client = await _client(app)
    try:
        resp = await client.get(
            "/api/v1/reviews/history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 403
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_history_returns_terminal_items_for_reviewer(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    base = time.time() - 1000
    await _row(status="pending", title="in-queue", created=base + 0, updated=base + 0)
    await _row(status="published", title="was-published", created=base + 1, updated=base + 1)
    await _row(status="rejected", title="was-rejected", created=base + 2, updated=base + 2)

    app = _make_app(monkeypatch)
    token = _session_token(REVIEWER_UID, ["submitter", "reviewer"])
    client = await _client(app)
    try:
        resp = await client.get(
            "/api/v1/reviews/history?limit=10",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        items = body["data"]["items"]
        titles = [item["title"] for item in items]
        assert titles == ["was-rejected", "was-published"]
        assert "in-queue" not in titles
        assert items[0]["status"] == "rejected"
        assert items[1]["status"] == "published"
        assert items[0]["updated_at"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_history_route_not_shadowed_by_review_id(monkeypatch, tmp_path):
    """/reviews/history must not be captured as review_id='history'."""
    await _db(monkeypatch, tmp_path)
    app = _make_app(monkeypatch)
    token = _session_token(REVIEWER_UID, ["submitter", "reviewer"])
    client = await _client(app)
    try:
        resp = await client.get(
            "/api/v1/reviews/history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 200
        body = await resp.json()
        # The history list shape, not a review_not_found / invalid_review_id error.
        assert "items" in body["data"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_history_bad_cursor_is_rejected(monkeypatch, tmp_path):
    """Invalid cursor mirrors the queue endpoint (409 review_error)."""
    await _db(monkeypatch, tmp_path)
    app = _make_app(monkeypatch)
    token = _session_token(REVIEWER_UID, ["submitter", "reviewer"])
    client = await _client(app)
    try:
        resp = await client.get(
            "/api/v1/reviews/history?cursor=not-a-cursor",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 409
        body = await resp.json()
        assert body["ok"] is False
    finally:
        await client.close()
