"""Durable webhook inbox: crash boundaries, restart recovery, conversation
resumption and per-update media dedup (§durable-inbox).

The restart boundaries are exercised against real SQLite (fresh connection =
a new process) — not mocks returning success. What is mocked is only the
Telegram wire (the recording bot), never the persistence or queue machinery.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from telegram import Chat, Message, MessageEntity, PhotoSize, Update, User
from telegram.ext import Application, ConversationHandler

from database import db_manager
from handlers import review  # noqa: F401  (import ordering side effects)
from main import build_persistence, setup_application
from models.state import STATE
from telepost.storage.sqlite.inbox import InboxRepository
from telepost.telegram.inbox_worker import UpdateInboxWorker
from utils.submission import append_entry, create_session, get_session

NOW = 1_800_000_000.0
SECRET = "inbox-test-secret"


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "bot.db"))
    await db_manager.init_db()
    return InboxRepository()


def _payload(update_id, text=None, *, photo=False):
    message = {
        "message_id": update_id, "date": 0,
        "chat": {"id": 42, "type": "private"},
        "from": {"id": 42, "is_bot": False, "first_name": "T"},
    }
    if text:
        message["text"] = text
        if text.startswith("/"):
            message["entities"] = [{
                "type": "bot_command", "offset": 0,
                "length": len(text.split()[0]),
            }]
    if photo:
        message["photo"] = [{
            "file_id": f"ph{update_id}", "file_unique_id": f"u{update_id}",
            "width": 10, "height": 10,
        }]
    return {"update_id": update_id, "message": message}


class _RecordingBot:
    def __init__(self):
        self.id = 777
        self.username = self.name = "testbot"
        self.outbox = []
        self._mid = 100

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    def _message(self):
        self._mid += 1
        message = MagicMock()
        message.message_id = self._mid
        return message

    async def send_message(self, chat_id, text, **kwargs):
        self.outbox.append(text)
        return self._message()

    async def send_photo(self, chat_id, photo, **kwargs):
        self.outbox.append("<photo>")
        return self._message()

    async def edit_message_text(self, text, **kwargs):
        self.outbox.append(text)
        return self._message()

    async def answer_callback_query(self, **kwargs):
        return True


@pytest.fixture
def no_blacklist(monkeypatch):
    import handlers.mode_selection as mode_selection
    monkeypatch.setattr(mode_selection, "is_blacklisted", lambda _uid: False)


async def _build_app(tmp_path, *, pickle_path):
    bot = _RecordingBot()
    persistence = build_persistence(str(pickle_path))
    application = Application.builder().bot(bot).persistence(persistence).build()
    setup_application(application)
    await application.initialize()
    await application.start()
    return application, bot


@pytest.mark.asyncio
async def test_live_worker_does_not_block_reject_or_session_persistence(
    store, tmp_path, monkeypatch, no_blacklist,
):
    pickle_path = tmp_path / "persistence.pickle"
    application, bot = await _build_app(tmp_path, pickle_path=pickle_path)
    worker = UpdateInboxWorker(application, repo=store, poll_interval=3600)
    application.bot_data["update_inbox_worker"] = worker
    worker.start()
    reject = AsyncMock()
    monkeypatch.setattr(review, "ADMIN_IDS", [42])
    monkeypatch.setattr(review.review_service, "reject", reject)
    try:
        application.user_data[42]["test_state"] = "retained"
        application.chat_data[42]["test_state"] = "retained"
        await worker.receive(1, _payload(1, "/submit"))
        await worker.receive(2, {
            "update_id": 2,
            "callback_query": {
                "id": "reject-test", "chat_instance": "test",
                "from": {"id": 42, "is_bot": False, "first_name": "T"},
                "message": _payload(2, "review card")["message"],
                "data": "review_reject:202",
            },
        })
        assert await worker.drain() == 2
        assert (await store.counts())["done"]["n"] == 2
        reject.assert_awaited_once_with(bot, 202, actor=42, source="telegram")
        assert any("202" in text and "已拒绝" in text for text in bot.outbox)

        restored = build_persistence(str(pickle_path))
        assert await restored.get_bot_data() == {}
        assert (await restored.get_chat_data())[42]["test_state"] == "retained"
        assert (await restored.get_user_data())[42]["test_state"] == "retained"
        conv = next(h for h in application.handlers[2]
                    if isinstance(h, ConversationHandler))
        assert (await restored.get_conversations(conv.name))[(42, 42)] == STATE["UPLOAD"]
    finally:
        await worker.stop()
        await application.stop()
        await application.shutdown()


# ---- receipt & dedup --------------------------------------------------------


@pytest.mark.asyncio
async def test_receive_persists_and_duplicate_never_requeues(store):
    assert await store.receive(7, json.dumps(_payload(7)))
    assert not await store.receive(7, json.dumps(_payload(7)))  # duplicate delivery
    async with db_manager.get_db() as conn:
        n = (await (await conn.execute(
            "SELECT COUNT(*) FROM webhook_inbox")).fetchone())[0]
    assert n == 1


@pytest.mark.asyncio
async def test_webhook_durable_path_200_only_after_commit(store, tmp_path,
                                                          monkeypatch):
    application = MagicMock()
    application.bot = None
    application.bot_data = {}
    application.update_queue.put = AsyncMock()
    worker = UpdateInboxWorker(application, repo=store)
    application.bot_data["update_inbox_worker"] = worker

    from utils.webhook_server import WebhookServer
    server = WebhookServer(application, 0, "/webhook/bot1", SECRET)
    server.web_app = web.Application()
    server.web_app.router.add_post("/webhook/bot1", server.webhook_handler)
    runner = web.AppRunner(server.web_app)
    await runner.setup()
    client = TestClient(TestServer(server.web_app))
    await client.start_server()
    try:
        resp = await client.post(
            "/webhook/bot1", json=_payload(11),
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
        assert resp.status == 200
        row = await store.claim_next_due(now=NOW, lease_seconds=60)
        assert row is not None and row["update_id"] == 11
        await store.mark_done(11, now=NOW)

        # Telegram redelivers the acknowledged update: still 200, no requeue.
        resp = await client.post(
            "/webhook/bot1", json=_payload(11),
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
        assert resp.status == 200
        assert await store.claim_next_due(now=NOW, lease_seconds=60) is None
    finally:
        await client.close()
        await runner.cleanup()
    application.update_queue.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_db_failure_returns_500_not_200(store, monkeypatch):
    async def _broken(self, update_id, payload_json, *, now=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr(InboxRepository, "receive", _broken)
    application = MagicMock()
    application.bot = None
    application.bot_data = {}
    worker = UpdateInboxWorker(application, repo=store)
    application.bot_data["update_inbox_worker"] = worker

    from utils.webhook_server import WebhookServer
    server = WebhookServer(application, 0, "/webhook/bot1", SECRET)
    server.web_app = web.Application()
    server.web_app.router.add_post("/webhook/bot1", server.webhook_handler)
    runner = web.AppRunner(server.web_app)
    await runner.setup()
    client = TestClient(TestServer(server.web_app))
    await client.start_server()
    try:
        resp = await client.post(
            "/webhook/bot1", json=_payload(12),
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
        assert resp.status == 500  # non-2xx → Telegram redelivers
    finally:
        await client.close()
        await runner.cleanup()


# ---- restart recovery -------------------------------------------------------


@pytest.mark.asyncio
async def test_crash_after_ack_before_processing_recovers_in_order(store):
    # Process died right after three 200s: rows committed, nothing processed.
    for uid in (31, 32, 33):
        await store.receive(uid, json.dumps(_payload(uid)))
    # A NEW process (fresh repo/worker) replays strictly by update_id.
    processed = []
    application = MagicMock()
    application.bot = None

    async def fake_process(update):
        processed.append(update.update_id)

    application.process_update = fake_process
    application.persistence = None
    worker = UpdateInboxWorker(application, repo=store)
    assert await worker.recover(now=NOW) == 0
    assert await worker.drain(now=NOW) == 3
    assert processed == [31, 32, 33]
    assert (await store.counts())["done"]["n"] == 3


@pytest.mark.asyncio
async def test_crash_mid_processing_reclaims_inflight_row(store):
    await store.receive(41, json.dumps(_payload(41)))
    row = await store.claim_next_due(now=NOW, lease_seconds=600)
    assert row["state"] == "processing"
    # Crash: the row stays 'processing' with a lease. A restarted process is
    # the single worker for this database, so recovery reclaims it outright.
    assert await store.recover_stale(now=NOW + 1) == 1
    application = MagicMock()
    application.bot = None
    application.persistence = None
    seen = []

    async def fake_process(update):
        seen.append(update.update_id)

    application.process_update = fake_process
    assert await UpdateInboxWorker(application, repo=store).drain(now=NOW + 2) == 1
    assert seen == [41]


@pytest.mark.asyncio
async def test_processing_failure_backs_off_then_goes_dead(store):
    await store.receive(51, json.dumps(_payload(51)))
    calls = []

    async def failing(update):
        calls.append(update.update_id)
        raise RuntimeError("transient")

    application = MagicMock()
    application.bot = None
    application.persistence = None
    application.process_update = failing
    worker = UpdateInboxWorker(application, repo=store, max_attempts=2)

    await worker.drain(now=NOW)
    assert (await store.counts())["pending"]["n"] == 1  # requeued with backoff
    async with db_manager.get_db() as conn:
        error = (await (await conn.execute(
            "SELECT last_error FROM webhook_inbox")).fetchone())["last_error"]
    assert error == "RuntimeError"
    # second (final) failure → dead, visible, never retried
    await worker.drain(now=NOW + 9999)
    assert (await store.counts())["dead"]["n"] == 1
    assert await worker.drain(now=NOW + 99999) == 0
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_done_is_marked_only_after_persistence_flush(store, tmp_path,
                                                           monkeypatch, no_blacklist):
    application, bot = await _build_app(
        tmp_path, pickle_path=tmp_path / "persistence.pickle")
    try:
        events = []

        class _AppProxy:
            """Application.update_persistence is read-only; route the worker
            through a spy that records flush-vs-done ordering."""
            bot = application.bot
            persistence = True  # force the worker's flush path

            async def process_update(self, update):
                await application.process_update(update)

            async def update_persistence(self):
                events.append("persist")
                await application.update_persistence()

        class _OrderRepo(InboxRepository):
            async def mark_done(self, update_id, *, now):
                events.append("done")
                await super().mark_done(update_id, now=now)

        repo = _OrderRepo()
        worker = UpdateInboxWorker(_AppProxy(), repo=repo)
        await worker.receive(1, _payload(1, "/submit"))
        await worker.drain()
        assert events == ["persist", "done"]
    finally:
        await application.stop()
        await application.shutdown()


@pytest.mark.asyncio
async def test_backed_off_head_blocks_later_updates(store):
    """Strict head-of-line ordering: a retrying update blocks everything
    behind it, so a replayed old update can never execute after a newer
    /submit (which would bump the session epoch)."""
    await store.receive(81, json.dumps(_payload(81)))
    await store.receive(82, json.dumps(_payload(82)))
    attempts = []

    async def failing(update):
        attempts.append(update.update_id)
        raise RuntimeError("boom")

    application = MagicMock()
    application.bot = None
    application.persistence = None
    application.process_update = failing
    worker = UpdateInboxWorker(application, repo=store)

    await worker.drain(now=NOW)
    assert attempts == [81]                      # 81 fails once, backs off
    assert (await store.counts())["pending"]["n"] == 2
    # 82 is pending and due, but 81 is the not-yet-due head → nothing runs.
    assert await worker.drain(now=NOW + 1) == 0
    assert attempts == [81]
    # Once 81's backoff expires it runs (and fails) again — still before 82.
    await worker.drain(now=NOW + 9999)
    assert attempts == [81, 81]
    assert (await store.counts())["pending"]["n"] == 2


# ---- conversation resumption across a crash ---------------------------------


@pytest.mark.asyncio
async def test_submission_conversation_survives_restart(store, tmp_path,
                                                         monkeypatch, no_blacklist):
    db_file, pickle_file = tmp_path / "bot.db", tmp_path / "persistence.pickle"
    monkeypatch.setattr(db_manager, "DB_PATH", str(db_file))
    await db_manager.init_db()

    # --- process 1: /submit + one photo, then a hard crash (no graceful stop;
    # --- the pickle was already flushed synchronously before each done mark).
    application, bot = await _build_app(
        tmp_path, pickle_path=pickle_file)
    worker = UpdateInboxWorker(application, repo=store)
    await worker.receive(1, _payload(1, "/submit"))
    await worker.receive(2, _payload(2, photo=True))
    await worker.drain()
    assert any("请直接上传" in text or "Upload" in text for text in bot.outbox)
    session = await get_session(42)
    assert json.loads(session["image_id"]) == ["photo:ph2"]

    # --- process 2: same DB + same pickle file, fresh Application.
    application2, bot2 = await _build_app(
        tmp_path, pickle_path=pickle_file)
    try:
        conv = next(h for h in application2.handlers[2]
                    if isinstance(h, ConversationHandler))
        # Restored conversation state: still in UPLOAD, not restarted.
        assert conv._conversations[(42, 42)] == STATE["UPLOAD"]

        worker2 = UpdateInboxWorker(application2, repo=store)
        await worker2.receive(3, _payload(3, "/done_media"))
        bot2.outbox.clear()
        await worker2.drain()
        # The conversation continued: preview stage reached, media intact.
        assert conv._conversations[(42, 42)] == STATE["PREVIEW"]
        assert bot2.outbox, "done_media after restart must still produce a preview"
        session = await get_session(42)
        assert json.loads(session["image_id"]) == ["photo:ph2"]
    finally:
        await application2.stop()
        await application2.shutdown()
        await worker.stop()
        await application.stop()
        await application.shutdown()


# ---- media append dedup -----------------------------------------------------


@pytest.mark.asyncio
async def test_media_append_dedup_by_update_not_by_file_id(store):
    await create_session(42, "tester")
    # Same update replayed (crash recovery): applied exactly once.
    assert await append_entry(42, "photo:same", update_id=101) == 1
    assert await append_entry(42, "photo:same", update_id=101) == 1
    # The user deliberately re-sending the same photo: a NEW update — kept.
    assert await append_entry(42, "photo:same", update_id=102) == 2
    session = await get_session(42)
    assert json.loads(session["image_id"]) == ["photo:same", "photo:same"]


@pytest.mark.asyncio
async def test_replayed_old_session_update_does_not_pollute_new_session(store):
    await create_session(42, "tester")           # epoch 1
    assert await append_entry(42, "photo:old", update_id=201) == 1
    await create_session(42, "tester")           # epoch 2: new submission
    # A replay of the old epoch's update must not re-append into epoch 2.
    assert await append_entry(42, "photo:old", update_id=201) == 0
    session = await get_session(42)
    assert json.loads(session["image_id"]) == []


@pytest.mark.asyncio
async def test_replayed_media_update_after_crash_appends_once(store, no_blacklist,
                                                              tmp_path,
                                                              monkeypatch):
    application, bot = await _build_app(
        tmp_path, pickle_path=tmp_path / "persistence.pickle")
    try:
        worker = UpdateInboxWorker(application, repo=store)
        await worker.receive(1, _payload(1, "/submit"))
        await worker.receive(2, _payload(2, photo=True))
        await worker.drain()
        # The done mark was lost in a crash; recovery replays update 2.
        async with db_manager.get_db() as conn:
            await conn.execute(
                "UPDATE webhook_inbox SET state='pending', next_attempt_at=0, "
                "done_at=NULL WHERE update_id=2")
        await worker.drain()
        session = await get_session(42)
        assert json.loads(session["image_id"]) == ["photo:ph2"]
        # Replies are at-least-once: the replayed ack is allowed, the media is not.
    finally:
        await application.stop()
        await application.shutdown()


# ---- retention & isolation --------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_purges_done_but_never_pending_or_dead(store, monkeypatch):
    for uid in (61, 62):
        await store.receive(uid, json.dumps(_payload(uid)), now=NOW)
        await store.mark_done(uid, now=NOW)
    await store.receive(63, json.dumps(_payload(63)), now=NOW)
    await store.mark_dead(63, now=NOW, error="unparsable_payload")
    await store.receive(64, json.dumps(_payload(64)), now=NOW)

    monkeypatch.setattr(
        db_manager, "datetime", _FakeLateDatetime(NOW + 8 * 86400))
    await db_manager.cleanup_old_data()
    counts = await store.counts()
    assert "done" not in counts          # purged by retention
    assert counts["dead"]["n"] == 1      # dead rows kept 30 days
    assert counts["pending"]["n"] == 1   # unfinished never purged


class _FakeLateDatetime:
    """datetime.now() far in the future, to age out retention windows."""
    def __init__(self, late):
        self._late = late

    def now(self):
        import datetime as _dt
        return _dt.datetime.fromtimestamp(self._late)


@pytest.mark.asyncio
async def test_two_bots_keep_separate_inboxes(tmp_path, monkeypatch):
    from telepost.storage.sqlite.inbox import InboxRepository as Repo
    db_a, db_b = tmp_path / "bot1.db", tmp_path / "bot2.db"
    # Each bot process points DB_PATH at its own database and never sees the
    # other's rows: the same update_id is independent per bot.
    monkeypatch.setattr(db_manager, "DB_PATH", str(db_a))
    await db_manager.init_db()
    await Repo().receive(71, json.dumps(_payload(71)))

    monkeypatch.setattr(db_manager, "DB_PATH", str(db_b))
    await db_manager.init_db()
    await Repo().receive(71, json.dumps(_payload(71)))  # same update_id, other bot
    assert (await Repo().counts())["pending"]["n"] == 1

    monkeypatch.setattr(db_manager, "DB_PATH", str(db_a))
    assert (await Repo().counts())["pending"]["n"] == 1
    await Repo().mark_done(71, now=NOW)
    assert "done" in await Repo().counts()

    monkeypatch.setattr(db_manager, "DB_PATH", str(db_b))
    counts = await Repo().counts()
    assert counts["pending"]["n"] == 1 and "done" not in counts
