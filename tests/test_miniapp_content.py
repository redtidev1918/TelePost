"""Mini App content entry: shared HotService + public post API contract."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from database import db_manager
from telepost.application.hot import HotService, hot_post_payload, parse_public_tags
from telepost.domain.hot import HotQuery
from utils import api_server


@pytest.fixture
async def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "content.db"))
    await db_manager.init_db()
    return tmp_path


async def _insert_post(**overrides):
    from database.db_manager import get_db
    now = float(overrides.pop("publish_time", time.time() - 60))
    message_id = int(overrides.pop("message_id", 1000 + int(now)))
    async with get_db() as conn:
        cursor = await conn.cursor()
        await cursor.execute(
            """
            INSERT INTO published_posts
              (message_id, user_id, username, title, tags, link, note,
               content_type, file_ids, caption, filename, publish_time,
               last_update, related_message_ids, heat_score, reactions)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                message_id,
                overrides.get("user_id", 7),
                overrides.get("username", "internal-user"),
                overrides.get("title", "标题"),
                overrides.get("tags", '["tag1","tag2"]'),
                overrides.get("link", "https://pixiv.example/1"),
                overrides.get("note", "简介"),
                overrides.get("content_type", "media"),
                overrides.get("file_ids", '["photo:fid-1"]'),
                overrides.get("caption", "caption"),
                overrides.get("filename", ""),
                now,
                now,
                overrides.get("related_message_ids", None),
                float(overrides.get("heat_score", 0)),
                int(overrides.get("reactions", 0)),
            ),
        )
    return message_id


@pytest.mark.asyncio
async def test_hot_service_is_shared_and_hides_identity(isolated_db):
    old = await _insert_post(message_id=101, heat_score=1, publish_time=time.time() - 9000)
    new = await _insert_post(message_id=102, heat_score=9)
    page = await HotService().page(HotQuery(scope="all", limit=10))
    assert [item.message_id for item in page.items] == [new, old]
    payload = hot_post_payload(page.items[0])
    assert payload["title"] == "标题"
    assert payload["tags"] == ["tag1", "tag2"]
    assert "user_id" not in payload
    assert "username" not in payload
    assert "file_ids" not in payload
    assert "caption" not in payload


@pytest.mark.asyncio
async def test_hot_cursor_pages_are_stable(isolated_db):
    for idx in range(3):
        await _insert_post(message_id=200 + idx, heat_score=30 - idx)
    first = await HotService().cursor_page("all", 2)
    assert len(first.items) == 2
    assert first.next_cursor
    second = await HotService().cursor_page("all", 2, first.next_cursor)
    assert [item.message_id for item in second.items] == [202]
    assert second.next_cursor is None


def test_tag_presentation_supports_json_and_legacy_space():
    assert parse_public_tags('["#a","#b"]') == ["a", "b"]
    assert parse_public_tags("#a #b") == ["a", "b"]


def _make_app(monkeypatch):
    monkeypatch.setattr(api_server, "authenticate", AsyncMock(return_value=None))
    application = MagicMock()
    application.bot = AsyncMock()
    app = web.Application()
    api_server.add_api_routes(app, application)
    return app


async def _client(app) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _session_token(uid: int = 123):
    import os
    from telepost.miniapp.session import issue_session
    os.environ["MINIAPP_SESSION_SECRET"] = "s" * 40
    return issue_session(uid, ["submitter"], username=f"user{uid}")


@pytest.mark.asyncio
async def test_hot_api_requires_user_session(monkeypatch, isolated_db):
    app = _make_app(monkeypatch)
    client = await _client(app)
    try:
        resp = await client.get("/api/v1/posts/hot")
        assert resp.status == 401
        token = _session_token()
        resp = await client.get("/api/v1/posts/hot",
                                headers={"Authorization": f"Bearer {token}"})
        assert resp.status == 200
        data = (await resp.json())["data"]
        assert "items" in data
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_post_detail_api_returns_safe_public_dto(monkeypatch, isolated_db):
    message_id = await _insert_post(message_id=301)
    app = _make_app(monkeypatch)
    client = await _client(app)
    try:
        token = _session_token()
        resp = await client.get(f"/api/v1/posts/{message_id}",
                                headers={"Authorization": f"Bearer {token}"})
        assert resp.status == 200
        data = (await resp.json())["data"]
        assert data["message_id"] == message_id
        assert data["note"] == "简介"
        assert data["media_count"] == 1
        assert "username" not in data
        assert "file_ids" not in data
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_content_feature_flag_disables_api(monkeypatch, isolated_db):
    monkeypatch.setattr(api_server, "MINIAPP_CONTENT_ENABLED", False)
    app = _make_app(monkeypatch)
    client = await _client(app)
    try:
        token = _session_token()
        resp = await client.get("/api/v1/posts/hot",
                                headers={"Authorization": f"Bearer {token}"})
        assert resp.status == 404
    finally:
        await client.close()
