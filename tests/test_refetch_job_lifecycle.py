"""Refetch as a first-class persisted JOB with a heartbeat (§refetch-lifecycle).

The refetch attempt is no longer a fire-and-forget HTTP call: it is a durable
row driven by a 30-second heartbeat loop with:

* a LOCAL heartbeat (``heartbeat_at``/``heartbeat_count``) proving OUR poller is
  alive, and REMOTE liveness clocks (``remote_heartbeat_at``/``remote_state_at``)
  proving the PixivFlow slot is alive;
* bounded budgets tied to LIVENESS — 排队超时 / 停摆 / 硬上限 / 看门狗心跳;
* a guaranteed, idempotent terminal notification (never a silent terminal);
* restart recovery from the durable rows only (never from memory).

Scenarios pinned here: normal replacement, remote failure verdict, no-candidate
verdict, every timeout class, a repeated click, restart recovery, a restart in
the middle of SEARCHING, and a 1000-attempt deterministic stress run.
"""
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from database import db_manager
from handlers import review
from telepost.storage.sqlite.refetch import RefetchRepository

from tests.helpers_refetch import (
    attempt as _attempt,
    callback_update as _callback_update,
    insert_review as _insert_review,
    photo_message as _photo_message,
)


@pytest.fixture(autouse=True)
def _reset_refetch_process_state(monkeypatch):
    """Every test starts as a FRESH process.

    ``_refetch_recovery_done`` is a once-per-process guard and ``_wake_pinged``
    is in-memory wake de-duplication; a leaked value from an earlier test would
    silently disable recovery or a wake and make these tests order-dependent.
    """
    monkeypatch.setattr(review, "_refetch_recovery_done", False)
    monkeypatch.setattr(review, "_wake_pinged", set())


def _stub_remote(monkeypatch, handler):
    """Patch the ONE patchable remote seam with a detail-aware stub."""
    def _reader(target_id, request_id, detail=False):
        value = handler(target_id, request_id)
        if detail:
            if isinstance(value, dict):
                return value
            return {"state": str(value or ""), "heartbeat": None}
        return value
    monkeypatch.setattr(review, "_read_pixivflow_refetch_status", _reader)


async def _attempt_row(request_id):
    return await RefetchRepository().find_by_request_id(request_id)


async def _start(*, state="searching", age_minutes=0.0, heartbeat_age_minutes=None,
                 pixiv_id="111", status="pending"):
    """Create a review + an admitted attempt, optionally aged."""
    review_id = await _insert_review(status=status, pixiv_id=pixiv_id)
    attempt, refused = await _attempt(
        chain_id=f"chain-{review_id}", source_review_id=review_id,
        callback_id=9001,
    )
    assert refused is None
    repo = RefetchRepository()
    if state != "requested":
        await repo.mark_admitted(attempt["request_id"], "slot-1")
    if age_minutes or heartbeat_age_minutes is not None:
        now = time.time()
        sets = []
        params = []
        if age_minutes:
            sets += ["created_at=?", "started_at=?", "updated_at=?"]
            stale = now - age_minutes * 60
            params += [stale, stale, stale]
        if heartbeat_age_minutes is not None:
            sets.append("heartbeat_at=?")
            params.append(now - heartbeat_age_minutes * 60)
        params.append(attempt["id"])
        async with db_manager.get_db() as conn:
            await conn.execute(
                f"UPDATE refetch_attempts SET {', '.join(sets)} WHERE id=?",
                tuple(params),
            )
    return review_id, attempt


def _bot():
    return AsyncMock()


# ---------------------------------------------------------------------------
# (a) normal replacement end-to-end
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_refetch_replacement_end_to_end(refetch_db, monkeypatch):
    """The happy path: heartbeat advances the stage, then the replacement
    submission installs the new generation head and resolves REPLACED."""
    review_id, attempt = await _start(state="searching")
    _stub_remote(monkeypatch, lambda t, r: "selected")
    bot = _bot()

    acted = await review.poll_refetch_jobs(bot, now=time.time())
    assert acted == 1
    row = await _attempt_row(attempt["request_id"])
    assert row["state"] == "filtering"
    # OUR heartbeat is recorded on a successful poll, and the tick does not
    # consume the terminal claim.
    assert row["heartbeat_at"] is not None
    assert int(row["heartbeat_count"] or 0) == 1
    assert int(row["poll_failures"] or 0) == 0
    assert row["next_poll_at"] is not None

    # PixivFlow delivers the replacement through the review intake.
    bot2 = _bot()
    bot2.send_photo.return_value = _photo_message()
    control = MagicMock()
    control.message_id = 11
    bot2.send_message.return_value = control
    await review.queue_review_from_file_ids(
        bot2, [{"type": "photo", "file_id": "NEW_ART"}], [],
        tags="#pixiv", title="candidate 222",
        link="https://www.pixiv.net/artworks/222",
        user_id=7, username="pixivflow", target_id="target-a",
        work_type="illustration", pixiv_id="222",
        idempotency_key="repl-1", source="api",
        refetch_request_id=attempt["request_id"],
    )

    row = await _attempt_row(attempt["request_id"])
    assert row["state"] == "replaced"
    assert row["result_review_id"]
    assert (row["terminal_reason"] or "").strip()
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT status, generation FROM pending_reviews WHERE id=?",
            (row["result_review_id"],))
        new_head = dict(await cur.fetchone())
    assert new_head["status"] == "pending"
    assert new_head["generation"] == 1


# ---------------------------------------------------------------------------
# (b) PixivFlow search-failure verdict / (c) no-candidate verdict
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remote_search_failure_verdict_is_visible(refetch_db, monkeypatch):
    review_id, attempt = await _start(state="searching")
    _stub_remote(monkeypatch, lambda t, r: "failed")
    bot = _bot()

    acted = await review.poll_refetch_jobs(bot, now=time.time())
    assert acted == 1
    row = await _attempt_row(attempt["request_id"])
    assert row["state"] == "failed"
    assert row["failure_code"] == "remote_failed"
    assert (row["terminal_reason"] or "").strip()
    assert row["finished_at"] is not None
    text = bot.send_message.await_args.kwargs["text"]
    assert "重抓失败" in text and "可以再次重抓" in text
    assert f"任务ID：refetch-{review_id}-" in text
    # 终态必通知: exactly one terminal notice, and the claim is durable.
    assert row["terminal_notified_at"] is not None
    # A second tick must not notify again (the attempt is terminal).
    await review.poll_refetch_jobs(bot, now=time.time() + 60)
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_no_candidate_verdict_is_visible(refetch_db, monkeypatch):
    review_id, attempt = await _start(state="searching")
    _stub_remote(monkeypatch, lambda t, r: "no_candidate")
    bot = _bot()

    acted = await review.poll_refetch_jobs(bot, now=time.time())
    assert acted == 1
    row = await _attempt_row(attempt["request_id"])
    assert row["state"] == "no_candidate"
    assert (row["terminal_reason"] or "").strip()
    text = bot.send_message.await_args.kwargs["text"]
    assert "没有找到新的可替换作品" in text
    assert f"任务ID：refetch-{review_id}-" in text
    # The current review is untouched (commit-after-success).
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT status FROM pending_reviews WHERE id=?", (review_id,))
        assert (await cur.fetchone())["status"] == "pending"


# ---------------------------------------------------------------------------
# (d) every timeout class
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_queued_remote_timeout(refetch_db, monkeypatch):
    """A remote that never claims the slot is a QUEUE problem: queued_too_long."""
    review_id, attempt = await _start(state="requested", age_minutes=31)
    monkeypatch.setattr(review, "REFETCH_QUEUED_TIMEOUT_MINUTES", 30)
    monkeypatch.setattr(review, "REFETCH_STALE_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_HARD_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_PROGRESS_REMIND_MINUTES", 0)
    _stub_remote(monkeypatch, lambda t, r: "pending")
    # An UNadmitted REQUESTED row is never polled remotely; admit it so the
    # queued branch (which needs a remote "pending") is the one under test.
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")
    bot = _bot()

    acted = await review.poll_refetch_jobs(bot, now=time.time())
    assert acted == 1
    row = await _attempt_row(attempt["request_id"])
    assert row["state"] == "timeout"
    assert row["failure_code"] == "queued_too_long"
    # terminal_reason carries the SAME machine token as failure_code (the row is
    # self-consistent and machine-checkable); the Chinese explanation is the
    # moderator-facing text below.
    assert row["terminal_reason"] == "queued_too_long"
    assert "排队超时" in bot.send_message.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_stalled_no_progress_timeout(refetch_db, monkeypatch):
    """No remote state change AND no fresh remote heartbeat → stalled."""
    review_id, attempt = await _start(state="searching", age_minutes=16)
    monkeypatch.setattr(review, "REFETCH_STAGE_TIMEOUT_MINUTES", 15)
    monkeypatch.setattr(review, "REFETCH_STALE_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_HARD_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_PROGRESS_REMIND_MINUTES", 0)
    _stub_remote(monkeypatch, lambda t, r: "pending")
    bot = _bot()

    acted = await review.poll_refetch_jobs(bot, now=time.time())
    assert acted == 1
    row = await _attempt_row(attempt["request_id"])
    assert row["state"] == "timeout"
    assert row["failure_code"] == "stalled_no_progress"
    assert "停留超过" in bot.send_message.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_live_remote_heartbeat_protects_a_slow_search(refetch_db, monkeypatch):
    """A 2–20 minute (once 10 hour) slot is never killed while it is ALIVE."""
    review_id, attempt = await _start(state="searching", age_minutes=30)
    monkeypatch.setattr(review, "REFETCH_STAGE_TIMEOUT_MINUTES", 15)
    monkeypatch.setattr(review, "REFETCH_STALE_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_HARD_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_QUEUED_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_PROGRESS_REMIND_MINUTES", 0)
    now = time.time()
    # The slot IS claimed (selected) and the remote heartbeat is 5 s old: the
    # local updated_at is 30 minutes stale, and that alone must NOT kill it.
    _stub_remote(monkeypatch, lambda t, r: {
        "state": "selected", "heartbeat": now - 5, "job_id": r,
    })
    bot = _bot()

    acted = await review.poll_refetch_jobs(bot, now=now)
    assert acted == 0
    row = await _attempt_row(attempt["request_id"])
    # Still running: the remote heartbeat is fresh (无进展且无心跳才算停摆).
    assert row["state"] == "filtering"
    assert row["remote_heartbeat_at"] == pytest.approx(now - 5, abs=1)
    assert bot.send_message.await_count == 0


@pytest.mark.asyncio
async def test_hard_timeout_is_absolute(refetch_db, monkeypatch):
    review_id, attempt = await _start(state="searching", age_minutes=91)
    monkeypatch.setattr(review, "REFETCH_HARD_TIMEOUT_MINUTES", 90)
    monkeypatch.setattr(review, "REFETCH_STAGE_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_STALE_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_PROGRESS_REMIND_MINUTES", 0)
    now = time.time()
    # Even a LIVE remote heartbeat cannot extend an attempt past the ceiling.
    _stub_remote(monkeypatch, lambda t, r: {
        "state": "pending", "heartbeat": now, "job_id": r,
    })
    bot = _bot()

    acted = await review.poll_refetch_jobs(bot, now=now)
    assert acted == 1
    row = await _attempt_row(attempt["request_id"])
    assert row["state"] == "timeout"
    assert row["failure_code"] == "stalled_after_hard_timeout"
    assert "未完成" in bot.send_message.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_watchdog_no_heartbeat_fails_the_attempt(refetch_db, monkeypatch):
    """OUR heartbeat stale (the poller itself is broken) → FAILED watchdog."""
    review_id, attempt = await _start(
        state="searching", age_minutes=40, heartbeat_age_minutes=25)
    monkeypatch.setattr(review, "REFETCH_STALE_TIMEOUT_MINUTES", 20)
    monkeypatch.setattr(review, "REFETCH_HARD_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_STAGE_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_PROGRESS_REMIND_MINUTES", 0)
    _stub_remote(monkeypatch, lambda t, r: "pending")
    bot = _bot()

    acted = await review.poll_refetch_jobs(bot, now=time.time())
    assert acted == 1
    row = await _attempt_row(attempt["request_id"])
    assert row["state"] == "failed"
    assert row["failure_code"] == "watchdog_no_heartbeat"
    assert (row["terminal_reason"] or "").strip()
    assert "本机轮询" in bot.send_message.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_never_polled_row_is_polled_not_failed(refetch_db, monkeypatch):
    """A NULL heartbeat means "never polled by this process" (a row adopted
    after a restart): it must be POLLED, never failed on first sight — the
    redeploy rule."""
    review_id, attempt = await _start(state="searching", age_minutes=40)
    monkeypatch.setattr(review, "REFETCH_STALE_TIMEOUT_MINUTES", 20)
    monkeypatch.setattr(review, "REFETCH_HARD_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_STAGE_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_QUEUED_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_PROGRESS_REMIND_MINUTES", 0)
    _stub_remote(monkeypatch, lambda t, r: "pending")
    bot = _bot()

    await review.poll_refetch_jobs(bot, now=time.time())
    row = await _attempt_row(attempt["request_id"])
    assert row["state"] == "searching"
    assert bot.send_message.await_count == 0


# ---------------------------------------------------------------------------
# (e) repeated clicks
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_second_click_while_active_creates_no_second_job(refetch_db, monkeypatch):
    """One active attempt per chain: a second click must not create a job."""
    review_id, attempt = await _start(state="searching")
    submitted = MagicMock(return_value={"status": "accepted", "slotId": "slot-2"})
    monkeypatch.setattr(review, "_submit_pixivflow_refetch", submitted)

    update = _callback_update(f"review_refetch:{review_id}", callback_id=9002)
    context = MagicMock(bot=AsyncMock())
    await review.refetch_review(update, context)

    assert "正在重抓，请稍候" in update.callback_query.answer.await_args.kwargs["text"]
    assert submitted.call_count == 0
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) AS c FROM refetch_attempts WHERE review_chain_id=?",
            (f"chain-{review_id}",))
        assert (await cur.fetchone())["c"] == 1


@pytest.mark.asyncio
async def test_same_callback_redelivery_reuses_the_same_job(refetch_db, monkeypatch):
    """Telegram redelivery is not a new job: SAME callback key → SAME request id."""
    review_id, attempt = await _start(state="searching")
    repo = RefetchRepository()
    replayed, refused = await repo.create_attempt(
        callback_key=f"cb:{review_id}:9001", review_chain_id=f"chain-{review_id}",
        generation=1, source_review_id=review_id, source_candidate_id="111",
    )
    assert refused is None
    assert replayed["request_id"] == attempt["request_id"]


# ---------------------------------------------------------------------------
# (f) restart recovery / (g) restart mid-SEARCHING
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_restart_recovery_applies_a_remote_terminal_state(refetch_db, monkeypatch):
    review_id, attempt = await _start(state="searching", heartbeat_age_minutes=10)
    _stub_remote(monkeypatch, lambda t, r: "no_candidate")
    bot = _bot()

    recovered = await review.recover_refetch_jobs(bot, now=time.time())
    assert recovered == 1
    row = await _attempt_row(attempt["request_id"])
    assert row["state"] == "no_candidate"
    assert (row["terminal_reason"] or "").strip()
    assert "没有找到新的可替换作品" in bot.send_message.await_args.kwargs["text"]

    # The audit trail records the recovery (once, with the count).
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) AS c FROM audit_events "
            "WHERE event='review.refetch_recovered_after_restart'")
        assert (await cur.fetchone())["c"] >= 1

    # Once per PROCESS: the next tick does not sweep again.
    assert await review.recover_refetch_jobs(bot, now=time.time()) == 0


@pytest.mark.asyncio
async def test_restart_mid_searching_keeps_the_job_running(refetch_db, monkeypatch):
    """A process restart in the middle of SEARCHING must not lose the job."""
    review_id, attempt = await _start(state="searching", heartbeat_age_minutes=10)
    now = time.time()
    _stub_remote(monkeypatch, lambda t, r: {
        "state": "selected", "heartbeat": now - 3, "job_id": r,
    })
    bot = _bot()

    # Startup sweep adopts the row and applies the remote's newer stage.
    await review.recover_refetch_jobs(bot, now=now)
    row = await _attempt_row(attempt["request_id"])
    assert row["state"] == "filtering"
    assert bot.send_message.await_count == 0  # nothing user-visible changed

    # The regular heartbeat loop then keeps driving it.
    now2 = now + 30
    _stub_remote(monkeypatch, lambda t, r: {
        "state": "selected", "heartbeat": now2 - 3, "job_id": r,
    })
    await review.poll_refetch_jobs(bot, now=now2)
    row = await _attempt_row(attempt["request_id"])
    assert row["state"] == "filtering"
    assert row["heartbeat_at"] == pytest.approx(now2, abs=1)


@pytest.mark.asyncio
async def test_terminal_notice_failure_is_persisted_for_retry(refetch_db, monkeypatch):
    """终态必通知: a Telegram failure must leave a DURABLE retry, never silence."""
    from telepost.storage.sqlite.submitter_notifications import (
        SubmitterNotificationRepository,
    )

    review_id, attempt = await _start(state="searching")
    _stub_remote(monkeypatch, lambda t, r: "failed")
    bot = _bot()
    bot.send_message.side_effect = RuntimeError("telegram down")

    await review.poll_refetch_jobs(bot, now=time.time())

    row = await _attempt_row(attempt["request_id"])
    assert row["state"] == "failed"
    queued = await SubmitterNotificationRepository().pending_refetch_terminal()
    assert len(queued) == 1
    assert f"任务ID：refetch-{review_id}-" in queued[0]["payload"]
    assert "重抓失败" in queued[0]["payload"]
    # An audit trail exists so doctor/operators can see the undelivered notice.
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) AS c FROM audit_events "
            "WHERE event='review.refetch_terminal_notify_undelivered'")
        assert (await cur.fetchone())["c"] == 1

    # The outbox flush delivers it later.
    bot.send_message.side_effect = None
    sent = await review.flush_refetch_terminal_notifications(bot)
    assert sent == 1
    assert await SubmitterNotificationRepository().pending_refetch_terminal() == []
    assert "重抓失败" in bot.send_message.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_remote_unreadable_is_not_a_silent_verdict(refetch_db, monkeypatch):
    """An unreadable remote records a failed poll + backoff, and stays active."""
    review_id, attempt = await _start(state="searching")
    monkeypatch.setattr(review, "REFETCH_PROGRESS_REMIND_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_STALE_TIMEOUT_MINUTES", 0)

    def _boom(target_id, request_id, detail=False):
        raise OSError("remote unavailable")

    monkeypatch.setattr(review, "_read_pixivflow_refetch_status", _boom)
    bot = _bot()
    now = time.time()

    acted = await review.poll_refetch_jobs(bot, now=now)
    assert acted == 0
    row = await _attempt_row(attempt["request_id"])
    assert row["state"] == "searching"
    assert int(row["poll_failures"] or 0) == 1
    assert row["heartbeat_at"] is not None  # our liveness is still recorded
    assert float(row["next_poll_at"]) > now  # capped exponential backoff


@pytest.mark.asyncio
async def test_unknown_remote_state_does_not_fake_progress(refetch_db, monkeypatch):
    """An empty/unknown remote state must not look like a state transition."""
    review_id, attempt = await _start(state="searching")
    monkeypatch.setattr(review, "REFETCH_PROGRESS_REMIND_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_STAGE_TIMEOUT_MINUTES", 0)
    _stub_remote(monkeypatch, lambda t, r: "")
    bot = _bot()

    await review.poll_refetch_jobs(bot, now=time.time())
    row = await _attempt_row(attempt["request_id"])
    assert row["state"] == "searching"
    assert not (row["last_remote_state"] or "")


# ---------------------------------------------------------------------------
# Stress: 1000 synthetic attempts, deterministic, no sleeping
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_thousand_attempts_all_reach_a_reported_terminal_state(
    refetch_db, monkeypatch,
):
    """1000 attempts, one bounded injected clock, no sleeping.

    A fixed mix of production failure modes is injected per attempt:
      * 0 — the remote reports a SEARCH FAILURE (``failed``)
      * 1 — the remote reports NO CANDIDATE (``no_candidate``)
      * 2 — the remote is stuck QUEUED forever (``pending``) → 排队超时
      * 3 — the remote endpoint is unreachable (404/raise) → polls fail,
            OUR heartbeat keeps being recorded, and the stall budget still ends it
      * 4 — the remote reaches a stage and never terminalizes (the replacement
            arrives but the delivery callback is lost) → 停摆超时

    Invariants over the whole population: (a) ZERO attempts left ACTIVE,
    (b) every terminal attempt carries a non-empty ``terminal_reason``,
    (c) no terminal attempt has an empty ``refetch_events`` timeline,
    (d) no attempt outlived its budget, and every attempt took exactly one
    durable terminal-notification claim — including the attempts whose direct
    Telegram send FAILED, which must survive in the durable outbox.
    """
    from telepost.storage.sqlite.submitter_notifications import (
        SubmitterNotificationRepository,
    )

    total = 1000
    base = time.time()
    monkeypatch.setattr(review, "REFETCH_PROGRESS_REMIND_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_WAKE_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_STAGE_TIMEOUT_MINUTES", 15)
    monkeypatch.setattr(review, "REFETCH_STALE_TIMEOUT_MINUTES", 0)
    monkeypatch.setattr(review, "REFETCH_HARD_TIMEOUT_MINUTES", 90)
    monkeypatch.setattr(review, "REFETCH_QUEUED_TIMEOUT_MINUTES", 30)
    # No real HTTP: the wake path must never escape into the network.
    monkeypatch.setattr(review, "_submit_pixivflow_refetch",
                        MagicMock(return_value={"status": "accepted"}))

    for index in range(total):
        review_id = await _insert_review(
            pixiv_id=str(100000 + index), target_id=f"target-{index}")
        attempt, refused = await _attempt(
            chain_id=f"chain-{review_id}", source_review_id=review_id,
            callback_id=9001 + index,
        )
        assert refused is None
        await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")

    scenario = {}
    seen = {}

    def _handler(target_id, request_id):
        mode = scenario.setdefault(request_id, abs(hash(request_id)) % 5)
        count = seen.get(request_id, 0)
        seen[request_id] = count + 1
        if mode == 0:
            return "failed"
        if mode == 1:
            return "no_candidate"
        if mode == 2:
            return "pending"
        if mode == 3:
            raise OSError("HTTP 404 from PixivFlow")
        return "delivery_pending"

    _stub_remote(monkeypatch, _handler)

    # callback delivery failure: every 4th terminal notice fails at Telegram.
    bot = _bot()
    send_failures = []
    counter = {"n": 0}

    def _maybe_fail(*args, **kwargs):
        counter["n"] += 1
        if counter["n"] % 4 == 0:
            send_failures.append(counter["n"])
            raise RuntimeError("telegram delivery failed")
        return MagicMock(message_id=1000 + counter["n"])

    bot.send_message.side_effect = _maybe_fail

    # One bounded injected clock: T, T+5s, T+16min, T+31min, then two more
    # ticks that must NOT notify anybody a second time.
    ticks = [0, 5, 16 * 60, 31 * 60, 32 * 60, 33 * 60]
    started = time.time()
    for offset in ticks:
        await review.poll_refetch_jobs(bot, now=base + offset)
    elapsed = time.time() - started
    # Gross hanging-guard only, not a perf benchmark: the 1000 machine rows and
    # the injected-clock poll run in ~11s on a quiet machine but public CI
    # runners are often CPU-starved (observed 58-75s) while still correct.
    assert elapsed < 120, f"stress run took {elapsed:.1f}s (target < 120s)"

    async with db_manager.get_db() as conn:
        cur = await conn.execute("SELECT request_id, state, terminal_reason, "
                                 "notify_count, terminal_notified_at, created_at, "
                                 "finished_at, failure_code FROM refetch_attempts")
        rows = [dict(r) for r in await cur.fetchall()]
        cur = await conn.execute(
            "SELECT request_id, COUNT(*) AS c FROM refetch_events "
            "GROUP BY request_id")
        event_counts = {r["request_id"]: r["c"] for r in await cur.fetchall()}

    assert len(rows) == total
    active = [r for r in rows if r["state"] in (
        "requested", "searching", "filtering", "candidate_found")]
    assert not active, f"{len(active)} attempts never converged"
    for row in rows:
        assert (row["terminal_reason"] or "").strip(), row["request_id"]
        assert event_counts.get(row["request_id"]), row["request_id"]
        assert row["terminal_notified_at"], row["request_id"]
        assert row["finished_at"]
        assert row["finished_at"] - row["created_at"] <= 90 * 60 + 60

    # The failure mix actually happened (no scenario silently never ran).
    # NOTE the precedence between the two liveness budgets, which the mix
    # exposes: for a remote that keeps reporting ``pending`` forever the STALL
    # budget (15 min) is shorter than the QUEUE budget (30 min), so a row first
    # observed early is reported as ``stalled_no_progress`` while a row first
    # observed after the queue budget already expired is reported as
    # ``queued_too_long``. Both are terminal, both carry ``terminal_reason``,
    # both reach the moderator — see AGENTS.md §refetch-lifecycle. A
    # ``no_alternative`` disposition writes no ``failure_code`` (only failure
    # terminals carry one), so the reason is the key for that row.
    codes = {}
    for row in rows:
        key = row["failure_code"] or row["terminal_reason"]
        codes[key] = codes.get(key, 0) + 1
    assert set(codes) == {"remote_failed", "no_alternative", "queued_too_long",
                          "stalled_no_progress"}, codes

    # 终态必通知 is EXACTLY once per attempt: 1000 attempts, 1000 direct sends,
    # and the ones Telegram refused are durable in the outbox.
    assert bot.send_message.await_count == total
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) AS c FROM submitter_notifications "
            "WHERE kind = 'refetch_terminal' AND state = 'pending'")
        outbox_pending = (await cur.fetchone())["c"]
    assert outbox_pending == len(send_failures) > 0
    # The outbox readers are deliberately bounded (one page of 50 per flush), so
    # a backlog drains over several 300s cleanup ticks rather than all at once.
    bot.send_message.side_effect = None
    flushed = 0
    while True:
        page = await review.flush_refetch_terminal_notifications(bot, limit=50)
        flushed += page
        if not page:
            break
    assert flushed == len(send_failures)
    assert await SubmitterNotificationRepository().pending_refetch_terminal(
        limit=50) == []
