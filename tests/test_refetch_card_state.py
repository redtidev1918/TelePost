"""Review-card state during a manual refetch (重抓) — §refetch-card-state.

现场反馈 (m10739): pressing 重抓 only appended a group notice while the post the
operator actually pressed stayed byte-identical — so the click read as a hang
(bug 1) and the candidate still looked publishable (bug 3).

The contract these tests pin:

  * while an attempt is ACTIVE the card says 「已提交重抓 / 当前候选已作废」 and
    drops 发布·拒绝·遮罩 (a replacement is the only way on);
  * when the attempt ends WITHOUT a replacement the card STAYS 「已作废（视为已拒
    绝）」 with only 重抓 + original link — pressing 重抓 means the operator rejected
    this work, so publish/reject must not resurrect; only a NEW 重抓 (fetching a
    new work, sequence incremented) moves forward;
  * a pending review that was NEVER refetched keeps its normal, fully-actionable
    card;
  * a decided/superseded review, a missing control message and a Telegram error
    are all no-ops: refreshing a card must never break the refetch flow.
"""
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from database import db_manager
from handlers import review
from telepost.application import refetch as refetch_workflow
from telepost.storage.sqlite.refetch import RefetchRepository
from telepost.telegram import review_keyboard


@pytest.fixture
async def card_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "refetch-card.db")
    monkeypatch.setattr(db_manager, "DB_PATH", db_path)
    monkeypatch.setattr(review, "REVIEW_CHAT_ID", -100123)
    monkeypatch.setattr(review, "ADMIN_IDS", [123456789])
    await db_manager.init_db()
    return db_path


async def _insert_review(*, status="pending", pixiv_id="111",
                         target_id="target-a", chain="", generation=0,
                         control_message_id=555,
                         link="https://www.pixiv.net/artworks/111",
                         media_json="[]", documents_json="[]"):
    now = time.time()
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            """
            INSERT INTO pending_reviews (
                idempotency_key, source, status, user_id, username,
                review_chat_id, media_json, documents_json, link,
                control_message_id, target_id, pixiv_id, work_type,
                review_chain_id, generation, created_at, updated_at
            ) VALUES (?, 'api', ?, 7, 'pf', ?, ?, ?, ?, ?, ?, ?,
                      'illustration', ?, ?, ?, ?)
            """,
            ("k%d" % int(now * 1000000), status, str(review.REVIEW_CHAT_ID),
             media_json, documents_json, link, control_message_id, target_id,
             pixiv_id, chain or "", generation, now, now),
        )
        review_id = cur.lastrowid
    if pixiv_id:
        async with db_manager.get_db() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO refetch_seen_candidates "
                "(review_chain_id, candidate_id, source, generation, created_at) "
                "VALUES (?, ?, 'original', ?, ?)",
                (chain or "chain-%d" % review_id, pixiv_id, generation, now),
            )
    return review_id


async def _row(review_id):
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT * FROM pending_reviews WHERE id=?", (review_id,))
        return await cur.fetchone()


async def _active_attempt(review_id, *, callback_id=9001, admitted=False):
    repo = RefetchRepository()
    attempt, refused = await repo.create_attempt(
        callback_key=f"cb:{review_id}:{callback_id}",
        review_chain_id=f"chain-{review_id}", generation=1,
        source_review_id=review_id, source_candidate_id="111",
    )
    assert refused is None
    if admitted:
        await repo.mark_admitted(attempt["request_id"], "slot-1")
    return attempt


def _labels(markup):
    return [button.text
            for row in markup.inline_keyboard
            for button in row]


# ---------------------------------------------------------------------------
# Card shapes
# ---------------------------------------------------------------------------
def test_refetch_pending_card_hides_publish_controls():
    text = review_keyboard.refetch_pending_text(review_id=135)
    assert "已提交重抓" in text and "已作废" in text
    assert "已等待约" not in text
    waited = review_keyboard.refetch_pending_text(review_id=135, minutes=2)
    assert "已等待约 2 分钟" in waited

    markup = review_keyboard.refetch_pending_keyboard(135, "https://ex/x")
    rows = markup.inline_keyboard
    assert rows[0][0].callback_data == "review_refetch:135"
    assert rows[1][0].url == "https://ex/x"
    for forbidden in ("发布", "拒绝", "遮罩"):
        assert all(forbidden not in label for label in _labels(markup))


@pytest.mark.asyncio
async def test_control_card_from_row_is_actionable_again(card_db):
    review_id = await _insert_review(
        media_json='[{"type": "photo", "file_id": "F1"}]')
    text, markup = review_keyboard.control_card_from_row(await _row(review_id))
    assert f"🕵️ 投稿待审核 #{review_id}" in text
    assert "1 个媒体 / 0 个文档" in text
    labels = _labels(markup)
    assert any("发布到频道" in label for label in labels)
    assert any("拒绝" in label for label in labels)
    assert any("重抓" in label for label in labels)


# ---------------------------------------------------------------------------
# refresh_refetch_card
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_refresh_card_shows_the_running_refetch(card_db):
    review_id = await _insert_review()
    await _active_attempt(review_id, admitted=True)

    bot = AsyncMock()
    assert await review.refresh_refetch_card(bot, review_id) is True

    kwargs = bot.edit_message_text.await_args.kwargs
    assert kwargs["chat_id"] == -100123
    assert kwargs["message_id"] == 555
    assert "已提交重抓" in kwargs["text"]
    labels = _labels(kwargs["reply_markup"])
    assert any("重抓" in label for label in labels)
    assert all("发布" not in label for label in labels)
    assert all("拒绝" not in label for label in labels)


@pytest.mark.asyncio
async def test_refresh_card_keeps_voided_card_when_failed_refetch(card_db):
    """A refetch that already ran must not resurrect publish/reject controls."""
    review_id = await _insert_review()
    attempt = await _active_attempt(review_id)
    repo = RefetchRepository()
    await repo.mark_failed(attempt["request_id"], "remote_failed")

    bot = AsyncMock()
    assert await review.refresh_refetch_card(bot, review_id) is True

    kwargs = bot.edit_message_text.await_args.kwargs
    assert "已作废" in kwargs["text"]
    assert "重抓提交失败" in kwargs["text"]
    labels = _labels(kwargs["reply_markup"])
    assert any("重抓" in label for label in labels)
    assert all("发布" not in label for label in labels)
    assert all("拒绝" not in label for label in labels)


@pytest.mark.asyncio
async def test_refresh_card_restores_the_card_when_never_refetched(card_db):
    """A pending review with no refetch history stays a normal actionable card."""
    review_id = await _insert_review()

    bot = AsyncMock()
    assert await review.refresh_refetch_card(bot, review_id) is True

    kwargs = bot.edit_message_text.await_args.kwargs
    assert f"🕵️ 投稿待审核 #{review_id}" in kwargs["text"]
    labels = _labels(kwargs["reply_markup"])
    assert any("发布到频道" in label for label in labels)
    assert any("拒绝" in label for label in labels)


@pytest.mark.asyncio
async def test_refresh_card_no_ops_for_decided_review(card_db):
    review_id = await _insert_review(status="rejected")
    bot = AsyncMock()
    assert await review.refresh_refetch_card(bot, review_id) is False
    bot.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_card_no_ops_without_a_control_message(card_db):
    review_id = await _insert_review(control_message_id=None)
    bot = AsyncMock()
    assert await review.refresh_refetch_card(bot, review_id) is False
    bot.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_card_survives_a_telegram_failure(card_db):
    review_id = await _insert_review()
    bot = AsyncMock()
    bot.edit_message_text.side_effect = RuntimeError("Bad Request: message to edit not found")

    # A cosmetic refresh must never bubble into the refetch flow.
    assert await review.refresh_refetch_card(bot, review_id) is False


# ---------------------------------------------------------------------------
# Wiring: the click, the remote acceptance and the progress watchdog
# ---------------------------------------------------------------------------
def _click(review_id, *, user_id=123456789, callback_id=7001):
    update = MagicMock()
    update.effective_user.id = user_id
    update.callback_query.data = f"review_refetch:{review_id}"
    update.callback_query.id = callback_id
    update.callback_query.answer = AsyncMock()
    context = MagicMock()
    context.bot = AsyncMock()
    return update, context


@pytest.mark.asyncio
async def test_refetch_click_moves_the_card_immediately(card_db, monkeypatch):
    review_id = await _insert_review()
    calls = []

    async def _fake_refresh(bot, rid, *, minutes=None):
        calls.append((rid, minutes))
        return True

    async def _fake_request(rid, **kwargs):
        return {"replayed": False, "state": "requested"}

    monkeypatch.setattr(review, "refresh_refetch_card", _fake_refresh)
    monkeypatch.setattr(refetch_workflow, "request_refetch", _fake_request)

    update, context = _click(review_id)
    await review.refetch_review(update, context)

    assert calls == [(review_id, None)]
    update.callback_query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_remote_acceptance_moves_the_card_and_notifies(card_db, monkeypatch):
    review_id = await _insert_review()
    calls = []

    async def _fake_refresh(bot, rid, *, minutes=None):
        calls.append((rid, minutes))
        return True

    async def _fake_request(rid, **kwargs):
        await kwargs["on_remote_result"]("accepted", {"slot": "bot1-daily"})
        return {"replayed": False, "state": "admitted"}

    monkeypatch.setattr(review, "refresh_refetch_card", _fake_refresh)
    monkeypatch.setattr(refetch_workflow, "request_refetch", _fake_request)

    update, context = _click(review_id)
    await review.refetch_review(update, context)

    assert calls == [(review_id, None), (review_id, None)]
    assert "已提交重抓" in context.bot.send_message.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_replayed_click_does_not_touch_the_card(card_db, monkeypatch):
    review_id = await _insert_review()
    calls = []

    async def _fake_refresh(bot, rid, *, minutes=None):
        calls.append(rid)
        return True

    async def _fake_request(rid, **kwargs):
        return {"replayed": True, "state": "admitted"}

    monkeypatch.setattr(review, "refresh_refetch_card", _fake_refresh)
    monkeypatch.setattr(refetch_workflow, "request_refetch", _fake_request)

    update, context = _click(review_id)
    await review.refetch_review(update, context)

    assert calls == []


@pytest.mark.asyncio
async def test_progress_reminder_shows_elapsed_time_on_the_card(card_db, monkeypatch):
    review_id = await _insert_review()
    attempt = await _active_attempt(review_id, admitted=True)
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE refetch_attempts SET created_at=? WHERE id=?",
            (time.time() - 10 * 60, attempt["id"]),
        )
    monkeypatch.setattr(review, "REFETCH_PROGRESS_REMIND_MINUTES", 5)
    monkeypatch.setattr(review, "REFETCH_STALE_TIMEOUT_MINUTES", 45)

    bot = AsyncMock()
    acted = await review.monitor_refetch_progress(bot)

    assert acted == 1
    assert "仍在处理中" in bot.send_message.await_args.kwargs["text"]
    assert "已等待约" in bot.edit_message_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_terminal_no_alternative_keeps_the_card_voided(card_db, monkeypatch):
    """A refetch that finds nothing leaves the card voided, not actionable."""
    review_id = await _insert_review()
    attempt = await _active_attempt(review_id, admitted=True)
    repo = RefetchRepository()
    await repo.mark_admitted(attempt["request_id"], "bot1-daily")
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE refetch_attempts SET created_at=? WHERE id=?",
            (time.time() - 50 * 60, attempt["id"]),
        )
    monkeypatch.setattr(review, "REFETCH_PROGRESS_REMIND_MINUTES", 2)
    monkeypatch.setattr(review, "REFETCH_STALE_TIMEOUT_MINUTES", 45)
    monkeypatch.setattr(review, "_read_pixivflow_refetch_status",
                        lambda target_id, request_id: "no_candidate")

    bot = AsyncMock()
    acted = await review.monitor_refetch_progress(bot)

    assert acted == 1
    assert "没有找到" in bot.send_message.await_args.kwargs["text"]
    kwargs = bot.edit_message_text.await_args.kwargs
    assert "已作废" in kwargs["text"]
    labels = _labels(kwargs["reply_markup"])
    assert any("重抓" in label for label in labels)
    assert all("发布" not in label for label in labels)
