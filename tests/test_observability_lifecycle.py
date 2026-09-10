"""Lifecycle audit events: submission duplicates, approve/publish, reconciler,
retention, CLI inspect, and the router /version + /health fields."""

import asyncio
import json
import os
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from database import db_manager
from handlers import review
from telepost.observability import audit as audit_mod


@pytest.fixture
async def review_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "reviews.db")
    monkeypatch.setattr(db_manager, "DB_PATH", db_path)
    monkeypatch.setattr(review, "REVIEW_CHAT_ID", -100123)
    monkeypatch.setattr(review, "ADMIN_IDS", [123456789])
    await db_manager.init_db()
    return db_path


def _photo_message(message_id=10, file_id="STAGED_PHOTO"):
    message = MagicMock()
    message.message_id = message_id
    message.photo = [MagicMock(file_id=file_id)]
    message.video = None
    message.animation = None
    message.audio = None
    message.document = None
    return message


def _callback_update(data, user_id=123456789):
    update = MagicMock()
    update.effective_user.id = user_id
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def _events(review_id):
    return audit_mod.list_events(review_id=review_id, limit=100)


@pytest.mark.asyncio
async def test_submission_duplicate_emitted_on_idempotent_replay(review_db):
    from telepost.application.review_queue import ReviewQueueService
    from telepost.storage.sqlite.reviews import NewReview, ReviewRepository

    repo = ReviewRepository()
    rid = await repo.insert(NewReview(
        idempotency_key="api:7:dup", source="api", user_id=7,
        username="flow", title="", tags="#x", note="", link="",
        anonymous=False, spoiler=False,
        media=[{"type": "photo", "file_id": "P"}], documents=[],
        review_chat_id="-100123", review_message_ids=[10],
    ))
    await repo.update_staged(rid, media=[{"type": "photo", "file_id": "P"}],
                             documents=[], preview_message_ids=[10])
    await repo.finalize_control(rid, 11)

    class Stager:
        async def notify_reused(self, row):
            self.row = row

        async def cleanup_files(self, files):
            pass

    command = review.QueueCommand(
        user_id=7, username="flow", tags="#x", title="", note="", link="",
        anonymous=False, spoiler=False, source="api",
        idempotency_key="api:7:dup", source_ref='{"executionId":"exec-dup"}',
        review_chat_id="-100123",
    )
    result = await ReviewQueueService().enqueue(command, Stager(),
                                                media=[{"x": 1}], documents=[])
    assert result["reused"] is True

    events = await _events(rid)
    names = [e["event"] for e in events]
    assert "submission.duplicate" in names
    dup = next(e for e in events if e["event"] == "submission.duplicate")
    assert audit_mod.detail_value(dup["detail"])["reused_id"] == rid
    assert dup["execution_id"] == "exec-dup"


@pytest.mark.asyncio
async def test_double_approve_audit_events(review_db):
    """review.approved exactly once; publish.completed once; the second click is
    suppressed as publish.duplicate_suppressed."""
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    control = MagicMock()
    control.message_id = 11
    bot.send_message.return_value = control
    queued = await review.queue_review_from_file_ids(
        bot,
        [{"type": "photo", "file_id": "ORIGINAL"}],
        [],
        tags="#pixiv", user_id=7, username="flow",
        idempotency_key="pixiv:audit",
    )
    rid = queued["review_id"]

    update = _callback_update(f"review_approve:{rid}")
    context = MagicMock()
    context.bot = bot
    publish_result = {
        "status": "published", "message_id": 99,
        "link": "https://t.me/c/1/99",
        "media_count": 1, "document_count": 0,
    }
    with patch("handlers.review.publish_from_file_ids",
               AsyncMock(return_value=publish_result)):
        await review.approve_review(update, context)
        await review.approve_review(update, context)

    # Let fire-and-forget audit tasks drain.
    await asyncio.sleep(0)

    events = await _events(rid)
    names = [e["event"] for e in events]
    assert names.count("review.approved") == 1
    assert names.count("publish.completed") == 1
    assert names.count("publish.duplicate_suppressed") == 1
    # Queue lifecycle present as well.
    for expected in ("review.created", "review.preview_staged",
                     "review.control_created", "review.pending"):
        assert expected in names


@pytest.mark.asyncio
async def test_approve_failure_records_typed_error_class(review_db):
    from telegram.error import TimedOut

    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    bot.send_message.return_value = MagicMock(message_id=11)
    queued = await review.queue_review_from_file_ids(
        bot, [{"type": "photo", "file_id": "X"}], [],
        tags="#x", user_id=7, username="u", idempotency_key="k-fail",
    )
    rid = queued["review_id"]
    with patch.object(
        review.review_service, "_publish",
        AsyncMock(side_effect=TimedOut("lost")),
    ):
        with pytest.raises(Exception):
            await review.review_service.approve(bot, rid, actor=123456789)
    await asyncio.sleep(0)
    events = await _events(rid)
    failed = [e for e in events if e["event"] == "review.failed"]
    assert failed and failed[0]["error_class"] == "network_timeout"


@pytest.mark.asyncio
async def test_reconciler_emits_review_recreated_event(review_db):
    from telepost.application.review_queue import ReviewQueueService
    from telepost.storage.sqlite.reviews import NewReview, ReviewRepository

    repo = ReviewRepository()
    rid = await repo.insert(NewReview(
        idempotency_key="api:7:rec", source="api", user_id=7,
        username="flow", title="", tags="#x", note="", link="",
        anonymous=False, spoiler=False,
        media=[{"type": "photo", "file_id": "P"}], documents=[],
        review_chat_id="-100123", review_message_ids=[10],
        status="failed",
    ))

    class Stager:
        async def send_control_message_id(self, **kwargs):
            return 22

        async def delete_preview_messages(self, ids):
            pass

        async def cleanup_files(self, files):
            pass

    repaired = await ReviewQueueService().reconcile_incomplete(
        Stager(), stale_seconds=0
    )
    assert repaired == 1
    events = await _events(rid)
    rec = [e for e in events if e["event"] == "review.reconciled"]
    assert len(rec) == 1
    assert audit_mod.detail_value(rec[0]["detail"])["action"] \
        == "control_message_recreated"
    assert rec[0]["actor"] == "system_reconciler"


@pytest.mark.asyncio
async def test_reconciler_orphan_preview_and_manual_intervention(review_db):
    from telepost.application.review_queue import ReviewQueueService
    from telepost.storage.sqlite.reviews import NewReview, ReviewRepository

    repo = ReviewRepository()

    # Orphan preview: no media/documents but preview ids exist.
    orphan = await repo.insert(NewReview(
        idempotency_key="api:7:orphan", source="api", user_id=7,
        username="flow", title="", tags="#x", note="", link="",
        anonymous=False, spoiler=False, media=[], documents=[],
        review_chat_id="-100123", review_message_ids=[55], status="preparing",
    ))

    # Control recreation itself fails -> manual intervention.
    manual = await repo.insert(NewReview(
        idempotency_key="api:7:manual", source="api", user_id=7,
        username="flow", title="", tags="#x", note="", link="",
        anonymous=False, spoiler=False,
        media=[{"type": "photo", "file_id": "P"}], documents=[],
        review_chat_id="-100123", review_message_ids=[10], status="failed",
    ))

    class Stager:
        async def send_control_message_id(self, **kwargs):
            if kwargs["review_id"] == manual:
                raise RuntimeError("telegram down")
            return 22

        async def delete_preview_messages(self, ids):
            pass

        async def cleanup_files(self, files):
            pass

    await ReviewQueueService().reconcile_incomplete(Stager(), stale_seconds=0)

    orphan_events = await _events(orphan)
    actions = [audit_mod.detail_value(e["detail"])["action"]
               for e in orphan_events if e["event"] == "review.reconciled"]
    assert "orphan_preview_removed" in actions

    manual_events = await _events(manual)
    rec = [e for e in manual_events if e["event"] == "review.reconciled"]
    assert rec[0]["error_class"] == "reconciliation_failed"
    assert audit_mod.detail_value(rec[0]["detail"])["action"] \
        == "manual_intervention_required"


@pytest.mark.asyncio
async def test_audit_retention_keeps_open_review_events(monkeypatch, tmp_path):
    db_path = str(tmp_path / "ret.db")
    monkeypatch.setattr(db_manager, "DB_PATH", db_path)
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "30")
    await db_manager.init_db()

    now = __import__("time").time()
    old = now - 40 * 86400
    from telepost.storage.sqlite.reviews import NewReview, ReviewRepository
    repo = ReviewRepository()
    open_id = await repo.insert(NewReview(
        idempotency_key="api:1:open", source="api", user_id=1,
        username="u", title="", tags="#x", note="", link="",
        anonymous=False, spoiler=False, media=[], documents=[],
        review_chat_id="c", review_message_ids=[], status="pending",
    ))
    closed_id = await repo.insert(NewReview(
        idempotency_key="api:1:closed", source="api", user_id=1,
        username="u", title="", tags="#x", note="", link="",
        anonymous=False, spoiler=False, media=[], documents=[],
        review_chat_id="c", review_message_ids=[], status="published",
    ))
    async with db_manager.get_db() as conn:
        await conn.execute(
            "INSERT INTO audit_events (ts,event,review_id) VALUES (?,?,?)",
            (old, "x", open_id),
        )
        await conn.execute(
            "INSERT INTO audit_events (ts,event,review_id) VALUES (?,?,?)",
            (old, "x", closed_id),
        )
        await conn.execute(
            "INSERT INTO audit_events (ts,event,review_id) VALUES (?,?,?)",
            (old, "x", None),
        )

    await db_manager.cleanup_old_data()

    async with db_manager.get_db() as conn:
        remaining = [
            row[0] for row in await (await conn.execute(
                "SELECT review_id FROM audit_events"
            )).fetchall()
        ]
    assert remaining == [open_id]


def test_cli_reviews_inspect_on_seeded_db(tmp_path, monkeypatch):
    db_path = tmp_path / "submissions.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    # Re-import settings/DB_PATH for the CLI resolution helper.
    import importlib
    import config.settings as settings_mod
    importlib.reload(settings_mod)
    monkeypatch.setattr(db_manager, "DB_PATH", str(db_path))
    import asyncio as _aio

    async def seed():
        await db_manager.init_db()
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "INSERT INTO pending_reviews (idempotency_key, source, status, "
                "user_id, review_chat_id, media_json, documents_json, "
                "review_message_ids, created_at, updated_at) "
                "VALUES ('api:1:k','api','pending',1,'c','[]','[]','[]',?,?)",
                (1.0, 1.0),
            )
            rid = cur.lastrowid
            await conn.execute(
                "INSERT INTO audit_events (ts,event,review_id,actor) "
                "VALUES (?,?,?,?)", (2.0, "review.created", rid, "api"),
            )
            await conn.execute(
                "INSERT INTO delivery_ledger (idempotency_key, target_id, "
                "pixiv_id, work_type, status, message_id, related_message_ids, "
                "user_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"review:{rid}:api:1:k", "", "", "", "published", 5, "[]", 1, 2.0),
            )
            return rid

    rid = _aio.new_event_loop().run_until_complete(seed())

    from telepost.observability import cli
    result = subprocess.run(
        [sys.executable, "-m", "telepost.observability.cli",
         "reviews", "inspect", str(rid)],
        cwd=__import__("telepost").__path__[0].rsplit("/telepost", 1)[0],
        capture_output=True, text=True,
        env={**os.environ, "DB_PATH": str(db_path), "TOKEN": "x",
             "CHANNEL_ID": "@c", "OWNER_ID": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert "review.created" in result.stdout
    assert f"review:{rid}:api:1:k" in result.stdout
    assert "pending_reviews" in result.stdout or "idempotency_key" in result.stdout


def test_router_version_and_health_fields():
    import run as run_mod
    from aiohttp.test_utils import TestClient, TestServer
    from aiohttp import web
    import aiohttp

    async def go():
        app = run_mod.build_router_app([])
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            version = await (await client.get("/version")).json()
            assert version["service"] == "telepost"
            assert version["version"]
            assert "commit" in version
            health = await (await client.get("/health")).json()
            assert health["version"] == version["version"]
            assert health["commit"] == version["commit"]
        finally:
            await client.close()

    asyncio.new_event_loop().run_until_complete(go())
