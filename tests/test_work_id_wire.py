"""Wire compatibility for the source-neutral ``work_id`` field.

Contract (TelePost ↔ producers):
  * ``work_id`` is the canonical submission field; ``pixiv_id`` is the
    deprecated alias and keeps working unchanged.
  * Both present → ``work_id`` wins.
  * Neither → behaviour unchanged (derived from ``link`` when possible).
  * Storage keeps the ``pixiv_id`` column forever (zero-migration); the
    domain/storage boundary maps names in one place.
  * delivery-lookup accepts ``work_id`` as an alias of ``pixiv_id`` and its
    response carries both keys.
"""
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from utils import api_server
from utils.api_server import _fields_work_id
from utils.cache import TTLCache
from tests import helpers_refetch


# ---------------------------------------------------------------------------
# field extraction
# ---------------------------------------------------------------------------

def test_fields_work_id_prefers_canonical_field():
    assert _fields_work_id({"work_id": "777", "pixiv_id": "55"}) == "777"


def test_fields_work_id_accepts_legacy_alias():
    assert _fields_work_id({"pixiv_id": "55"}) == "55"


def test_fields_work_id_empty_when_absent():
    assert _fields_work_id({}) == ""
    assert _fields_work_id({"work_id": "  "}) == ""


def test_fields_work_id_alias_is_bounded():
    assert len(_fields_work_id({"work_id": "x" * 100})) == 32


# ---------------------------------------------------------------------------
# submission wire (JSON file_id path)
# ---------------------------------------------------------------------------

_TOKEN_ROW = {"id": 1, "telegram_user_id": 5073758941, "name": "script",
              "created_at": 1.0}
_SERVICE_PRINCIPAL = {
    "kind": "service", "telegram_user_id": 5073758941, "name": "script",
    "username": "", "display_name": "", "roles": None, "surface": "api",
    "token_id": 1, "token_name": "script", "actor_subject": "api_token:1",
}


async def _client(app) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _submission_app(monkeypatch):
    monkeypatch.setattr(api_server, "authenticate",
                        AsyncMock(return_value=_TOKEN_ROW))
    monkeypatch.setattr(api_server, "_resolve_principal",
                        AsyncMock(return_value=_SERVICE_PRINCIPAL))
    monkeypatch.setattr(api_server, "_rate_cache",
                        TTLCache(default_ttl=3600, max_size=4096))
    queue_mock = AsyncMock(return_value={
        "status": "pending_review", "review_id": 42,
        "media_count": 1, "document_count": 0,
    })
    monkeypatch.setattr("handlers.review.queue_review_from_file_ids", queue_mock)
    application = MagicMock()
    application.bot = AsyncMock()
    app = web.Application()
    api_server.add_api_routes(app, application)
    return app, queue_mock


async def _post_file_id_submission(client, extra_fields):
    payload = {
        "media": [{"type": "photo", "file_id": "AAA"}],
        "tags": "#t",
        "target_id": "bot1",
        "work_type": "illustration",
    }
    payload.update(extra_fields)
    return await client.post(
        "/api/v1/submissions", json=payload,
        headers={"Authorization": "Bearer tp_ok"},
    )


@pytest.mark.asyncio
async def test_submission_with_work_id_only(monkeypatch):
    app, queue_mock = _submission_app(monkeypatch)
    client = await _client(app)
    try:
        resp = await _post_file_id_submission(client, {"work_id": "777"})
        assert resp.status == 201
        assert queue_mock.call_args.kwargs["work_id"] == "777"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_submission_with_legacy_pixiv_id_only(monkeypatch):
    """Old producers keep working: pixiv_id maps onto domain work_id."""
    app, queue_mock = _submission_app(monkeypatch)
    client = await _client(app)
    try:
        resp = await _post_file_id_submission(client, {"pixiv_id": "55"})
        assert resp.status == 201
        assert queue_mock.call_args.kwargs["work_id"] == "55"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_submission_with_both_fields_work_id_wins(monkeypatch):
    app, queue_mock = _submission_app(monkeypatch)
    client = await _client(app)
    try:
        resp = await _post_file_id_submission(
            client, {"work_id": "777", "pixiv_id": "55"})
        assert resp.status == 201
        assert queue_mock.call_args.kwargs["work_id"] == "777"
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# storage boundary: domain work_id lands in the (immutable) pixiv_id column
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_work_id_persists_to_pixiv_id_column(refetch_db, monkeypatch):
    from database import db_manager
    from handlers import review

    bot = AsyncMock()
    bot.send_photo.return_value = helpers_refetch.photo_message()
    control = MagicMock()
    control.message_id = 11
    bot.send_message.return_value = control

    result = await review.queue_review_from_file_ids(
        bot, [{"type": "photo", "file_id": "ART"}],
        [],
        tags="#pixiv", title="t", user_id=7, username="pf",
        target_id="target-a", work_type="illustration", work_id="424242",
        idempotency_key="wire-1", source="api",
    )
    assert result["status"] == "pending_review"
    async with db_manager.get_db() as conn:
        row = await (await conn.execute(
            "SELECT pixiv_id FROM pending_reviews WHERE id=?",
            (result["review_id"],),
        )).fetchone()
    assert row["pixiv_id"] == "424242"


# ---------------------------------------------------------------------------
# delivery-lookup: work_id alias ≡ pixiv_id, additive response keys
# ---------------------------------------------------------------------------

async def _lookup_app(monkeypatch):
    monkeypatch.setattr(api_server, "authenticate",
                        AsyncMock(return_value=_TOKEN_ROW))
    application = MagicMock()
    application.bot = AsyncMock()
    app = web.Application()
    api_server.add_api_routes(app, application)
    return app


async def _seed_published(work_id):
    review_id = await helpers_refetch.insert_review(
        status="published", pixiv_id=work_id, target_id="target-a")
    from database import db_manager
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE pending_reviews SET decided_at=? WHERE id=?",
            (time.time(), review_id),
        )
    return review_id


@pytest.mark.asyncio
async def test_delivery_lookup_accepts_work_id(refetch_db, monkeypatch):
    await _seed_published("989898")
    app = await _lookup_app(monkeypatch)
    client = await _client(app)
    try:
        resp = await client.get(
            "/api/v1/deliveries/lookup",
            params={"work_type": "illustration", "work_id": "989898",
                    "target": "target-a"},
            headers={"Authorization": "Bearer tp_ok"},
        )
        assert resp.status == 200
        data = (await resp.json())["data"]
        assert data["found"] is True
        assert data["work_id"] == "989898"
        # Additive: the legacy key stays for old callers.
        assert data["pixiv_id"] == "989898"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_delivery_lookup_legacy_pixiv_id_equivalent(refetch_db, monkeypatch):
    await _seed_published("989898")
    app = await _lookup_app(monkeypatch)
    client = await _client(app)
    try:
        resp = await client.get(
            "/api/v1/deliveries/lookup",
            params={"work_type": "illustration", "pixiv_id": "989898"},
            headers={"Authorization": "Bearer tp_ok"},
        )
        assert resp.status == 200
        data = (await resp.json())["data"]
        assert data["found"] is True
        assert data["work_id"] == "989898"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_delivery_lookup_requires_some_work_id(refetch_db, monkeypatch):
    app = await _lookup_app(monkeypatch)
    client = await _client(app)
    try:
        resp = await client.get(
            "/api/v1/deliveries/lookup",
            params={"work_type": "illustration"},
            headers={"Authorization": "Bearer tp_ok"},
        )
        assert resp.status == 400
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_delivery_lookup_not_found_echoes_both_keys(refetch_db, monkeypatch):
    app = await _lookup_app(monkeypatch)
    client = await _client(app)
    try:
        resp = await client.get(
            "/api/v1/deliveries/lookup",
            params={"work_type": "illustration", "work_id": "000"},
            headers={"Authorization": "Bearer tp_ok"},
        )
        assert resp.status == 200
        data = (await resp.json())["data"]
        assert data["found"] is False
        assert data["work_id"] == "000"
        assert data["pixiv_id"] == "000"
    finally:
        await client.close()
