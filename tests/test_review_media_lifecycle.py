"""§review-media-ttl: 36 小时清理、按需恢复、删除时限与不确定窗口。"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest, TimedOut

from database import db_manager
from handlers import review
from telepost.storage.sqlite.reviews import ReviewRepository
from telepost.telegram import review_media
from telepost.telegram.review_stager import TelegramReviewStager

NOW = 1_800_000_000.0
HOUR = 3600.0
CHAT = "-100123"

MEDIA = [{"type": "photo", "file_id": "file-a"}, {"type": "photo", "file_id": "file-b"}]
DOCS = [{"type": "document", "file_id": "file-doc"}]
_KEYS = iter(range(1, 10_000))


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'media.db'))
    await db_manager.init_db()
    return ReviewRepository()


async def add(
    store,
    *,
    sent_age_hours=35.0,
    state="present",
    status="pending",
    ids="[101, 102]",
    control=103,
    chat=CHAT,
    media=None,
    documents=None,
    attempts=0,
    updated_at=None,
    legacy=False,
):
    """Insert a review whose preview media was sent ``sent_age_hours`` ago."""
    created = NOW - sent_age_hours * HOUR
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "INSERT INTO pending_reviews (idempotency_key,source,user_id,"
            "submitter_user_id,title,link,review_chat_id,review_message_ids,"
            "control_message_id,media_json,documents_json,spoiler,status,"
            "created_at,updated_at,preview_state,preview_sent_at,preview_attempts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{next(_KEYS)}:{status}:{sent_age_hours}:{ids}", "chat", 99, 7, "<title>",
             "https://www.pixiv.net/artworks/12345", chat, ids, control,
             json.dumps(MEDIA if media is None else media),
             json.dumps(DOCS if documents is None else documents),
             0, status, created, NOW if updated_at is None else updated_at,
             None if legacy else state,
             None if legacy else created, attempts),
        )
        return cur.lastrowid


def _fake_stage(monkeypatch, new_ids=(900, 901), error=None):
    """Stand-in for the Telegram staging so regen runs without network."""
    calls = []

    async def stage(self, media, documents, *, caption, spoiler,
                    message_ids=None, **kwargs):
        calls.append({"media": media, "documents": documents,
                      "spoiler": spoiler})
        if error is not None:
            message_ids.append(new_ids[0])
            raise error
        message_ids.extend(new_ids)
        return media, documents, list(message_ids)

    monkeypatch.setattr(TelegramReviewStager, "stage_file_ids", stage)
    return calls


def _admin_click(rid, admin=7):
    query = AsyncMock()
    query.data = f"review_preview:{rid}"
    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=admin)
    return update, query


def _callback_data(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


async def _audit_events(event):
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event=?", (event,))
        return (await cur.fetchone())[0]


# ---- 36 小时边界 -----------------------------------------------------------


@pytest.mark.asyncio
async def test_media_kept_at_35_hours(store):
    rid = await add(store, sent_age_hours=35.0)
    bot = AsyncMock()
    assert await review_media.clean_review_preview_media(bot, now=NOW) == 0
    bot.delete_message.assert_not_awaited()
    row = await store.get(rid)
    assert row["preview_state"] == "present"
    assert json.loads(row["review_message_ids"]) == [101, 102]


@pytest.mark.asyncio
async def test_media_cleared_at_36_hours_and_control_card_survives(store):
    rid = await add(store, sent_age_hours=36.5)
    bot = AsyncMock()
    assert await review_media.clean_review_preview_media(bot, now=NOW) == 1
    deleted = sorted(c.kwargs["message_id"] for c in bot.delete_message.await_args_list)
    assert deleted == [101, 102]  # never the control card
    row = await store.get(rid)
    assert row["preview_state"] == "cleared"
    assert row["status"] == "pending"  # moderation still possible
    assert row["preview_sent_at"] is None
    assert await _audit_events("review.preview_cleared") == 1
    # the card gains the on-demand restore button, moderation buttons stay
    markup = bot.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    data = _callback_data(markup)
    assert bot.edit_message_reply_markup.await_args.kwargs["message_id"] == 103
    assert f"review_preview:{rid}" in data
    assert any(item and item.startswith("review_approve:") for item in data)


@pytest.mark.asyncio
async def test_ttl_zero_disables_cleanup(store, monkeypatch):
    await add(store, sent_age_hours=100.0)
    monkeypatch.setattr(review_media, "REVIEW_MEDIA_TTL_HOURS", 0.0)
    bot = AsyncMock()
    assert await review_media.clean_review_preview_media(bot, now=NOW) == 0
    bot.delete_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_rows_anchor_on_created_at(store):
    rid = await add(store, sent_age_hours=40.0, legacy=True)
    bot = AsyncMock()
    assert await review_media.clean_review_preview_media(bot, now=NOW) == 1
    assert (await store.get(rid))["preview_state"] == "cleared"


@pytest.mark.asyncio
async def test_terminal_reviews_are_never_cleaned(store):
    await add(store, sent_age_hours=40.0, status="expired")
    await add(store, sent_age_hours=40.0, status="published")
    bot = AsyncMock()
    assert await review_media.clean_review_preview_media(bot, now=NOW) == 0
    bot.delete_message.assert_not_awaited()


# ---- 按需恢复 --------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_restore_resends_and_restarts_the_clock(store, monkeypatch):
    rid = await add(store, sent_age_hours=36.5)
    bot = AsyncMock()
    await review_media.clean_review_preview_media(bot, now=NOW)  # cleared
    calls = _fake_stage(monkeypatch, new_ids=(900, 901))
    monkeypatch.setattr(review, "ADMIN_IDS", {7})

    update, query = _admin_click(rid)
    await review.restore_review_preview(update, context=MagicMock(bot=bot))

    assert len(calls) == 1
    assert calls[0]["media"] == MEDIA and calls[0]["documents"] == DOCS
    row = await store.get(rid)
    assert row["preview_state"] == "present"
    assert json.loads(row["review_message_ids"]) == [900, 901]
    assert "2" in query.answer.await_args.kwargs["text"]
    markup = query.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    assert f"review_preview:{rid}" not in _callback_data(markup)

    # the restored media gets its own TTL window
    fresh = row["preview_sent_at"]
    bot2 = AsyncMock()
    assert await review_media.clean_review_preview_media(
        bot2, now=fresh + 35 * HOUR) == 0
    assert await review_media.clean_review_preview_media(
        bot2, now=fresh + 36.5 * HOUR) == 1
    assert sorted(c.kwargs["message_id"]
                  for c in bot2.delete_message.await_args_list) == [900, 901]


@pytest.mark.asyncio
async def test_repeated_clicks_never_duplicate_preview(store, monkeypatch):
    rid = await add(store, sent_age_hours=36.5)
    bot = AsyncMock()
    await review_media.clean_review_preview_media(bot, now=NOW)
    calls = _fake_stage(monkeypatch, new_ids=(900,))
    monkeypatch.setattr(review, "ADMIN_IDS", {7})

    update, query = _admin_click(rid)
    await review.restore_review_preview(update, context=MagicMock(bot=bot))
    await review.restore_review_preview(update, context=MagicMock(bot=bot))
    assert len(calls) == 1  # second click answered, media untouched

    # an in-flight claim is not restartable either
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE pending_reviews SET preview_state='regenerating' WHERE id=?",
            (rid,))
    await review.restore_review_preview(update, context=MagicMock(bot=bot))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_failed_resend_tracks_what_was_sent_and_blocks_duplicates(
        store, monkeypatch):
    rid = await add(store, sent_age_hours=36.5)
    bot = AsyncMock()
    await review_media.clean_review_preview_media(bot, now=NOW)
    calls = _fake_stage(monkeypatch, new_ids=(900,), error=TimedOut())
    monkeypatch.setattr(review, "ADMIN_IDS", {7})

    update, query = _admin_click(rid)
    await review.restore_review_preview(update, context=MagicMock(bot=bot))
    row = await store.get(rid)
    assert row["preview_state"] == "present"  # the sent media is tracked
    assert json.loads(row["review_message_ids"]) == [900]
    assert row["preview_sent_at"] is not None
    assert "show_alert" in query.answer.await_args.kwargs

    await review.restore_review_preview(update, context=MagicMock(bot=bot))
    assert len(calls) == 1  # no duplicate send from a second click


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["present", "partial", "restricted", "uncertain"])
async def test_non_cleared_states_refuse_restore(store, monkeypatch, state):
    rid = await add(store, sent_age_hours=10.0, state=state)
    calls = _fake_stage(monkeypatch)
    monkeypatch.setattr(review, "ADMIN_IDS", {7})
    update, query = _admin_click(rid)
    await review.restore_review_preview(update, context=MagicMock(bot=AsyncMock()))
    assert calls == []
    assert (await store.get(rid))["preview_state"] == state
    assert "show_alert" not in query.answer.await_args.kwargs or state != "present"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["published", "rejected", "expired", "superseded"])
async def test_terminal_status_refuses_restore(store, monkeypatch, status):
    rid = await add(store, sent_age_hours=36.5, status=status)
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE pending_reviews SET preview_state='cleared' WHERE id=?", (rid,))
    calls = _fake_stage(monkeypatch)
    monkeypatch.setattr(review, "ADMIN_IDS", {7})
    update, query = _admin_click(rid)
    await review.restore_review_preview(update, context=MagicMock(bot=AsyncMock()))
    assert calls == []
    assert "无法恢复预览" in query.answer.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_non_admin_cannot_restore(store, monkeypatch):
    rid = await add(store, sent_age_hours=36.5)
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE pending_reviews SET preview_state='cleared' WHERE id=?", (rid,))
    calls = _fake_stage(monkeypatch)
    monkeypatch.setattr(review, "ADMIN_IDS", {7})
    update, query = _admin_click(rid, admin=1234)
    await review.restore_review_preview(update, context=MagicMock(bot=AsyncMock()))
    assert calls == []
    assert "没有审核权限" in query.answer.await_args.kwargs["text"]


# ---- 崩溃/不确定窗口 -------------------------------------------------------


@pytest.mark.asyncio
async def test_abandoned_regen_becomes_uncertain_and_is_never_resent(
        store, monkeypatch):
    rid = await add(store, sent_age_hours=36.5, state="regenerating",
                    updated_at=NOW - 10 * 60)
    assert await review_media.resolve_stale_preview_regens(now=NOW) == 1
    assert (await store.get(rid))["preview_state"] == "uncertain"
    assert await _audit_events("review.preview_regen_uncertain") == 1
    # idempotent: a later sweep settles nothing new
    assert await review_media.resolve_stale_preview_regens(now=NOW + 600) == 0

    calls = _fake_stage(monkeypatch)
    monkeypatch.setattr(review, "ADMIN_IDS", {7})
    update, query = _admin_click(rid)
    await review.restore_review_preview(update, context=MagicMock(bot=AsyncMock()))
    assert calls == []
    assert "核对" in query.answer.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_fresh_regen_claim_is_not_settled(store):
    rid = await add(store, sent_age_hours=36.5, state="regenerating",
                    updated_at=NOW - 60)
    assert await review_media.resolve_stale_preview_regens(now=NOW) == 0
    assert (await store.get(rid))["preview_state"] == "regenerating"


# ---- 删除失败分类与有界重试 ------------------------------------------------


@pytest.mark.asyncio
async def test_already_gone_messages_still_clear(store):
    rid = await add(store, sent_age_hours=36.5)
    bot = AsyncMock()
    bot.delete_message.side_effect = BadRequest("Message to delete not found")
    assert await review_media.clean_review_preview_media(bot, now=NOW) == 1
    assert (await store.get(rid))["preview_state"] == "cleared"


@pytest.mark.asyncio
async def test_restricted_deletion_is_visible_not_false_success(store):
    rid = await add(store, sent_age_hours=36.5)
    bot = AsyncMock()
    bot.delete_message.side_effect = BadRequest("Message can't be deleted")
    assert await review_media.clean_review_preview_media(bot, now=NOW) == 0
    row = await store.get(rid)
    assert row["preview_state"] == "restricted"
    assert json.loads(row["review_message_ids"]) == [101, 102]
    assert await _audit_events("review.preview_delete_restricted") == 1
    assert await review_media.clean_review_preview_media(bot, now=NOW + HOUR) == 0


@pytest.mark.asyncio
async def test_transient_failure_retries_then_completes(store):
    rid = await add(store, sent_age_hours=36.5)
    bot = AsyncMock()
    bot.delete_message.side_effect = TimedOut()
    assert await review_media.clean_review_preview_media(bot, now=NOW) == 0
    row = await store.get(rid)
    assert row["preview_state"] == "partial"
    assert row["preview_attempts"] == 1

    bot.delete_message.side_effect = None
    assert await review_media.clean_review_preview_media(bot, now=NOW + 600) == 1
    assert (await store.get(rid))["preview_state"] == "cleared"


@pytest.mark.asyncio
async def test_transient_failure_past_the_delete_deadline_gives_up(store):
    rid = await add(store, sent_age_hours=48.0)
    bot = AsyncMock()
    bot.delete_message.side_effect = TimedOut()
    assert await review_media.clean_review_preview_media(bot, now=NOW) == 0
    row = await store.get(rid)
    assert row["preview_state"] == "restricted"  # no endless retry, no lie
    assert json.loads(row["review_message_ids"]) == [101, 102]
    assert await _audit_events("review.preview_delete_restricted") == 1
    count = bot.delete_message.await_count
    assert await review_media.clean_review_preview_media(bot, now=NOW + HOUR) == 0
    assert bot.delete_message.await_count == count  # settled, never retried


# ---- 多处数据隔离 ----------------------------------------------------------


@pytest.mark.asyncio
async def test_messages_are_deleted_in_their_own_review_chat(store):
    first = await add(store, sent_age_hours=40.0, ids="[101]", control=103,
                      chat="-100123")
    second = await add(store, sent_age_hours=40.0, ids="[201]", control=203,
                       chat="-100456")
    bot = AsyncMock()
    assert await review_media.clean_review_preview_media(bot, now=NOW) == 2
    calls = {(c.kwargs["chat_id"], c.kwargs["message_id"])
             for c in bot.delete_message.await_args_list}
    assert calls == {(-100123, 101), (-100456, 201)}
    assert (await store.get(first))["preview_state"] == "cleared"
    assert (await store.get(second))["preview_state"] == "cleared"


@pytest.mark.asyncio
async def test_cleanup_batch_is_bounded(store, monkeypatch):
    for _ in range(3):
        await add(store, sent_age_hours=40.0)
    monkeypatch.setattr(review_media, "REVIEW_MEDIA_CLEANUP_BATCH_SIZE", 2)
    bot = AsyncMock()
    assert await review_media.clean_review_preview_media(bot, now=NOW) == 2
    assert bot.delete_message.await_count == 4


@pytest.mark.asyncio
async def test_control_message_id_is_never_deleted(store):
    rid = await add(store, sent_age_hours=40.0, ids="[101, 102, 103]", control=103)
    bot = AsyncMock()
    await review_media.clean_review_preview_media(bot, now=NOW)
    assert sorted(c.kwargs["message_id"]
                  for c in bot.delete_message.await_args_list) == [101, 102]
    row = await store.get(rid)
    assert row["control_message_id"] == 103
    assert row["preview_state"] == "cleared"


@pytest.mark.asyncio
async def test_markup_failure_does_not_block_cleanup(store):
    rid = await add(store, sent_age_hours=40.0)
    bot = AsyncMock()
    bot.edit_message_reply_markup.side_effect = TimedOut()
    assert await review_media.clean_review_preview_media(bot, now=NOW) == 1
    assert (await store.get(rid))["preview_state"] == "cleared"


@pytest.mark.asyncio
async def test_regen_claims_are_sequential(store):
    rid = await add(store, sent_age_hours=36.5, state="cleared")
    first, second = await asyncio.gather(
        store.begin_preview_regen(rid), store.begin_preview_regen(rid))
    assert sorted([first, second]) == [False, True]


@pytest.mark.asyncio
async def test_production_callback_dispatch_reaches_restore(monkeypatch):
    """The button must be routed by the same wiring production registers."""
    import re
    from handlers import callback_handlers

    assert any(re.match(pattern, f"review_preview:{1}")
               for pattern in callback_handlers.GLOBAL_CALLBACK_PATTERNS)
    seen = []

    async def fake_handler(update, context):
        seen.append(update.callback_query.data)

    monkeypatch.setattr(review, "restore_review_preview", fake_handler)
    query = AsyncMock()
    query.data = "review_preview:7"
    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=7)
    await callback_handlers.handle_callback_query(update, MagicMock())
    assert seen == ["review_preview:7"]
