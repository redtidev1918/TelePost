"""Tests for the new PTB-free application/domain/delivery core.

These tests never touch Telegram or SQLite: the planner is a pure function,
the executor only knows a Sender protocol, the review rules are pure state
transitions, and PublicationService is driven by fakes.
"""
import pytest

from telepost.domain.delivery import (
    DeliveryRequest, DeliveryResult, DeliveryState, DeliveredMessage,
    MediaItem, MediaKind, ReplyMode,
)
from telepost.domain.review import (
    ReviewStatus, ReviewTransitionError, can_claim, can_reject, can_edit,
    next_after_outcome, assert_can_claim,
)
from telepost.telegram.delivery.planner import (
    BatchKind, PlanningOrder, plan_delivery,
)
from telepost.telegram.delivery.executor import (
    NetworkFailure, execute_plan,
)


# --------------------------------------------------------------------------
# Review state machine
# --------------------------------------------------------------------------

@pytest.mark.parametrize("current,stale,expected", [
    (ReviewStatus.PENDING, False, True),
    (ReviewStatus.FAILED, False, True),
    (ReviewStatus.PUBLISHING, False, False),   # live claim: concurrent approve loses
    (ReviewStatus.PUBLISHING, True, True),     # crashed worker: stale reclaim
    (ReviewStatus.PUBLISHED, False, False),
    (ReviewStatus.REJECTED, False, False),
    (ReviewStatus.EXPIRED, True, False),
    (ReviewStatus.DELETED, True, False),
])
def test_review_claim_rules(current, stale, expected):
    assert can_claim(current, stale=stale) is expected


def test_review_editable_rules():
    assert can_edit(ReviewStatus.PENDING)
    assert can_edit(ReviewStatus.FAILED)
    for status in (ReviewStatus.PUBLISHING, ReviewStatus.PUBLISHED,
                   ReviewStatus.REJECTED, ReviewStatus.EXPIRED,
                   ReviewStatus.DELETED):
        assert not can_reject(status)


def test_assert_can_claim_raises_typed_error():
    with pytest.raises(ReviewTransitionError):
        assert_can_claim(ReviewStatus.PUBLISHED)


def test_next_after_outcome():
    assert next_after_outcome(True) is ReviewStatus.PUBLISHED
    assert next_after_outcome(False) is ReviewStatus.FAILED


# --------------------------------------------------------------------------
# Planner (pure)
# --------------------------------------------------------------------------

def _fid(kind, index):
    return MediaItem.file_id(kind, f"{kind}-{index}")


def _visuals(n):
    return [_fid("photo", i) for i in range(n)]


def test_single_photo_is_a_single_batch():
    plan = plan_delivery(_visuals(1))
    assert len(plan.batches) == 1
    assert plan.batches[0].kind is BatchKind.SINGLE


def test_ten_photos_make_one_album():
    plan = plan_delivery(_visuals(10))
    assert len(plan.batches) == 1
    assert plan.batches[0].is_album and len(plan.batches[0].items) == 10


def test_eleven_photos_split_into_album_of_ten_plus_single():
    plan = plan_delivery(_visuals(11))
    assert [len(b.items) for b in plan.batches] == [10, 1]
    assert plan.batches[0].is_album and not plan.batches[1].is_album


def test_photo_and_video_share_visual_album():
    items = [_fid("photo", 1), _fid("video", 1), _fid("photo", 2)]
    plan = plan_delivery(items)
    assert len(plan.batches) == 1
    assert plan.batches[0].family == "visual"
    assert len(plan.batches[0].items) == 3


def test_animation_is_always_standalone():
    items = _visuals(2) + [_fid("animation", 1)] + _visuals(2)
    plan = plan_delivery(items)
    anim = [b for b in plan.batches if b.family == "animation"]
    assert len(anim) == 1 and len(anim[0].items) == 1


def test_audio_is_always_standalone():
    items = _visuals(2) + [_fid("audio", 1)]
    plan = plan_delivery(items)
    assert any(b.family == "audio" and not b.is_album for b in plan.batches)


def test_documents_form_their_own_albums():
    items = [_fid("photo", i) for i in range(3)] + [
        _fid("document", i) for i in range(3)
    ]
    plan = plan_delivery(items)
    families = [b.family for b in plan.batches]
    assert families == ["visual", "document"]
    assert all(b.is_album for b in plan.batches)


def test_family_order_puts_visuals_first():
    items = [_fid("document", 1), _fid("photo", 1)]
    plan = plan_delivery(items, ordering=PlanningOrder.FAMILY)
    assert plan.batches[0].family == "visual"


def test_default_ordering_keeps_the_artwork_order():
    items = [_fid("document", 1), _fid("photo", 1), _fid("photo", 2)]
    plan = plan_delivery(items)
    assert [b.family for b in plan.batches] == ["document", "visual"]


def test_input_ordering_preserves_upload_order_runs():
    items = [_fid("document", 1), _fid("photo", 1)]
    plan = plan_delivery(items, ordering=PlanningOrder.INPUT)
    assert plan.batches[0].family == "document"


# --------------------------------------------------------------------------
# Executor (fault injection against a fake Sender)
# --------------------------------------------------------------------------

class FakeSender:
    def __init__(self, album_returns=None, album_raises=None,
                 single_raises=None, count_mismatch=False):
        self.album_calls = []
        self.single_calls = []
        self._album_returns = album_returns
        self._album_raises = album_raises or []
        self._single_raises = single_raises or []
        self._count_mismatch = count_mismatch
        self._id = 100

    def _delivered(self, item):
        mid = self._id
        self._id += 1
        return DeliveredMessage(
            chat_id=-100, message_id=mid,
            kind=item.kind, file_id=f"fid-{mid}",
        )

    async def send_album(self, batch, *, reply_to, caption):
        self.album_calls.append({"reply_to": reply_to, "caption": caption,
                                 "size": len(batch.items)})
        if self._album_raises:
            raise self._album_raises.pop(0)
        count = len(batch.items)
        if self._count_mismatch:
            count -= 1
        return [self._delivered(item) for item in batch.items[:count]]

    async def send_single(self, item, *, reply_to, caption):
        self.single_calls.append({"reply_to": reply_to, "caption": caption})
        if self._single_raises:
            raise self._single_raises.pop(0)
        return self._delivered(item)


def _plan(items=None, reply_mode=ReplyMode.CHAIN, anchor=None):
    return plan_delivery(
        items or _visuals(3), reply_mode=reply_mode, anchor_message_id=anchor,
    )


@pytest.mark.asyncio
async def test_executor_success_chain_replies():
    sender = FakeSender()
    result = await execute_plan(_plan(), sender, caption="cap")
    assert result.ok and len(result.messages) == 3
    # CHAIN: first replies to anchor(None), each later to previous message.
    assert sender.album_calls[0]["reply_to"] is None
    assert sender.album_calls[0]["caption"] == "cap"


@pytest.mark.asyncio
async def test_executor_anchor_replies_to_anchor():
    sender = FakeSender()
    result = await execute_plan(_plan(anchor=77), sender)
    assert result.ok
    assert sender.album_calls[0]["reply_to"] == 77


@pytest.mark.asyncio
async def test_executor_caption_only_on_first_message():
    sender = FakeSender()
    plan = plan_delivery(_visuals(11))
    result = await execute_plan(plan, sender, caption="cap")
    assert result.ok
    assert sender.album_calls[0]["caption"] == "cap"
    assert sender.single_calls[0]["caption"] is None


@pytest.mark.asyncio
async def test_executor_post_mode_all_replies_to_first_message():
    sender = FakeSender()
    plan = plan_delivery(_visuals(4), reply_mode=ReplyMode.POST)
    result = await execute_plan(plan, sender, caption="cap")
    assert result.ok
    # One album of 4 → single call reply None; force singles via count=...
    # instead use two families: visual album + document album
    sender2 = FakeSender()
    items = _visuals(2) + [_fid("document", i) for i in range(2)]
    plan2 = plan_delivery(items, reply_mode=ReplyMode.POST)
    result2 = await execute_plan(plan2, sender2, caption="cap")
    assert result2.ok
    first_id = result2.messages[0].message_id
    assert [c["reply_to"] for c in sender2.album_calls] == [None, first_id]


@pytest.mark.asyncio
async def test_executor_album_network_failure_is_uncertain_and_no_fallback():
    sender = FakeSender(album_raises=[NetworkFailure("timeout")])
    result = await execute_plan(_plan(), sender)
    assert result.is_uncertain
    assert sender.single_calls == []   # never degrade to singles


@pytest.mark.asyncio
async def test_executor_album_other_error_degrades_to_singles():
    sender = FakeSender(album_raises=[ValueError("bad request")])
    result = await execute_plan(_plan(), sender, caption="cap")
    assert result.ok
    assert len(sender.single_calls) == 3
    assert sender.single_calls[0]["caption"] == "cap"
    assert sender.single_calls[1]["caption"] is None


@pytest.mark.asyncio
async def test_executor_single_network_failure_is_uncertain():
    sender = FakeSender(single_raises=[NetworkFailure("timeout")])
    # Force singles: 3 photos where album deterministically fails first...
    # simpler: one single photo with a single-side network error.
    sender = FakeSender(single_raises=[NetworkFailure("timeout")])
    plan = plan_delivery([_fid("animation", 1)])
    result = await execute_plan(plan, sender)
    assert result.is_uncertain


@pytest.mark.asyncio
async def test_executor_single_other_error_is_failed_with_known_messages():
    sender = FakeSender(single_raises=[ValueError("bad media")])
    plan = plan_delivery([_fid("photo", 1), _fid("photo", 2)])
    plan_album_fail = FakeSender(
        album_raises=[ValueError("bad album")],
        single_raises=[None, ValueError("second failed")],
    )
    # First single succeeds, second deterministically fails.
    async def send_album_ok(*a, **k):
        raise ValueError("bad album")
    sender = FakeSender(album_raises=[ValueError("bad album")])

    async def broken_send_single(item, *, reply_to, caption):
        sender.single_calls.append({"reply_to": reply_to, "caption": caption})
        if len(sender.single_calls) == 2:
            raise ValueError("second failed")
        return sender._delivered(item)
    sender.send_single = broken_send_single
    result = await execute_plan(plan, sender, caption="cap")
    assert result.state is DeliveryState.FAILED
    assert len(result.known_messages) == 1


@pytest.mark.asyncio
async def test_executor_album_count_mismatch_is_uncertain():
    sender = FakeSender(count_mismatch=True)
    result = await execute_plan(_plan(_visuals(3)), sender)
    assert result.is_uncertain
    # The two messages Telegram did return are preserved for rollback.
    assert len(result.known_messages) == 2


@pytest.mark.asyncio
async def test_executor_preserves_original_network_exception():
    original = TimeoutError("response lost")

    class _NF(NetworkFailure):
        pass

    failure = NetworkFailure("timeout", original=original)
    sender = FakeSender(album_raises=[failure])
    result = await execute_plan(_plan(), sender)
    assert result.error is original


# --------------------------------------------------------------------------
# DeliveryResult invariants
# --------------------------------------------------------------------------

def test_empty_delivered_becomes_failed():
    result = DeliveryResult.delivered([])
    assert not result.ok and result.state is DeliveryState.FAILED


def test_channel_message_ids_filter_by_chat():
    messages = [
        DeliveredMessage(chat_id=-100, message_id=1, kind=MediaKind.PHOTO,
                         file_id="a"),
        DeliveredMessage(chat_id=-200, message_id=2, kind=MediaKind.PHOTO,
                         file_id="b"),
    ]
    result = DeliveryResult.delivered(messages)
    assert result.channel_message_ids(-100) == [1]
