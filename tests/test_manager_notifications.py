"""Durable manager-new-submission notifications."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from database import db_manager
from telepost.application.review_queue import QueueCommand, ReviewQueueService
from telepost.application.submitter_notify import (
    ManagerAcceptanceContext,
    ManagerNotifyService,
    flush_manager_notifications,
)


class Stager:
    async def stage_file_ids(self, media, documents, **_kwargs):
        return media, documents, [10]

    async def send_control_message_id(self, **_kwargs):
        return 11

    async def delete_preview_messages(self, _ids):
        return None

    async def notify_reused(self, _row):
        return None

    async def cleanup_files(self, _files):
        return None


@pytest.fixture
async def notification_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "notifications.db"))
    import config.settings as settings
    monkeypatch.setattr(settings, "NOTIFY_OWNER", True)
    monkeypatch.setattr(settings, "OWNER_ID", 900)
    await db_manager.init_db()


async def _manager_rows():
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT * FROM submitter_notifications "
            "WHERE kind='manager_accepted' ORDER BY id"
        )
        return await cur.fetchall()


@pytest.mark.asyncio
async def test_review_acceptance_enqueues_once_after_durable_staging(notification_db):
    command = QueueCommand(
        user_id=42, username="alice", tags="#x", title="T", note="", link="",
        anonymous=False, spoiler=False, idempotency_key="chat:42:k",
        review_chat_id="-100", source="chat", submitter_user_id=42,
        submitter_username="alice", submitter_display_name="Alice Zhang",
    )
    service = ReviewQueueService()
    first = await service.enqueue(
        command, Stager(), media=[{"type": "photo", "file_id": "P"}], documents=[]
    )
    second = await service.enqueue(
        command, Stager(), media=[{"type": "photo", "file_id": "P"}], documents=[]
    )

    rows = await _manager_rows()
    assert first["status"] == "pending_review" and second["reused"] is True
    assert len(rows) == 1
    assert rows[0]["idempotency_key"] == (
        f"submission:review:{first['review_id']}:manager-accepted"
    )


@pytest.mark.asyncio
async def test_direct_publication_success_enqueues_manager_once(notification_db, monkeypatch):
    import config.settings as settings
    from utils.api_server import _maybe_notify_direct_human

    monkeypatch.setattr(settings, "SUBMITTER_PUBLISH_NOTIFY", "published")
    result = {"status": "published", "message_id": 88}
    await _maybe_notify_direct_human(
        result, 42, "alice", "Alice Zhang", False, "api_direct"
    )
    await _maybe_notify_direct_human(
        {**result, "reused": True}, 42, "alice", "Alice Zhang", False,
        "api_direct",
    )

    assert len(await _manager_rows()) == 1
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) AS c FROM submitter_notifications "
            "WHERE kind='api_direct'"
        )
        assert (await cur.fetchone())["c"] == 1


@pytest.mark.asyncio
async def test_service_and_manager_self_submission_are_skipped(notification_db):
    service = ManagerNotifyService()
    assert await service.notify_accepted(ManagerAcceptanceContext(
        logical_submission_id="service", submitter_user_id=0,
    )) is False
    assert await service.notify_accepted(ManagerAcceptanceContext(
        logical_submission_id="self", submitter_user_id=900,
    )) is False
    assert await _manager_rows() == []


@pytest.mark.asyncio
async def test_same_logical_submission_is_not_realerted_for_later_generations(
    notification_db,
):
    service = ManagerNotifyService()
    context = ManagerAcceptanceContext(
        logical_submission_id="review:12", submitter_user_id=42,
        submitter_username="alice",
    )
    assert await service.notify_accepted(context) is True
    # Refetch/editorial keep the same logical submission and therefore the
    # same durable key if a caller is replayed accidentally.
    assert await service.notify_accepted(context) is False
    assert len(await _manager_rows()) == 1


@pytest.mark.asyncio
async def test_manager_identity_entity_targets_explicit_submitter(notification_db):
    service = ManagerNotifyService()
    await service.notify_accepted(ManagerAcceptanceContext(
        logical_submission_id="human", submitter_user_id=42,
        submitter_username="alice", submitter_display_name="Alice Zhang",
        review_id=7,
    ))
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=99)

    assert await flush_manager_notifications(bot) == 1
    kwargs = bot.send_message.await_args.kwargs
    assert "投稿人：@alice" in kwargs["text"]
    assert len(kwargs["entities"]) == 1
    entity = kwargs["entities"][0]
    assert entity.type == "text_link"
    assert entity.url == "tg://user?id=42"


@pytest.mark.asyncio
async def test_no_username_uses_clickable_display_name(notification_db):
    await ManagerNotifyService().notify_accepted(ManagerAcceptanceContext(
        logical_submission_id="display", submitter_user_id=43,
        submitter_display_name="Alice Zhang",
    ))
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=100)

    await flush_manager_notifications(bot)
    kwargs = bot.send_message.await_args.kwargs
    assert "投稿人：Alice Zhang" in kwargs["text"]
    assert "user43" not in kwargs["text"]
    assert kwargs["entities"][0].url == "tg://user?id=43"


@pytest.mark.asyncio
async def test_anonymous_alert_hides_identity_but_keeps_owner(notification_db):
    await ManagerNotifyService().notify_accepted(ManagerAcceptanceContext(
        logical_submission_id="anon", submitter_user_id=44,
        submitter_username="secret", submitter_display_name="Secret Name",
        anonymous=True,
    ))
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=101)

    await flush_manager_notifications(bot)
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["text"] == "📨 新匿名投稿"
    assert "entities" not in kwargs
    assert "secret" not in kwargs["text"].lower()


@pytest.mark.asyncio
async def test_delivery_failure_stays_pending_and_retries_once(notification_db):
    await ManagerNotifyService().notify_accepted(ManagerAcceptanceContext(
        logical_submission_id="retry", submitter_user_id=45,
    ))
    bot = AsyncMock()
    bot.send_message.side_effect = [RuntimeError("down"), SimpleNamespace(message_id=102)]

    assert await flush_manager_notifications(bot) == 0
    assert (await _manager_rows())[0]["state"] == "pending"
    assert await flush_manager_notifications(bot) == 1
    assert (await _manager_rows())[0]["state"] == "sent"
    assert bot.send_message.await_count == 2
