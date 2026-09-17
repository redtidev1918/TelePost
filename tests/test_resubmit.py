"""Submission resubmit (§resubmit) — outcome state machine + idempotency.

Business model pinned here:

* a still-pending / preparing chain head returns ``resubmit_already_pending``
  and NEVER creates a second row (double-click safety);
* a rejected head creates a NEW pending review in the SAME chain (generation+1,
  supersedes the rejected head) while the rejected row stays as history;
* a resubmitted review that is rejected again leaves TWO rejected rows (old +
  new) — nothing is overwritten;
* a genuine enqueue/DB failure maps to ``resubmit_failed`` with a user-safe
  message and never pretends the business state is a failure.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from database import db_manager
from handlers import review

from telepost.application.resubmit import (
    ResubmitError,
    RESUBMIT_ALREADY_PENDING,
    RESUBMIT_FAILED,
    RESUBMIT_SUCCESS,
    request_resubmit,
)
from tests.helpers_refetch import insert_review


def _photo_message(message_id=10, file_id="STAGED_PHOTO"):
    message = MagicMock()
    message.message_id = message_id
    message.photo = [MagicMock(file_id=file_id)]
    message.video = None
    message.animation = None
    message.audio = None
    message.document = None
    return message


async def _row(review_id):
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT * FROM pending_reviews WHERE id=?", (review_id,))
        return dict(await cur.fetchone())


async def _gen(review_id):
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT generation, status, supersedes_review_id "
            "FROM pending_reviews WHERE id=?", (review_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def _resubmit(review_id, *, enqueue=None, actor=7):
    """Local seam that stages via the real queue + a stubbed bot."""
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    control = MagicMock()
    control.message_id = 11
    bot.send_message.return_value = control

    async def _enqueue(*args, **kwargs):
        return await review.queue_review_from_file_ids(*args, **kwargs)

    return await request_resubmit(
        review_id, actor=actor, surface="miniapp", callback_key=f"cb:{review_id}",
        bot=bot, _queue_review_from_file_ids=enqueue or _enqueue,
    )


@pytest.mark.asyncio
async def test_pending_head_returns_already_pending(refetch_db):
    review_id = await insert_review(status="pending", submitter_user_id=7)
    result = await _resubmit(review_id)

    assert result["outcome"] == RESUBMIT_ALREADY_PENDING
    assert result["new_review_id"] is None
    assert result["message"] == "该作品已经提交审核，请等待处理。"

    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) AS c FROM pending_reviews WHERE submitter_user_id=7")
        assert (await cur.fetchone())["c"] == 1


@pytest.mark.asyncio
async def test_rejected_head_creates_new_generation_preserving_history(refetch_db):
    old_id = await insert_review(
        status="rejected", pixiv_id="111", chain="chain-resubmit",
        generation=0, submitter_user_id=7,
        media_json='[{"type":"photo","file_id":"OLD_ART"}]')
    result = await _resubmit(old_id)

    assert result["outcome"] == RESUBMIT_SUCCESS
    assert result["message"] == "已重新提交审核。"
    new_id = result["new_review_id"]
    assert new_id

    old = await _gen(old_id)
    new = await _gen(new_id)
    assert old["status"] == "rejected"           # history preserved
    assert new["status"] == "pending"            # fresh review, not reuse
    assert new["generation"] == old["generation"] + 1
    assert new["supersedes_review_id"] == old_id


@pytest.mark.asyncio
async def test_resubmit_rejected_again_keeps_two_rejected_rows(refetch_db):
    from services.review_service import ReviewService

    old_id = await insert_review(
        status="rejected", pixiv_id="111", chain="chain-resubmit",
        generation=0, submitter_user_id=7,
        media_json='[{"type":"photo","file_id":"OLD_ART"}]')
    first = await _resubmit(old_id)
    new_id = first["new_review_id"]
    assert new_id

    # Reviewer rejects the resubmitted generation.
    service = ReviewService()
    await service.reject(AsyncMock(), new_id, actor=1, source="telegram",
                         notify_chat_submitter=False)

    before = await _row(old_id)
    after = await _row(new_id)
    assert before["status"] == "rejected"
    assert after["status"] == "rejected"
    # Chain still has exactly two rows (no overwrite / no third copy).
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) AS c FROM pending_reviews "
            "WHERE review_chain_id='chain-resubmit'")
        assert (await cur.fetchone())["c"] == 2


@pytest.mark.asyncio
async def test_rapid_double_click_converges_to_one(refetch_db):
    old_id = await insert_review(
        status="rejected", pixiv_id="111", chain="chain-resubmit",
        generation=0, submitter_user_id=7,
        media_json='[{"type":"photo","file_id":"OLD_ART"}]')
    first = await _resubmit(old_id, enqueue=None)
    assert first["outcome"] == RESUBMIT_SUCCESS

    # The head is now pending → a second click must not create anything.
    second = await _resubmit(first["new_review_id"], enqueue=None)
    assert second["outcome"] == RESUBMIT_ALREADY_PENDING
    assert second["new_review_id"] is None

    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) AS c FROM pending_reviews "
            "WHERE review_chain_id='chain-resubmit'")
        assert (await cur.fetchone())["c"] == 2  # rejected old + one pending new


@pytest.mark.asyncio
async def test_system_failure_maps_to_resubmit_failed(refetch_db):
    old_id = await insert_review(
        status="rejected", pixiv_id="111", chain="chain-resubmit",
        generation=0, submitter_user_id=7,
        media_json='[{"type":"photo","file_id":"OLD_ART"}]')

    async def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    with pytest.raises(ResubmitError) as exc:
        await _resubmit(old_id, enqueue=_boom)
    assert exc.value.code == "RuntimeError"
    assert "请稍后重试" in str(exc.value)
