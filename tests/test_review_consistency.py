"""State-consistency invariants for review publish + spoiler (mask).

These tests pin the business guarantees behind the review-group UI:

* Invariant A  ``review.publish_state == channel.publish_state``: the review
  row only ever reaches ``published`` (and the review-group text only ever says
  "已发布") AFTER a *confirmed* channel delivery returns a real message id.
  A lost/uncertain/timeout delivery marks the review ``failed`` — it never
  fabricates a publish.
* Invariant B  ``review.mask == channel.mask``: the mask (``spoiler``) used for
  the channel media group is taken from the persisted review row at claim time,
  so the review-group "遮罩：开/关" projection, the publish intent and the
  final channel mask all share one source of truth.
* Guarding: a stale/late writer (old worker, late callback) can never overwrite
  a row that a newer actor already resolved.
"""

import time as _time

import pytest
from telegram.error import TimedOut
from unittest.mock import MagicMock

from database import db_manager
from services.review_service import PublishFailedError, ReviewService
from telepost.storage.sqlite.reviews import NewReview, ReviewRepository


class FakePublisher:
    """Mimics ``handlers.publish.publish_from_file_ids`` and records the
    exact spoiler/message the channel would receive."""

    def __init__(self, message_id=99):
        self.message_id = message_id
        self.calls = []

    async def __call__(self, bot, media, documents, **kwargs):
        self.calls.append(dict(kwargs))
        return {
            "status": "published",
            "message_id": self.message_id,
            "link": f"https://t.me/c/1/{self.message_id}",
            "media_count": len(list(media)),
            "document_count": len(list(documents)),
            "delivery_status": "published",
        }


class FailingPublisher:
    """Raises on delivery like a lost/uncertain Telegram response."""

    def __init__(self, error=None):
        self._error = error or TimedOut("response lost")
        self.calls = []

    async def __call__(self, bot, media, documents, **kwargs):
        self.calls.append(dict(kwargs))
        raise self._error


@pytest.fixture
async def consistency_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "reviews.db")
    monkeypatch.setattr(db_manager, "DB_PATH", db_path)
    await db_manager.init_db()
    return db_path


async def _insert(consistency_db, *, spoiler=False, idempotency_key="k"):
    repo = ReviewRepository()
    return await repo.insert(NewReview(
        idempotency_key=idempotency_key, source="api", user_id=7,
        username="tester", title="", tags="#pixiv", note="", link="",
        anonymous=False, spoiler=spoiler,
        media=[{"type": "photo", "file_id": "STAGED"}], documents=[],
        review_chat_id="-100123", review_message_ids=[10],
    ))


async def _row(review_id):
    return await ReviewRepository().get(review_id)


# ---------------------------------------------------------------------------
# Invariant B — mask (spoiler) consistency: review == channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_without_override_publishes_review_db_mask(consistency_db):
    """The API/Telegram approve path that does NOT pass a client spoiler must
    publish exactly the mask persisted on the review row (button path)."""
    for spoiler in (True, False):
        review_id = await _insert(consistency_db, spoiler=spoiler,
                                  idempotency_key=f"k-{spoiler}")
        publisher = FakePublisher()
        result = await ReviewService(publisher=publisher).approve(
            MagicMock(), review_id, actor=11, source="test",
            notify_chat_submitter=False,
        )
        assert result.status == "published"
        assert publisher.calls[0]["spoiler"] is spoiler
        db_row = await _row(review_id)
        # review.mask  == channel.mask  (single DB source of truth)
        assert bool(db_row["spoiler"]) is bool(publisher.calls[0]["spoiler"])
        assert db_row["status"] == "published"


@pytest.mark.asyncio
async def test_latest_toggle_right_before_approve_wins(consistency_db):
    """A mask change right before publish (latest intended state) is what the
    channel receives — not a stale default/intent."""
    review_id = await _insert(consistency_db, spoiler=False)
    service = ReviewService()
    await service.toggle_spoiler(review_id, actor=11)  # false -> true
    assert bool((await _row(review_id))["spoiler"]) is True

    publisher = FakePublisher()
    await ReviewService(publisher=publisher).approve(
        MagicMock(), review_id, actor=11, source="test",
        notify_chat_submitter=False,
    )
    assert publisher.calls[0]["spoiler"] is True
    db_row = await _row(review_id)
    assert bool(db_row["spoiler"]) is True
    # 审核群显示已发布 + 遮罩开 ⇔ 频道确认存在对应成功发布 + 遮罩开
    assert bool(db_row["spoiler"]) is publisher.calls[0]["spoiler"]


@pytest.mark.asyncio
async def test_on_off_publish_pairs_match_independently(consistency_db):
    """Independent ON / OFF reviews each publish their own persisted mask."""
    on = await _insert(consistency_db, spoiler=True, idempotency_key="on")
    off = await _insert(consistency_db, spoiler=False, idempotency_key="off")
    on_pub = FakePublisher(message_id=100)
    off_pub = FakePublisher(message_id=101)
    await ReviewService(publisher=on_pub).approve(
        MagicMock(), on, actor=11, source="test", notify_chat_submitter=False)
    await ReviewService(publisher=off_pub).approve(
        MagicMock(), off, actor=11, source="test", notify_chat_submitter=False)
    assert on_pub.calls[0]["spoiler"] is True
    assert off_pub.calls[0]["spoiler"] is False
    assert (await _row(on))["status"] == "published"
    assert (await _row(off))["status"] == "published"


# ---------------------------------------------------------------------------
# Invariant A — publish state consistency: review == channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_published_only_after_channel_confirm(consistency_db):
    """Review reaches published with the SAME message id the confirmed channel
    delivery returned — a true projection of the channel result."""
    review_id = await _insert(consistency_db, spoiler=False)
    publisher = FakePublisher(message_id=777)
    result = await ReviewService(publisher=publisher).approve(
        MagicMock(), review_id, actor=11, source="test",
        notify_chat_submitter=False,
    )
    assert result.status == "published"
    assert result.message_id == 777
    db_row = await _row(review_id)
    assert db_row["status"] == "published"
    assert db_row["published_message_id"] == 777  # review == channel


@pytest.mark.asyncio
async def test_unconfirmed_delivery_never_marks_published(consistency_db):
    """A lost/uncertain delivery must NOT claim publish: the review is failed
    (retryable) with no channel message id recorded."""
    review_id = await _insert(consistency_db, spoiler=False)
    service = ReviewService(publisher=FailingPublisher())
    with pytest.raises(PublishFailedError) as exc_info:
        await service.approve(
            MagicMock(), review_id, actor=11, source="test",
            notify_chat_submitter=False,
        )
    # The failure hint tells the operator the publish outcome is uncertain —
    # encourage checking the channel before retrying, never claim success.
    assert "不确定" in exc_info.value.retry_hint
    db_row = await _row(review_id)
    assert db_row["status"] == "failed"       # NOT published
    assert db_row["published_message_id"] is None


# ---------------------------------------------------------------------------
# Guarding — a stale late writer cannot overwrite a newer actor's state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_late_mark_published_cannot_overwrite(consistency_db):
    """An old worker whose claim was already resolved (e.g. the row was failed
    by a newer actor after a timeout) cannot force the row back to published."""
    repo = ReviewRepository()
    review_id = await _insert(consistency_db, spoiler=False)
    claimed, _ = await repo.claim_for_publishing(review_id, stale_seconds=300)
    assert claimed is True

    # A newer actor already declared publish failed / resolved the row.
    assert await repo.mark_failed(review_id, "newer actor resolution") is True

    # The stale old worker's late confirmation is a no-op.
    assert await repo.mark_published(
        review_id, actor=11, message_id=999) is False
    db_row = await _row(review_id)
    assert db_row["status"] == "failed"      # not clobbered to published
    assert db_row["published_message_id"] is None


@pytest.mark.asyncio
async def test_publish_failed_is_retryable_later_to_published(consistency_db):
    """After a confirmed-delivery failure the same review can be reclaimed and
    then, once the channel *does* confirm, genuinely becomes published."""
    review_id = await _insert(consistency_db, spoiler=False)
    service = ReviewService(publisher=FailingPublisher())
    with pytest.raises(Exception):
        await service.approve(MagicMock(), review_id, actor=11, source="test",
                              notify_chat_submitter=False)
    assert (await _row(review_id))["status"] == "failed"

    publisher = FakePublisher(message_id=321)
    result = await ReviewService(publisher=publisher).approve(
        MagicMock(), review_id, actor=11, source="test",
        notify_chat_submitter=False)
    assert result.status == "published"
    db_row = await _row(review_id)
    assert db_row["status"] == "published"
    assert db_row["published_message_id"] == 321