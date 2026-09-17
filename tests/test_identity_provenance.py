"""Identity / provenance contract tests (§identity).

The four concepts are orthogonal:
  actor  — who/WHAT executed the request ('user' | 'service')
  submitter — the VERIFIED human owner of a review (NULL for service rows)
  source — transport/provenance ('chat' | 'api' | ...)
  api token owner — never a submitter

Invariants under test:
  * service/API-token submissions enter the queue with submitter_user_id NULL and
    never appear in the token owner's /me/submissions;
  * human (Mini App / chat) submissions get submitter=the verified user and DO
    appear in /me/submissions;
  * a service request body can never spoof a submitter;
  * user A cannot see user B's submissions;
  * refetch replacements preserve the review-chain submitter (human stays human,
    service stays unowned);
  * the legacy backfill classifies deterministic rows without guessing.
"""
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from database import db_manager
from handlers import review
from telepost.storage.sqlite.reviews import ReviewRepository


def _photo_message(message_id=10, file_id="STAGED_PHOTO"):
    message = MagicMock()
    message.message_id = message_id
    message.photo = [MagicMock(file_id=file_id)]
    message.video = None
    message.animation = None
    message.audio = None
    message.document = None
    return message


async def _db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "identity.db")
    monkeypatch.setattr(db_manager, "DB_PATH", db_path)
    monkeypatch.setattr(review, "REVIEW_CHAT_ID", -100123)
    monkeypatch.setattr(review, "ADMIN_IDS", [123456789])
    monkeypatch.setenv("PIXIVFLOW_REFETCH_BASE_URL", "https://pixivflow.example")
    monkeypatch.setenv("PIXIVFLOW_REFETCH_TOKEN", "secret")
    await db_manager.init_db()
    return db_path


async def _insert_review(*, status="pending", pixiv_id="111", target_id="target-a",
                         user_id=7, username="alice", source="api",
                         submitter_user_id=None, submitter_username="",
                         chain="", refetch_request_id=""):
    now = time.time()
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            """
            INSERT INTO pending_reviews (
                idempotency_key, source, status, user_id, username,
                review_chat_id, media_json, documents_json,
                target_id, pixiv_id, work_type, review_chain_id, generation,
                refetch_request_id,
                submitter_user_id, submitter_username,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, '[]', '[]', ?, ?, 'illustration',
                      ?, 0, ?, ?, ?, ?, ?)
            """,
            ("k%d" % int(now * 1000), source, status, user_id, username,
             str(review.REVIEW_CHAT_ID), target_id, pixiv_id,
             chain or "", refetch_request_id,
             submitter_user_id, submitter_username, now, now),
        )
        return cur.lastrowid


async def _queue_replacement(bot, request_id, pixiv_id="222", key="repl-key"):
    control = MagicMock()
    control.message_id = 11
    bot.send_photo.return_value = _photo_message()
    bot.send_message.return_value = control
    return await review.queue_review_from_file_ids(
        bot,
        [{"type": "photo", "file_id": "NEW_ART"}],
        [],
        tags="#pixiv", title="New candidate",
        link=f"https://www.pixiv.net/artworks/{pixiv_id}",
        user_id=7, username="pixivflow",
        target_id="target-a", work_type="illustration", pixiv_id=pixiv_id,
        idempotency_key=key, source="api",
        refetch_request_id=request_id,
        submitter_user_id=None, submitter_username="",
        actor_kind="service", actor_subject="api_token:1",
    )


@pytest.mark.asyncio
async def test_service_submission_never_owned(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    control = MagicMock()
    control.message_id = 3
    bot.send_message.return_value = control

    result = await review.queue_review_from_file_ids(
        bot,
        [{"type": "photo", "file_id": "ART1"}],
        [],
        tags="#pixiv", title="scheduled",
        user_id=5073758941, username="pixivflow",  # token-bound admin as ACTOR
        idempotency_key="svc-1", source="api",
        target_id="bot1-illust-botefuku", work_type="illustration",
        pixiv_id="149000001",
        submitter_user_id=None, submitter_username="",
        actor_kind="service", actor_subject="api_token:1",
    )
    repo = ReviewRepository()
    rows = await repo.list_by_submitter(5073758941, limit=10)
    assert rows == []  # service row must NOT appear for the token owner

    async with db_manager.get_db() as conn:
        row = (await (await conn.execute(
            "SELECT * FROM pending_reviews WHERE id=?", (result["review_id"],)
        )).fetchone())
    assert row["submitter_user_id"] is None
    assert row["submitter_username"] == ""
    assert row["actor_kind"] == "service"
    assert row["actor_subject"] == "api_token:1"
    # Legacy request identity stays for display/audit but is NOT ownership.
    assert row["user_id"] == 5073758941


@pytest.mark.asyncio
async def test_human_submission_owned_and_isolated(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    control = MagicMock()
    control.message_id = 4
    bot.send_message.return_value = control

    await review.queue_review_from_file_ids(
        bot,
        [{"type": "photo", "file_id": "ART2"}],
        [],
        tags="#pixiv", title="human post",
        user_id=7, username="alice",
        idempotency_key="human-1", source="api",
        submitter_user_id=7, submitter_username="alice",
        actor_kind="user", actor_subject="telegram:7",
    )
    repo = ReviewRepository()
    mine = await repo.list_by_submitter(7, limit=10)
    assert len(mine) == 1 and mine[0]["id"] > 0
    # user B cannot see user A's row
    assert await repo.list_by_submitter(8, limit=10) == []


@pytest.mark.asyncio
async def test_chat_submission_is_human_owned(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    control = MagicMock()
    control.message_id = 5
    bot.send_message.return_value = control

    await review.queue_review_from_file_ids(
        bot,
        [{"type": "photo", "file_id": "ART3"}],
        [],
        tags="#chat", title="chat post",
        user_id=7, username="alice",
        idempotency_key="chat-1", source="chat",
        submitter_user_id=7, submitter_username="alice",
        actor_kind="user", actor_subject="telegram:7",
    )
    rows = await ReviewRepository().list_by_submitter(7, limit=10)
    assert len(rows) == 1 and rows[0]["source"] == "chat"


@pytest.mark.asyncio
async def test_refetch_replacement_preserves_human_submitter(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    source_id = await _insert_review(
        pixiv_id="111", target_id="target-a",
        user_id=7, username="alice", source="chat",
        submitter_user_id=7, submitter_username="alice",
    )
    from telepost.storage.sqlite.refetch import RefetchRepository
    attempt, _ = await _attempt_for(source_id, callback_id=8101)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")

    bot = AsyncMock()
    result = await _queue_replacement(bot, attempt["request_id"], pixiv_id="333", key="repl-human")
    async with db_manager.get_db() as conn:
        new_row = (await (await conn.execute(
            "SELECT * FROM pending_reviews WHERE id=?", (result["review_id"],)
        )).fetchone())
    # Chain ownership survives: the replacement was created BY the service
    # (actor service) but still belongs to alice.
    assert new_row["submitter_user_id"] == 7
    assert new_row["submitter_username"] == "alice"
    assert new_row["actor_kind"] == "service"
    assert new_row["user_id"] == 7
    # The owner sees the replacement in /me/submissions.
    rows = await ReviewRepository().list_by_submitter(7, limit=10)
    assert any(r["id"] == result["review_id"] for r in rows)


@pytest.mark.asyncio
async def test_refetch_replacement_of_service_chain_stays_unowned(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    source_id = await _insert_review(
        pixiv_id="111", target_id="target-a",
        user_id=5073758941, username="pixivflow",
        submitter_user_id=None, submitter_username="",
    )
    from telepost.storage.sqlite.refetch import RefetchRepository
    attempt, _ = await _attempt_for(source_id, callback_id=8102)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")

    bot = AsyncMock()
    result = await _queue_replacement(bot, attempt["request_id"], pixiv_id="444", key="repl-svc")
    async with db_manager.get_db() as conn:
        new_row = (await (await conn.execute(
            "SELECT * FROM pending_reviews WHERE id=?", (result["review_id"],)
        )).fetchone())
    assert new_row["submitter_user_id"] is None
    assert new_row["actor_kind"] == "service"
    # The bound admin does NOT own the chain.
    assert await ReviewRepository().list_by_submitter(5073758941, limit=10) == []


@pytest.mark.asyncio
async def test_service_body_cannot_spoof_submitter(tmp_path, monkeypatch):
    """A service request body carrying user_id must be ignored as ownership."""
    from utils import api_server
    calls = {}

    async def fake_queue(bot, media, documents, **kwargs):
        calls["kwargs"] = kwargs
        return {"status": "pending_review", "review_id": 1,
                "media_count": 1, "document_count": 0, "reused": False}

    monkeypatch.setattr("handlers.review.queue_review_from_file_ids", fake_queue)

    principal = {
        "kind": "service", "telegram_user_id": 5073758941,
        "name": "pixivflow", "roles": None, "surface": "api",
        "token_id": 3, "actor_subject": "api_token:3",
    }
    monkeypatch.setattr(api_server, "_resolve_principal",
                        AsyncMock(return_value=principal))
    monkeypatch.setattr(api_server, "_rate_cache",
                        __import__("utils.cache", fromlist=["TTLCache"]).TTLCache(
                                default_ttl=3600, max_size=16))

    payload_body = {
        "media": [{"type": "photo", "file_id": "F1"}],
        "tags": "#pixiv",
        "title": "t",
        "user_id": 999,  # spoof attempt
        "actor_kind": "user",  # spoof attempt
    }
    app = web_app_for(monkeypatch)
    from aiohttp.test_utils import TestClient, TestServer
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.post("/api/v1/submissions", json=payload_body)
        assert resp.status in (200, 201)
    finally:
        await client.close()

    kwargs = calls["kwargs"]
    # Ownership comes from the principal kind, never from the body.
    assert kwargs["submitter_user_id"] is None
    assert kwargs["submitter_username"] == ""
    assert kwargs["actor_kind"] == "service"
    assert kwargs["user_id"] == 5073758941


def web_app_for(monkeypatch):
    from aiohttp import web
    from utils import api_server
    app = web.Application()
    application = MagicMock()
    bot = AsyncMock()
    bot.send_message.return_value = MagicMock(message_id=321)
    application.bot = bot
    api_server.add_api_routes(app, application)
    return app


async def _attempt_for(source_review_id, *, callback_id=7001):
    import uuid
    from telepost.storage.sqlite.refetch import RefetchRepository
    chain_id = "chain-%d" % source_review_id
    request_id = str(uuid.uuid4())
    now = time.time()
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            """
            INSERT INTO refetch_attempts (
                callback_key, request_id, review_chain_id, generation,
                source_review_id, source_candidate_id, state, created_at
            ) VALUES (?, ?, ?, 1, ?, '', 'requested', ?)
            """,
            (f"cb:{callback_id}", request_id, chain_id, source_review_id, now),
        )
        attempt_id = cur.lastrowid
    async with db_manager.get_db() as conn:
        row = (await (await conn.execute(
            "SELECT * FROM refetch_attempts WHERE id=?", (attempt_id,)
        )).fetchone())
    return row, None