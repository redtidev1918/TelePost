"""Novel cover semantics + fallback card regressions (§novel-cover).

Cases mirror the production contract:
  1. real cover + TXT        → cover root + TXT reply
  2. no cover + fallback ON  → fallback card root + TXT reply (temp cleaned)
  3. no cover + fallback OFF → TXT-only root (text-only fallback)
  4. cover + TXT + inline    → cover root + TXT reply; inline stays in reader
  5. fallback render failure → text-only root + TXT reply, never a failure
  6. TXT only                → delivery non-empty
  7. legacy payload (inline-only assets) → body art never becomes a cover
"""
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from database import db_manager
from handlers import publish


@pytest.fixture
async def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "novel.db"))
    await db_manager.init_db()
    return tmp_path


def _photo_message(message_id=10, file_id="COVER", unique_id="U_COVER"):
    message = MagicMock()
    message.message_id = message_id
    message.photo = [MagicMock(file_id=file_id, file_unique_id=unique_id)]
    message.video = None
    message.animation = None
    message.audio = None
    message.document = None
    return message


def _doc_message(message_id=11, file_id="TXT"):
    message = MagicMock()
    message.message_id = message_id
    message.photo = None
    message.video = None
    message.animation = None
    message.audio = None
    document = MagicMock()
    document.file_id = file_id
    document.file_unique_id = "U_TXT"
    message.document = document
    return message


COVER_ASSET = {
    "asset_id": "pixiv:9:novelcover",
    "kind": "image",
    "source_url": "https://i.pximg.net/novel-cover-master/img/cover.jpg",
}
INLINE_ASSETS = [
    {
        "asset_id": f"pixiv:9:uploadedimage:{i}",
        "kind": "image",
        "source_url": f"https://i.pximg.net/img-original/{i}.png",
    }
    for i in range(50)
]
TXT_DOC = [{"file_id": "TXT_FILE_ID", "filename": "9_novel.txt"}]


def _patch_common(monkeypatch, tmp_path, materialize=True):
    monkeypatch.setattr(publish, "save_published_post", AsyncMock())
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    bot.send_document.return_value = _doc_message()
    if materialize:
        from telepost.domain.delivery import LocalFile, MediaItem, MediaKind
        from telepost.telegram.delivery import gateway

        fake_path = tmp_path / "cover.jpg"
        fake_path.write_bytes(b"image")
        local_item = MediaItem(
            MediaKind.PHOTO,
            LocalFile(str(fake_path), "cover.jpg"),
        )
        monkeypatch.setattr(
            gateway, "materialize_remote", AsyncMock(return_value=local_item)
        )
    return bot


@pytest.mark.unit
def test_novel_cover_asset_partitioning():
    assert publish.novel_cover_asset_ids([COVER_ASSET] + INLINE_ASSETS) == {
        "pixiv:9:novelcover"
    }
    assert publish.novel_cover_asset_ids(INLINE_ASSETS) == set()
    assert publish.novel_cover_asset_ids([]) == set()


@pytest.mark.asyncio
async def test_case1_real_cover_root_and_txt_reply(monkeypatch, tmp_path, isolated_db):
    bot = _patch_common(monkeypatch, tmp_path, isolated_db)
    await publish.publish_from_file_ids(
        bot, [], TXT_DOC,
        media_assets=[COVER_ASSET],
        tags="tag1 tag2", title="封面小说",
        user_id=7, username="u", work_type="novel", pixiv_id="9",
    )
    assert bot.send_photo.await_count == 1, "cover must be the visual root"
    assert bot.send_document.await_count == 1, "TXT ships as the document reply"
    bot.send_media_group.assert_not_called()


@pytest.mark.asyncio
async def test_case2_fallback_card_root_and_temp_cleaned(monkeypatch, tmp_path, isolated_db):
    bot = _patch_common(monkeypatch, tmp_path, materialize=False)
    created = []
    real_render = publish._render_novel_root_card

    def spy(title, tags):
        path = real_render(title, tags)
        if path:
            created.append(path)
        return path

    monkeypatch.setattr(publish, "_render_novel_root_card", spy)
    await publish.publish_from_file_ids(
        bot, [], TXT_DOC,
        media_assets=[*INLINE_ASSETS[:3]],
        tags="tag1", title="无封面小说",
        user_id=7, username="u", work_type="novel", pixiv_id="9",
    )
    assert bot.send_photo.await_count == 1, "fallback card is the root"
    assert bot.send_document.await_count == 1
    photo = bot.send_photo.await_args.kwargs.get("photo")
    assert getattr(photo, "filename", "") == "novel-card.png"
    assert created, "the card render must have run"
    for path in created:
        assert not os.path.exists(path), "the temp card must be cleaned up"


@pytest.mark.asyncio
async def test_case3_fallback_disabled_keeps_text_only_root(monkeypatch, tmp_path, isolated_db):
    from config import settings
    monkeypatch.setattr(settings, "NOVEL_FALLBACK_CARD_ENABLED", False)
    bot = _patch_common(monkeypatch, tmp_path, materialize=False)
    await publish.publish_from_file_ids(
        bot, [], TXT_DOC,
        media_assets=[*INLINE_ASSETS[:3]],
        title="无封面小说", user_id=7, username="u",
        work_type="novel", pixiv_id="9",
    )
    assert bot.send_photo.await_count == 0
    assert bot.send_document.await_count == 1


@pytest.mark.asyncio
async def test_case4_cover_plus_inline_only_sends_cover(monkeypatch, tmp_path, isolated_db):
    bot = _patch_common(monkeypatch, tmp_path, isolated_db)
    await publish.publish_from_file_ids(
        bot, [], TXT_DOC,
        media_assets=[COVER_ASSET] + INLINE_ASSETS,
        title="带插图小说", user_id=7, username="u",
        work_type="novel", pixiv_id="9",
    )
    assert bot.send_photo.await_count == 1, "inline images never join the channel"
    assert bot.send_document.await_count == 1


@pytest.mark.asyncio
async def test_case5_card_failure_degrades_to_text_root(monkeypatch, tmp_path, isolated_db):
    bot = _patch_common(monkeypatch, tmp_path, materialize=False)
    monkeypatch.setattr(publish, "_render_novel_root_card", lambda title, tags: None)
    await publish.publish_from_file_ids(
        bot, [], TXT_DOC,
        media_assets=[*INLINE_ASSETS[:3]],
        title="卡片失败小说", user_id=7, username="u",
        work_type="novel", pixiv_id="9",
    )
    assert bot.send_photo.await_count == 0, "failed card must not fail delivery"
    assert bot.send_document.await_count == 1


@pytest.mark.asyncio
async def test_case6_txt_only_is_never_empty(monkeypatch, tmp_path, isolated_db):
    bot = _patch_common(monkeypatch, tmp_path, materialize=False)
    monkeypatch.setattr(publish, "_render_novel_root_card", lambda title, tags: None)
    result = await publish.publish_from_file_ids(
        bot, [], TXT_DOC,
        title="纯文本小说", user_id=7, username="u",
        work_type="novel", pixiv_id="9",
    )
    assert result.get("message_id"), "TXT-only delivery must still publish"
    assert bot.send_document.await_count == 1


@pytest.mark.asyncio
async def test_case7_fifty_inline_images_never_become_cover(monkeypatch, tmp_path, isolated_db):
    bot = _patch_common(monkeypatch, tmp_path, materialize=False)
    monkeypatch.setattr(publish, "_render_novel_root_card", lambda title, tags: None)
    await publish.publish_from_file_ids(
        bot, [], TXT_DOC,
        media_assets=list(INLINE_ASSETS),
        title="多插图小说", user_id=7, username="u",
        work_type="novel", pixiv_id="9",
    )
    assert bot.send_photo.await_count == 0
    assert bot.send_document.await_count == 1
    bot.send_media_group.assert_not_called()
