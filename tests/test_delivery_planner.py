"""Step 11 DeliveryPlanner: TelePost decides how to deliver review media."""
import pytest

from telepost.application.delivery_planner import (
    STRATEGY_FILE_ID,
    STRATEGY_REMOTE_URL,
    plan_review_media,
)


class TestPlanReviewMedia:
    def test_no_assets_uses_local_file_ids(self):
        plan = plan_review_media(
            [{"type": "photo", "file_id": "AAA"}],
            [],
        )
        assert plan.strategy == STRATEGY_FILE_ID
        assert len(plan.entries) == 1
        assert plan.entries[0].strategy == STRATEGY_FILE_ID
        assert plan.entries[0].file_id == "AAA"

    def test_asset_with_file_id_prefers_zero_reupload(self):
        plan = plan_review_media(
            [{"type": "photo", "file_id": "AAA"}],
            [{"asset_id": "pixiv-1", "kind": "image",
              "source_url": "https://i.pximg.net/1.jpg"}],
        )
        assert plan.strategy == STRATEGY_FILE_ID
        assert plan.entries[0].strategy == STRATEGY_FILE_ID
        assert plan.entries[0].asset_id == "pixiv-1"

    def test_asset_without_file_id_falls_back_to_remote_url(self):
        plan = plan_review_media(
            [],
            [{"asset_id": "pixiv-1", "kind": "image",
              "source_url": "https://proxy.example/pixiv/1.jpg"}],
        )
        assert plan.strategy == STRATEGY_REMOTE_URL
        assert plan.entries[0].strategy == STRATEGY_REMOTE_URL
        assert plan.entries[0].source_url == "https://proxy.example/pixiv/1.jpg"

    def test_mixed_plan(self):
        plan = plan_review_media(
            [{"type": "photo", "file_id": "AAA"}],
            [
                {"asset_id": "a1", "kind": "image", "source_url": "https://x/a"},
                {"asset_id": "a2", "kind": "image", "source_url": "https://x/b"},
            ],
        )
        assert plan.strategy == "mixed"
        strategies = [e.strategy for e in plan.entries]
        assert strategies == [STRATEGY_FILE_ID, STRATEGY_REMOTE_URL]

    def test_leftover_document_keeps_file_id(self):
        plan = plan_review_media(
            [{"type": "photo", "file_id": "AAA"}],
            [{"asset_id": "a1", "kind": "image", "source_url": "https://x/a"}],
            documents=[{"file_id": "DDD", "filename": "novel.txt"}],
        )
        assert [e.kind for e in plan.entries] == ["photo", "document"]
        assert [e.strategy for e in plan.entries] == [STRATEGY_FILE_ID, STRATEGY_FILE_ID]
        assert plan.entries[1].file_id == "DDD"

    def test_empty_plan(self):
        plan = plan_review_media([], [])
        assert plan.strategy == "empty"
        assert plan.to_media_items() == []

    def test_to_media_items(self):
        plan = plan_review_media(
            [],
            [{"asset_id": "a1", "kind": "image",
              "source_url": "https://proxy.example/pixiv/1.jpg"}],
        )
        items = plan.to_media_items()
        assert len(items) == 1
        assert items[0].source.url == "https://proxy.example/pixiv/1.jpg"
        assert items[0].telegram_file_id is None
