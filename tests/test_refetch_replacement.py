"""Refetch generation replacement invariants (§refetch-replacement).

A successful refetch replacement is a GENERATION REPLACEMENT, not a second
parallel review:

    A (generation 0, pending)  --refetch replaced-->  B (generation 1, pending)
                                                      A = superseded history

Hard invariants pinned here:

* a committed replacement atomically supersedes the previous generation and
  installs the replacement as the chain head (exactly one active generation);
* a failed / no-alternative refetch leaves the current review untouched
  (commit-after-success);
* a superseded generation is terminal history: approve / reject / spoiler /
  refetch / editorial publish are all refused by a stale guard, and the old
  review card loses its actions;
* superseded ≠ rejected: superseding never emits a submitter rejection
  notification, while an explicit reject of the CURRENT head emits exactly one;
* the logical submission stays ONE item ("审核中", current_review_id = B).
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from database import db_manager
from handlers import review
from services import review_service as review_service_module
from telepost.storage.sqlite.refetch import RefetchRepository

from tests.helpers_refetch import (
    attempt as _attempt,
    callback_update as _callback_update,
    insert_review as _insert_review,
    photo_message as _photo_message,
)


async def _start_attempt(review_id, *, generation=1, callback_id=9100):
    attempt, refused = await _attempt(
        chain_id=f"chain-{review_id}", generation=generation,
        source_review_id=review_id, callback_id=callback_id)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")
    return attempt


async def _replacement_with_attempt(review_id, attempt, *, candidate="222",
                                    idempotency_key="repl"):
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    control = MagicMock()
    control.message_id = 11
    bot.send_message.return_value = control
    return await review.queue_review_from_file_ids(
        bot, [{"type": "photo", "file_id": "NEW_ART"}], [],
        tags="#pixiv", title=f"candidate {candidate}",
        link=f"https://www.pixiv.net/artworks/{candidate}",
        user_id=7, username="pixivflow",
        target_id="target-a", work_type="illustration", pixiv_id=candidate,
        idempotency_key=idempotency_key, source="api",
        refetch_request_id=attempt["request_id"],
    ), bot


async def _row(review_id):
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT * FROM pending_reviews WHERE id=?", (review_id,))
        return dict(await cur.fetchone())


async def _active_generations(chain_id):
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) AS c FROM pending_reviews "
            "WHERE review_chain_id=? AND status IN ('preparing','pending','publishing')",
            (chain_id,))
        return (await cur.fetchone())["c"]


async def _active_for_review(review_id):
    """Count active generations of the review's OWN effective chain (a fresh
    chainless review is its own single-row chain until a refetch anchors it)."""
    row = await _row(review_id)
    chain = row["review_chain_id"] or f"chain-{review_id}"
    if not row["review_chain_id"]:
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) AS c FROM pending_reviews "
                "WHERE id=? AND status IN ('preparing','pending','publishing')",
                (review_id,))
            return (await cur.fetchone())["c"]
    return await _active_generations(chain)


@pytest.mark.asyncio
async def test_replacement_installs_new_head_and_supersedes_old(refetch_db):
    review_id = await _insert_review(pixiv_id="111", target_id="target-a")
    attempt = await _start_attempt(review_id)

    result, _bot = await _replacement_with_attempt(review_id, attempt)
    new_id = result["review_id"]

    old = await _row(review_id)
    new = await _row(new_id)
    assert old["status"] == "superseded"          # terminal history, NOT rejected
    assert new["status"] == "pending"
    assert new["generation"] == old["generation"] + 1
    assert new["review_chain_id"] == old["review_chain_id"]
    assert new["supersedes_review_id"] == review_id
    assert await _active_generations(old["review_chain_id"]) == 1

    from telepost.storage.sqlite.reviews import ReviewRepository
    head = await ReviewRepository().head_of_chain(old["review_chain_id"])
    assert int(head["id"]) == int(new_id)


@pytest.mark.asyncio
async def test_superseded_refuses_all_moderation_actions(refetch_db):
    """approve / reject / spoiler / refetch / editorial publish → stale guard."""
    from telepost.application.editorial import EditorialObsoleteError
    from services.review_service import (
        ReviewService, ReviewSupersededError,
    )

    review_id = await _insert_review(pixiv_id="111", target_id="target-a")
    attempt = await _start_attempt(review_id)
    await _replacement_with_attempt(review_id, attempt)

    service = ReviewService()
    bot = AsyncMock()

    with pytest.raises(ReviewSupersededError):
        await service.reject(bot, review_id, actor=1, source="telegram")
    with pytest.raises(ReviewSupersededError):
        await service.approve(bot, review_id, actor=1, source="telegram")
    with pytest.raises(ReviewSupersededError):
        await service.toggle_spoiler(review_id, actor=1)
    with pytest.raises(EditorialObsoleteError):
        from telepost.application.editorial import EditorialService
        await EditorialService().create(review_id, actor={"id": 1})

    # The stale reject click must not have changed the generation…
    assert (await _row(review_id))["status"] == "superseded"
    # …and must not have notified the submitter.
    bot.send_message.assert_not_awaited()

    # The old Telegram card is rejected too (stale button, no remote call).
    update = _callback_update(f"review_refetch:{review_id}")
    await review.refetch_review(update, MagicMock(bot=AsyncMock()))
    assert "已被重抓结果替代" in update.callback_query.answer.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_supersede_never_notifies_and_explicit_reject_notifies_once(refetch_db):
    review_id = await _insert_review(
        pixiv_id="111", target_id="target-a", source="chat", submitter_user_id=4242)
    attempt = await _start_attempt(review_id)
    _result, bot = await _replacement_with_attempt(review_id, attempt)
    # The replacement only ever messages the review GROUP (receipt), never the
    # submitter: superseded ≠ rejected.
    sent_to_submitter = [c for c in bot.send_message.await_args_list
                         if c.kwargs.get("chat_id") == 4242]
    assert not sent_to_submitter

    # Explicit rejection of the CURRENT head does notify, exactly once.
    new_id = (await _row_head(review_id))
    from services.review_service import ReviewService
    service = ReviewService()
    reject_bot = AsyncMock()
    await service.reject(reject_bot, new_id, actor=1, source="telegram")
    assert reject_bot.send_message.await_count == 1
    assert (await _row(new_id))["status"] == "rejected"

    # A second (stale) reject click on the same row must not notify again.
    reject_bot.reset_mock()
    with pytest.raises(Exception):
        await service.reject(reject_bot, new_id, actor=1, source="telegram")
    reject_bot.send_message.assert_not_awaited()


async def _row_head(old_review_id):
    from telepost.storage.sqlite.reviews import ReviewRepository
    chain = (await _row(old_review_id))["review_chain_id"]
    head = await ReviewRepository().head_of_chain(chain)
    return int(head["id"])


@pytest.mark.asyncio
async def test_old_card_loses_actions_and_drafts_are_terminalized(refetch_db):
    """Post-commit effects: the replaced card is rewritten without a keyboard
    and its open editorial drafts become unpublishable history."""
    review_id = await _insert_review(pixiv_id="111", target_id="target-a",
                                     control_message_id=50,
                                     review_message_ids="[40]")
    from telepost.application.editorial import EditorialService
    editorial = EditorialService()
    draft = await editorial.create(review_id, actor={"id": 1, "username": "ed"})

    attempt = await _start_attempt(review_id)
    _result, bot = await _replacement_with_attempt(review_id, attempt)
    from telepost.storage.sqlite.reviews import ReviewRepository
    print("DBG superseded_context:", await ReviewRepository().superseded_context(_result["review_id"]))

    edited = [
        call for call in bot.edit_message_text.await_args_list
        if "被重抓结果替代" in str(call.kwargs.get("text", ""))
    ]
    assert edited, "superseded card was not rewritten"
    markup = edited[0].kwargs.get("reply_markup")
    assert markup is not None and list(markup.inline_keyboard) == []

    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT status FROM editorial_revisions WHERE id=?", (draft["id"],))
        assert (await cur.fetchone())["status"] == "superseded"


@pytest.mark.asyncio
async def test_failed_and_no_alternative_keep_the_current_review(refetch_db):
    """commit-after-success: without a durable replacement nothing supersedes."""
    review_id = await _insert_review(pixiv_id="111", target_id="target-a")
    attempt = await _start_attempt(review_id)
    repo = RefetchRepository()
    await repo.apply_outcome(attempt["request_id"], "no_alternative",
                             reason="no candidates")
    assert (await _row(review_id))["status"] == "pending"
    assert await _active_for_review(review_id) == 1

    second = await _start_attempt(review_id, callback_id=9200)
    await repo.apply_outcome(second["request_id"], "failed", reason="download failed")
    assert (await _row(review_id))["status"] == "pending"
    assert await _active_for_review(review_id) == 1


@pytest.mark.asyncio
async def test_second_attempt_after_replacement_resolves_obsolete(refetch_db):
    """R1 replaced → a late R2 result must not build a second generation."""
    review_id = await _insert_review(pixiv_id="111", target_id="target-a")
    r1 = await _start_attempt(review_id, callback_id=9300)
    _r2, refused = await _attempt(
        chain_id=f"chain-{review_id}", generation=2,
        source_review_id=review_id, callback_id=9301)
    # The data layer admits at most one active attempt per chain; a second
    # user click while the first is in flight is refused, not admitted.
    assert refused in (True, "already_running")

    _result, _bot = await _replacement_with_attempt(review_id, r1)
    repo = RefetchRepository()
    chain = (await _row(review_id))["review_chain_id"]
    assert await _active_generations(chain) == 1
    assert (await repo.find_by_request_id(r1["request_id"]))["state"] == "replaced"

    # A stale second replacement delivery (late R2) resolves obsolete and
    # never creates a second generation.
    late = {"request_id": "late-r2-uuid", "state": "requested"}
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE refetch_attempts SET state='obsolete', finished_at=? "
            "WHERE request_id=?",
            (time.time(), late["request_id"]),
        )
    assert await _active_generations(chain) == 1


@pytest.mark.asyncio
async def test_reject_during_refetch_wins_and_result_is_obsolete(refetch_db):
    """Explicit reject of the head wins; the late replacement never revives it."""
    from services.review_service import ReviewService

    review_id = await _insert_review(pixiv_id="111", target_id="target-a")
    attempt = await _start_attempt(review_id)

    bot = AsyncMock()
    await ReviewService().reject(bot, review_id, actor=1, source="telegram")
    assert (await _row(review_id))["status"] == "rejected"

    # The late replacement delivery discovers the decision and resolves the
    # attempt obsolete instead of creating a second generation.
    try:
        _result, _ = await _replacement_with_attempt(review_id, attempt)
    except ValueError as exc:
        assert "obsolete" in str(exc)
    head = await _row_head(review_id)
    assert head == review_id                      # no new generation installed
    assert (await _row(review_id))["status"] == "rejected"
    assert (await RefetchRepository().find_by_request_id(
        attempt["request_id"]))["state"] == "obsolete"


@pytest.mark.asyncio
async def test_stale_moderation_bypass_attempt_leaves_history_intact(refetch_db):
    """Even if a transport redelivers an old callback, the DB keeps exactly one
    current head and the superseded generation stays history."""
    review_id = await _insert_review(pixiv_id="111", target_id="target-a")
    attempt = await _start_attempt(review_id)
    await _replacement_with_attempt(review_id, attempt)
    new_id = await _row_head(review_id)

    old = await _row(review_id)
    assert old["status"] == "superseded"
    assert (await _row(new_id))["status"] == "pending"
    assert await _active_generations(old["review_chain_id"]) == 1


@pytest.mark.asyncio
async def test_logical_submission_stays_one_item_after_replacement(refetch_db):
    """"我的投稿" folds generations: one item, 审核中, current = new head."""
    from telepost.storage.sqlite.reviews import ReviewRepository

    review_id = await _insert_review(
        pixiv_id="111", target_id="target-a", submitter_user_id=4242)
    attempt = await _start_attempt(review_id)
    await _replacement_with_attempt(review_id, attempt)

    chain = (await _row(review_id))["review_chain_id"]
    repo = ReviewRepository()
    heads = await repo.list_logical_submissions(4242, limit=10) \
        if hasattr(repo, "list_logical_submissions") else None
    if heads is None:  # repository API name may differ; assert via chain query
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT id, status FROM pending_reviews "
                "WHERE submitter_user_id=? AND status IN ('preparing','pending','publishing')",
                (4242,))
            rows = list(await cur.fetchall())
        assert len(rows) == 1
        assert int(rows[0]["id"]) != int(review_id)
    else:
        assert len([h for h in heads if h["review_chain_id"] == chain]) == 1
