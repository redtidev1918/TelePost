"""Real SQLite expiry checkpoints, restart boundaries and external failures."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest, TimedOut

from database import db_manager
from handlers import review
from telepost.storage.sqlite.reviews import ReviewRepository
from telepost.application.submitter_notify import flush_submitter_notifications

NOW = 1_800_000_000.0


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'expiry.db'))
    monkeypatch.setattr(review, 'PENDING_REVIEW_RETENTION_DAYS', 14)
    monkeypatch.setattr(review, '_expiry_lock', asyncio.Lock())
    await db_manager.init_db()
    return ReviewRepository()


async def add(*, age=14, source='chat', owner=7, control=103):
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "INSERT INTO pending_reviews (idempotency_key,source,user_id,submitter_user_id,"
            "title,review_chat_id,review_message_ids,control_message_id,created_at,updated_at) "
            "VALUES (?,?,?,?,?,'-100123','[101,102]',?,?,?)",
            (f'{age}:{source}:{owner}', source, 99, owner, '<title> @someone',
             control, NOW - age * 86400, NOW - age * 86400))
        return cur.lastrowid


@pytest.mark.asyncio
async def test_commit_then_restart_finishes_once_and_preserves_history(store, monkeypatch):
    old = await add()
    fresh = await add(age=13)
    await store.expire_pending(cutoff=NOW - 14*86400, now=NOW, batch_size=20,
                               error='pending review expired after 14 days')
    await db_manager.init_db()  # process restarted after transaction, before Telegram
    bot = AsyncMock()
    bot.send_message.return_value = MagicMock(message_id=500)
    assert await review.expire_stale_reviews(bot, now=NOW) == 0
    assert (await store.get(old))['status'] == 'expired'
    assert (await store.get(fresh))['status'] == 'pending'
    assert bot.delete_message.await_count == 2
    kwargs = bot.edit_message_text.await_args.kwargs
    assert kwargs['message_id'] == 103 and kwargs['reply_markup'] is None
    assert 'UTC' in kwargs['text'] and '@someone' not in kwargs['text']
    await flush_submitter_notifications(bot)
    assert bot.send_message.await_args.kwargs['chat_id'] == 7
    assert '#1' in bot.send_message.await_args.kwargs['text']
    await review.expire_stale_reviews(bot, now=NOW+1000)
    await flush_submitter_notifications(bot)
    assert bot.send_message.await_count == 1
    assert bot.edit_message_text.await_count == 1
    async with db_manager.get_db() as conn:
        assert (await (await conn.execute("SELECT count(*) FROM audit_events WHERE event='review.expired'")).fetchone())[0] == 1
    claimed, _ = await store.claim_for_publishing(old, stale_seconds=300)
    assert not claimed


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['delete_message', 'edit_message_text'])
async def test_failed_effect_retries_even_when_expiry_disabled(store, monkeypatch, method):
    rid = await add(source='api', owner=None)
    bot = AsyncMock()
    getattr(bot, method).side_effect = TimedOut()
    await review.expire_stale_reviews(bot, now=NOW)
    row = await store.get(rid)
    assert row['status'] == 'expired'
    assert row['expiry_next_attempt_at'] > NOW
    monkeypatch.setattr(review, 'PENDING_REVIEW_RETENTION_DAYS', 0)
    getattr(bot, method).side_effect = None
    await review.expire_stale_reviews(bot, now=NOW+86400)
    assert (await store.get(rid))['expiry_next_attempt_at'] is None
    await flush_submitter_notifications(bot)
    bot.send_message.assert_not_awaited()  # credential holder 99 is not submitter


@pytest.mark.asyncio
async def test_telegram_age_limit_is_visible_not_false_cleanup_success(store):
    rid = await add()
    bot = AsyncMock()
    bot.delete_message.side_effect = BadRequest("Message can't be deleted")
    await review.expire_stale_reviews(bot, now=NOW)
    progress = json.loads((await store.get(rid))['expiry_progress'])
    assert progress['blocked'] == [101, 102]
    assert '需管理员手动清理' in bot.edit_message_text.await_args.kwargs['text']
    assert '原审核媒体已清理' not in bot.edit_message_text.await_args.kwargs['text']


@pytest.mark.asyncio
async def test_missing_control_fallback_is_not_resent_after_uncertain_send(store):
    rid = await add(control=None)
    bot = AsyncMock()
    bot.send_message.side_effect = TimedOut()
    await review.expire_stale_reviews(bot, now=NOW)
    await review.expire_stale_reviews(bot, now=NOW+86400)
    assert bot.send_message.await_count == 1
    assert 'manual verification' in json.loads((await store.get(rid))['expiry_progress'])['last_error']


@pytest.mark.asyncio
async def test_legacy_expired_rows_are_not_backfilled(store):
    rid = await add()
    async with db_manager.get_db() as conn:
        await conn.execute("UPDATE pending_reviews SET status='expired' WHERE id=?", (rid,))
    await db_manager.init_db()
    bot = AsyncMock()
    await review.expire_stale_reviews(bot, now=NOW)
    bot.delete_message.assert_not_awaited()
    bot.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_after_media_effect_retries_idempotently(store, monkeypatch):
    rid = await add()
    bot = AsyncMock()
    checkpoint = ReviewRepository.checkpoint_expiry

    async def interrupted(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(ReviewRepository, 'checkpoint_expiry', interrupted)
    with pytest.raises(asyncio.CancelledError):
        await review.expire_stale_reviews(bot, now=NOW)
    assert bot.delete_message.await_count == 1
    monkeypatch.setattr(ReviewRepository, 'checkpoint_expiry', checkpoint)
    bot.delete_message.side_effect = [BadRequest('Message to delete not found'), True]
    await review.expire_stale_reviews(bot, now=NOW+300)
    assert (await store.get(rid))['expiry_next_attempt_at'] is None


@pytest.mark.asyncio
async def test_notification_failure_stays_durable_and_owned(store):
    await add(source='miniapp', owner=42)
    bot = AsyncMock()
    await review.expire_stale_reviews(bot, now=NOW)
    bot.send_message.side_effect = TimedOut()
    assert await flush_submitter_notifications(bot) == 0
    bot.send_message.side_effect = None
    bot.send_message.return_value = MagicMock(message_id=555)
    assert await flush_submitter_notifications(bot) == 1
    assert bot.send_message.await_args.kwargs['chat_id'] == 42


@pytest.mark.asyncio
async def test_terminal_pruner_preserves_incomplete_expiry(store, monkeypatch):
    rid = await add()
    await store.expire_pending(cutoff=NOW-14*86400, now=NOW, batch_size=20,
                               error='pending review expired after 14 days')
    async with db_manager.get_db() as conn:
        await conn.execute('UPDATE pending_reviews SET updated_at=1 WHERE id=?', (rid,))
    await db_manager.cleanup_old_data()
    assert await store.get(rid) is not None
    bot = AsyncMock()
    await review.expire_stale_reviews(bot, now=NOW)
    await db_manager.cleanup_old_data()
    assert await store.get(rid) is None
