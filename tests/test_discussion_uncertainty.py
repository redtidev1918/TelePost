"""Response loss must never turn unrelated forwards into publication evidence."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest, TimedOut

from telepost.domain.delivery import (
    DeliveredMessage, DeliveryRequest, DeliveryResult, MediaItem, MediaKind, ReplyMode,
)
from telepost.telegram.delivery.discussion import DiscussionStrategy
from telepost.telegram.delivery.gateway import PTBTelegramDeliveryGateway
from telepost.telegram.delivery.registry import ForwardRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize("landed", [False, True])
async def test_response_loss_never_resends_or_deletes_even_with_unrelated_forward(landed):
    bot = AsyncMock()
    registry = ForwardRegistry()
    registry.capture(source_chat_id=-1001, source_message_id=999,
                     discussion_chat_id=-1002, discussion_message_id=888)
    actual_posts = []

    async def send(**kwargs):
        if landed:
            actual_posts.append(1000)
        raise TimedOut()

    bot.send_animation.side_effect = send
    chain = PTBTelegramDeliveryGateway(bot)
    scanner = AsyncMock(return_value=(999, -1002, 888))
    strategy = DiscussionStrategy(chain, bot, registry=registry, scanner=scanner,
                                  retry_sleep=0)
    gateway = PTBTelegramDeliveryGateway(bot, discussion=strategy)
    # Two animations select the discussion path without album conversion.
    request = DeliveryRequest(-1001, [MediaItem.file_id('animation', 'a')] * 2,
                              reply_mode=ReplyMode.DISCUSSION)
    result = await gateway.deliver(request)
    assert result.is_uncertain and not result.retryable
    assert not result.known_messages
    assert actual_posts == ([1000] if landed else [])
    assert bot.send_animation.await_count == 1
    scanner.assert_not_awaited()
    bot.delete_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_rejection_preserves_reason_and_is_not_response_loss():
    bot = AsyncMock()
    bot.send_animation.side_effect = BadRequest('wrong file identifier')
    chain = PTBTelegramDeliveryGateway(bot)
    strategy = DiscussionStrategy(chain, bot, retry_sleep=0)
    gateway = PTBTelegramDeliveryGateway(bot, discussion=strategy)
    result = await gateway.deliver(DeliveryRequest(
        -1001, [MediaItem.file_id('animation', 'a')] * 2,
        reply_mode=ReplyMode.DISCUSSION))
    assert not result.ok and not result.is_uncertain
    assert 'wrong file identifier' in result.reason
    assert bot.send_animation.await_count == 2
    bot.delete_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_registry_requires_exact_identity_and_expires_entries():
    registry = ForwardRegistry()
    registry.capture(source_chat_id=-1001, source_message_id=999,
                     discussion_chat_id=-1002, discussion_message_id=888)
    assert await registry.scan_recent(-1001) is None
    assert await registry.scan_recent(-1001, message_id=1000,
                                      deadline_seconds=.001, interval=.001) is None
    registry._recent[0] = (*registry._recent[0][:4], registry._now() - 3600)
    assert await registry.scan_recent(-1001, message_id=999,
                                      deadline_seconds=.001, interval=.001) is None


@pytest.mark.asyncio
async def test_uncertain_legacy_delivery_is_durable_and_blocks_repeat(tmp_path, monkeypatch):
    from database import db_manager
    from handlers import publish
    from telepost.application.publication import PublicationService, PublishCommand
    from telepost.storage.sqlite.ledger import DeliveryLedgerRepository

    monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'ledger.db'))
    await db_manager.init_db()
    bot = AsyncMock()
    bot.get_chat.return_value = SimpleNamespace(id=-1001, linked_chat_id=-1002)
    bot.send_animation.side_effect = TimedOut()
    service = PublicationService(delivery=publish._LegacyDeliveryPort(bot))
    command = PublishCommand(chat_id=-1001,
                             items=[MediaItem.file_id('animation', 'a')],
                             caption_data={}, user_id=1,
                             idempotency_key='review:208:synthetic',
                             reply_mode=ReplyMode.DISCUSSION)
    first = await service.publish(command)
    assert first.uncertain
    ledger = await DeliveryLedgerRepository().find_by_key(command.idempotency_key)
    assert ledger.status == 'uncertain' and ledger.message_id is None
    # A new service instance represents replay after process restart.
    replay = await PublicationService(delivery=publish._LegacyDeliveryPort(bot)).publish(command)
    assert replay.uncertain and not replay.retryable
    assert bot.send_animation.await_count == 1
    bot.delete_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmed_partial_root_is_not_reposted():
    bot = AsyncMock()
    message = DeliveredMessage(-1001, 42, MediaKind.PHOTO)
    chain = SimpleNamespace(deliver=AsyncMock(return_value=DeliveryResult.failed(
        'second batch rejected', known_messages=[message])))
    strategy = DiscussionStrategy(chain, bot, retry_sleep=0)
    gateway = PTBTelegramDeliveryGateway(bot, discussion=strategy)
    result = await gateway.deliver(DeliveryRequest(
        -1001, [MediaItem.file_id('photo', 'a')] * 2,
        reply_mode=ReplyMode.DISCUSSION))
    assert result.is_uncertain and result.known_messages == [message]
    chain.deliver.assert_awaited_once()
    bot.delete_message.assert_not_awaited()
