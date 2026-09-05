from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers.publish import handle_document_publish, handle_media_publish
from handlers import publish


@pytest.mark.asyncio
async def test_single_reply_document_uses_send_document():
    bot = AsyncMock()
    bot.send_document.return_value = SimpleNamespace(message_id=20)
    context = SimpleNamespace(bot=bot)

    sent = await handle_document_publish(
        context,
        ["document:FILE_ID:novel.txt"],
        caption=None,
        reply_to_message_id=10,
    )

    assert sent.message_id == 20
    bot.send_media_group.assert_not_awaited()
    assert bot.send_document.await_args.kwargs["reply_to_message_id"] == 10


@pytest.mark.asyncio
async def test_animations_are_sent_standalone_as_reply_chain():
    bot = AsyncMock()
    bot.send_animation.side_effect = [
        SimpleNamespace(message_id=30),
        SimpleNamespace(message_id=31),
    ]
    context = SimpleNamespace(bot=bot)

    main, message_ids = await handle_media_publish(
        context,
        ["animation:GIF_1", "animation:GIF_2"],
        "caption",
        True,
    )

    assert main.message_id == 30
    assert message_ids == [30, 31]
    bot.send_media_group.assert_not_awaited()
    calls = [call.kwargs for call in bot.send_animation.await_args_list]
    assert calls[0]["reply_to_message_id"] is None
    assert calls[1]["reply_to_message_id"] == 30


@pytest.mark.asyncio
async def test_last_single_photo_after_full_album_is_sent_as_reply():
    bot = AsyncMock()
    bot.send_media_group.return_value = [
        SimpleNamespace(message_id=40 + index) for index in range(10)
    ]
    bot.send_photo.return_value = SimpleNamespace(message_id=50)
    context = SimpleNamespace(bot=bot)

    main, message_ids = await handle_media_publish(
        context,
        [f"photo:PHOTO_{index}" for index in range(11)],
        "caption",
        False,
    )

    assert main.message_id == 40
    assert message_ids == list(range(40, 51))
    bot.send_media_group.assert_awaited_once()
    assert bot.send_photo.await_args.kwargs["reply_to_message_id"] == 49


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 2])
async def test_chat_delivery_applies_spoiler(count):
    bot = AsyncMock()
    bot.send_photo.return_value = SimpleNamespace(message_id=1)
    bot.send_media_group.return_value = [SimpleNamespace(message_id=i + 1) for i in range(count)]
    items = publish._normalize_chat_items([f"photo:f{i}" for i in range(count)], [])
    await publish.deliver_items_to_chat(bot, "@channel", items, caption="c", spoiler=True)
    if count == 1:
        assert bot.send_photo.await_args.kwargs["has_spoiler"] is True
    else:
        assert all(m.has_spoiler for m in bot.send_media_group.await_args.kwargs["media"])
