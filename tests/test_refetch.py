"""Durable review-refetch state machine tests (TelePost side).

Covers the split-worker contract:
  pending click → durable attempt + remote submit
  same-callback webhook redelivery → SAME attempt / SAME request id
  new intentional click after a terminal attempt → new generation
  click while active → data-layer one-active-per-chain refusal
  stale button on a superseded/ended review → rejected without remote call
  no_alternative/failed outcome → current review unchanged, user informed
  approve/reject race → late result becomes obsolete, never overwrites
  replacement submission → old review superseded only after the new review
      is durably created; lineage (chain/generation) recorded
  outcome endpoint idempotency / auth / validation
"""
import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import FormData
from aiohttp import web

from database import db_manager
from handlers import review
from telepost.storage.sqlite.refetch import RefetchRepository


def _callback_update(data, user_id=123456789, callback_id=7001):
    update = MagicMock()
    update.effective_user.id = user_id
    update.callback_query.data = data
    update.callback_query.id = callback_id
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


@pytest.fixture
async def refetch_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "refetch.db")
    monkeypatch.setattr(db_manager, "DB_PATH", db_path)
    monkeypatch.setattr(review, "REVIEW_CHAT_ID", -100123)
    monkeypatch.setattr(review, "ADMIN_IDS", [123456789])
    monkeypatch.setenv("PIXIVFLOW_REFETCH_BASE_URL", "https://pixivflow.example")
    monkeypatch.setenv("PIXIVFLOW_REFETCH_TOKEN", "secret")
    await db_manager.init_db()
    return db_path


async def _insert_review(*, status="pending", pixiv_id="111", target_id="target-a",
                         chain="", generation=0):
    now = time.time()
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            """
            INSERT INTO pending_reviews (
                idempotency_key, source, status, user_id, username,
                review_chat_id, media_json, documents_json,
                target_id, pixiv_id, work_type, review_chain_id, generation,
                created_at, updated_at
            ) VALUES (?, 'api', ?, 7, 'pf', ?, '[]', '[]', ?, ?, 'illustration', ?, ?, ?, ?)
            """,
            ("k%d" % int(now * 1000), status, str(review.REVIEW_CHAT_ID),
             target_id, pixiv_id, chain or "", generation, now, now),
        )
        review_id = cur.lastrowid
    # Seed seen-history exactly like production (init_db bootstrap / _reserve).
    if pixiv_id:
        chain_id = chain or "chain-%d" % review_id
        async with db_manager.get_db() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO refetch_seen_candidates "
                "(review_chain_id, candidate_id, source, generation, created_at) "
                "VALUES (?, ?, 'original', ?, ?)",
                (chain_id, pixiv_id, generation, now),
            )
    return review_id


async def _attempt(*, chain_id, generation=1, source_review_id, callback_id=7001,
                   request_id=None, state="requested"):
    repo = RefetchRepository()
    row, refused = await repo.create_attempt(
        callback_key=f"cb:{source_review_id}:{callback_id}",
        review_chain_id=chain_id, generation=generation,
        source_review_id=source_review_id,
        source_candidate_id="111",
        request_id=request_id,
    )
    return row, refused


def _photo_message(message_id=10, file_id="STAGED_PHOTO"):
    message = MagicMock()
    message.message_id = message_id
    message.photo = [MagicMock(file_id=file_id)]
    message.video = None
    message.animation = None
    message.audio = None
    message.document = None
    return message


# ---------------------------------------------------------------------------
# Button handler: review-state gate + attempt lifecycle
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_refetch_rejected_when_review_superseded(refetch_db, monkeypatch):
    review_id = await _insert_review(status="superseded", pixiv_id="111")
    update = _callback_update(f"review_refetch:{review_id}")
    context = MagicMock(bot=AsyncMock())

    await review.refetch_review(update, context)

    text = update.callback_query.answer.await_args.kwargs["text"]
    assert "已被替换" in text
    context.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_refetch_rejected_when_review_ended(refetch_db, monkeypatch):
    for status in ("approved", "rejected", "expired", "published"):
        review_id = await _insert_review(status=status, pixiv_id="111")
        update = _callback_update(f"review_refetch:{review_id}")
        context = MagicMock(bot=AsyncMock())
        await review.refetch_review(update, context)
        text = update.callback_query.answer.await_args.kwargs["text"]
        assert "已结束" in text
        context.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_refetch_requires_target_id(refetch_db, monkeypatch):
    review_id = await _insert_review(target_id="", pixiv_id="111")
    update = _callback_update(f"review_refetch:{review_id}")
    context = MagicMock(bot=AsyncMock())
    await review.refetch_review(update, context)
    assert "缺少抓取目标" in update.callback_query.answer.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_refetch_requires_remote_configuration(refetch_db, monkeypatch):
    monkeypatch.delenv("PIXIVFLOW_REFETCH_BASE_URL")
    monkeypatch.delenv("PIXIVFLOW_REFETCH_TOKEN")
    review_id = await _insert_review(pixiv_id="111")
    update = _callback_update(f"review_refetch:{review_id}")
    context = MagicMock(bot=AsyncMock())
    await review.refetch_review(update, context)
    assert "未配置" in update.callback_query.answer.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_same_callback_retry_reuses_same_request_id(refetch_db, monkeypatch):
    review_id = await _insert_review(pixiv_id="111")
    submitted = MagicMock(return_value={"status": "accepted", "slotId": "slot-1"})
    monkeypatch.setattr(review, "_submit_pixivflow_refetch", submitted)

    update = _callback_update(f"review_refetch:{review_id}", callback_id=9001)
    context = MagicMock(bot=AsyncMock())
    await review.refetch_review(update, context)
    for _ in range(200):
        if context.bot.send_message.await_count:
            break
        await asyncio.sleep(0.005)

    repo = RefetchRepository()
    first = await repo.find_by_callback_key("cb:%d:9001" % review_id)
    assert first is not None
    request_id = first["request_id"]
    assert submitted.call_count == 1

    # Webhook redelivery of the SAME press (same callback id): no new attempt,
    # no second remote call, same request identity, "正在重抓" replay.
    update2 = _callback_update(f"review_refetch:{review_id}", callback_id=9001)
    context2 = MagicMock(bot=AsyncMock())
    await review.refetch_review(update2, context2)
    for _ in range(200):
        if context2.bot.send_message.await_count:
            break
        await asyncio.sleep(0.005)

    assert submitted.call_count == 1
    replay = await repo.find_by_callback_key("cb:%d:9001" % review_id)
    assert replay["request_id"] == request_id
    assert "正在重抓" in update2.callback_query.answer.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_new_intentional_click_after_terminal_creates_new_generation(refetch_db, monkeypatch):
    review_id = await _insert_review(pixiv_id="111")
    submitted = MagicMock(return_value={"status": "accepted", "slotId": "slot-1"})
    monkeypatch.setattr(review, "_submit_pixivflow_refetch", submitted)

    # First click (callback 1) → terminal failed (remote network error).
    update = _callback_update(f"review_refetch:{review_id}", callback_id=9001)
    context = MagicMock(bot=AsyncMock())
    await review.refetch_review(update, context)

    repo = RefetchRepository()
    attempts = []
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT * FROM refetch_attempts WHERE source_review_id=?", (review_id,)
        )
        attempts = list(await cur.fetchall())
    assert len(attempts) == 1
    first = attempts[0]
    await repo.mark_failed(first["request_id"], "network_error")

    # New intentional click → NEW attempt, NEW request id, next generation.
    update2 = _callback_update(f"review_refetch:{review_id}", callback_id=9002)
    context2 = MagicMock(bot=AsyncMock())
    await review.refetch_review(update2, context2)

    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT * FROM refetch_attempts WHERE source_review_id=? ORDER BY id",
            (review_id,),
        )
        all_rows = list(await cur.fetchall())
    assert len(all_rows) == 2
    second = all_rows[1]
    assert second["request_id"] != first["request_id"]
    assert second["generation"] == first["generation"] + 1
    assert second["state"] == "requested"


@pytest.mark.asyncio
async def test_click_while_active_refused_by_data_layer(refetch_db, monkeypatch):
    review_id = await _insert_review(pixiv_id="111")
    chain = "chain-%d" % review_id
    attempt, refused = await _attempt(
        chain_id=chain, source_review_id=review_id, callback_id=9001,
        state="admitted",
    )
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")
    submitted = MagicMock(return_value={"status": "accepted", "slotId": "slot-2"})
    monkeypatch.setattr(review, "_submit_pixivflow_refetch", submitted)

    update = _callback_update(f"review_refetch:{review_id}", callback_id=9002)
    context = MagicMock(bot=AsyncMock())
    await review.refetch_review(update, context)

    assert "正在重抓，请稍候" in update.callback_query.answer.await_args.kwargs["text"]
    assert submitted.call_count == 0


@pytest.mark.asyncio
async def test_remote_failure_marks_attempt_failed_and_keeps_review(refetch_db, monkeypatch):
    review_id = await _insert_review(pixiv_id="111")

    def _boom(target_id, request_id, correlation):
        raise RuntimeError("PixivFlow 拒绝重抓（HTTP 401）")

    monkeypatch.setattr(review, "_submit_pixivflow_refetch", _boom)
    update = _callback_update(f"review_refetch:{review_id}", callback_id=9001)
    context = MagicMock(bot=AsyncMock())
    await review.refetch_review(update, context)
    for _ in range(200):
        if context.bot.send_message.await_count:
            break
        await asyncio.sleep(0.005)

    repo = RefetchRepository()
    attempt = await repo.find_by_callback_key("cb:%d:9001" % review_id)
    assert attempt["state"] == "failed"
    assert attempt["failure_code"] == "unauthorized"
    assert "重抓失败，当前稿件未变" in context.bot.send_message.await_args.kwargs["text"]

    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT status FROM pending_reviews WHERE id=?", (review_id,)
        )
        row = await cur.fetchone()
    assert row["status"] == "pending"


# ---------------------------------------------------------------------------
# Outcome endpoint (PixivFlow → TelePost)
# ---------------------------------------------------------------------------
def _make_api_app(monkeypatch):
    import utils.api_server as api_server

    async def _authenticate(bearer: str):
        # Realistic: only a presented bearer token authenticates.
        return {"id": 1, "name": "pixivflow"} if bearer else None

    monkeypatch.setattr(api_server, "authenticate", _authenticate)
    application = MagicMock()
    application.bot = AsyncMock()
    application.bot.send_message.return_value = MagicMock(message_id=321)
    app = web.Application()
    api_server.add_api_routes(app, application)
    return api_server, application, app


async def _client(app):
    from aiohttp.test_utils import TestClient, TestServer
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_outcome_no_alternative_updates_attempt_and_notifies(refetch_db, monkeypatch):
    review_id = await _insert_review(pixiv_id="111")
    chain = "chain-%d" % review_id
    attempt, _ = await _attempt(chain_id=chain, source_review_id=review_id, callback_id=9001)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")

    api_server, application, app = _make_api_app(monkeypatch)
    client = await _client(app)
    try:
        resp = await client.post("/api/v1/refetch/outcomes", headers={"Authorization": "Bearer tp_test"}, json={
            "request_id": attempt["request_id"],
            "disposition": "no_alternative",
            "scanned": 5,
            "skipped": {"duplicate": 4, "invalid": 1, "unavailable": 0},
        })
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert data["data"]["attempt_state"] == "no_alternative"
    finally:
        await client.close()

    repo = RefetchRepository()
    updated = await repo.find_by_request_id(attempt["request_id"])
    assert updated["state"] == "no_alternative"
    assert updated["scanned"] == 5
    assert updated["skipped_duplicate"] == 4
    # Current review stays untouched.
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT status FROM pending_reviews WHERE id=?", (review_id,)
        )
        row = await cur.fetchone()
    assert row["status"] == "pending"
    assert "没有找到新的可替换作品" in application.bot.send_message.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_outcome_failed_notifies_and_keeps_review(refetch_db, monkeypatch):
    review_id = await _insert_review(pixiv_id="111")
    attempt, _ = await _attempt(
        chain_id="chain-%d" % review_id, source_review_id=review_id, callback_id=9001)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")

    api_server, application, app = _make_api_app(monkeypatch)
    client = await _client(app)
    try:
        resp = await client.post("/api/v1/refetch/outcomes", headers={"Authorization": "Bearer tp_test"}, json={
            "request_id": attempt["request_id"],
            "disposition": "failed",
            "reason": "pixiv auth failure",
        })
        assert resp.status == 200
    finally:
        await client.close()

    repo = RefetchRepository()
    updated = await repo.find_by_request_id(attempt["request_id"])
    assert updated["state"] == "failed"
    assert "重抓失败，当前稿件未变" in application.bot.send_message.await_args.kwargs["text"]
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT status FROM pending_reviews WHERE id=?", (review_id,)
        )
        row = await cur.fetchone()
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_outcome_rejected_after_approve_becomes_obsolete(refetch_db, monkeypatch):
    # Race: reviewer approves while the refetch runs; the late outcome must not
    # touch the now-terminal review (stale-result protection).
    review_id = await _insert_review(pixiv_id="111")
    attempt, _ = await _attempt(
        chain_id="chain-%d" % review_id, source_review_id=review_id, callback_id=9001)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE pending_reviews SET status='published', decided_at=?, decided_by=? "
            "WHERE id=?",
            (time.time(), 123456789, review_id),
        )

    api_server, application, app = _make_api_app(monkeypatch)
    client = await _client(app)
    try:
        resp = await client.post("/api/v1/refetch/outcomes", headers={"Authorization": "Bearer tp_test"}, json={
            "request_id": attempt["request_id"],
            "disposition": "no_alternative",
        })
        assert resp.status == 200
        data = await resp.json()
        assert data["data"]["attempt_state"] == "obsolete"
    finally:
        await client.close()

    repo = RefetchRepository()
    updated = await repo.find_by_request_id(attempt["request_id"])
    assert updated["state"] == "obsolete"
    # Reviewer decision untouched and NOT re-notified.
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT status FROM pending_reviews WHERE id=?", (review_id,)
        )
        row = await cur.fetchone()
    assert row["status"] == "published"
    application.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_outcome_endpoint_validation_and_idempotency(refetch_db, monkeypatch):
    api_server, application, app = _make_api_app(monkeypatch)
    client = await _client(app)
    try:
        # Missing auth (no bearer token → 401)
        resp = await client.post("/api/v1/refetch/outcomes", json={"request_id": "x", "disposition": "failed"})
        assert resp.status == 401
        # Invalid disposition
        resp = await client.post("/api/v1/refetch/outcomes", headers={"Authorization": "Bearer tp_test"}, json={"request_id": "x", "disposition": "bogus"})
        assert resp.status == 400
        # Unknown attempt
        resp = await client.post("/api/v1/refetch/outcomes", headers={"Authorization": "Bearer tp_test"}, json={
            "request_id": "11111111-2222-4333-8444-555555555555", "disposition": "failed",
        })
        assert resp.status == 404

        # Valid → terminal; a second identical call is an idempotent 200 with
        # exactly ONE group notification.
        review_id = await _insert_review(pixiv_id="111")
        attempt, _ = await _attempt(
            chain_id="chain-%d" % review_id, source_review_id=review_id, callback_id=9001)
        await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")
        body = {"request_id": attempt["request_id"], "disposition": "no_alternative"}
        resp = await client.post("/api/v1/refetch/outcomes", headers={"Authorization": "Bearer tp_test"}, json=body)
        assert resp.status == 200
        application.bot.send_message.assert_awaited_once()
        resp = await client.post("/api/v1/refetch/outcomes", headers={"Authorization": "Bearer tp_test"}, json=body)
        assert resp.status == 200
        assert application.bot.send_message.await_count == 1
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Replacement (submission correlation): commit-after-success + lineage
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_replacement_submission_supersedes_only_after_new_review(refetch_db, monkeypatch):
    review_id = await _insert_review(pixiv_id="111", target_id="target-a")
    attempt, _ = await _attempt(
        chain_id="chain-%d" % review_id, source_review_id=review_id, callback_id=9001)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")

    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    control = MagicMock()
    control.message_id = 11
    bot.send_message.return_value = control

    result = await review.queue_review_from_file_ids(
        bot,
        [{"type": "photo", "file_id": "NEW_ART"}],
        [],
        tags="#pixiv", title="New candidate",
        link="https://www.pixiv.net/artworks/222",
        user_id=7, username="pixivflow",
        target_id="target-a", work_type="illustration", pixiv_id="222",
        idempotency_key="repl-1", source="api",
        refetch_request_id=attempt["request_id"],
    )
    new_id = result["review_id"]
    assert new_id != review_id

    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT * FROM pending_reviews WHERE id=?", (new_id,)
        )
        new_row = await cur.fetchone()
        cur = await conn.execute(
            "SELECT * FROM pending_reviews WHERE id=?", (review_id,)
        )
        old_row = await cur.fetchone()
        cur = await conn.execute(
            "SELECT * FROM refetch_seen_candidates WHERE review_chain_id=?",
            ("chain-%d" % review_id,),
        )
        seen = list(await cur.fetchall())

    assert old_row["status"] == "superseded"
    assert new_row["review_chain_id"] == "chain-%d" % review_id
    assert new_row["generation"] == 1
    assert new_row["supersedes_review_id"] == review_id
    assert new_row["refetch_request_id"] == attempt["request_id"]
    seen_ids = {row["candidate_id"] for row in seen}
    assert "111" in seen_ids  # original bootstrap
    assert "222" in seen_ids  # replacement
    repo = RefetchRepository()
    updated = await repo.find_by_request_id(attempt["request_id"])
    assert updated["state"] == "replaced"
    assert updated["result_candidate_id"] == "222"


@pytest.mark.asyncio
async def test_replacement_staging_failure_keeps_source_pending(refetch_db):
    review_id = await _insert_review(pixiv_id="111", target_id="target-a")
    attempt, _ = await _attempt(
        chain_id="chain-%d" % review_id, source_review_id=review_id, callback_id=9001)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")
    bot = AsyncMock()
    bot.send_photo.side_effect = RuntimeError("preview staging failed")

    with pytest.raises(RuntimeError, match="preview staging failed"):
        await review.queue_review_from_file_ids(
            bot, [{"type": "photo", "file_id": "NEW_ART"}], [],
            tags="#pixiv", title="New candidate", user_id=7, username="pixivflow",
            target_id="target-a", work_type="illustration", pixiv_id="222",
            idempotency_key="repl-failed", source="api",
            refetch_request_id=attempt["request_id"],
        )
    async with db_manager.get_db() as conn:
        cur = await conn.execute("SELECT status FROM pending_reviews WHERE id=?", (review_id,))
        assert (await cur.fetchone())["status"] == "pending"
    assert (await RefetchRepository().find_by_request_id(attempt["request_id"]))["state"] == "admitted"

    bot.send_photo.side_effect = None
    bot.send_photo.return_value = _photo_message()
    bot.send_message.return_value = MagicMock(message_id=11)
    result = await review.queue_review_from_file_ids(
        bot, [{"type": "photo", "file_id": "NEW_ART"}], [],
        tags="#pixiv", title="New candidate", user_id=7, username="pixivflow",
        target_id="target-a", work_type="illustration", pixiv_id="222",
        idempotency_key="repl-failed", source="api",
        refetch_request_id=attempt["request_id"],
    )
    assert result["status"] == "pending_review" and not result["reused"]
    assert (await RefetchRepository().find_by_request_id(attempt["request_id"]))["state"] == "replaced"


@pytest.mark.asyncio
async def test_replacement_after_approve_race_does_not_supersede(refetch_db, monkeypatch):
    review_id = await _insert_review(pixiv_id="111", target_id="target-a")
    attempt, _ = await _attempt(
        chain_id="chain-%d" % review_id, source_review_id=review_id, callback_id=9001)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE pending_reviews SET status='published', decided_at=?, decided_by=? "
            "WHERE id=?",
            (time.time(), 123456789, review_id),
        )

    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    control = MagicMock()
    control.message_id = 11
    bot.send_message.return_value = control

    with pytest.raises(ValueError, match="obsolete"):
        await review.queue_review_from_file_ids(
            bot,
            [{"type": "photo", "file_id": "NEW_ART"}],
            [],
            tags="#pixiv", title="Late candidate",
            link="https://www.pixiv.net/artworks/333",
            user_id=7, username="pixivflow",
            target_id="target-a", work_type="illustration", pixiv_id="333",
            idempotency_key="repl-2", source="api",
            refetch_request_id=attempt["request_id"],
        )

    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT status FROM pending_reviews WHERE id=?", (review_id,)
        )
        old_row = await cur.fetchone()
        cur = await conn.execute(
            "SELECT COUNT(*) AS count FROM pending_reviews WHERE pixiv_id='333'"
        )
        assert (await cur.fetchone())["count"] == 0
    # The reviewer's decision wins; no late review is created.
    assert old_row["status"] == "published"
    bot.send_photo.assert_not_awaited()
    repo = RefetchRepository()
    updated = await repo.find_by_request_id(attempt["request_id"])
    assert updated["state"] == "obsolete"


@pytest.mark.asyncio
async def test_replacement_decided_during_staging_is_obsolete(refetch_db):
    review_id = await _insert_review(pixiv_id="111", target_id="target-a")
    attempt, _ = await _attempt(
        chain_id="chain-%d" % review_id, source_review_id=review_id, callback_id=9001)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()

    async def decide_before_control(**kwargs):
        async with db_manager.get_db() as conn:
            await conn.execute(
                "UPDATE pending_reviews SET status='rejected' WHERE id=?", (review_id,)
            )
        return MagicMock(message_id=11)

    bot.send_message.side_effect = decide_before_control
    with pytest.raises(RuntimeError, match="审核控制消息无法绑定"):
        await review.queue_review_from_file_ids(
            bot, [{"type": "photo", "file_id": "NEW_ART"}], [],
            tags="#pixiv", title="Late candidate", user_id=7, username="pixivflow",
            target_id="target-a", work_type="illustration", pixiv_id="222",
            idempotency_key="repl-raced", source="api",
            refetch_request_id=attempt["request_id"],
        )
    async with db_manager.get_db() as conn:
        cur = await conn.execute("SELECT status FROM pending_reviews WHERE id=?", (review_id,))
        assert (await cur.fetchone())["status"] == "rejected"
        cur = await conn.execute("SELECT status FROM pending_reviews WHERE pixiv_id='222'")
        assert (await cur.fetchone())["status"] == "failed"
    assert (await RefetchRepository().find_by_request_id(attempt["request_id"]))["state"] == "obsolete"

# ---------------------------------------------------------------------------
# API plumbing: refetch_request_id rides the submission into the queue service
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submission_api_passes_refetch_request_id(refetch_db, monkeypatch):
    import utils.api_server as api_server

    async def _authenticate(bearer: str):
        return {"id": 1, "telegram_user_id": 7, "name": "pixivflow"} if bearer else None

    monkeypatch.setattr(api_server, "authenticate", _authenticate)
    monkeypatch.setattr(api_server, "API_REVIEW_REQUIRED", True)
    queue_mock = AsyncMock(return_value={"status": "pending_review", "review_id": 42})
    monkeypatch.setattr("handlers.review.queue_review_from_file_ids", queue_mock)
    application = MagicMock()
    application.bot = AsyncMock()
    app = web.Application()
    api_server.add_api_routes(app, application)
    client = await _client(app)
    try:
        resp = await client.post(
            "/api/v1/submissions",
            headers={"Authorization": "Bearer tp_ok"},
            json={
                "media": [{"type": "photo", "file_id": "AAA"}],
                "tags": "Pixiv",
                "target_id": "target-a",
                "work_type": "illustration",
                "pixiv_id": "222",
                "refetch_request_id": "6eb50329-20f2-4ea7-b95b-e4676b50d9f1",
            },
        )
        assert resp.status == 201
        kw = queue_mock.call_args.kwargs
        assert kw["refetch_request_id"] == "6eb50329-20f2-4ea7-b95b-e4676b50d9f1"
        # A schedule-originated submission without the field stays empty.
        queue_mock.reset_mock()
        resp2 = await client.post(
            "/api/v1/submissions",
            headers={"Authorization": "Bearer tp_ok"},
            json={
                "media": [{"type": "photo", "file_id": "BBB"}],
                "tags": "Pixiv",
                "target_id": "target-a",
                "work_type": "illustration",
                "pixiv_id": "333",
            },
        )
        assert resp2.status == 201
        assert queue_mock.call_args.kwargs["refetch_request_id"] == ""
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_submission_rejects_unresolved_refetch_provenance(refetch_db, monkeypatch, tmp_path):
    import utils.api_server as api_server

    async def _authenticate(bearer: str):
        return {"id": 1, "telegram_user_id": 7, "name": "pixivflow"} if bearer else None

    monkeypatch.setattr(api_server, "authenticate", _authenticate)
    monkeypatch.setattr(api_server, "API_REVIEW_REQUIRED", True)
    monkeypatch.chdir(tmp_path)
    queue_mock = AsyncMock()
    monkeypatch.setattr("handlers.review.queue_review_from_file_ids", queue_mock)
    monkeypatch.setattr("handlers.review.queue_review_from_files", queue_mock)
    app = web.Application()
    api_server.add_api_routes(app, MagicMock(bot=AsyncMock()))
    client = await _client(app)
    try:
        resp = await client.post(
            "/api/v1/submissions", headers={"Authorization": "Bearer tp_ok"},
            json={"media": [{"type": "photo", "file_id": "AAA"}], "tags": "Pixiv",
                  "refetch_request_id": "{{refetchRequestId}}"},
        )
        assert resp.status == 400
        assert (await resp.json())["error"]["code"] == "invalid_refetch_provenance"

        form = FormData()
        form.add_field("files", b"photo", filename="photo.jpg", content_type="image/jpeg")
        form.add_field("tags", "Pixiv")
        form.add_field("refetch_request_id", "{{refetchRequestId}}")
        resp = await client.post(
            "/api/v1/submissions", headers={"Authorization": "Bearer tp_ok"}, data=form,
        )
        assert resp.status == 400
        assert (await resp.json())["error"]["code"] == "invalid_refetch_provenance"
        queue_mock.assert_not_awaited()
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Superseded old-card cleanup (SUPERSEDED_RETENTION_DAYS)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cleanup_superseded_deletes_old_card_keeps_lineage(refetch_db, monkeypatch):
    import time as _t
    old_id = await _insert_review(status="superseded", pixiv_id="111")
    fresh_id = await _insert_review(status="superseded", pixiv_id="222")
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE pending_reviews SET updated_at=? WHERE id=?",
            (_t.time() - 40 * 86400, old_id),
        )
    # Lineage for the old chain must survive the card cleanup.
    attempt, _ = await _attempt(
        chain_id="chain-%d" % old_id, source_review_id=old_id, callback_id=9001)

    monkeypatch.setattr(review, "SUPERSEDED_RETENTION_DAYS", 30)
    deleted = AsyncMock()
    monkeypatch.setattr(review, "_delete_messages", deleted)
    bot = AsyncMock()

    count = await review.cleanup_superseded_reviews(bot)
    assert count == 1
    deleted.assert_awaited_once()

    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT id FROM pending_reviews WHERE id IN (?, ?)", (old_id, fresh_id)
        )
        remaining = [r[0] for r in await cur.fetchall()]
        cur = await conn.execute(
            "SELECT COUNT(*) FROM refetch_attempts WHERE review_chain_id=?",
            ("chain-%d" % old_id,),
        )
        attempts_kept = (await cur.fetchone())[0]
        cur = await conn.execute(
            "SELECT COUNT(*) FROM refetch_seen_candidates WHERE review_chain_id=?",
            ("chain-%d" % old_id,),
        )
        seen_kept = (await cur.fetchone())[0]
    assert remaining == [fresh_id]          # only the fresh card survives
    assert attempts_kept == 1               # lineage retained
    assert seen_kept >= 1                   # candidate history retained


@pytest.mark.asyncio
async def test_cleanup_superseded_disabled_by_zero(refetch_db, monkeypatch):
    old_id = await _insert_review(status="superseded", pixiv_id="111")
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE pending_reviews SET updated_at=? WHERE id=?",
            (time.time() - 40 * 86400, old_id),
        )
    monkeypatch.setattr(review, "SUPERSEDED_RETENTION_DAYS", 0)
    deleted = AsyncMock()
    monkeypatch.setattr(review, "_delete_messages", deleted)

    count = await review.cleanup_superseded_reviews(AsyncMock())
    assert count == 0
    deleted.assert_not_awaited()
    async with db_manager.get_db() as conn:
        cur = await conn.execute("SELECT id FROM pending_reviews WHERE id=?", (old_id,))
        assert (await cur.fetchone()) is not None


# ---------------------------------------------------------------------------
# Progress watchdog: reminders + stale-timeout failure notification
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_progress_watchdog_reminds_then_stale_fails(refetch_db, monkeypatch):
    import time as _t
    review_id = await _insert_review(pixiv_id="111")
    attempt, _ = await _attempt(
        chain_id="chain-%d" % review_id, source_review_id=review_id, callback_id=9001)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")
    # Age the attempt: 10 minutes old (remind=5, stale=45 → remind window).
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE refetch_attempts SET created_at=? WHERE id=?",
            (_t.time() - 10 * 60, attempt["id"]),
        )
    monkeypatch.setattr(review, "REFETCH_PROGRESS_REMIND_MINUTES", 5)
    monkeypatch.setattr(review, "REFETCH_STALE_TIMEOUT_MINUTES", 45)
    bot = AsyncMock()

    acted = await review.monitor_refetch_progress(bot)
    assert acted == 1
    msg = bot.send_message.await_args.kwargs["text"]
    assert "仍在处理中" in msg and str(review_id) in msg
    first_send_count = bot.send_message.await_count

    # Immediate re-run: same remind window → no duplicate reminder.
    acted = await review.monitor_refetch_progress(bot)
    assert acted == 0
    assert bot.send_message.await_count == first_send_count

    # A long-running attempt gets only one progress message, not one per window.
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE refetch_attempts SET created_at=? WHERE id=?",
            (_t.time() - 30 * 60, attempt["id"]),
        )
    acted = await review.monitor_refetch_progress(bot)
    assert acted == 0
    assert bot.send_message.await_count == first_send_count

    # Age past the stale timeout → attempt fails + user notified.
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE refetch_attempts SET created_at=? WHERE id=?",
            (_t.time() - 50 * 60, attempt["id"]),
        )
    acted = await review.monitor_refetch_progress(bot)
    assert acted == 1
    assert "超时未完成" in bot.send_message.await_args.kwargs["text"]
    repo = RefetchRepository()
    updated = await repo.find_by_request_id(attempt["request_id"])
    assert updated["state"] == "failed"
    assert updated["failure_code"] == "stale_timeout"
    # Current review untouched.
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT status FROM pending_reviews WHERE id=?", (review_id,)
        )
        row = await cur.fetchone()
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_progress_watchdog_disabled_when_zero(refetch_db, monkeypatch):
    import time as _t
    review_id = await _insert_review(pixiv_id="111")
    attempt, _ = await _attempt(
        chain_id="chain-%d" % review_id, source_review_id=review_id, callback_id=9001)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE refetch_attempts SET created_at=? WHERE id=?",
            (_t.time() - 50 * 60, attempt["id"]),
        )
    monkeypatch.setattr(review, "REFETCH_PROGRESS_REMIND_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_STALE_TIMEOUT_MINUTES", 0)

    acted = await review.monitor_refetch_progress(AsyncMock())
    assert acted == 0
    repo = RefetchRepository()
    assert (await repo.find_by_request_id(attempt["request_id"]))["state"] == "admitted"


# ---------------------------------------------------------------------------
# Replacement success receipt in the review group
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submission_receipt_notifies_group_on_replacement(refetch_db, monkeypatch):
    import utils.api_server as api_server

    async def _authenticate(bearer: str):
        return {"id": 1, "telegram_user_id": 7, "name": "pixivflow"} if bearer else None

    monkeypatch.setattr(api_server, "authenticate", _authenticate)
    monkeypatch.setattr(api_server, "API_REVIEW_REQUIRED", True)
    monkeypatch.setattr(api_server, "REVIEW_CHAT_ID", -100123)
    source_id = await _insert_review(pixiv_id="111", target_id="target-a")
    attempt, _ = await _attempt(
        chain_id="chain-%d" % source_id, source_review_id=source_id, callback_id=9001)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")
    # The replacement already landed: attempt is terminal 'replaced'.
    await RefetchRepository().mark_replaced(attempt["request_id"], "222")

    queue_mock = AsyncMock(return_value={
        "status": "pending_review", "review_id": 999, "media_count": 1,
        "document_count": 0, "reused": False,
    })
    monkeypatch.setattr("handlers.review.queue_review_from_file_ids", queue_mock)
    application = MagicMock()
    application.bot = AsyncMock()
    app = web.Application()
    api_server.add_api_routes(app, application)
    client = await _client(app)
    try:
        resp = await client.post(
            "/api/v1/submissions",
            headers={"Authorization": "Bearer tp_ok"},
            json={
                "media": [{"type": "photo", "file_id": "AAA"}],
                "tags": "Pixiv",
                "target_id": "target-a",
                "work_type": "illustration",
                "pixiv_id": "222",
                "refetch_request_id": attempt["request_id"],
            },
        )
        assert resp.status == 201
    finally:
        await client.close()

    text = application.bot.send_message.await_args.kwargs["text"]
    assert "重抓成功" in text and "#999" in text


@pytest.mark.asyncio
async def test_submission_receipt_silent_for_active_or_reused(refetch_db, monkeypatch):
    import utils.api_server as api_server

    async def _authenticate(bearer: str):
        return {"id": 1, "telegram_user_id": 7, "name": "pixivflow"} if bearer else None

    monkeypatch.setattr(api_server, "authenticate", _authenticate)
    monkeypatch.setattr(api_server, "API_REVIEW_REQUIRED", True)
    monkeypatch.setattr(api_server, "REVIEW_CHAT_ID", -100123)
    source_id = await _insert_review(pixiv_id="111", target_id="target-a")
    attempt, _ = await _attempt(
        chain_id="chain-%d" % source_id, source_review_id=source_id, callback_id=9001)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")

    queue_mock = AsyncMock(return_value={
        "status": "pending_review", "review_id": 999, "media_count": 1,
        "document_count": 0, "reused": False,
    })
    monkeypatch.setattr("handlers.review.queue_review_from_file_ids", queue_mock)
    application = MagicMock()
    application.bot = AsyncMock()
    app = web.Application()
    api_server.add_api_routes(app, application)
    client = await _client(app)
    try:
        resp = await client.post(
            "/api/v1/submissions",
            headers={"Authorization": "Bearer tp_ok"},
            json={
                "media": [{"type": "photo", "file_id": "AAA"}],
                "tags": "Pixiv",
                "target_id": "target-a",
                "work_type": "illustration",
                "pixiv_id": "222",
                "refetch_request_id": attempt["request_id"],
            },
        )
        assert resp.status == 201
    finally:
        await client.close()
    # attempt still active (not replaced) → no success receipt.
    application.bot.send_message.assert_not_awaited()
