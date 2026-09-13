"""Regression tests: media classification + album planning.

Production bug: a Pixiv multi-image gallery reached the review group as a photo
album followed by a separate batch of *documents*, including files well under
10 MB that could and should have been photos.

Root cause (``telepost/telegram/delivery/preparation.py``): every violation that
could have been fixed by producing a smaller photo — a width+height sum over
Telegram's 10000 limit, an over-budget decode, a byte cap the quality ladder
could not reach — was answered with ``MediaKind.DOCUMENT`` instead. The planner
then grouped those documents into their own album.

The classification decision is now always taken on the FINAL artifact, and a
photo is only abandoned when no compliant photo artifact is reachable.

Telegram constraints asserted here come from https://core.telegram.org/bots/api
(sendPhoto: "at most 10 MB in size", "width and height must not exceed 10000 in
total", "Width and height ratio must be at most 20"; sendMediaGroup: "2-10
items"; sendAnimation/sendDocument: "up to 50 MB").
"""
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from telepost.domain.delivery import DeliveredMessage, LocalFile, MediaItem, MediaKind
from telepost.telegram.delivery import preparation
from telepost.telegram.delivery.planner import BatchKind, plan_delivery

PHOTO_LIMIT = preparation.PHOTO_MAX_BYTES


# --------------------------------------------------------------------------
# fixtures built at test time (no binaries in the repo)
# --------------------------------------------------------------------------

def _noise_jpeg(path, size, quality=95):
    """A JPEG whose byte size is driven by real image entropy."""
    image = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    image.save(path, "JPEG", quality=quality)
    return path


def _flat_jpeg(path, size, quality=70):
    """Compressible high-resolution JPEG: big pixels, small file."""
    Image.linear_gradient("L").resize(size).convert("RGB").save(
        path, "JPEG", quality=quality
    )
    return path


def _animated(path, frames=4, fmt="GIF"):
    images = [Image.new("RGB", (64, 64), (i * 40 % 255, 20, 30))
              for i in range(frames)]
    images[0].save(path, format=fmt, save_all=True, append_images=images[1:],
                   duration=100, loop=0)
    return path


def _gallery(tmp_path):
    """small / large / small / large in *source* order."""
    return [
        _flat_jpeg(tmp_path / "g0.jpg", (900, 700)),
        _noise_jpeg(tmp_path / "g1.jpg", (3000, 3000)),
        _flat_jpeg(tmp_path / "g2.jpg", (800, 600)),
        _noise_jpeg(tmp_path / "g3.jpg", (3000, 3000)),
    ]


def _flatten(plan):
    return [
        item.filename or item.telegram_file_id
        for batch in plan.batches for item in batch.items
    ]


def _fake_message(index):
    return SimpleNamespace(
        message_id=index + 1,
        chat=SimpleNamespace(id=-100123),
        photo=[SimpleNamespace(file_id=f"F{index}")],
    )


# --------------------------------------------------------------------------
# 1-4: single-image classification
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_single_ordinary_jpeg_is_a_photo(tmp_path):
    path = _flat_jpeg(tmp_path / "ordinary.jpg", (800, 600))
    result = preparation.MediaPreparationPolicy().prepare(str(path))
    assert result.kind is MediaKind.PHOTO
    assert result.reason is preparation.PreparationDecision.PASS_THROUGH
    assert result.delivery_source == str(path)
    assert result.decision["violation"] is None


@pytest.mark.unit
def test_nine_megabyte_jpeg_passes_through_untouched(tmp_path):
    path = _noise_jpeg(tmp_path / "nine.jpg", (2800, 2800), quality=95)
    assert PHOTO_LIMIT - 1_000_000 < path.stat().st_size <= PHOTO_LIMIT

    result = preparation.MediaPreparationPolicy().prepare(str(path))
    assert result.kind is MediaKind.PHOTO
    assert result.reason is preparation.PreparationDecision.PASS_THROUGH
    assert result.delivery_source == str(path), "a legal photo must not be re-encoded"
    assert result.decision["decision"] == "photo_passthrough"
    assert result.decision["reason"] == "already_within_limits"


@pytest.mark.unit
def test_fifteen_megabyte_jpeg_is_compressed_and_stays_a_photo(tmp_path):
    """The old ladder could only lower quality; the target kind must stay photo."""
    path = _noise_jpeg(tmp_path / "fifteen.jpg", (3500, 3500), quality=95)
    assert path.stat().st_size > 10 * 1024 * 1024

    result = preparation.MediaPreparationPolicy().prepare(str(path))
    assert result.kind is MediaKind.PHOTO, "an oversized JPEG must not become a document"
    assert result.reason is preparation.PreparationDecision.SAFE_COMPRESS
    assert result.decision["violation"] == "photo_bytes_exceeded"

    final = result.decision
    assert final["final_bytes"] <= PHOTO_LIMIT
    assert final["final_width"] + final["final_height"] <= 10000
    assert final["final_format"] == "JPEG"
    assert result.delivery_source != str(path)
    assert path.stat().st_size > 10 * 1024 * 1024, "the original stays immutable"


@pytest.mark.unit
def test_high_resolution_small_bytes_jpeg_is_downscaled_to_a_photo(tmp_path):
    """Primary regression: 6000x5000 / ~0.5 MB used to be sent as a document.

    width + height = 11000 > 10000, so Telegram refuses it as a photo — but a
    downscale produces a legal photo, which is always the preferred target.
    """
    path = _flat_jpeg(tmp_path / "huge-small.jpg", (6000, 5000))
    assert path.stat().st_size < PHOTO_LIMIT, "the byte cap is not the problem here"

    result = preparation.MediaPreparationPolicy().prepare(str(path))
    assert result.kind is MediaKind.PHOTO, "dimension violation must downscale, not demote"
    assert result.reason is preparation.PreparationDecision.SAFE_COMPRESS

    payload = result.decision
    assert payload["violation"] == "photo_dimensions_exceeded"
    assert payload["resized"] is True
    assert payload["final_width"] + payload["final_height"] <= 10000
    assert payload["final_bytes"] <= PHOTO_LIMIT
    assert payload["final_format"] == "JPEG"
    # the audit line must reconstruct the whole decision
    assert {
        "original_bytes", "final_bytes", "final_width", "final_height",
        "final_format", "original_format", "target_kind", "decision",
        "fallback_reason", "violation",
    } <= set(payload)
    assert payload["original_format"] == "JPEG"
    assert payload["target_kind"] == "photo"


@pytest.mark.unit
def test_decode_budget_routes_to_a_reduced_decode_instead_of_document(tmp_path):
    """The budget is a routing decision: JPEG can decode at a reduced scale."""
    path = _flat_jpeg(tmp_path / "budget.jpg", (6000, 5000))
    policy = preparation.MediaPreparationPolicy(
        limits=preparation.PhotoLimits(max_bytes=PHOTO_LIMIT, decode_budget_bytes=1)
    )
    result = policy.prepare(str(path))
    assert result.kind is MediaKind.PHOTO
    # Image.draft() dropped DCT blocks before allocating pixels.
    assert result.decision["final_width"] <= 3000


@pytest.mark.unit
def test_impossible_case_records_an_explicit_document_fallback_reason(tmp_path):
    """A PNG has no reduced-scale decode in Pillow: over the budget *and* over
    the dimension rule, no compliant photo exists — and the log says exactly why."""
    path = tmp_path / "huge.png"
    Image.new("RGBA", (6000, 5000), (10, 20, 30, 255)).save(path)
    assert path.stat().st_size < PHOTO_LIMIT

    result = preparation.MediaPreparationPolicy().prepare(str(path))
    assert result.kind is MediaKind.DOCUMENT
    assert result.reason is preparation.PreparationDecision.DOCUMENT_FALLBACK
    assert result.delivery_source == str(path)
    payload = result.decision
    assert payload["violation"] == "photo_dimensions_exceeded"
    assert payload["fallback_reason"] == "decode_budget_exceeded"
    assert payload["target_kind"] == "document"


# --------------------------------------------------------------------------
# 5-7: album planning
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_gallery_of_interleaved_sizes_plans_photo_albums_in_source_order(tmp_path):
    paths = _gallery(tmp_path)
    source_order = [path.name for path in paths]
    items = [
        MediaItem.local("photo", str(path), path.name) for path in paths
    ]

    prepared = preparation.reclassify_oversized(items)
    assert all(item.kind is MediaKind.PHOTO for item in prepared), \
        "no item of this gallery needs to be a document"

    plan = plan_delivery(prepared, album_size=10)
    assert len(plan.batches) == 1
    assert plan.batches[0].kind is BatchKind.ALBUM
    assert [batch.family for batch in plan.batches] == ["visual"]
    assert _flatten(plan) == source_order, "artwork order must survive preparation"


@pytest.mark.unit
def test_ten_items_make_one_album():
    plan = plan_delivery(
        [MediaItem.file_id("photo", f"p{i}") for i in range(10)]
    )
    assert len(plan.batches) == 1
    assert plan.batches[0].is_album


@pytest.mark.unit
@pytest.mark.parametrize("count", [11, 23])
def test_more_than_ten_items_split_into_albums_keeping_order(count):
    items = [MediaItem.file_id("photo", f"p{i}") for i in range(count)]
    plan = plan_delivery(items, album_size=10)
    assert [len(batch.items) for batch in plan.batches] == (
        [10] * (count // 10) + ([count % 10] if count % 10 else [])
    )
    assert _flatten(plan) == [f"p{i}" for i in range(count)]


@pytest.mark.unit
def test_mixed_gallery_keeps_order_and_adds_no_document_split(tmp_path):
    """A gallery that can be made fully compliant must never split photo/document."""
    paths = _gallery(tmp_path)
    animated = _animated(tmp_path / "loop.gif")
    items = [MediaItem.local("photo", str(path), path.name) for path in paths]
    # an animation that arrived misclassified as a photo
    items.insert(2, MediaItem.local("photo", str(animated), "loop.gif"))

    prepared = preparation.reclassify_oversized(items)
    plan = plan_delivery(prepared, album_size=10)

    assert _flatten(plan) == ["g0.jpg", "g1.jpg", "loop.gif", "g2.jpg", "g3.jpg"]
    assert {batch.family for batch in plan.batches} == {"visual", "animation"}
    assert all(
        item.kind is not MediaKind.DOCUMENT
        for batch in plan.batches for item in batch.items
    )
    families = [batch.family for batch in plan.batches]
    assert families.count("animation") == 1, "the animation stays standalone"


# --------------------------------------------------------------------------
# 8: animations are never staticized
# --------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("fmt,suffix", [("GIF", ".gif"), ("WEBP", ".webp"),
                                        ("PNG", ".png")])
def test_animated_images_are_never_staticized(tmp_path, fmt, suffix):
    path = _animated(tmp_path / f"loop{suffix}", fmt=fmt)
    pytest.importorskip("PIL")
    if Image.open(path).n_frames < 2:  # pragma: no cover - Pillow build without support
        pytest.skip(f"{fmt} animation not supported by this Pillow build")

    result = preparation.MediaPreparationPolicy().prepare(str(path))
    assert result.kind is MediaKind.ANIMATION
    assert result.reason is preparation.PreparationDecision.ANIMATION_PRESERVED
    assert result.delivery_source == str(path), "the animation must not be re-encoded"
    assert result.decision["target_kind"] == "animation"
    assert result.decision["reason"] == "animated_never_static"


@pytest.mark.unit
def test_animation_arriving_as_photo_is_reclassified_and_explained(tmp_path):
    """An animated item tagged 'photo' must not silently become a static photo."""
    path = _animated(tmp_path / "loop.gif")
    items, decisions = preparation.reclassify_oversized_dicts(
        [{"kind": "photo", "path": str(path), "filename": "loop.gif"}]
    )
    assert items[0]["kind"] == "animation"
    assert items[0]["preparation_reason"] == "animation_preserved"
    assert decisions[0]["reason"] == "animated_never_static"


@pytest.mark.unit
def test_animation_over_the_animation_byte_limit_becomes_a_document(tmp_path, monkeypatch):
    path = _animated(tmp_path / "loop.gif")
    monkeypatch.setattr(preparation, "MEDIA_MAX_BYTES", 1)
    result = preparation.MediaPreparationPolicy().prepare(str(path))
    assert result.kind is MediaKind.DOCUMENT
    assert result.decision["reason"] == "animation_exceeds_animation_byte_limit"


# --------------------------------------------------------------------------
# 9: bounded photo retry on a real Telegram rejection
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_photo_rejected_by_telegram_is_reprocessed_once_then_sent(tmp_path):
    from telegram.error import BadRequest
    from telepost.telegram.review_stager import TelegramReviewStager

    path = _flat_jpeg(tmp_path / "photo.jpg", (4000, 3000))
    sent_paths = []
    bot = AsyncMock()

    async def send_photo(**kwargs):
        sent_paths.append(kwargs["photo"].input_file_content.name)
        if len(sent_paths) == 1:
            raise BadRequest("Photo_invalid_dimensions")
        return _fake_message(0)

    bot.send_photo = AsyncMock(side_effect=send_photo)
    stager = TelegramReviewStager(bot, -100123, sleep=AsyncMock())
    item = {"kind": "photo", "path": str(path), "filename": "photo.jpg"}

    message = await stager._local_single(item, None, False, None)

    assert message.message_id == 1
    assert len(sent_paths) == 2, "exactly ONE bounded re-process attempt"
    assert sent_paths[0] == str(path)
    assert sent_paths[1] != str(path), "the retry must use a fresh derivative"
    assert item["kind"] == "photo", "the retry stays a photo"
    bot.send_document.assert_not_awaited()


class _RejectingPhotoSender:
    """Fake transport for the executor: rejects N photo sends, then succeeds."""

    def __init__(self, rejections=1, error="Bad Request: PHOTO_INVALID_DIMENSIONS"):
        self.rejections = rejections
        self.error = error
        self.calls = []

    async def send_album(self, batch, *, reply_to, caption):
        raise AssertionError("this test never plans an album")

    async def send_single(self, item, *, reply_to, caption):
        self.calls.append((item.kind, item.local_path, item.filename))
        if item.kind is MediaKind.PHOTO and self.rejections > 0:
            self.rejections -= 1
            raise RuntimeError(self.error)
        return DeliveredMessage(chat_id=-100, message_id=len(self.calls),
                                kind=item.kind)


@pytest.mark.asyncio
async def test_executor_reprocesses_a_telegram_rejected_photo_once(tmp_path):
    from telepost.telegram.delivery.executor import execute_plan

    path = _flat_jpeg(tmp_path / "channel.jpg", (4000, 3000))
    item = MediaItem.local("photo", str(path), "channel.jpg")
    sender = _RejectingPhotoSender(rejections=1)

    result = await execute_plan(plan_delivery([item]), sender)

    assert result.ok, result.reason
    assert len(sender.calls) == 2, "exactly one retry"
    assert sender.calls[0][0] is MediaKind.PHOTO
    assert sender.calls[0][1] == str(path)
    assert sender.calls[1][0] is MediaKind.PHOTO, "the retry is still a photo"
    assert sender.calls[1][1] != str(path)
    assert not os.path.exists(sender.calls[1][1]), "the derivative is cleaned up"
    assert path.exists()


@pytest.mark.asyncio
async def test_executor_falls_back_to_document_only_after_the_retry(tmp_path):
    from telepost.telegram.delivery.executor import execute_plan

    path = _flat_jpeg(tmp_path / "channel.jpg", (4000, 3000))
    item = MediaItem.local("photo", str(path), "channel.jpg")
    sender = _RejectingPhotoSender(rejections=99)

    result = await execute_plan(plan_delivery([item]), sender)

    assert result.ok, "the artwork must still ship, as a document"
    assert [call[0] for call in sender.calls] == [
        MediaKind.PHOTO, MediaKind.PHOTO, MediaKind.DOCUMENT,
    ]
    assert sender.calls[2][1] == str(path), "the document is the original"


@pytest.mark.asyncio
async def test_executor_does_not_retry_unrelated_photo_errors(tmp_path):
    from telepost.telegram.delivery.executor import execute_plan

    path = _flat_jpeg(tmp_path / "channel.jpg", (4000, 3000))
    item = MediaItem.local("photo", str(path), "channel.jpg")
    sender = _RejectingPhotoSender(rejections=99, error="Bad Request: chat not found")

    result = await execute_plan(plan_delivery([item]), sender)

    assert not result.ok
    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_photo_rejected_twice_falls_back_to_document(tmp_path):
    from telegram.error import BadRequest
    from telepost.telegram.review_stager import TelegramReviewStager

    path = _flat_jpeg(tmp_path / "photo.jpg", (4000, 3000))
    bot = AsyncMock()
    bot.send_photo = AsyncMock(side_effect=BadRequest("Photo_invalid_dimensions"))
    bot.send_document = AsyncMock(return_value=_fake_message(0))
    stager = TelegramReviewStager(bot, -100123, sleep=AsyncMock())
    item = {"kind": "photo", "path": str(path), "filename": "photo.jpg"}

    message = await stager._local_single(item, None, False, None)

    assert bot.send_photo.await_count == 2, "no unbounded retry loop"
    assert bot.send_document.await_count == 1
    assert item["kind"] == "document"
    assert item["preparation_reason"] == "photo_constraint_document_fallback"
    assert message.message_id == 1


@pytest.mark.asyncio
async def test_unrelated_photo_errors_are_not_retried(tmp_path):
    from telegram.error import BadRequest
    from telepost.telegram.review_stager import TelegramReviewStager

    path = _flat_jpeg(tmp_path / "photo.jpg", (4000, 3000))
    bot = AsyncMock()
    bot.send_photo = AsyncMock(side_effect=BadRequest("Chat not found"))
    stager = TelegramReviewStager(bot, -100123, sleep=AsyncMock())

    with pytest.raises(BadRequest):
        await stager._local_single(
            {"kind": "photo", "path": str(path), "filename": "photo.jpg"},
            None, False, None,
        )
    assert bot.send_photo.await_count == 1


# --------------------------------------------------------------------------
# 11: the review group sees one ordered photo album (end to end)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_review_group_gallery_is_one_photo_album_in_source_order(tmp_path):
    from telepost.telegram.review_stager import TelegramReviewStager

    paths = _gallery(tmp_path)
    files = [{"kind": "photo", "path": str(path), "filename": path.name}
             for path in paths]
    bot = AsyncMock()

    async def send_media_group(**kwargs):
        return [_fake_message(i) for i in range(len(kwargs["media"]))]

    bot.send_media_group = AsyncMock(side_effect=send_media_group)
    stager = TelegramReviewStager(bot, -100123, sleep=AsyncMock())

    media, documents, _ids, _decisions = await stager.stage_local(
        files, caption="caption", spoiler=False
    )

    assert bot.send_media_group.await_count == 1
    assert bot.send_document.await_count == 0
    assert bot.send_photo.await_count == 0
    assert len(bot.send_media_group.call_args.kwargs["media"]) == 4
    assert documents == []
    assert [m["type"] for m in media] == ["photo"] * 4
    assert [m["file_id"] for m in media] == ["F0", "F1", "F2", "F3"]


@pytest.mark.unit
def test_prepared_derivatives_are_temp_files_that_can_be_cleaned(tmp_path):
    """Memory hygiene: one derivative per transformed image, removed by cleanup."""
    path = _noise_jpeg(tmp_path / "fifteen.jpg", (3500, 3500), quality=95)
    item = MediaItem.local("photo", str(path), "fifteen.jpg")
    prepared = preparation.reclassify_oversized([item])[0]
    derivative = prepared.source.path

    assert isinstance(prepared.source, LocalFile)
    assert prepared.source.temporary is True
    assert prepared.source.original_path == str(path)
    assert os.path.exists(derivative)

    preparation.cleanup_prepared([prepared])
    assert not os.path.exists(derivative)
    assert path.exists()
