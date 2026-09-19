"""Step 12 TelegramMediaCache: same-asset file_id cache on media_asset_refs."""
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
        assert refs[0]["file_id"] == "FILE_ID_1"
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
        by_id = {r["asset_id"]: r for r in refs}
        assert by_id["a1"]["file_id"] == "AA"
        assert by_id["a1"]["file_unique_id"] == "UA"
        # Unknown asset never invents a cache row.
        assert by_id["a2"]["file_id"] == ""

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
