"""Regression: Telegram transport type never changes submission cardinality."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import json
import pytest

from database import db_manager
from handlers import publish
from telepost.application.delivery_planner import plan_review_media
from telepost.application.posts import PublishedPostInput, record_published_post
from telepost.application.publication import PublicationService, PublishCommand
from telepost.domain.delivery import MediaItem
from telepost.telegram.delivery.gateway import PTBTelegramDeliveryGateway


CAPTION = "🔗 链接： https://example.test\n🔖 标题： Test"


@pytest.fixture
async def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "publication.db"))
    await db_manager.init_db()


def _file_message(message_id, *, photo=False, doc=False, file_id="F"):
    message = MagicMock()
    message.message_id = message_id
    message.photo = [MagicMock(file_id=file_id, file_unique_id=f"U{file_id}")] if photo else None
    message.video = None
    message.animation = None
    message.audio = None
    message.document = MagicMock(file_id=file_id, file_unique_id=f"U{file_id}") if doc else None
    return message


def _messages(start, count, *, photo=False, doc=False):
    return [
        _file_message(start + index, photo=photo, doc=doc, file_id=f"F{start + index}")
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_document_only_publication_keeps_all_files_and_sends_final_text(isolated_db):
    bot = AsyncMock()
    bot.send_media_group.return_value = _messages(10, 4, doc=True)
    bot.send_message.return_value = _file_message(20)
    captured = {}

    async def record(command, result, media_count, document_count):
        captured.update(
            media_count=media_count,
            document_count=document_count,
            known=result.known_messages,
        )

    service = PublicationService(
        delivery=PTBTelegramDeliveryGateway(bot),
        link_builder=lambda message_id: f"https://example.test/{message_id}",
        record_post=record,
    )
    command = PublishCommand(
        chat_id="@channel",
        items=[
            MediaItem.file_id("document", f"D{index}", filename=f"file-{index}.png")
            for index in range(4)
        ],
        caption_data={"title": "Test", "tags": "#a", "link": "https://example.test",
                      "media_types": ["document"] * 4},
        user_id=1,
        idempotency_key="doc-only",
    )
    outcome = await service.publish(command)

    assert outcome.ok
    assert outcome.media_count == 0
    assert outcome.document_count == 4
    assert [m.kind.value for m in outcome.known_messages] == [
        "document", "document", "document", "document", "text"
    ]
    bot.send_media_group.assert_awaited_once()
    media = bot.send_media_group.await_args.kwargs["media"]
    assert len(media) == 4
    assert all(member.caption is None for member in media)
    bot.send_message.assert_awaited_once()
    final_text = bot.send_message.await_args.kwargs["text"]
    assert "Test" in final_text and "https://example.test" in final_text
    assert bot.send_message.await_args.kwargs["reply_to_message_id"] == 13
    assert captured["document_count"] == 4


@pytest.mark.asyncio
async def test_mixed_photo_and_document_publication_keeps_all_assets(isolated_db):
    bot = AsyncMock()
    bot.send_media_group.side_effect = [
        _messages(10, 2, photo=True),
        _messages(20, 2, doc=True),
    ]
    captured = {}

    async def record(command, result, media_count, document_count):
        captured.update(
            media_count=media_count,
            document_count=document_count,
            known=result.known_messages,
        )

    service = PublicationService(
        delivery=PTBTelegramDeliveryGateway(bot),
        link_builder=lambda message_id: f"https://example.test/{message_id}",
        record_post=record,
    )
    items = [
        MediaItem.file_id("photo", "P1"),
        MediaItem.file_id("document", "D1", filename="big-1.png"),
        MediaItem.file_id("photo", "P2"),
        MediaItem.file_id("document", "D2", filename="big-2.png"),
    ]
    outcome = await service.publish(PublishCommand(
        chat_id="@channel",
        items=items,
        caption_data={"title": "Mixed", "tags": "#a",
                      "media_types": ["photo", "document", "photo", "document"]},
        user_id=1,
        idempotency_key="mixed",
    ))

    assert outcome.ok
    assert outcome.media_count == 2
    assert outcome.document_count == 2
    assert [m.kind.value for m in outcome.known_messages] == [
        "photo", "photo", "document", "document"
    ]
    assert bot.send_message.await_count == 0
    visual = bot.send_media_group.await_args_list[0].kwargs["media"]
    documents = bot.send_media_group.await_args_list[1].kwargs["media"]
    assert visual[0].caption and visual[1].caption is None
    assert all(member.caption is None for member in documents)


@pytest.mark.asyncio
async def test_fifty_item_submission_is_packed_without_asset_loss(isolated_db):
    bot = AsyncMock()
    groups = (
        [_messages(100, 10, photo=True), _messages(120, 10, photo=True),
         _messages(140, 5, photo=True), _messages(150, 10, doc=True),
         _messages(170, 10, doc=True), _messages(190, 5, doc=True)]
    )
    bot.send_media_group.side_effect = groups
    outcome = await PublicationService(
        delivery=PTBTelegramDeliveryGateway(bot),
        link_builder=lambda message_id: str(message_id),
    ).publish(PublishCommand(
        chat_id="@channel",
        items=(
            [MediaItem.file_id("photo", f"P{index}") for index in range(25)]
            + [MediaItem.file_id("document", f"D{index}", filename=f"f{index}")
               for index in range(25)]
        ),
        caption_data={"title": "50", "tags": "#a",
                      "media_types": ["photo"] * 25 + ["document"] * 25},
        user_id=1,
        idempotency_key="fifty",
    ))

    assert outcome.ok
    assert len(outcome.known_messages) == 50
    assert sum(m.kind is not None and m.kind.value == "photo" for m in outcome.known_messages) == 25
    assert sum(m.kind is not None and m.kind.value == "document" for m in outcome.known_messages) == 25
    assert bot.send_media_group.await_count == 6
    assert [len(args.kwargs["media"]) for args in bot.send_media_group.await_args_list] == [10, 10, 5, 10, 10, 5]


def test_planner_preserves_document_fallback_assets():
    documents = [{"file_id": f"D{i}", "filename": f"image-{i}.png"} for i in range(4)]
    plan = plan_review_media([], [], documents=documents)
    assert [entry.kind for entry in plan.entries] == ["document"] * 4
    assert [entry.file_id for entry in plan.entries] == [f"D{i}" for i in range(4)]


@pytest.mark.asyncio
async def test_published_post_archive_keeps_mixed_transport_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "posts.db"))
    await db_manager.init_db()
    await record_published_post(PublishedPostInput(
        user_id=1,
        username="tester",
        main_message_id=1,
        title="Mixed",
        media_compact=["photo:PHOTO"],
        document_compact=["document:DOCUMENT:big.png"],
        all_message_ids=[1, 2],
    ))
    import aiosqlite
    async with aiosqlite.connect(db_manager.DB_PATH) as db:
        db.row_factory = __import__("sqlite3").Row
        cursor = await db.execute(
            "select content_type, file_ids from published_posts where message_id=1"
        )
        row = await cursor.fetchone()
    assert row is not None
    assert row["content_type"] == "mixed"
    assert json.loads(row["file_ids"]) == ["photo:PHOTO", "document:DOCUMENT:big.png"]


def test_legacy_kind_capture_recognizes_submission_text():
    message = SimpleNamespace(message_id=9, text="caption", photo=None, video=None,
                              animation=None, audio=None, document=None)
    assert publish._kind_of_raw_message(message) == "text"


@pytest.mark.asyncio
async def test_single_document_keeps_its_caption(isolated_db):
    bot = AsyncMock()
    bot.send_document.return_value = _file_message(1, doc=True, file_id="D0")

    async def record(*args, **kwargs):
        return None

    service = PublicationService(
        delivery=PTBTelegramDeliveryGateway(bot),
        link_builder=lambda message_id: f"https://example.test/{message_id}",
        record_post=record,
    )
    outcome = await service.publish(PublishCommand(
        chat_id="@channel",
        items=[MediaItem.file_id("document", "D0", filename="novel.txt")],
        caption_data={"title": "Novel", "tags": "#txt",
                      "media_types": ["document"]},
        user_id=1,
        idempotency_key="single-doc",
    ))

    assert outcome.ok
    assert bot.send_message.await_count == 0
    assert "Novel" in bot.send_document.await_args.kwargs["caption"]
