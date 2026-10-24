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

    def test_stale_chain_asset_never_downgrades_local_animation_to_photo(self):
        """Regression (review 208): a refetch replacement staged an animation
        file_id while the chain's media_asset_refs still declared the OLD
        multi-page work as kind=image. The indexed local item is authoritative:
        sendPhoto on an animation file_id is a certain Telegram reject."""
        plan = plan_review_media(
            [{"type": "animation", "file_id": "UGOIRA_FID"}],
            [
                {"asset_id": "pixiv:1:illust:page-1", "kind": "image",
                 "source_url": "https://x/1"},
                {"asset_id": "pixiv:1:illust:page-2", "kind": "image",
                 "source_url": "https://x/2"},
            ],
        )
        assert plan.entries[0].kind == "animation"
        assert plan.entries[0].strategy == STRATEGY_FILE_ID
        assert plan.entries[0].file_id == "UGOIRA_FID"
        # The asset-only tail keeps the documented remote fallback.
        assert plan.entries[1].kind == "photo"
        assert plan.entries[1].strategy == STRATEGY_REMOTE_URL

    def test_local_video_and_audio_keep_their_kind_with_image_assets(self):
        plan = plan_review_media(
            [
                {"type": "video", "file_id": "VID"},
                {"type": "audio", "file_id": "AUD"},
            ],
            [
                {"asset_id": "a1", "kind": "image", "source_url": "https://x/1"},
                {"asset_id": "a2", "kind": "image", "source_url": "https://x/2"},
            ],
        )
        assert [e.kind for e in plan.entries] == ["video", "audio"]
        assert all(e.file_id for e in plan.entries)

    def test_foreign_work_refs_are_dropped_from_the_plan(self):
        """Regression (review 208): a ugoira refetch-replaced a 4-page
        illustration; the chain kept the OLD work's pixiv refs. With work_id
        the plan must contain ONLY this work's staged animation — never the
        superseded work's pages as remote photos."""
        plan = plan_review_media(
            [{"type": "animation", "file_id": "UGOIRA_FID"}],
            [
                {"asset_id": "pixiv:151182115:illust:page-1", "kind": "image",
                 "source_url": "https://x/1"},
                {"asset_id": "pixiv:151182115:illust:page-2", "kind": "image",
                 "source_url": "https://x/2"},
                {"asset_id": "pixiv:151182115:illust:page-3", "kind": "image",
                 "source_url": "https://x/3"},
                {"asset_id": "pixiv:151182115:illust:page-4", "kind": "image",
                 "source_url": "https://x/4"},
            ],
            work_id="151190970",
        )
        assert len(plan.entries) == 1
        entry = plan.entries[0]
        assert entry.kind == "animation"
        assert entry.strategy == STRATEGY_FILE_ID
        assert entry.file_id == "UGOIRA_FID"

    def test_same_work_and_non_pixiv_refs_survive_the_filter(self):
        plan = plan_review_media(
            [{"type": "document", "file_id": "TXT"}],
            [
                {"asset_id": "pixiv:29430449:novelcover", "kind": "image",
                 "source_url": "https://x/cover"},
                {"asset_id": "uploadedimage:abc", "kind": "image",
                 "source_url": "https://x/up"},
                {"asset_id": "pixiv:29430449:illust:page-1", "kind": "image",
                 "source_url": "https://x/1"},
            ],
            work_id="29430449",
        )
        kinds = [e.kind for e in plan.entries]
        # same-work cover + upload pair first, TXT document keeps its slot.
        assert "photo" in kinds and "document" in kinds
        assert not any(e.asset_id and e.asset_id.startswith("pixiv:999") for e in plan.entries)

    def test_no_work_id_keeps_legacy_unfiltered_behavior(self):
        plan = plan_review_media(
            [{"type": "animation", "file_id": "UGOIRA_FID"}],
            [
                {"asset_id": "pixiv:151182115:illust:page-1", "kind": "image",
                 "source_url": "https://x/1"},
                {"asset_id": "pixiv:151182115:illust:page-2", "kind": "image",
                 "source_url": "https://x/2"},
            ],
        )
        assert [e.kind for e in plan.entries] == ["animation", "photo"]
        assert plan.entries[1].strategy == STRATEGY_REMOTE_URL

    def test_local_photo_stays_photo_with_image_asset(self):
        plan = plan_review_media(
            [{"type": "photo", "file_id": "AAA"}],
            [{"asset_id": "a1", "kind": "image", "source_url": "https://x/a"}],
        )
        assert plan.entries[0].kind == "photo"

    def test_indexed_document_is_not_photo(self):
        """Regression: novel cover assets + indexed TXT file_id."""
        plan = plan_review_media(
            [],
            [
                {"asset_id": "a1", "kind": "image",
                 "source_url": "https://x/a"},
                {"asset_id": "txt", "kind": "image",
                 "source_url": "https://x/txt"},
            ],
            documents=[{"file_id": "DDD", "filename": "novel.txt"}],
        )
        assert [e.kind for e in plan.entries] == ["photo", "document"]
        assert plan.strategy == "mixed"
        items = plan.to_media_items()
        assert items[0].kind.value == "photo"
        assert items[1].kind.value == "document"
        assert items[1].telegram_file_id == "DDD"

    def test_single_cover_asset_pairs_with_indexed_txt_document(self):
        """Regression: a novel with one cover asset and one TXT must not plan
        the TXT as a photo; that made novel_images_preview_only remove every
        item and the publication failed with "delivery returned no messages"."""
        plan = plan_review_media(
            [],
            [{"asset_id": "cover", "kind": "image",
              "source_url": "https://i.pximg.net/cover.png"}],
            documents=[{"file_id": "TXT_FILE_ID", "filename": "novel.txt"}],
        )
        assert [e.kind for e in plan.entries] == ["document"]
        assert [e.strategy for e in plan.entries] == [STRATEGY_FILE_ID]
        assert plan.entries[0].file_id == "TXT_FILE_ID"
        assert plan.entries[0].filename == "novel.txt"

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
