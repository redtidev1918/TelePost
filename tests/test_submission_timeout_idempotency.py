"""Regression: a slow submission must stay decidable and non-duplicating.

Production evidence (2026-09-12, bot2-daily@2026-09-12T1810, review #59):

* the request staged two large illustration previews inline (18:48:38Z);
* the periodic reconciliation sweep (``stale_seconds=60``) selected the LIVE
  row — ``status='preparing'`` with ``control_message_id IS NULL`` — deleted
  the previews this request had just uploaded and marked the review ``failed``
  at 18:50:28Z;
* the routing layer answered 502 at 18:53:39Z (``ROUTER_TIMEOUT_SECONDS``)
  while the handler was still running, so the caller could not tell "not
  processed" from "processed and failed";
* the retry with the same idempotency key returned the reused ``failed`` row as
  HTTP 200 / ``ok: true`` / ``business_status=idempotent_replay`` /
  ``delivery_status=failed``, which the caller recorded as an end-to-end
  success.

Each test below fails against the pre-fix implementation.
"""
import asyncio
import json
import time

import pytest
from unittest.mock import AsyncMock

from database import db_manager
from handlers import review
from telegram.error import RetryAfter


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
        idempotency_key=key, source_ref='{"executionId":"exec-timeout"}',
        review_chat_id="-100123",
    )


class _Stager:
    """Minimal StagingPort double with per-test hooks."""

    def __init__(self, *, on_stage=None):
        self._on_stage = on_stage
        self.deleted = []
        self.cleanup = []
        self.deadline = "unset"
        self.stages = 0

    def set_staging_deadline(self, deadline):
        self.deadline = deadline

    async def stage_local(self, files, *, caption, spoiler, message_ids=None):
        self.stages += 1
        ids = message_ids if message_ids is not None else []
        extra = []
        if self._on_stage is not None:
            extra = await self._on_stage(ids) or []
        else:
            ids.append(10)
        return [{"type": "photo", "file_id": "P"}], [], ids, extra

    async def stage_file_ids(self, media, documents, *, caption, spoiler,
                             message_ids=None):
        self.stages += 1
        ids = message_ids if message_ids is not None else []
        if self._on_stage is not None:
            await self._on_stage(ids)
        else:
            ids.append(10)
        return [{"type": "photo", "file_id": "P"}], [], ids

    async def delete_preview_messages(self, ids):
        self.deleted.extend(ids or [])

    async def cleanup_files(self, files):
        self.cleanup.append(files)

    async def send_control_message_id(self, **kwargs):
        return 11

    async def notify_reused(self, row):
        self.row = row


# ---- Test D: the sweep must not hijack a live request --------------------
@pytest.mark.asyncio
async def test_reconciler_does_not_hijack_live_submission(review_db):
    """A request that is still staging keeps its row and its previews."""
    from telepost.application.review_queue import ReviewQueueService
    from telepost.storage.sqlite.reviews import ReviewRepository

    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_stage(ids):
        entered.set()
        await release.wait()            # a genuinely slow Telegram upload
        ids.append(10)
        return []

    stager = _Stager(on_stage=slow_stage)
    service = ReviewQueueService(heartbeat_seconds=0.01)
    task = asyncio.create_task(
        service.enqueue(_command("api:9:live"), stager,
                        media=[{"x": 1}], documents=[])
    )
    await asyncio.wait_for(entered.wait(), timeout=5)

    # 证明心跳确实在续命：等到 updated_at 被刷新两次，再按「观测到的年龄」推导
    # staleness 窗口。以前这里 sleep(0.06) 后固定 stale_seconds=0.03 —— 在慢 CI
    # runner 上，最后一次心跳到 reconcile 读取 time.time() 之间的调度抖动就会超过
    # 0.03s，活行被误判为 crash leftover，release 的 Test 步骤随机失败
    # （2026-09-13 TelePost release 2.17.6 build-plan 实测 1 failed）。
    repo = ReviewRepository()
    stamps = []
    deadline = time.monotonic() + 10
    while len(stamps) < 2 and time.monotonic() < deadline:
        probe = await repo.find_active_by_key("api:9:live", 0)
        stamp = probe["updated_at"] if probe else None
        if stamp is not None and (not stamps or stamp > stamps[-1]):
            stamps.append(stamp)
        await asyncio.sleep(0.005)
    assert len(stamps) == 2, "heartbeat never renewed the row while staging"

    age = max(0.0, time.time() - stamps[-1])
    swept = await ReviewQueueService().reconcile_incomplete(
        _Stager(), stale_seconds=max(0.03, age * 20)
    )
    assert swept == 0, "the sweep repaired a live, heartbeating submission"

    row = await repo.find_active_by_key("api:9:live", 0)
    assert row["status"] == "preparing", "live row was moved by the sweep"
    assert stager.deleted == [], "live previews were deleted by the sweep"

    release.set()
    result = await asyncio.wait_for(task, timeout=5)
    assert result["status"] == "pending_review"
    assert result["reused"] is False
    assert stager.deleted == []
    assert stager.deadline is None, "staging budget leaked past the request"

    rows = await _all_reviews()
    assert len(rows) == 1, "exactly one review per idempotency key"


# ---- Test D (control): a genuinely abandoned row is still repaired -------
@pytest.mark.asyncio
async def test_reconciler_still_repairs_abandoned_row(review_db):
    """No liveness signal => the row really is crash leftovers."""
    from telepost.application.review_queue import ReviewQueueService
    from telepost.storage.sqlite.reviews import NewReview, ReviewRepository

    repo = ReviewRepository()
    rid = await repo.insert(NewReview(
        idempotency_key="api:9:dead", source="api", user_id=7,
        username="flow", title="", tags="#x", note="", link="",
        anonymous=False, spoiler=False, media=[], documents=[],
        review_chat_id="-100123", review_message_ids=[55], status="preparing",
    ))

    await ReviewQueueService().reconcile_incomplete(_Stager(), stale_seconds=0)

    row = await repo.get(rid)
    assert row["status"] == "failed", "an abandoned row must still be repaired"


@pytest.mark.asyncio
async def test_heartbeat_only_renews_preparing_rows(review_db):
    from telepost.storage.sqlite.reviews import NewReview, ReviewRepository

    repo = ReviewRepository()
    rid = await repo.insert(NewReview(
        idempotency_key="api:9:heart", source="api", user_id=7,
        username="flow", title="", tags="#x", note="", link="",
        anonymous=False, spoiler=False, media=[{"type": "photo", "file_id": "P"}],
        documents=[], review_chat_id="-100123", review_message_ids=[10],
        status="failed",
    ))
    assert await repo.touch_preparing(rid) is False, \
        "a heartbeat must never touch a row that is no longer preparing"


# ---- Test A / C: bounded staging, no unbounded synchronous path ---------
@pytest.mark.asyncio
async def test_send_throttled_never_starts_an_attempt_without_budget():
    from telepost.telegram.review_stager import TelegramReviewStager

    calls = []

    async def factory():
        calls.append(1)
        raise RetryAfter(1)

    stager = TelegramReviewStager(
        AsyncMock(), -100123, sleep=AsyncMock(), preview_max_attempts=1000
    )
    stager.set_staging_deadline(time.monotonic() - 1.0)
    with pytest.raises(RuntimeError) as err:
        await stager._send_throttled(factory)
    assert "timeout" in str(err.value).lower(), \
        "the deadline error must be classifiable as retryable"
    assert calls == [], "an attempt started after the staging deadline"


@pytest.mark.asyncio
async def test_send_throttled_stops_at_deadline_instead_of_retrying():
    """Flood retries must not outlive the budget the router allows."""
    from telepost.telegram.review_stager import TelegramReviewStager

    calls = []

    async def factory():
        calls.append(1)
        raise RetryAfter(1)

    stager = TelegramReviewStager(
        AsyncMock(), -100123, preview_max_attempts=1000
    )
    stager.set_staging_deadline(time.monotonic() + 0.05)
    with pytest.raises(Exception):
        await stager._send_throttled(factory)
    assert 0 < len(calls) <= 2, f"unbounded retry: {len(calls)} attempts"


@pytest.mark.asyncio
async def test_timeouts_are_capped_to_remaining_budget():
    from telepost.telegram.review_stager import TelegramReviewStager

    stager = TelegramReviewStager(AsyncMock(), -100123, preview_timeout=120.0)
    assert stager._timeouts_now() == stager._timeouts      # unbounded by default
    stager.set_staging_deadline(time.monotonic() + 7.0)
    capped = stager._timeouts_now()
    assert 0 < capped["read_timeout"] <= 7.0, "call outlives the request budget"
    stager.set_staging_deadline(None)
    assert stager._timeouts_now() == stager._timeouts


# ---- Test B: a failed first attempt stays one review, and is not "ok" ----
@pytest.mark.asyncio
async def test_deadline_failure_is_coherent_and_never_duplicates(review_db):
    from telepost.application.review_queue import ReviewQueueService
    from telepost.storage.sqlite.reviews import ReviewRepository

    def _deadline_stage(ids):
        ids.append(10)
        raise RuntimeError(
            "审核预览暂存超时（submission staging timeout），本次投稿未完成"
        )

    stager = _Stager(on_stage=_deadline_stage)
    service = ReviewQueueService()
    with pytest.raises(RuntimeError):
        await service.enqueue(_command("api:9:deadline"), stager,
                              media=[{"x": 1}], documents=[])

    repo = ReviewRepository()
    row = await repo.find_active_by_key("api:9:deadline", 0)
    assert row["status"] == "failed"
    assert stager.deleted == [10], "previews must be rolled back"
    assert stager.deadline is None, "staging budget leaked past the request"

    # Retrying the same key reuses THAT row: one review, never two.
    retry = await ReviewQueueService().enqueue(
        _command("api:9:deadline"), _Stager(), media=[{"x": 1}], documents=[]
    )
    assert retry["reused"] is True
    assert retry["review_id"] == row["id"]
    assert retry["status"] == "failed"
    assert retry["reuse_reason"] == "idempotent_replay"
    assert len(await _all_reviews()) == 1


@pytest.mark.asyncio
async def test_server_side_success_makes_an_ambiguous_retry_a_success(review_db):
    """If the lost 502 hid a real success, the retry must ACK it as one."""
    from telepost.application.review_queue import ReviewQueueService
    from telepost.storage.sqlite.reviews import NewReview, ReviewRepository

    repo = ReviewRepository()
    rid = await repo.insert(NewReview(
        idempotency_key="api:9:lostack", source="api", user_id=7,
        username="flow", title="", tags="#x", note="", link="",
        anonymous=False, spoiler=False,
        media=[{"type": "photo", "file_id": "P"}], documents=[],
        review_chat_id="-100123", review_message_ids=[10], status="pending",
    ))

    class Stager(_Stager):
        async def notify_reused(self, row):
            self.row = row

    result = await ReviewQueueService().enqueue(
        _command("api:9:lostack"), Stager(), media=[{"x": 1}], documents=[]
    )
    assert result["reused"] is True
    assert result["review_id"] == rid
    assert result["status"] == "pending_review"
    assert result["delivery_status"] == "pending_review"
    assert len(await _all_reviews()) == 1


def test_reused_failed_review_is_not_reported_as_ok():
    """The production pair: review 59 (failed) must not look like review 60."""
    from utils import api_server

    failed = api_server._business_ack({
        "status": "failed", "review_id": 59, "reused": True,
        "reuse_reason": "idempotent_replay", "delivery_status": "failed",
    })
    body = json.loads(failed.body)
    assert failed.status == 400, "a reused failed review is not a success"
    assert body["ok"] is False
    assert body["data"]["business_status"] == "permanent_failure"

    pending = api_server._business_ack({
        "status": "pending_review", "review_id": 60, "reused": True,
        "reuse_reason": "idempotent_replay", "delivery_status": "pending_review",
    })
    pending_body = json.loads(pending.body)
    assert pending.status == 200
    assert pending_body["ok"] is True
    assert pending_body["data"]["business_status"] == "idempotent_replay"


async def _all_reviews() -> list:
    async with db_manager.get_db() as conn:
        cur = await conn.execute("SELECT id, status FROM pending_reviews")
        return list(await cur.fetchall())
