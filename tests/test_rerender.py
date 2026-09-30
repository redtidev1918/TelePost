"""Review re-render (§rerender) — outcome state machine + idempotency.

Business model pinned here:

* a review keeps its STORED file_ids (``media_json``/``documents_json``) and a
  rerender re-stages them as a fresh ``pending`` card under the CURRENT code;
* the rerender forces a new generation in the SAME chain that supersedes the
  CURRENT chain head (the superseded head becomes ``superseded`` history and its
  editorial drafts are superseded) — this mirrors refetch's replacement contract
  but via the plain (non-refetch) insert, which the rerender service closes
  explicitly with ``mark_superseded``;
* a DECIDED chain (published / approved / publishing) is refused — a rerender
  never re-authors immutable history;
* repeated/running request converges to ``rerender_already_pending`` (one head);
* a review with no stored content has nothing to re-render (state error);
* a genuine enqueue/DB failure maps to ``RerenderError`` with a user-safe
  message and never pretends the business state is a failure.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from database import db_manager
from handlers import review

from telepost.application.rerender import (
    RERENDER_ALREADY_PENDING,
    RERENDER_SUCCESS,
    RerenderError,
    RerenderNotFoundError,
    RerenderStateError,
    request_rerender,
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
            "SELECT generation, status, supersedes_review_id, "
            "control_message_id, review_message_ids "
            "FROM pending_reviews WHERE id=?", (review_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def _count_in_chain(chain):
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) AS c FROM pending_reviews WHERE review_chain_id=?",
            (chain,))
        return (await cur.fetchone())["c"]


async def _rerender(review_id, *, enqueue=None, actor=7, callback_key=None):
    """Local seam that stages via the real queue + a stubbed bot."""
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    control = MagicMock()
    control.message_id = 11
    bot.send_message.return_value = control
    doc_message = MagicMock()
    doc_message.message_id = 12
    doc_message.photo = None
    doc_message.video = None
    doc_message.animation = None
    doc_message.audio = None
    doc_message.document = MagicMock(file_id="STAGED_DOC", file_name="staged.bin")
    bot.send_document.return_value = doc_message

    async def _enqueue(*args, **kwargs):
        return await review.queue_review_from_file_ids(*args, **kwargs)

    return await request_rerender(
        review_id, actor=actor, surface="api",
        callback_key=callback_key or f"cb:{review_id}",
        bot=bot, _queue_review_from_file_ids=enqueue or _enqueue,
    )


@pytest.mark.asyncio
async def test_render_missing_review_raises_not_found(refetch_db):
    with pytest.raises(RerenderNotFoundError):
        await _rerender(999999)


@pytest.mark.asyncio
async def test_render_no_content_raises_state_error(refetch_db):
    review_id = await insert_review(status="pending", media_json="[]",
                                    documents_json="[]")
    with pytest.raises(RerenderStateError) as exc:
        await _rerender(review_id)
    assert "没有可重新渲染的媒体内容" in str(exc.value)


@pytest.mark.asyncio
async def test_render_resizes_pending_head_to_new_generation_superseding(refetch_db):
    old_id = await insert_review(
        status="pending", pixiv_id="111", chain="chain-rerender",
        generation=0, media_json='[{"type":"photo","file_id":"OLD_ART"}]')
    result = await _rerender(old_id)

    assert result["outcome"] == RERENDER_SUCCESS
    new_id = result["new_review_id"]
    assert new_id
    assert result["media_count"] == 1
    assert result["current_review_id"] == old_id

    old = await _gen(old_id)
    new = await _gen(new_id)
    assert old["status"] == "superseded"          # old head superseded
    assert new["status"] == "pending"             # fresh card under current code
    assert new["generation"] == old["generation"] + 1
    assert new["supersedes_review_id"] == old_id
    new_row = await _row(new_id)
    assert new_row["control_message_id"] is not None  # re-staged under current code
    assert "STAGED_PHOTO" in new_row["media_json"]


@pytest.mark.asyncio
async def test_render_superseded_target_rerenders_against_current_head(refetch_db):
    # Simulate the production scenario: old superseded card content (e.g. #109)
    # re-staged over a NEWER pending head (#111).
    old = await insert_review(
        status="superseded", pixiv_id="111", chain="chain-rerender2",
        generation=0,
        media_json='[{"type":"photo","file_id":"OLD_DOC_CONTENT"}]',
        documents_json='[{"type":"document","file_id":"ORIG_DOC",'
                        '"original":true,"file_name":"原图.png"}]')
    head = await insert_review(
        status="pending", pixiv_id="333", chain="chain-rerender2",
        generation=1, media_json='[{"type":"photo","file_id":"NEW_ART"}]')
    result = await _rerender(old)

    assert result["outcome"] == RERENDER_SUCCESS
    new_id = result["new_review_id"]
    assert new_id != old and new_id != head

    head_row = await _gen(head)
    new_row = await _gen(new_id)
    assert head_row["status"] == "superseded"     # current head superseded
    assert new_row["generation"] == 2             # head(1) + 1
    assert new_row["supersedes_review_id"] == head
    staged = await _row(new_id)
    assert "ORIG_DOC" not in staged["documents_json"]  # re-staged under new file_ids
    assert '"original": true' in staged["documents_json"]  # 「原图」flag survives re-render

    # Chain ends up with exactly the 3 rows (old superseded history + head + new).
    assert await _count_in_chain("chain-rerender2") == 3


@pytest.mark.asyncio
async def test_render_decided_chain_is_refused(refetch_db):
    review_id = await insert_review(
        status="published", pixiv_id="111", chain="chain-decided",
        generation=0, media_json='[{"type":"photo","file_id":"ART"}]')
    with pytest.raises(RerenderStateError) as exc:
        await _rerender(review_id)
    assert "不能重新渲染" in str(exc.value)


@pytest.mark.asyncio
async def test_rapid_double_click_converges_to_one_head(refetch_db):
    old_id = await insert_review(
        status="pending", pixiv_id="111", chain="chain-double",
        generation=0, media_json='[{"type":"photo","file_id":"ART"}]')
    first = await _rerender(old_id, callback_key="cb-same")
    assert first["outcome"] == RERENDER_SUCCESS

    # Replaying the identical callback must reuse the SAME new head — the
    # head is now pending, so a second click idempotently returns.
    second = await _rerender(old_id, callback_key="cb-same")
    assert second["outcome"] == RERENDER_ALREADY_PENDING
    assert second["new_review_id"] == first["new_review_id"]

    assert await _count_in_chain("chain-double") == 2  # superseded old + new


@pytest.mark.asyncio
async def test_system_failure_maps_to_rerender_error(refetch_db):
    old_id = await insert_review(
        status="pending", pixiv_id="111", chain="chain-fail",
        generation=0, media_json='[{"type":"photo","file_id":"ART"}]')

    async def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    with pytest.raises(RerenderError) as exc:
        await _rerender(old_id, enqueue=_boom)
    assert exc.value.code == "RuntimeError"
    assert "请稍后重试" in str(exc.value)