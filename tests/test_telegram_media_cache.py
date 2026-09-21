"""Step 12 TelegramMediaCache: same-asset file_id cache on media_asset_refs."""
from unittest.mock import AsyncMock
import pytest

from database import db_manager
from telepost.application.delivery_planner import STRATEGY_FILE_ID, plan_review_media
from telepost.storage.sqlite.media_assets import (
    mark_delivered_for_chain,
    replace_for_chain,
)


@pytest.fixture
async def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "cache.db"))
    await db_manager.init_db()
    return tmp_path


class TestPlannerUsesCachedFileId:
    @pytest.mark.asyncio
    async def test_cached_file_id_used_without_local_media(self, isolated_db):
        await replace_for_chain("chain-1", [
            {"asset_id": "pixiv-1", "kind": "image",
             "source_url": "https://i.pximg.net/1.jpg"},
        ])
        await mark_delivered_for_chain("chain-1", [
            {"asset_id": "pixiv-1", "file_id": "FILE_ID_1", "file_unique_id": "UNIQ_1"}
        ])
        refs = __import__("telepost.storage.sqlite.media_assets", fromlist=["list_for_chain"])
        refs = await refs.list_for_chain("chain-1")
        assert refs[0].file_id == "FILE_ID_1"
        # The planner sees the cached file_id even when media_json is empty.
        plan = plan_review_media([], refs)
        assert plan.strategy == STRATEGY_FILE_ID
        assert plan.entries[0].file_id == "FILE_ID_1"
        assert plan.entries[0].source_url is None


class TestMarkDelivered:
    @pytest.mark.asyncio
    async def test_persists_cache_facts(self, isolated_db):
        await replace_for_chain("chain-x", [
            {"asset_id": "a1", "kind": "image", "source_url": "https://x/a"},
            {"asset_id": "a2", "kind": "image", "source_url": "https://x/b"},
        ])
        updated = await mark_delivered_for_chain("chain-x", [
            {"asset_id": "a1", "file_id": "AA", "file_unique_id": "UA"},
            {"asset_id": "unknown", "file_id": "BB", "file_unique_id": "UB"},
        ])
        assert updated >= 1
        refs = await __import__(
            "telepost.storage.sqlite.media_assets", fromlist=["list_for_chain"]
        ).list_for_chain("chain-x")
        by_id = {r.asset_id: r for r in refs}
        assert by_id["a1"].file_id == "AA"
        assert by_id["a1"].file_unique_id == "UA"
        # Unknown asset never invents a cache row.
        assert by_id["a2"].file_id == ""

    @pytest.mark.asyncio
    async def test_empty_chain_is_noop(self, isolated_db):
        assert await mark_delivered_for_chain("", [{"asset_id": "a", "file_id": "x"}]) == 0


class TestSenderUniqueId:
    def test_file_unique_id_of_photo(self):
        from types import SimpleNamespace
        from telepost.telegram.delivery.sender import file_unique_id_of

        message = SimpleNamespace(
            photo=(SimpleNamespace(file_id="LARGE", file_unique_id="uniq-large"),),
            video=None, animation=None, audio=None, document=None,
        )
        assert file_unique_id_of(message) == "uniq-large"

    def test_file_unique_id_of_document(self):
        from types import SimpleNamespace
        from telepost.telegram.delivery.sender import file_unique_id_of

        message = SimpleNamespace(
            photo=None, video=None, animation=None, audio=None,
            document=SimpleNamespace(file_id="doc1", file_unique_id="uniq-doc"),
        )
        assert file_unique_id_of(message) == "uniq-doc"

    def test_missing_returns_none(self):
        from types import SimpleNamespace
        from telepost.telegram.delivery.sender import file_unique_id_of

        message = SimpleNamespace(photo=None, video=None, animation=None,
                                  audio=None, document=None)
        assert file_unique_id_of(message) is None


class TestPublisherAdoptsPlanner:
    @staticmethod
    def _photo_message(message_id=10, file_id="STAGED_PHOTO", unique_id="U_PHOTO"):
        from unittest.mock import MagicMock
        message = MagicMock()
        message.message_id = message_id
        message.photo = [MagicMock(file_id=file_id, file_unique_id=unique_id)]
        message.video = None
        message.animation = None
        message.audio = None
        message.document = None
        return message

    @pytest.mark.asyncio
    async def test_remote_asset_uses_remote_url(self, monkeypatch, tmp_path):
        from handlers import publish
        from telepost.domain.delivery import LocalFile, MediaItem, MediaKind
        from telepost.telegram.delivery import gateway

        path = tmp_path / "materialized.jpg"
        path.write_bytes(b"image")
        local_item = MediaItem(
            MediaKind.PHOTO,
            LocalFile(str(path), "materialized.jpg", temporary=True),
        )
        monkeypatch.setattr(
            gateway,
            "materialize_remote",
            AsyncMock(return_value=local_item),
        )

        bot = AsyncMock()
        bot.send_photo.return_value = self._photo_message(
            message_id=56, file_id="RID", unique_id="RU"
        )
        monkeypatch.setattr(publish, "save_published_post", AsyncMock())
        result = await publish.publish_from_file_ids(
            bot,
            [],
            [],
            media_assets=[
                {"asset_id": "a1", "kind": "image",
                 "source_url": "https://proxy.example/pixiv/1.jpg"}
            ],
            user_id=7,
            username="x",
        )
        assert result["message_id"] == 56
        assert result["known_messages"][0]["file_id"] == "RID"
        bkwargs = bot.send_photo.await_args.kwargs
        assert bkwargs["photo"] != "https://proxy.example/pixiv/1.jpg"
        bot.send_media_group.assert_not_called()

    @pytest.mark.asyncio
    async def test_cached_file_id_uses_zero_reupload(self, monkeypatch):
        from handlers import publish

        bot = AsyncMock()
        bot.send_photo.return_value = self._photo_message(
            message_id=57, file_id="CACHED", unique_id="CU"
        )
        monkeypatch.setattr(publish, "save_published_post", AsyncMock())
        result = await publish.publish_from_file_ids(
            bot,
            [],
            [],
            media_assets=[
                {"asset_id": "a1", "kind": "image",
                 "source_url": "https://proxy.example/pixiv/1.jpg",
                 "file_id": "CACHED", "file_unique_id": "CU"}
            ],
            user_id=7,
            username="x",
        )
        assert result["message_id"] == 57
        bkwargs = bot.send_photo.await_args.kwargs
        assert bkwargs["photo"] == "CACHED"


class TestSecondDeliveryReusesCachedFileId:
    """Step 14: after first delivery caches file_id, the next delivery of the
    same canonical asset reuses the Telegram file_id (zero re-upload)."""

    @pytest.mark.asyncio
    async def test_second_delivery_uses_cached_file_id(self, isolated_db, monkeypatch, tmp_path):
        from handlers import publish
        from telepost.domain.delivery import LocalFile, MediaItem, MediaKind
        from telepost.telegram.delivery import gateway
        from telepost.storage.sqlite.media_assets import (
            list_for_chain,
            replace_for_chain,
        )

        path = tmp_path / "materialized.jpg"
        path.write_bytes(b"image")
        local_item = MediaItem(
            MediaKind.PHOTO,
            LocalFile(str(path), "materialized.jpg", temporary=True),
        )
        monkeypatch.setattr(
            gateway,
            "materialize_remote",
            AsyncMock(return_value=local_item),
        )

        chain_id = "chain-step14"
        await replace_for_chain(chain_id, [
            {"asset_id": "a1", "kind": "image",
             "source_url": "https://proxy.example/pixiv/1.jpg"}
        ])

        bot1 = AsyncMock()
        bot1.send_photo.return_value = TestPublisherAdoptsPlanner._photo_message(
            message_id=101, file_id="FIRST", unique_id="U1"
        )
        monkeypatch.setattr(publish, "save_published_post", AsyncMock())
        first = await publish.publish_from_file_ids(
            bot1, [], [],
            media_assets=await list_for_chain(chain_id),
            review_chain_id=chain_id,
            idempotency_key="step14-1",
            user_id=7, username="x",
        )
        assert first["status"] == "published"
        assert first["known_messages"][0]["file_id"] == "FIRST"

        refs = await list_for_chain(chain_id)
        assert refs[0].file_id == "FIRST"
        assert refs[0].file_unique_id == "U1"

        bot2 = AsyncMock()
        bot2.send_photo.return_value = TestPublisherAdoptsPlanner._photo_message(
            message_id=102, file_id="SECOND", unique_id="U2"
        )
        second = await publish.publish_from_file_ids(
            bot2, [], [],
            media_assets=refs,
            review_chain_id=chain_id,
            idempotency_key="step14-2",
            user_id=7, username="x",
        )
        assert second["status"] == "published"
        captured = bot2.send_photo.await_args.kwargs
        assert captured["photo"] == "FIRST"
        bot2.send_media_group.assert_not_called()


class TestNoteSanitizedOnInsert:
    """Pixiv descriptions carry HTML (<br>, entities); previews are plain text."""

    @pytest.mark.asyncio
    async def test_br_becomes_newline_and_tags_stripped(self, isolated_db):
        from telepost.storage.sqlite.reviews import NewReview, ReviewRepository
        from telepost.storage.sqlite.media_assets import replace_for_chain

        await replace_for_chain("chain-sanitize", [])
        repo = ReviewRepository()
        payload = dict(
            idempotency_key="sanitize-1", source="api", user_id=7,
            username="x", title="标题", tags="#a",
            note="第一行<br>第二行<br/>第三行 <b>粗体</b> &amp; 更多",
            link="", anonymous=False, spoiler=False,
            media=[], documents=[], review_chat_id="-100",
            review_message_ids=[], target_id="", source_label="",
            source_ref="", pixiv_id="", work_type="",
            delivery_target="", review_chain_id="chain-sanitize",
            generation=0, supersedes_review_id=None, refetch_request_id="",
            submitter_user_id=7, submitter_username="x",
            submitter_display_name="", actor_kind="api",
        )
        review_id = await repo.insert(NewReview(**payload))
        row = await repo.get(review_id)
        assert "<br" not in row["note"]
        assert "<b>" not in row["note"]
        assert row["note"] == "第一行\n第二行\n第三行 粗体 & 更多"
