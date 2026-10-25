"""Staging reliability repair (2026-10-25 incident, review #209 class).

Covers the five defects the incident exposed, as separate invariants:

A. Telegram ``retry_after`` is a minimum: the wait is paid in full (never
   truncated to 60s, never amplified exponentially), and a wait that cannot
   fit the remaining staging budget stops immediately instead of retrying.
B. A staging failure keeps its preview-message ids as durable external-effect
   evidence, and the ``review.failed`` audit carries the retry timeline.
C. A same-key retry of a staging-failed review RECOVERS it under the original
   review identity via a single-writer CAS; concurrent retries converge on
   ``recovery_in_progress``; rows that were ever actionable (control card,
   staged media, published, refetch) keep the stable terminal answer.
D. The reconciler never fabricates an actionable「投稿待审核」card over a
   0-media failed row.
E. (api mapping) ``recovery_in_progress`` answers 503 retryable-later.
"""
import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest
from telegram.error import RetryAfter

from database import db_manager
from handlers import review
from telepost.observability import audit as audit_mod


@pytest.fixture
async def review_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "reviews.db")
    monkeypatch.setattr(db_manager, "DB_PATH", db_path)
    monkeypatch.setattr(review, "REVIEW_CHAT_ID", -100123)
    monkeypatch.setattr(review, "ADMIN_IDS", [123456789])
    await db_manager.init_db()
    return db_path


def _command(key: str) -> "review.QueueCommand":
    return review.QueueCommand(
        user_id=7, username="flow", tags="#x", title="", note="", link="",
        anonymous=False, spoiler=False, source="api",
        idempotency_key=key, source_ref='{"executionId":"exec-staging"}',
        review_chat_id="-100123",
    )


class _Stager:
    """Minimal StagingPort double with per-test hooks."""

    def __init__(self, *, on_stage=None):
        self._on_stage = on_stage
        self.deleted = []
        self.deadline = "unset"
        self.stages = 0
        self.controls = 0

    def set_staging_deadline(self, deadline):
        self.deadline = deadline

    async def stage_file_ids(self, media, documents, *, caption, spoiler,
                             message_ids=None, cover_url=None, specs=None):
        self.stages += 1
        ids = message_ids if message_ids is not None else []
        if self._on_stage is not None:
            result = await self._on_stage(ids)
            if isinstance(result, Exception):
                raise result
        else:
            ids.append(10)
            if specs is not None:
                specs.append({"kind": "photo", "file_id": "P"})
        return [{"type": "photo", "file_id": "P"}], [], ids

    async def stage_local(self, files, **kwargs):
        return await self.stage_file_ids([], [], **kwargs)

    async def delete_preview_messages(self, ids):
        self.deleted.extend(ids or [])

    async def cleanup_files(self, files):
        pass

    async def send_control_message_id(self, **kwargs):
        self.controls += 1
        return 11

    async def notify_reused(self, row):
        pass


def _events(review_id):
    return audit_mod.list_events(review_id=review_id, limit=100)


# ---- A: RetryAfter is a minimum, and the budget decides, not a 60s cap ----
@pytest.mark.asyncio
async def test_retry_after_wait_is_paid_in_full_never_truncated():
    """retry_after=120 must wait 121s, not the old 60s cap (early re-send)."""
    from telepost.telegram.review_stager import TelegramReviewStager

    calls, sleeps = [], []

    async def factory():
        calls.append(1)
        if len(calls) == 1:
            raise RetryAfter(120)
        return "ok"

    async def sleep(delay):
        sleeps.append(delay)

    stager = TelegramReviewStager(
        AsyncMock(), -100123, sleep=sleep, preview_max_attempts=5
    )
    assert await stager._send_throttled(factory) == "ok"
    assert sleeps == [121.0], \
        "Telegram's retry_after is a minimum; the old 60s cap re-sent early"


@pytest.mark.asyncio
async def test_retry_after_is_not_exponentially_amplified():
    from telepost.telegram.review_stager import TelegramReviewStager

    calls, sleeps = [], []

    async def factory():
        calls.append(1)
        if len(calls) <= 2:
            raise RetryAfter(5)
        return "ok"

    async def sleep(delay):
        sleeps.append(delay)

    stager = TelegramReviewStager(
        AsyncMock(), -100123, sleep=sleep, preview_max_attempts=5
    )
    await stager._send_throttled(factory)
    assert sleeps == [6.0, 6.0], \
        "each 429 carries its own authoritative wait; no 2**attempt growth"


@pytest.mark.asyncio
async def test_production_9_17_60_backoff_sequence_succeeds_in_budget():
    """The incident's exact sequence fits when the budget allows it."""
    from telepost.telegram.review_stager import TelegramReviewStager

    calls, sleeps = [], []

    async def factory():
        calls.append(1)
        waits = [9, 17, 60]
        if len(calls) <= len(waits):
            raise RetryAfter(waits[len(calls) - 1])
        return "ok"

    async def sleep(delay):
        sleeps.append(delay)

    stager = TelegramReviewStager(
        AsyncMock(), -100123, sleep=sleep, preview_max_attempts=5
    )
    stager.set_staging_deadline(time.monotonic() + 240.0)
    assert await stager._send_throttled(factory) == "ok"
    assert sleeps == [10.0, 18.0, 61.0]
    assert len(calls) == 4
    stats = stager.last_staging_stats.get("retries", [])
    assert [s["retry_after"] for s in stats] == [9.0, 17.0, 60.0]
    assert all(isinstance(s["budget_left"], float) for s in stats)


@pytest.mark.asyncio
async def test_demanded_wait_beyond_budget_stops_without_sleeping():
    from telepost.telegram.review_stager import TelegramReviewStager

    calls, sleeps = [], []

    async def factory():
        calls.append(1)
        raise RetryAfter(60)

    async def sleep(delay):
        sleeps.append(delay)

    stager = TelegramReviewStager(
        AsyncMock(), -100123, sleep=sleep, preview_max_attempts=5
    )
    stager.set_staging_deadline(time.monotonic() + 30.0)
    with pytest.raises(RuntimeError, match="submission staging timeout"):
        await stager._send_throttled(factory)
    assert calls == [1], "no second send after a budget-exceeding 429"
    assert sleeps == [], "must stop immediately, not sleep out the clock"


# ---- C: controlled idempotent recovery of staging-failed reviews ----------
@pytest.mark.asyncio
async def test_same_key_retry_recovers_the_failed_review_identity(review_db):
    from telepost.application.review_queue import ReviewQueueService
    from telepost.storage.sqlite.reviews import ReviewRepository

    async def timeout_stage(ids):
        ids.append(41)
        return RuntimeError(
            "审核预览暂存超时（submission staging timeout），本次投稿未完成"
        )

    failing = _Stager(on_stage=timeout_stage)
    with pytest.raises(RuntimeError):
        await ReviewQueueService().enqueue(
            _command("api:11:recover"), failing, media=[{"x": 1}], documents=[]
        )

    repo = ReviewRepository()
    row = await repo.find_active_by_key("api:11:recover", 0)
    assert row["status"] == "failed"
    # P0-B: the ids STAY on the row as external-effect evidence (the in-out
    # list recorded 41 even though the send outcome was uncertain).
    assert json.loads(row["review_message_ids"]) == [41]
    assert failing.deleted == [41]

    retry = _Stager()
    result = await ReviewQueueService().enqueue(
        _command("api:11:recover"), retry, media=[{"x": 1}], documents=[]
    )
    assert result["status"] == "pending_review"
    assert result["reused"] is False

    rows = await _all_reviews()
    assert len(rows) == 1, "recovery must reuse the review identity"
    after = await repo.get(row["id"])
    assert after["status"] == "pending"
    assert after["control_message_id"] == 11
    # Old preview evidence was cleaned before re-staging.
    assert 41 in retry.deleted
    # The claim is auditable.
    events = await _events(row["id"])
    claimed = [e for e in events if e["event"] == "review.recovery_claimed"]
    assert len(claimed) == 1


@pytest.mark.asyncio
async def test_concurrent_recovery_answers_in_progress(review_db):
    from telepost.application.review_queue import ReviewQueueService
    from telepost.storage.sqlite.reviews import NewReview, ReviewRepository

    repo = ReviewRepository()
    rid = await repo.insert(NewReview(
        idempotency_key="api:11:race", source="api", user_id=7,
        username="flow", title="", tags="#x", note="", link="",
        anonymous=False, spoiler=False, media=[], documents=[],
        review_chat_id="-100123", review_message_ids=[], status="preparing",
    ))
    # Realistic staging failure (sets the error text), then a winner claims it.
    assert await repo.mark_preparation_failed(
        rid, "preview staging failed"
    ) is True
    assert await repo.claim_failed_recovery(rid) is True
    # A second claim while the row is mid-recovery must lose.
    assert await repo.claim_failed_recovery(rid) is False

    result = await ReviewQueueService().enqueue(
        _command("api:11:race"), _Stager(), media=[{"x": 1}], documents=[]
    )
    assert result["reused"] is True
    assert result["reuse_reason"] == "recovery_in_progress"


def test_api_maps_recovery_in_progress_to_retryable_503():
    from utils import api_server

    response = api_server._business_ack({
        "status": "preparing", "review_id": 12, "reused": True,
        "reuse_reason": "recovery_in_progress",
        "delivery_status": "preparing",
    })
    body = json.loads(response.body)
    assert response.status == 503, "the caller must converge later, not now"
    assert body["ok"] is False
    assert body["data"]["business_status"] == "recovery_in_progress"


@pytest.mark.asyncio
async def test_failed_row_with_control_card_is_not_auto_recovered(review_db):
    """Once a card existed the row was actionable: keep the stable 400."""
    from telepost.application.review_queue import ReviewQueueService
    from telepost.storage.sqlite.reviews import NewReview, ReviewRepository

    repo = ReviewRepository()
    rid = await repo.insert(NewReview(
        idempotency_key="api:11:card", source="api", user_id=7,
        username="flow", title="", tags="#x", note="", link="",
        anonymous=False, spoiler=False, media=[], documents=[],
        review_chat_id="-100123", review_message_ids=[7],
        status="failed",
    ))
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE pending_reviews SET control_message_id=7 WHERE id=?", (rid,)
        )

    stager = _Stager()
    result = await ReviewQueueService().enqueue(
        _command("api:11:card"), stager, media=[{"x": 1}], documents=[]
    )
    assert result["reused"] is True
    assert result["status"] == "failed"
    assert result["reuse_reason"] == "idempotent_replay"
    assert stager.stages == 0, "an actionable row must not be re-staged"

    from utils import api_server
    response = api_server._business_ack(result)
    assert response.status == 400
    body = json.loads(response.body)
    assert body["data"]["business_status"] == "permanent_failure"


@pytest.mark.asyncio
async def test_publish_failure_row_with_staged_media_is_not_auto_recovered(
        review_db):
    """The #208 class: staged media means publication was reachable."""
    from telepost.application.review_queue import ReviewQueueService
    from telepost.storage.sqlite.reviews import NewReview, ReviewRepository

    repo = ReviewRepository()
    rid = await repo.insert(NewReview(
        idempotency_key="api:11:pubfail", source="api", user_id=7,
        username="flow", title="", tags="#x", note="", link="",
        anonymous=False, spoiler=False,
        media=[{"type": "photo", "file_id": "P"}], documents=[],
        review_chat_id="-100123", review_message_ids=[10], status="failed",
    ))

    stager = _Stager()
    result = await ReviewQueueService().enqueue(
        _command("api:11:pubfail"), stager, media=[{"x": 1}], documents=[]
    )
    assert result["reused"] is True
    assert result["status"] == "failed"
    assert stager.stages == 0
    row = await repo.get(rid)
    assert row["status"] == "failed"


@pytest.mark.asyncio
async def test_refetch_failed_row_uses_its_own_path(review_db):
    from telepost.application.review_queue import ReviewQueueService
    from telepost.storage.sqlite.reviews import NewReview, ReviewRepository

    repo = ReviewRepository()
    await repo.insert(NewReview(
        idempotency_key="api:11:refetch", source="api", user_id=7,
        username="flow", title="", tags="#x", note="", link="",
        anonymous=False, spoiler=False, media=[], documents=[],
        review_chat_id="-100123", review_message_ids=[], status="failed",
        refetch_request_id="refetch-55-1",
    ))

    # A NON-refetch retry of a refetch row must not claim it via the normal
    # recovery path (refetch rows have their own delete-and-recreate flow).
    stager = _Stager()
    result = await ReviewQueueService().enqueue(
        _command("api:11:refetch"), stager, media=[{"x": 1}], documents=[]
    )
    assert result["reused"] is True
    assert stager.stages == 0


# ---- D: the reconciler never fabricates an actionable 0-media card -------
@pytest.mark.asyncio
async def test_reconciler_skips_failed_row_without_staged_media(review_db):
    from telepost.application.review_queue import ReviewQueueService
    from telepost.storage.sqlite.reviews import NewReview, ReviewRepository

    repo = ReviewRepository()
    rid = await repo.insert(NewReview(
        idempotency_key="api:11:phantom", source="api", user_id=7,
        username="flow", title="", tags="#x", note="", link="",
        anonymous=False, spoiler=False, media=[], documents=[],
        review_chat_id="-100123", review_message_ids=[], status="failed",
    ))

    stager = _Stager()
    repaired = await ReviewQueueService().reconcile_incomplete(
        stager, stale_seconds=0
    )
    assert repaired == 0
    assert stager.controls == 0, \
        "a 0-media failed row must not get an actionable 待审核 card"
    row = await repo.get(rid)
    assert row["status"] == "failed"
    assert row["control_message_id"] is None


@pytest.mark.asyncio
async def test_reconciler_converges_crashed_recovery_to_failed(review_db):
    """A process that dies mid-recovery must not leave an eternal preparing."""
    from telepost.application.review_queue import ReviewQueueService
    from telepost.storage.sqlite.reviews import NewReview, ReviewRepository

    repo = ReviewRepository()
    rid = await repo.insert(NewReview(
        idempotency_key="api:11:crash", source="api", user_id=7,
        username="flow", title="", tags="#x", note="", link="",
        anonymous=False, spoiler=False, media=[], documents=[],
        review_chat_id="-100123", review_message_ids=[66], status="preparing",
    ))

    stager = _Stager()
    repaired = await ReviewQueueService().reconcile_incomplete(
        stager, stale_seconds=0
    )
    assert repaired == 0
    assert stager.controls == 0
    assert 66 in stager.deleted, "the orphan preview must still be cleaned"
    row = await repo.get(rid)
    assert row["status"] == "failed"


@pytest.mark.asyncio
async def test_reconciler_still_recreates_card_when_media_staged(review_db):
    from telepost.application.review_queue import ReviewQueueService
    from telepost.storage.sqlite.reviews import NewReview, ReviewRepository

    repo = ReviewRepository()
    rid = await repo.insert(NewReview(
        idempotency_key="api:11:withmedia", source="api", user_id=7,
        username="flow", title="", tags="#x", note="", link="",
        anonymous=False, spoiler=False,
        media=[{"type": "photo", "file_id": "P"}], documents=[],
        review_chat_id="-100123", review_message_ids=[10], status="failed",
    ))

    stager = _Stager()
    repaired = await ReviewQueueService().reconcile_incomplete(
        stager, stale_seconds=0
    )
    assert repaired == 1
    row = await repo.get(rid)
    assert row["status"] == "pending"
    assert row["control_message_id"] == 11


# ---- D: operator-facing wording for a reused failed row ------------------
def test_reused_notice_for_failed_row_says_staging_failure():
    from telepost.telegram import review_keyboard

    class Row(dict):
        def __getitem__(self, key):  # sqlite.Row-like access
            return dict.__getitem__(self, key)

    text = review_keyboard.reused_notice_text(Row(
        id=209, status="failed", review_message_ids="[]",
        control_message_id=None,
    ))
    assert "审核准备失败" in text
    assert "发布失败" not in text, \
        "a staging failure never attempted publication"


async def _all_reviews() -> list:
    async with db_manager.get_db() as conn:
        cur = await conn.execute("SELECT id, status FROM pending_reviews")
        return list(await cur.fetchall())
