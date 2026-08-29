"""API/chat review queue and approval callbacks."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from database import db_manager
from handlers import review


def _photo_message(message_id=10, file_id="STAGED_PHOTO"):
    message = MagicMock()
    message.message_id = message_id
    message.photo = [MagicMock(file_id=file_id)]
    message.video = None
    message.animation = None
    message.audio = None
    message.document = None
    return message


def test_file_id_extraction_accepts_tuple_photo_sizes():
    from handlers.publish import _file_id_of

    message = _photo_message()
    message.photo = (
        MagicMock(file_id="SMALL_PHOTO"),
        MagicMock(file_id="LARGE_PHOTO"),
    )

    assert _file_id_of(message) == "LARGE_PHOTO"


def _callback_update(data, user_id=123456789):
    update = MagicMock()
    update.effective_user.id = user_id
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


@pytest.fixture
async def review_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "reviews.db")
    monkeypatch.setattr(db_manager, "DB_PATH", db_path)
    monkeypatch.setattr(review, "REVIEW_CHAT_ID", -100123)
    monkeypatch.setattr(review, "ADMIN_IDS", [123456789])
    await db_manager.init_db()
    return db_path


@pytest.mark.asyncio
async def test_file_id_submission_is_durable_and_idempotent(review_db):
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    control = MagicMock()
    control.message_id = 11
    bot.send_message.return_value = control

    first = await review.queue_review_from_file_ids(
        bot,
        [{"type": "photo", "file_id": "ORIGINAL"}],
        [],
        tags="#pixiv",
        title="Artwork",
        link="https://www.pixiv.net/artworks/123",
        user_id=7,
        username="pixivflow",
        idempotency_key="pixiv:123",
    )
    second = await review.queue_review_from_file_ids(
        bot,
        [{"type": "photo", "file_id": "ORIGINAL"}],
        [],
        tags="#pixiv",
        user_id=7,
        username="pixivflow",
        idempotency_key="pixiv:123",
    )

    assert first["status"] == "pending_review"
    assert second["review_id"] == first["review_id"]
    assert bot.send_photo.await_count == 1

    async with db_manager.get_db() as conn:
        cursor = await conn.execute("SELECT * FROM pending_reviews")
        row = await cursor.fetchone()
    assert row["status"] == "pending"
    assert json.loads(row["media_json"])[0]["file_id"] == "STAGED_PHOTO"
    assert row["control_message_id"] == 11


@pytest.mark.asyncio
async def test_failed_local_staging_deletes_uploaded_preview(review_db, tmp_path):
    source = tmp_path / "preview.png"
    source.write_bytes(b"not-a-real-image")
    message = _photo_message(message_id=77)
    message.photo = ()
    bot = AsyncMock()
    bot.send_photo.return_value = message

    with pytest.raises(RuntimeError, match="file_id"):
        await review.queue_review_from_files(
            bot,
            [{"kind": "photo", "path": str(source), "filename": "preview.png"}],
            tags="#test",
            user_id=7,
            username="tester",
        )

    bot.delete_message.assert_awaited_once_with(chat_id=-100123, message_id=77)
    assert not source.exists()


@pytest.mark.asyncio
async def test_owner_can_approve_once(review_db):
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    control = MagicMock()
    control.message_id = 11
    bot.send_message.return_value = control
    queued = await review.queue_review_from_file_ids(
        bot,
        [{"type": "photo", "file_id": "ORIGINAL"}],
        [],
        tags="#pixiv",
        user_id=7,
        username="pixivflow",
        idempotency_key="pixiv:approve",
    )

    update = _callback_update(f"review_approve:{queued['review_id']}")
    context = MagicMock()
    context.bot = bot
    publish_result = {
        "status": "published",
        "message_id": 99,
        "link": "https://t.me/c/1/99",
        "media_count": 1,
        "document_count": 0,
    }
    with patch("handlers.review.publish_from_file_ids", AsyncMock(return_value=publish_result)) as publish:
        await review.approve_review(update, context)
        await review.approve_review(update, context)

    publish.assert_awaited_once()
    async with db_manager.get_db() as conn:
        cursor = await conn.execute(
            "SELECT status, published_message_id FROM pending_reviews WHERE id=?",
            (queued["review_id"],),
        )
        row = await cursor.fetchone()
    assert row["status"] == "published"
    assert row["published_message_id"] == 99


@pytest.mark.asyncio
async def test_chat_submission_enters_review_and_notifies_after_approval(
    review_db, monkeypatch
):
    from handlers import publish

    async with db_manager.get_db() as conn:
        await conn.execute(
            """
            INSERT INTO submissions (
                user_id, timestamp, mode, image_id, document_id, tags, link,
                title, note, spoiler, anonymous, username
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                7,
                1234.5,
                "media",
                json.dumps(["photo:ORIGINAL"]),
                json.dumps([]),
                "#chat",
                "",
                "Chat artwork",
                "",
                "false",
                "false",
                "chat_user",
            ),
        )

    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    control = MagicMock()
    control.message_id = 11
    bot.send_message.return_value = control

    update = _callback_update("publish", user_id=7)
    update.effective_user.username = "chat_user"
    context = MagicMock()
    context.bot = bot
    monkeypatch.setattr(publish, "CHAT_REVIEW_REQUIRED", True)

    await publish.publish_submission(update, context)

    assert bot.send_photo.await_args.kwargs["chat_id"] == -100123
    assert "进入审核队列" in update.callback_query.edit_message_text.await_args.args[0]
    async with db_manager.get_db() as conn:
        cursor = await conn.execute("SELECT * FROM pending_reviews")
        row = await cursor.fetchone()
        cursor = await conn.execute("SELECT * FROM submissions WHERE user_id=7")
        session = await cursor.fetchone()
    assert row["source"] == "chat"
    assert session is None

    bot.send_message.reset_mock()
    approval = _callback_update(f"review_approve:{row['id']}")
    with patch(
        "handlers.review.publish_from_file_ids",
        AsyncMock(return_value={
            "status": "published",
            "message_id": 99,
            "link": "https://t.me/test/99",
        }),
    ):
        await review.approve_review(approval, context)
    bot.send_message.assert_awaited_once_with(
        chat_id=7,
        text="✅ 你的投稿已通过审核并发布到频道。\nhttps://t.me/test/99",
    )


@pytest.mark.asyncio
async def test_non_admin_cannot_reject(review_db):
    update = _callback_update("review_reject:1", user_id=999)
    context = MagicMock()
    context.bot = AsyncMock()
    await review.reject_review(update, context)
    update.callback_query.answer.assert_awaited_once_with(
        text="你没有审核权限", show_alert=True
    )


@pytest.mark.asyncio
async def test_single_file_id_uses_send_photo_not_media_group(monkeypatch):
    from handlers import publish

    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message(message_id=55, file_id="PHOTO")
    monkeypatch.setattr(publish, "save_published_post", AsyncMock())

    result = await publish.publish_from_file_ids(
        bot,
        [{"type": "photo", "file_id": "PHOTO"}],
        [],
        tags="#pixiv",
        user_id=7,
        username="pixivflow",
    )

    assert result["message_id"] == 55
    bot.send_photo.assert_awaited_once()
    bot.send_media_group.assert_not_called()
