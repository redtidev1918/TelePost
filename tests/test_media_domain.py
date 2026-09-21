"""Batch 5 — MediaAsset domain model: wire/row constructors and variant enum."""
import pytest

from telepost.domain.media import DeliveryVariant, MediaAsset


class TestMediaAssetFromWire:
    def test_minimal_wire_payload(self):
        asset = MediaAsset.from_wire({
            "asset_id": "pixiv:1:illust:page-1",
            "kind": "image",
            "source_url": "https://i.pximg.net/1.jpg",
        })
        assert asset.asset_id == "pixiv:1:illust:page-1"
        assert asset.kind == "image"
        assert asset.file_id == ""
        assert asset.to_dict() == {
            "asset_id": "pixiv:1:illust:page-1", "kind": "image",
            "source_url": "https://i.pximg.net/1.jpg", "mime_type": "",
            "file_id": "", "file_unique_id": "",
        }

    def test_fields_are_truncated_like_the_old_validator(self):
        asset = MediaAsset.from_wire({
            "asset_id": "a" * 300, "kind": "image",
            "source_url": "https://x/" + "b" * 3000, "mime_type": "m" * 200,
        })
        assert len(asset.asset_id) == 200
        assert len(asset.source_url) == 2048
        assert len(asset.mime_type) == 100

    @pytest.mark.parametrize("payload, message", [
        ({"kind": "image", "source_url": "https://x/a"}, "asset_id"),
        ({"asset_id": "a1", "source_url": "ftp://x/a"}, r"http\(s\)"),
        ({"asset_id": "a1", "kind": "video", "source_url": "https://x/a"}, "kind"),
    ])
    def test_invalid_wire_payload_raises(self, payload, message):
        with pytest.raises(ValueError, match=message):
            MediaAsset.from_wire(payload)


class TestMediaAssetFromRow:
    def test_row_mapping(self):
        asset = MediaAsset.from_row({
            "asset_id": "a1", "kind": "image", "source_url": "https://x/a",
            "mime_type": "image/jpeg", "file_id": "F", "file_unique_id": "U",
        })
        assert asset.file_id == "F"
        assert asset.file_unique_id == "U"

    def test_null_cache_columns_become_empty_strings(self):
        asset = MediaAsset.from_row({
            "asset_id": "a1", "kind": "image", "source_url": "https://x/a",
            "mime_type": None, "file_id": None, "file_unique_id": None,
        })
        assert asset.file_id == "" and asset.mime_type == ""


class TestCoerceAndVariant:
    def test_coerce_passes_media_asset_through(self):
        asset = MediaAsset(asset_id="a", kind="image", source_url="https://x/a")
        assert MediaAsset.coerce(asset) is asset

    def test_coerce_accepts_camel_and_snake_wire_dicts(self):
        snake = MediaAsset.coerce({"asset_id": "a", "kind": "image",
                                   "source_url": "https://x/a", "file_id": "F"})
        camel = MediaAsset.coerce({"asset_id": "a", "kind": "image",
                                   "sourceUrl": "https://x/a", "fileId": "F"})
        assert snake.source_url == camel.source_url == "https://x/a"
        assert snake.file_id == camel.file_id == "F"

    def test_delivery_variant_values_match_wire_strategy_strings(self):
        assert DeliveryVariant.TELEGRAM_FILE_ID.value == "telegram_file_id"
        assert DeliveryVariant.REMOTE_URL.value == "remote_url"
        assert DeliveryVariant.LOCAL_UPLOAD.value == "local_upload"
        # str-Enum: existing string comparisons and JSON serialization keep working
        assert DeliveryVariant.TELEGRAM_FILE_ID == "telegram_file_id"
