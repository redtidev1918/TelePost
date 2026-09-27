"""Terminal-reason normalization and the one-shot terminal-notification claim.

Two defects from the 2026-09 field acceptance:

  (B) ``refetch_attempts.failure_code`` is a closed-vocabulary CODE column, but
      the remote verdict's raw free text was written into it. A live row held a
      220-char multi-line nginx 502 HTML page, which also reached the Mini App
      state view and the doctor's 24h failure composition. The producer now
      sends a normalized ``reason_code``; the store normalizes at its single
      write choke point so no caller can leak free text.

  (C) ``POST /api/v1/refetch/outcomes`` hand-rolled its own ``send_message`` and
      never claimed the one-shot ``terminal_notified_at`` clock. Every terminal
      path now funnels through ``handlers.review`` and that one clock.
"""
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from database import db_manager
from handlers import review
from telepost.domain import refetch_state as fsm
from telepost.storage.sqlite.refetch import RefetchRepository

#: The exact shape production stored: a multi-line upstream HTTP body with HTML.
BLOB = (
    "job-level outage (network_outage): Pixiv API error: 502 Bad Gateway - "
    "<html>\r\n<head><title>502 Bad Gateway</title></head>\r\n"
    "<body>\r\n<center><h1>502 Bad Gateway</h1></center>\r\n"
    "<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n"
)


@pytest.fixture
async def refetch_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "refetch-reason.db")
    monkeypatch.setattr(db_manager, "DB_PATH", db_path)
    monkeypatch.setattr(review, "REVIEW_CHAT_ID", -100123)
    await db_manager.init_db()
    return db_path


async def _insert_review(*, status="pending"):
    now = time.time()
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            """
            INSERT INTO pending_reviews (
                idempotency_key, source, status, user_id, username,
                review_chat_id, media_json, documents_json,
                target_id, pixiv_id, work_type, review_chain_id, generation,
                created_at, updated_at
            ) VALUES (?, 'api', ?, 7, 'pf', ?, '[]', '[]',
                      'target-a', '111', 'illustration', '', 0, ?, ?)
            """,
            ("k%d" % int(now * 1000), status, str(review.REVIEW_CHAT_ID),
             now, now),
        )
        return cur.lastrowid


async def _live_attempt(repo, review_id, *, request_id):
    row, refused = await repo.create_attempt(
        callback_key="cb:%d:%d" % (review_id, int(time.time() * 1000)),
        review_chain_id="chain-%d" % review_id,
        generation=1,
        source_review_id=review_id,
        source_candidate_id="111",
        request_id=request_id,
    )
    assert refused is None, refused
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# (B) failure_code stays a code; terminal_reason stays a bounded one-liner
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_free_text_never_reaches_failure_code(refetch_db):
    review_id = await _insert_review()
    repo = RefetchRepository()
    rid = "req-reason-a"
    await _live_attempt(repo, review_id, request_id=rid)

    _, _, changed = await repo.apply_outcome(
        rid, "failed", reason=BLOB, reason_code="source_error",
    )
    assert changed is True

    row = await repo.find_by_request_id(rid)
    assert row["state"] == fsm.FAILED
    # The code column carries the producer's normalized protocol code...
    assert row["failure_code"] == "source_error"
    # ...and the human column is a bounded, single-line message with no markup.
    reason = row["terminal_reason"]
    assert reason
    assert "<" not in reason and ">" not in reason
    assert "\n" not in reason and "\r" not in reason
    assert len(reason) <= fsm.TERMINAL_REASON_LIMIT
    # The verbatim upstream body (markup, CRLF, hundreds of chars) is gone; what
    # remains is the collapsed, tag-stripped single line.
    assert BLOB.strip() not in reason
    assert "<html>" not in reason and "nginx</center>" not in reason


@pytest.mark.asyncio
async def test_old_producer_without_a_reason_code_still_yields_a_code(refetch_db):
    """Defence in depth: the column can never hold free text, even with no code."""
    review_id = await _insert_review()
    repo = RefetchRepository()
    rid = "req-reason-b"
    await _live_attempt(repo, review_id, request_id=rid)

    await repo.apply_outcome(rid, "failed", reason=BLOB)

    row = await repo.find_by_request_id(rid)
    assert row["failure_code"] == fsm.REMOTE_FAILURE_CODE
    assert row["failure_code"] != BLOB
    assert "\n" not in row["terminal_reason"]


@pytest.mark.asyncio
async def test_local_watchdog_code_passes_through_unchanged(refetch_db):
    """The local vocabulary must not be rewritten by the new choke point."""
    review_id = await _insert_review()
    repo = RefetchRepository()
    rid = "req-reason-c"
    await _live_attempt(repo, review_id, request_id=rid)

    await repo.mark_failed(rid, "watchdog_no_heartbeat")

    row = await repo.find_by_request_id(rid)
    assert row["failure_code"] == "watchdog_no_heartbeat"
    assert row["terminal_reason"] == "watchdog_no_heartbeat"


@pytest.mark.asyncio
async def test_no_candidate_verdict_keeps_the_code_column_empty(refetch_db):
    """``failure_code`` answers "why did it FAIL", so it is failure-terminal only.

    A ``no_alternative`` verdict terminalises as ``no_candidate``: the reason is
    still sanitized into ``terminal_reason``, and the code column stays empty
    exactly as before fix B.
    """
    review_id = await _insert_review()
    repo = RefetchRepository()
    rid = "req-reason-d"
    await _live_attempt(repo, review_id, request_id=rid)

    _, applied, changed = await repo.apply_outcome(
        rid, "no_alternative", reason="没有候选", reason_code="no_candidate",
    )

    assert changed is True
    row = await repo.find_by_request_id(rid)
    assert row["state"] == fsm.NO_CANDIDATE
    assert applied == "no_alternative"
    assert row["failure_code"] == ""
    assert row["terminal_reason"] == "没有候选"


# ---------------------------------------------------------------------------
# (C) every terminal path claims the ONE one-shot clock
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_outcome_seam_claims_the_terminal_clock_exactly_once(refetch_db):
    review_id = await _insert_review()
    repo = RefetchRepository()
    rid = "req-seam-1"
    row = await _live_attempt(repo, review_id, request_id=rid)
    bot = AsyncMock()
    task_id = "refetch-%d-%d" % (review_id, int(row["created_at"]))

    applied, changed = await review.apply_refetch_outcome_and_notify(
        bot, repo, row, request_id=rid, disposition="failed",
        review_id=review_id, task_id=task_id,
        reason="网络异常", reason_code="source_error",
    )

    assert changed is True
    fresh = await repo.find_by_request_id(rid)
    assert fresh["terminal_notified_at"] is not None
    # The 任务ID line is the seam's job, not each caller's.
    sent = [c.args[1] if len(c.args) > 1 else c.kwargs.get("text", "")
            for c in bot.send_message.await_args_list]
    assert any(task_id in (t or "") for t in sent), sent
    first_claim = fresh["terminal_notified_at"]
    first_calls = bot.send_message.await_count
    assert first_calls >= 1

    # A replay of the same verdict is already terminal: nothing is notified and
    # the one-shot clock is NOT re-claimed.
    applied2, changed2 = await review.apply_refetch_outcome_and_notify(
        bot, repo, row, request_id=rid, disposition="failed",
        review_id=review_id, task_id=task_id,
        reason="网络异常", reason_code="source_error",
    )

    assert changed2 is False
    assert bot.send_message.await_count == first_calls
    again = await repo.find_by_request_id(rid)
    assert again["terminal_notified_at"] == first_claim
    assert again["failure_code"] == "source_error"


@pytest.mark.asyncio
async def test_progress_clock_is_not_the_terminal_clock(refetch_db):
    """A progress reminder must never consume the terminal one-shot budget."""
    review_id = await _insert_review()
    repo = RefetchRepository()
    rid = "req-seam-2"
    row = await _live_attempt(repo, review_id, request_id=rid)

    await repo.record_poll(rid, now=time.time(), remote_state="pending")
    await repo.bump_progress_notified(rid, time.time())
    mid = await repo.find_by_request_id(rid)
    assert mid["last_progress_notified_at"] is not None
    assert mid["terminal_notified_at"] is None

    bot = AsyncMock()
    await review.apply_refetch_outcome_and_notify(
        bot, repo, mid, request_id=rid, disposition="failed",
        review_id=review_id, task_id="refetch-%d-0" % review_id,
        reason="网络异常", reason_code="source_error",
    )

    after = await repo.find_by_request_id(rid)
    assert after["terminal_notified_at"] is not None
    assert after["last_progress_notified_at"] == mid["last_progress_notified_at"]
