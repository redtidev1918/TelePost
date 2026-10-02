"""§novel-cover: the review group must show a novel's REAL cover.

The channel publication shape is ``visual root + TXT document reply``, where the
root is the canonical ``pixiv:<id>:novelcover`` asset. PixivFlow never
materializes that cover locally, so it can only be a URL, and the Telegram
review group used to show the TXT alone because review staging never received
the asset sidecar.

These tests pin the fix: the cover is staged by URL (exactly like the channel
does), it leads the submission, and it stays ``staging_only`` — presentation
only, never part of the review's recorded media.
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from database import db_manager
from handlers import review
from telepost.application.review_queue import QueueCommand, novel_cover_preview_url
from telepost.domain.media import MediaAsset
from telepost.telegram import review_stager

COVER_URL = "https://i.pximg.net/c/240x480/img/novel-cover.jpg"


def _photo_message(message_id=10, file_id="STAGED_PHOTO"):
    message = MagicMock()
    message.message_id = message_id
    message.photo = [MagicMock(file_id=file_id)]
    message.video = None
    message.animation = None
    message.audio = None
    message.document = None
    return message


def _document_message(message_id=30, file_id="STAGED_DOC", filename=None):
    message = MagicMock()
    message.message_id = message_id
    message.photo = None
    message.video = None
    message.animation = None
    message.audio = None
    message.document = MagicMock(file_id=file_id, file_name=filename or "novel.txt")
    return message


def _stager(bot):
    return review_stager.TelegramReviewStager(
        bot, -100123, album_size=10, preview_interval=0.0, thread=False,
    )


def _sent_kinds(bot):
    return [c[0] for c in bot.mock_calls if c[0] in ("send_photo", "send_document")]


def _command(**kwargs):
    payload = dict(
        user_id=7, username="pixivflow", tags="#pixiv", title="", note="", link="",
        anonymous=False, spoiler=False, source="api",
    )
    payload.update(kwargs)
    return QueueCommand(**payload)


@pytest.fixture
async def review_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "reviews.db"))
    monkeypatch.setattr(review, "REVIEW_CHAT_ID", -100123)
    await db_manager.init_db()
    return str(tmp_path / "reviews.db")


# ---- the stager sends the cover itself -----------------------------------
@pytest.mark.asyncio
async def test_stage_file_ids_previews_the_cover_url_first():
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message(message_id=21, file_id="COVER")
    bot.send_document.return_value = _document_message(message_id=22, file_id="TXT")

    media, documents, ids = await _stager(bot).stage_file_ids(
        [],
        [{"file_id": "TXT", "filename": "novel.txt"}],
        caption="正文",
        spoiler=False,
        cover_url=COVER_URL,
    )

    assert bot.send_photo.await_count == 1
    photo_kwargs = bot.send_photo.await_args.kwargs
    # A URL (not an InputFile) is what makes the cover reachable without a local
    # copy; Telegram fetches it with the same proxy the channel publication uses.
    assert photo_kwargs["photo"] == COVER_URL
    assert photo_kwargs["caption"] == "正文"
    assert photo_kwargs["has_spoiler"] is False
    # Cover first, TXT document after — the review group mirrors the channel.
    assert _sent_kinds(bot) == ["send_photo", "send_document"]
    # Presentation only: the cover never becomes review media (the publication
    # builds its own root from the canonical asset).
    assert media == []
    assert documents == [{"file_id": "TXT", "filename": "novel.txt"}]
    assert ids == [21, 22]


@pytest.mark.asyncio
async def test_stage_local_previews_the_cover_url_first(tmp_path):
    """The multipart/local path is production's actual staging path."""
    novel = tmp_path / "novel.txt"
    novel.write_text("正文", encoding="utf-8")
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message(message_id=21, file_id="COVER")
    bot.send_document.return_value = _document_message(message_id=22, file_id="TXT")

    media, documents, ids, _decisions = await _stager(bot).stage_local(
        [{"kind": "document", "path": str(novel), "filename": "novel.txt"}],
        caption="正文",
        spoiler=True,
        cover_url=COVER_URL,
    )

    assert bot.send_photo.await_count == 1
    assert bot.send_photo.await_args.kwargs["photo"] == COVER_URL
    # The review's own spoiler policy governs the cover in the review group too.
    assert bot.send_photo.await_args.kwargs["has_spoiler"] is True
    assert _sent_kinds(bot) == ["send_photo", "send_document"]
    assert media == []
    assert documents == [{"file_id": "TXT", "filename": "novel.txt"}]
    assert ids == [21, 22]


@pytest.mark.asyncio
async def test_stage_without_a_cover_stays_unchanged():
    bot = AsyncMock()
    bot.send_document.return_value = _document_message(message_id=22, file_id="TXT")

    media, documents, ids = await _stager(bot).stage_file_ids(
        [], [{"file_id": "TXT", "filename": "novel.txt"}],
        caption="正文", spoiler=False,
    )

    assert bot.send_photo.await_count == 0
    assert _sent_kinds(bot) == ["send_document"]
    assert media == []
    assert documents == [{"file_id": "TXT", "filename": "novel.txt"}]
    assert ids == [22]


# ---- which assets may act as a cover --------------------------------------
def test_novel_cover_preview_url_only_accepts_explicit_cover_assets():
    body_art = {"asset_id": "pixiv:123:1", "kind": "image", "source_url": COVER_URL}
    assert novel_cover_preview_url(_command(media_assets=(body_art,))) is None

    cover = {"asset_id": "pixiv:123:novelcover", "kind": "image", "source_url": COVER_URL}
    assert novel_cover_preview_url(_command(media_assets=(body_art, cover))) == COVER_URL

    domain_asset = MediaAsset(
        asset_id="pixiv:123:novelcover", kind="image", source_url=COVER_URL
    )
    assert novel_cover_preview_url(_command(media_assets=(domain_asset,))) == COVER_URL


def test_novel_cover_preview_url_ignores_malformed_and_hostile_entries():
    # A preview enrichment must never turn a submission into a failure.
    assert novel_cover_preview_url(_command()) is None
    assert novel_cover_preview_url(_command(media_assets=(None, "nope", 7))) is None
    assert novel_cover_preview_url(
        _command(media_assets=({"asset_id": "pixiv:1:novelcover"},))
    ) is None
    # A malformed entry must not hide a real cover that follows it.
    assert novel_cover_preview_url(
        _command(media_assets=(
            {"asset_id": "pixiv:1:novelcover", "source_url": "   "},
            {"asset_id": "pixiv:1:novelcover", "source_url": COVER_URL},
        ))
    ) == COVER_URL


def test_novel_cover_preview_url_uses_the_media_proxy(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "MEDIA_PROXY_BASE_URL", "https://media.example")
    monkeypatch.setattr(settings, "MEDIA_PROXY_HOSTS", frozenset({"i.pximg.net"}))

    assert novel_cover_preview_url(
        _command(media_assets=(
            {"asset_id": "pixiv:9:novelcover", "source_url": COVER_URL + "?x=1"},
        ))
    ) == "https://media.example/media/i.pximg.net/c/240x480/img/novel-cover.jpg?x=1"

    # An unlisted host is never re-pointed through the proxy.
    monkeypatch.setattr(settings, "MEDIA_PROXY_HOSTS", frozenset({"other.example"}))
    assert novel_cover_preview_url(
        _command(media_assets=(
            {"asset_id": "pixiv:9:novelcover", "source_url": COVER_URL},
        ))
    ) == COVER_URL


# ---- the submission path that production actually uses --------------------
@pytest.mark.asyncio
async def test_multipart_novel_submission_shows_the_cover_in_the_review_group(
    review_db, tmp_path
):
    novel = tmp_path / "novel.txt"
    novel.write_text("正文", encoding="utf-8")
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message(message_id=21, file_id="COVER")
    bot.send_document.return_value = _document_message(message_id=22, file_id="TXT")
    control = MagicMock()
    control.message_id = 23
    bot.send_message.return_value = control

    result = await review.queue_review_from_files(
        bot,
        [{"kind": "document", "path": str(novel), "filename": "novel.txt"}],
        tags="#pixiv",
        title="某小说",
        link="https://www.pixiv.net/novel/show.php?id=123",
        user_id=7,
        username="pixivflow",
        idempotency_key="pixiv:novel:123",
        work_type="novel",
        work_id="123",
        media_assets=[
            {"asset_id": "pixiv:123:novelcover", "kind": "image",
             "source_url": COVER_URL},
        ],
    )

    assert result["status"] == "pending_review"
    assert bot.send_photo.await_count == 1
    assert bot.send_photo.await_args.kwargs["photo"] == COVER_URL

    async with db_manager.get_db() as conn:
        cursor = await conn.execute("SELECT * FROM pending_reviews")
        row = await cursor.fetchone()

    # The cover is reviewer-facing presentation: the review's own media stays
    # empty, so publication can never count the cover twice.
    assert json.loads(row["media_json"]) == []
    assert [item["file_id"] for item in json.loads(row["documents_json"])] == ["TXT"]
    assert 21 in json.loads(row["review_message_ids"])
