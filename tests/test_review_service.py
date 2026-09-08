"""ReviewService owns headless state transitions and publishing claims."""

import asyncio
import io
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from database import db_manager
from services.review_service import (
    PreviewUnavailableError,
    PublishFailedError,
    ReviewBusyError,
    ReviewError,
    ReviewMedia,
    ReviewNotFoundError,
    ReviewService,
    ReviewStateError,
)


@pytest.fixture
async def service_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "reviews.db"))
    await db_manager.init_db()
    yield


async def _insert_review(review_id=1, *, status="pending", media=None, documents=None, spoiler=0):
    media = media if media is not None else [{"type": "photo", "file_id": "IMG"}]
    documents = documents if documents is not None else []
    async with db_manager.get_db() as conn:
        await conn.execute(
            """
            INSERT INTO pending_reviews (
                id, idempotency_key, source, status, user_id, username,
                title, tags, note, link, anonymous, spoiler, media_json,
                documents_json, review_chat_id, created_at, updated_at
            ) VALUES (?, ?, 'api', ?, 7, 'u', 'Title', '#tag', 'note',
                      'https://example.com', 0, ?, ?, ?, '-100', 1, 1)
            """,
            (
                review_id,
                f"key-{review_id}",
                status,
                spoiler,
                json.dumps(media),
                json.dumps(documents),
            ),
        )


@pytest.mark.asyncio
async def test_list_get_and_summary(service_db):
    await _insert_review(
        media=[{"type": "photo", "file_id": "A"}],
        documents=[{"file_id": "D", "filename": "novel.txt"}],
    )
    service = ReviewService()

    listed = await service.list_pending()
    item = await service.get_review(1)

    assert listed["items"][0]["media_count"] == 1
    assert listed["items"][0]["document_count"] == 1
    assert item.id == 1
    assert item.tags == ["#tag"]
    assert item.media == [
        ReviewMedia(index=0, kind="photo", file_id="A", filename=None,
                    mime_type=None, thumbnail_file_id=None),
        ReviewMedia(index=1, kind="document", file_id="D",
                    filename="novel.txt", mime_type=None,
                    thumbnail_file_id=None),
    ]


@pytest.mark.asyncio
async def test_approve_publishes_once_and_reuses_concurrent_result(service_db):
    await _insert_review()
    claimed = asyncio.Event()
    release = asyncio.Event()
    publish_calls = 0

    async def publisher(*args, **kwargs):
        nonlocal publish_calls
        publish_calls += 1
        claimed.set()
        await release.wait()
        return {"message_id": 99, "link": "https://t.me/c/1/99"}

    service = ReviewService(publisher)
    bot = object()

    async def first_approve():
        return await service.approve(bot, 1, actor=123)

    first_task = asyncio.create_task(first_approve())
    await claimed.wait()
    with pytest.raises(ReviewBusyError):
        await service.approve(bot, 1, actor=456)
    release.set()
    first = await first_task

    assert first.status == "published"
    assert first.message_id == 99
    assert publish_calls == 1
    async with db_manager.get_db() as conn:
        row = await (await conn.execute(
            "SELECT status, decided_by FROM pending_reviews WHERE id=1"
        )).fetchone()
    assert row["status"] == "published"
    assert row["decided_by"] == 123


@pytest.mark.asyncio
async def test_publish_failure_marks_failed_and_is_retryable(service_db):
    await _insert_review()
    publisher = AsyncMock(side_effect=[RuntimeError("telegram lost"), {"message_id": 7}])
    service = ReviewService(publisher)

    with pytest.raises(PublishFailedError, match="telegram lost"):
        await service.approve(AsyncMock(), 1, actor=1)
    result = await service.approve(AsyncMock(), 1, actor=1)

    assert result.status == "published"
    assert publisher.await_count == 2


@pytest.mark.asyncio
async def test_reject_spoiler_and_invalid_transitions(service_db):
    await _insert_review()
    bot = AsyncMock()
    service = ReviewService()

    spoiler = await service.set_spoiler(1, True, actor=1)
    rejected = await service.reject(bot, 1, reason="bad\n\t<script>", actor=1)

    assert spoiler.status == "pending"
    assert rejected.status == "rejected"
    with pytest.raises(ReviewStateError):
        await service.set_spoiler(1, False, actor=1)
    with pytest.raises(ReviewStateError):
        await service.reject(bot, 1, actor=1)
    async with db_manager.get_db() as conn:
        row = await (await conn.execute("SELECT spoiler FROM pending_reviews")).fetchone()
    assert row["spoiler"] == 1


@pytest.mark.asyncio
async def test_get_missing_review_and_media_index(service_db):
    await _insert_review(media=[{"type": "document", "file_id": "D", "filename": "a.zip"}])
    service = ReviewService()

    with pytest.raises(ReviewNotFoundError):
        await service.get_review(999)
    with pytest.raises(ReviewError):
        await service.get_media(AsyncMock(), 1, 9)
    with pytest.raises(PreviewUnavailableError):
        await service.get_media(AsyncMock(), 1, 0)


@pytest.mark.asyncio
async def test_image_preview_is_bounded_jpeg(service_db):
    from PIL import Image

    source = io.BytesIO()
    Image.new("RGB", (3000, 1500), "red").save(source, format="PNG")
    source_bytes = source.getvalue()

    await _insert_review(media=[{"type": "photo", "file_id": "BIG"}])
    tg_file = MagicMock()
    tg_file.file_size = len(source_bytes)
    tg_file.file_path = "photos/big.png"
    async def download_to_memory(buffer):
        buffer.write(source_bytes)
    tg_file.download_to_memory = download_to_memory
    bot = AsyncMock()
    bot.get_file.return_value = tg_file

    result = await ReviewService().get_media(bot, 1, 0, "preview")
    image = Image.open(io.BytesIO(result.data))

    assert result.mime_type == "image/jpeg"
    assert result.kind == "image"
    assert max(image.size) <= 1600
    assert result.size < len(source_bytes)
