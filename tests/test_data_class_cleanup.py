"""Data-class marker + safe purge + identity-boundary invariants (§data-class).

Background (§data-class):
* every ``pending_reviews`` row carries a durable ``data_class`` marker —
  ``'real'`` (production) or ``'test'`` (fixture) — the ONLY criterion the
  test-data purge may delete on (never a ``username LIKE`` pattern; the
  fixc-verify LIKE cleanup deleted the real review #140 in the 2026-09-28
  incident).
* ``ReviewRepository.purge_test_data`` deletes test rows but REFUSES any chain
  that also contains a real row, so real data streamed into a test chain can
  never be carried off by a purge.
* ``_reserve_replacement`` inherits identity from the source chain EXCEPT the
  boundary case where a test-typed chain delivers a REAL candidate — then the
  candidate keeps its own clean identity (never the fixture's).
"""
import time
from unittest.mock import MagicMock

import pytest

from database import db_manager
from handlers import review
from telepost.storage.sqlite.reviews import ReviewRepository

from tests.helpers_refetch import (
    attempt as _attempt,
    insert_review as _insert_review,
    photo_message as _photo_message,
)


async def _row(review_id):
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT * FROM pending_reviews WHERE id=?", (review_id,))
        row = await cur.fetchone()
        return dict(row) if row is not None else None


async def _start_attempt(review_id, *, generation=1, callback_id=9100):
    from telepost.storage.sqlite.refetch import RefetchRepository
    attempt, _ = await _attempt(
        chain_id=f"chain-{review_id}", generation=generation,
        source_review_id=review_id, callback_id=callback_id)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")
    return attempt


async def _replacement_with_attempt(review_id, attempt, *, candidate="222",
                                    idempotency_key="repl", data_class="real"):
    from unittest.mock import AsyncMock
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
        target_id="target-a", work_type="illustration", work_id=candidate,
        idempotency_key=idempotency_key, source="api",
        refetch_request_id=attempt["request_id"],
        data_class=data_class,
    ), bot


# ---------------------------------------------------------------- purge

@pytest.mark.asyncio
async def test_purge_deletes_only_test_rows(refetch_db):
    test_id = await _insert_review(
        pixiv_id="111", target_id="target-t", data_class="test",
        username="fixc-verify")
    real_id = await _insert_review(
        pixiv_id="222", target_id="target-r", data_class="real",
        username="realuser")

    summary = await ReviewRepository().purge_test_data()

    assert summary["deleted"] == 1
    assert summary["refused"] == []
    row = await _row(test_id)
    assert row is None
    assert (await _row(real_id)) is not None


@pytest.mark.asyncio
async def test_purge_refuses_chain_containing_real_row(refetch_db):
    # A test row whose chain ALSO carries a real row must not be deleted.
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "INSERT INTO pending_reviews (idempotency_key, source, status,"
            " user_id, username, review_chat_id, media_json, documents_json,"
            " title, review_chain_id, generation, data_class, created_at, updated_at)"
            " VALUES (?, 'api', 'pending', 7, 'fixc-verify', '-100', '[]', '[]',"
            " 't', 'shared-chain', 0, 'test', ?, ?)",
            ("px%d" % int(time.time()), time.time(), time.time()))
        test_id = cur.lastrowid
    await _insert_review(pixiv_id="222", target_id="target-r",
                         data_class="real", username="realuser",
                         chain="shared-chain", generation=1)

    summary = await ReviewRepository().purge_test_data()

    assert summary["deleted"] == 0
    assert "shared-chain" in summary["refused"]
    assert (await _row(test_id)) is not None


# ------------------------------------------------------------- identity boundary (B-b)

@pytest.mark.asyncio
async def test_replacement_from_test_chain_keeps_clean_identity(refetch_db):
    """A REAL candidate arriving in a TEST-typed chain must NOT inherit the
    fixture's identity: it keeps its own username/user_id and 'real' class."""
    source_id = await _insert_review(
        pixiv_id="111", target_id="target-a", data_class="test",
        username="fixc-verify", user_id=1)
    attempt = await _start_attempt(source_id)

    result, _bot = await _replacement_with_attempt(source_id, attempt)
    new_id = result["review_id"]
    new = await _row(new_id)

    assert new["data_class"] == "real"          # candidate is real data
    assert new["username"] == "pixivflow"       # clean, not fixc-verify
    assert int(new["user_id"]) == 7
    assert new["username"] != "fixc-verify"


@pytest.mark.asyncio
async def test_replacement_from_real_chain_keeps_inheritance(refetch_db):
    """Real→real still inherits the source chain identity (design intent)."""
    source_id = await _insert_review(
        pixiv_id="111", target_id="target-a", data_class="real",
        username="realuser", user_id=42)
    attempt = await _start_attempt(source_id)

    result, _bot = await _replacement_with_attempt(source_id, attempt)
    new = await _row(result["review_id"])

    assert new["data_class"] == "real"
    assert new["username"] == "realuser"
    assert int(new["user_id"]) == 42


@pytest.mark.asyncio
async def test_replacement_test_into_test_keeps_fixture_identity(refetch_db):
    """test→test refetch still inherits (fixture continuity, no real data)."""
    source_id = await _insert_review(
        pixiv_id="111", target_id="target-a", data_class="test",
        username="fixc-verify", user_id=1)
    attempt = await _start_attempt(source_id)

    result, _bot = await _replacement_with_attempt(source_id, attempt, data_class="test")
    new = await _row(result["review_id"])

    assert new["data_class"] == "test"
    assert new["username"] == "fixc-verify"
    assert int(new["user_id"]) == 1