"""Callback failures must never destroy the reviewer's card (§review-card-recovery).

The production incident (2026-10-22) had two independent defects:

1. a handler exception was *swallowed* after replacing the card with
   ``❌ 操作失败，请稍后重试。`` — buttons gone, bot data unchanged, and the
   durable inbox marked the update ``done`` so it was never retried;
2. a replay of a click on an already-decided review replaced the card with
   ``ℹ️ 该投稿当前状态：rejected`` — again without ``reply_markup``.

These tests drive real PTB routing (``setup_application`` + ``process_update``)
over real SQLite and a real ``UpdateInboxWorker``; only the Telegram wire is a
recording fake. Every assertion is about what the moderator can still do — and
about review state never changing as a side effect of failure handling.
"""
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest
from telegram.ext import Application

from database import db_manager
from handlers import review
from main import build_persistence, setup_application
from telepost.storage.sqlite.inbox import InboxRepository
from telepost.storage.sqlite.refetch import RefetchRepository
from telepost.telegram.inbox_worker import UpdateInboxWorker


@pytest.fixture(autouse=True)
def _no_blacklist(monkeypatch):
    import handlers.mode_selection as mode_selection
    monkeypatch.setattr(mode_selection, "is_blacklisted", lambda _uid: False)


class _RecordingBot:
    """Telegram wire fake: records every answer/edit, never talks to the API."""

    def __init__(self):
        self.id = 777
        self.username = self.name = "testbot"
        self.outbox = []
        self.edits = []          # (chat_id, message_id, text, markup)
        self.answers = []        # (text, show_alert)
        self.fail_edit = None    # optional exception raised on the next edit
        self._mid = 500

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    def _message(self):
        self._mid += 1
        message = MagicMock()
        message.message_id = self._mid
        return message

    async def send_message(self, chat_id, text, **kwargs):
        self.outbox.append(text)
        return self._message()

    async def send_photo(self, chat_id, photo, **kwargs):
        return self._message()

    async def edit_message_text(self, text=None, **kwargs):
        if self.fail_edit is not None:
            exc, self.fail_edit = self.fail_edit, None
            raise exc
        if text is None:
            text = kwargs.get("text", "")
        self.outbox.append(text)
        self.edits.append((kwargs.get("chat_id"), kwargs.get("message_id"),
                           text, kwargs.get("reply_markup")))
        return self._message()

    async def edit_message_reply_markup(self, **kwargs):
        return self._message()

    async def edit_message_media(self, **kwargs):
        return self._message()

    async def delete_message(self, **kwargs):
        return True

    async def answer_callback_query(self, **kwargs):
        self.answers.append((kwargs.get("text"), kwargs.get("show_alert")))
        return True


class _Harness:
    def __init__(self, application, bot, worker, store):
        self.application = application
        self.bot = bot
        self.worker = worker
        self.store = store

    async def click(self, action: str, review_id: int, *, update_id: int = 1,
                    actor: int = 42):
        message = {
            "message_id": 900 + update_id, "date": 0,
            "chat": {"id": -100, "type": "supergroup"},
            "from": {"id": actor, "is_bot": False, "first_name": "Reviewer"},
            "text": "review card",
        }
        payload = {"update_id": update_id, "callback_query": {
            "id": f"click-{update_id}", "chat_instance": "review-test",
            "from": {"id": actor, "is_bot": False, "first_name": "Reviewer"},
            "message": message,
            "data": f"review_{action}:{review_id}",
        }}
        await self.worker.receive(update_id, payload)
        return await self.worker.drain()

    async def raw_click(self, data: str, *, update_id: int = 1, actor: int = 42):
        message = {
            "message_id": 900 + update_id, "date": 0,
            "chat": {"id": -100, "type": "supergroup"},
            "from": {"id": actor, "is_bot": False, "first_name": "Reviewer"},
            "text": "menu",
        }
        payload = {"update_id": update_id, "callback_query": {
            "id": f"click-{update_id}", "chat_instance": "menu-test",
            "from": {"id": actor, "is_bot": False, "first_name": "Reviewer"},
            "message": message, "data": data,
        }}
        await self.worker.receive(update_id, payload)
        return await self.worker.drain()

    async def command(self, text: str, *, update_id: int = 50, actor: int = 42):
        """Route a /command through the SAME handler graph production uses."""
        payload = {"update_id": update_id, "message": {
            "message_id": update_id, "date": 0,
            "chat": {"id": actor, "type": "private"},
            "from": {"id": actor, "is_bot": False, "first_name": "Admin"},
            "text": text,
            "entities": [{"type": "bot_command", "offset": 0,
                          "length": len(text.split()[0])}],
        }}
        await self.worker.receive(update_id, payload)
        return await self.worker.drain()

    @property
    def last_edit(self):
        return self.bot.edits[-1][2] if self.bot.edits else ""

    @property
    def last_markup(self):
        return self.bot.edits[-1][3] if self.bot.edits else None

    @property
    def last_alert(self):
        return self.bot.answers[-1] if self.bot.answers else (None, None)


@pytest.fixture
async def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "bot.db"))
    await db_manager.init_db()
    monkeypatch.setattr(review, "ADMIN_IDS", [42])
    monkeypatch.setattr(review, "REVIEW_CHAT_ID", "-100")
    bot = _RecordingBot()
    persistence = build_persistence(str(tmp_path / "persistence.pickle"))
    application = Application.builder().bot(bot).persistence(persistence).build()
    setup_application(application)
    await application.initialize()
    await application.start()
    store = InboxRepository()
    worker = UpdateInboxWorker(application, repo=store, poll_interval=3600)
    application.bot_data["update_inbox_worker"] = worker
    worker.start()
    try:
        yield _Harness(application, bot, worker, store)
    finally:
        await worker.stop()
        await application.stop()
        await application.shutdown()


async def _seed_review(rid: int, *, status: str = "pending",
                       control: int | None = None,
                       chain: str = "", preview_state: str = "present",
                       link: str = "") -> None:
    now = time.time()
    async with db_manager.get_db() as conn:
        await conn.execute(
            "INSERT INTO pending_reviews (id,idempotency_key,source,status,user_id,"
            "submitter_user_id,actor_kind,actor_subject,title,tags,link,spoiler,"
            "media_json,documents_json,review_chat_id,control_message_id,"
            "review_chain_id,preview_state,target_id,pixiv_id,work_type,"
            "created_at,updated_at) "
            "VALUES (?,?,'api',?,7,7,'service','api:test-token','Title','#t',?,0,"
            "?,'[]','-100',?,?,?,'test-target',?,'illustration',?,?)",
            (rid, f"review-test-{rid}", status,
             link or f"https://www.pixiv.net/artworks/{rid}",
             json.dumps([{"type": "photo", "file_id": "IMG"}]),
             control if control is not None else 500 + rid,
             chain or f"chain-{rid}", preview_state, str(rid), now, now),
        )


async def _review_row(rid: int) -> dict:
    async with db_manager.get_db() as conn:
        cur = await conn.execute("SELECT * FROM pending_reviews WHERE id=?", (rid,))
        row = await cur.fetchone()
    return dict(row) if row is not None else {}


def _callback_datas(markup) -> list[str]:
    if markup is None:
        return []
    return [button.callback_data for row in markup.inline_keyboard
            for button in row if button.callback_data]


def _assert_no_generic_error(harness) -> None:
    """The old destructive fallback text must never replace a review card."""
    assert not any("操作失败，请稍后重试" in text for text in harness.bot.outbox)


# ---- the card survives failures --------------------------------------------


@pytest.mark.asyncio
async def test_state_mismatch_alerts_and_rebuilds_card_without_state_change(harness):
    """The exact production replay: click a decided review, keep the card."""
    await _seed_review(202, status="rejected")
    before = await _review_row(202)

    assert await harness.click("approve", 202) == 1

    alert_text, show_alert = harness.last_alert
    assert show_alert is True
    assert "rejected" in (alert_text or "")
    _assert_no_generic_error(harness)
    # The card is rebuilt read-only from the row: facts kept, moderation gone.
    assert "已拒绝（只读）" in harness.last_edit
    assert "审核 #202" in harness.last_edit
    assert "review_approve:" not in _callback_datas(harness.last_markup)
    assert "review_reject:" not in _callback_datas(harness.last_markup)
    assert await _review_row(202) == before, "failure path must not touch review state"


@pytest.mark.asyncio
async def test_pending_card_restore_keeps_action_buttons(harness):
    await _seed_review(301, status="pending")
    result = await review.restore_review_card(harness.bot, 301)
    assert result["ok"] is True and result["card"] == "pending"
    assert {"review_approve:301", "review_reject:301"} <= set(
        _callback_datas(harness.last_markup))


@pytest.mark.asyncio
async def test_repeat_clicks_do_not_mutate_state_or_duplicate_audit(harness):
    await _seed_review(302, status="pending")
    assert await harness.click("reject", 302, update_id=1) == 1
    decided = await _review_row(302)
    assert decided["status"] == "rejected"

    for update_id in (2, 3):
        assert await harness.click("reject", 302, update_id=update_id) == 1

    after = await _review_row(302)
    assert after["status"] == "rejected"
    assert after["decided_at"] == decided["decided_at"]
    assert after["decided_by"] == decided["decided_by"]
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event='review.rejected' AND review_id=302"
        )
        assert (await cur.fetchone())[0] == 1, "terminal state must not be re-decided"
    assert harness.last_alert[1] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status,note", [
    ("published", "已发布（只读）"),
    ("rejected", "已拒绝（只读）"),
    ("expired", "已过期（只读）"),
    ("publishing", "正在发布（只读）"),
])
async def test_terminal_cards_render_read_only(harness, status, note):
    await _seed_review(310, status=status)
    result = await review.restore_review_card(harness.bot, 310)
    assert result["ok"] is True and result["card"] == "read_only"
    assert note in harness.last_edit
    assert "review_approve:" not in _callback_datas(harness.last_markup)


@pytest.mark.asyncio
async def test_superseded_card_is_history_not_actions(harness):
    await _seed_review(311, status="superseded")
    result = await review.restore_review_card(harness.bot, 311)
    assert result["ok"] is True
    assert "已被重抓结果替代（只读）" in harness.last_edit
    assert _callback_datas(harness.last_markup) == []


@pytest.mark.asyncio
async def test_refetch_active_card_restores_refetch_projection(harness):
    """A pending review mid-重抓 must not come back with publish/reject."""
    await _seed_review(312, status="pending", chain="chain-312")
    await RefetchRepository().create_attempt(
        callback_key="cb-312", review_chain_id="chain-312", generation=0,
        source_review_id=312, source_candidate_id="https://www.pixiv.net/artworks/312",
    )
    result = await review.restore_review_card(harness.bot, 312)
    assert result["ok"] is True
    assert "已提交重抓" in harness.last_edit
    assert "review_approve:" not in _callback_datas(harness.last_markup)
    assert "review_refetch:312" in _callback_datas(harness.last_markup)


# ---- failures are classified, not swallowed ---------------------------------


@pytest.mark.asyncio
async def test_handler_failure_keeps_card_and_leaves_update_for_retry(harness, monkeypatch):
    """Window 1: a real failure must reach the inbox (retry), not mark done."""
    await _seed_review(320, status="pending")
    monkeypatch.setattr(
        review.review_service, "approve",
        AsyncMock(side_effect=RuntimeError("publisher exploded")),
    )

    assert await harness.click("approve", 320) == 1

    _assert_no_generic_error(harness)
    assert harness.last_alert[1] is True
    # Card rebuilt (buttons still there), state untouched.
    assert {"review_approve:320", "review_reject:320"} <= set(
        _callback_datas(harness.last_markup))
    row = await _review_row(320)
    assert row["status"] == "pending"
    counts = await harness.store.counts()
    assert counts.get("done", {}).get("n", 0) == 0, "failed update must not be done"
    assert counts.get("pending", {}).get("n", 0) == 1
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT state, attempts, last_error FROM webhook_inbox WHERE update_id=1")
        state, attempts, last_error = await cur.fetchone()
    assert state == "pending" and attempts == 1
    assert "Runtime" in (last_error or "")


@pytest.mark.asyncio
async def test_ui_only_edit_failure_is_not_retried(harness, monkeypatch):
    """Window 2: Telegram refusing the edit is not a business failure."""
    await _seed_review(321, status="pending")
    monkeypatch.setattr(
        review.review_service, "approve",
        AsyncMock(side_effect=BadRequest("Message is not modified")),
    )

    assert await harness.click("approve", 321) == 1

    counts = await harness.store.counts()
    assert counts.get("done", {}).get("n", 0) == 1
    _assert_no_generic_error(harness)


@pytest.mark.asyncio
async def test_unknown_callback_preserves_card(harness):
    await harness.raw_click("some_retired_namespace:9")

    assert harness.bot.edits == [], "unknown callback must not rewrite the message"
    assert harness.last_alert[0] == "❌ 未知操作"
    assert harness.last_alert[1] is True


# ---- the minimal auditable restore path ------------------------------------


@pytest.mark.asyncio
async def test_restore_command_is_admin_only(harness):
    await _seed_review(330, status="published")
    before = await _review_row(330)

    await harness.command("/restore_card 330", update_id=51, actor=43)
    assert any("没有审核权限" in text for text in harness.bot.outbox)
    assert harness.bot.edits == []

    await harness.command("/restore_card 330", update_id=52, actor=42)
    assert harness.last_edit and "已发布（只读）" in harness.last_edit
    assert await _review_row(330) == before


@pytest.mark.asyncio
async def test_restore_command_dry_run_does_not_edit(harness):
    await _seed_review(331, status="rejected")
    await harness.command("/restore_card 331 --dry-run", update_id=53)
    assert harness.bot.edits == []
    assert any("未修改任何内容" in text for text in harness.bot.outbox)


@pytest.mark.asyncio
async def test_missing_control_message_never_reposts(harness):
    await _seed_review(332, status="published", control=0)
    async with db_manager.get_db() as conn:
        await conn.execute("UPDATE pending_reviews SET control_message_id=NULL WHERE id=332")

    result = await review.restore_review_card(harness.bot, 332)

    assert result == {"review_id": 332, "ok": False,
                      "status": "published", "control_message_id": None,
                      "chat_id": "-100", "reason": "no_control_message"}
    assert harness.bot.edits == [] and harness.bot.outbox == []


@pytest.mark.asyncio
async def test_gone_message_is_reported_without_raising(harness):
    await _seed_review(333, status="rejected")
    harness.bot.fail_edit = BadRequest("Message to edit not found")
    result = await review.restore_review_card(harness.bot, 333)
    assert result["ok"] is False and result["reason"] == "message_gone"


@pytest.mark.asyncio
async def test_restore_unknown_review_is_harmless(harness):
    result = await review.restore_review_card(harness.bot, 999999)
    assert result["ok"] is False and result["reason"] == "not_found"
    assert harness.bot.edits == []


@pytest.mark.asyncio
async def test_restore_never_touches_review_state(harness):
    await _seed_review(334, status="published")
    before = await _review_row(334)
    await review.restore_review_card(harness.bot, 334)
    await review.restore_review_card(harness.bot, 334)
    assert await _review_row(334) == before


def test_failure_classification():
    from telepost.telegram.callback_feedback import (
        is_retryable_error, is_ui_only_error,
    )

    assert is_ui_only_error(BadRequest("Message is not modified"))
    assert is_ui_only_error(BadRequest("Query is too old and response timeout expired"))
    assert not is_ui_only_error(RuntimeError("publisher exploded"))
    assert is_retryable_error(ConnectionError("reset"))
    assert not is_retryable_error(ValueError("bad id"))
