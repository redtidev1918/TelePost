"""Application-service tests: idempotency, dedupe and atomic review claims.

These cover the converged publication core that chat, review and HTTP paths
share. Real SQLite (tmp file) is used for the concurrency claim test; the
delivery transport is always an in-memory fake.
"""
import asyncio

import pytest

from database import db_manager
from telepost.application.publication import (
    PublicationService, PublishCommand,
)
from telepost.domain.delivery import (
    DeliveryRequest, DeliveryResult, DeliveredMessage, MediaItem, MediaKind,
)
from telepost.storage.sqlite.ledger import DeliveryLedgerRepository
from telepost.storage.sqlite.reviews import NewReview, ReviewRepository


class RecordingDelivery:
    def __init__(self, result=None):
        self.calls = 0
        self._result = result

    async def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        self.calls += 1
        if self._result is not None:
            return self._result
        message = DeliveredMessage(
            chat_id=request.chat_id, message_id=1000 + self.calls,
            kind=MediaKind.PHOTO, file_id=f"fid-{self.calls}",
        )
        return DeliveryResult.delivered([message])


class UncertainDelivery:
    async def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        return DeliveryResult.uncertain("response lost")


def _command(key="k-1", *, target="", work="", pixiv=""):
    return PublishCommand(
        chat_id="@channel",
        items=[MediaItem.file_id("photo", "PHOTO-1")],
        caption_data={"tags": "#x"},
        user_id=7,
        idempotency_key=key,
        target_id=target,
        work_type=work,
        pixiv_id=pixiv,
    )


@pytest.fixture
async def ledger_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "core.db"))
    await db_manager.init_db()
    return DeliveryLedgerRepository()


@pytest.mark.asyncio
async def test_same_idempotency_key_delivers_only_once(ledger_db):
    delivery = RecordingDelivery()
    service = PublicationService(
        delivery=delivery, ledger=ledger_db, link_builder=lambda mid: f"/{mid}",
    )

    first = await service.publish(_command("client-key"))
    second = await service.publish(_command("client-key"))

    assert first.status == "published" and first.message_id == 1001
    assert delivery.calls == 1
    assert second.reused and second.reuse_reason == "idempotent_replay"
    assert second.message_id == 1001


@pytest.mark.asyncio
async def test_ten_repeated_requests_send_exactly_once(ledger_db):
    delivery = RecordingDelivery()
    service = PublicationService(delivery=delivery, ledger=ledger_db)
    outcomes = await asyncio.gather(
        *[service.publish(_command("hot-key")) for _ in range(10)]
    )
    message_ids = {o.message_id for o in outcomes if o.status == "published"}
    assert delivery.calls == 1
    assert message_ids == {1001}


@pytest.mark.asyncio
async def test_different_key_same_work_is_historical_duplicate(ledger_db):
    delivery = RecordingDelivery()
    service = PublicationService(
        delivery=delivery, ledger=ledger_db,
        dedup_window_seconds=7 * 24 * 3600,
    )
    first = await service.publish(
        _command("key-a", target="pixiv", work="illust", pixiv="999")
    )
    second = await service.publish(
        _command("key-b", target="pixiv", work="illust", pixiv="999")
    )
    assert first.message_id == 1001
    assert delivery.calls == 1
    assert second.reused and second.reuse_reason == "duplicate_existing"


@pytest.mark.asyncio
async def test_uncertain_delivery_is_never_acked_as_success_or_ledgered(ledger_db):
    service = PublicationService(
        delivery=UncertainDelivery(), ledger=ledger_db,
    )
    outcome = await service.publish(_command("uncertain-key"))
    assert outcome.status == "uncertain" and outcome.uncertain
    assert not outcome.ok
    # Nothing confirmed -> the key must not poison future retries.
    assert await ledger_db.find_by_key("uncertain-key") is None


# --------------------------------------------------------------------------
# Atomic review claim (SQLite)
# --------------------------------------------------------------------------

@pytest.fixture
async def review_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "reviews.db"))
    await db_manager.init_db()
    return ReviewRepository()


async def _insert_pending(repo, key="review-key") -> int:
    return await repo.insert(NewReview(
        idempotency_key=key, source="api", user_id=7, username="u",
        title="t", tags="#x", note="", link="", anonymous=False,
        spoiler=False, media=[{"type": "photo", "file_id": "F"}],
        documents=[], review_chat_id="-100", review_message_ids=[1],
    ))


@pytest.mark.asyncio
async def test_concurrent_approve_has_single_winner(review_db):
    review_id = await _insert_pending(review_db)

    async def claim_once():
        # SQLite may raise "database is locked" under a write race; a real
        # handler retries the claim (service-layer stale/fresh semantics),
        # so retry the same conditional UPDATE instead of treating lock
        # contention as a lost claim.
        for _ in range(50):
            try:
                return await review_db.claim_for_publishing(
                    review_id, stale_seconds=300
                )
            except Exception as exc:
                if "locked" not in str(exc).lower():
                    raise
                await asyncio.sleep(0.01)
        raise AssertionError("claim kept hitting database lock")

    results = await asyncio.gather(claim_once(), claim_once())
    winners = [claimed for claimed, _ in results]
    assert sorted(winners) == [False, True]
    for _, row in results:
        assert row["status"] == "publishing"


@pytest.mark.asyncio
async def test_stale_publishing_row_is_reclaimable(review_db):
    review_id = await _insert_pending(review_db, key="stale-key")
    claimed, _ = await review_db.claim_for_publishing(review_id, stale_seconds=300)
    assert claimed

    # Simulate a crashed publisher: force the timestamp far into the past.
    import time
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE pending_reviews SET updated_at=? WHERE id=?",
            (time.time() - 10_000, review_id),
        )
        await conn.commit()

    reclaimed, row = await review_db.claim_for_publishing(
        review_id, stale_seconds=300
    )
    assert reclaimed and row["status"] == "publishing"


@pytest.mark.asyncio
async def test_fresh_publishing_row_is_not_reclaimable(review_db):
    review_id = await _insert_pending(review_db, key="fresh-key")
    await review_db.claim_for_publishing(review_id, stale_seconds=300)
    claimed, row = await review_db.claim_for_publishing(
        review_id, stale_seconds=300
    )
    assert not claimed and row["status"] == "publishing"


@pytest.mark.asyncio
async def test_mark_published_is_guarded_by_publishing_status(review_db):
    review_id = await _insert_pending(review_db, key="guard-key")
    # Still pending: the guarded UPDATE must not touch the row.
    assert not await review_db.mark_published(
        review_id, actor=7, message_id=55
    )
    claimed, _ = await review_db.claim_for_publishing(review_id, stale_seconds=300)
    assert claimed
    assert await review_db.mark_published(
        review_id, actor=7, message_id=55
    )
    row = await review_db.get(review_id)
    assert row["status"] == "published" and row["published_message_id"] == 55
