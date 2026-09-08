"""频道多相册投递的回复层级测试（chain vs post）。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import TelegramError

from handlers import publish


class _Msg:
    def __init__(self, message_id, chat_id=None):
        self.message_id = message_id
        if chat_id is not None:
            self.chat = SimpleNamespace(id=chat_id)


def _make_album(counter, reply_to_log):
    async def send_album(media_group, reply_to):
        reply_to_log.append(reply_to)
        messages = []
        for _ in media_group:
            counter[0] += 1
            messages.append(_Msg(counter[0]))
        return messages

    return send_album


async def _send_one(item, cap, reply_to):
    return _Msg(9999)


@pytest.mark.asyncio
async def test_chain_mode_replies_to_previous_batch():
    items = [{"kind": "photo", "file_id": f"f{i}", "filename": f"{i}.jpg"} for i in range(25)]
    counter, reply_log = [0], []
    sent, main = await publish._run_item_batches(
        items,
        caption="c",
        album_size=10,
        send_one=_send_one,
        send_album=_make_album(counter, reply_log),
        reply_mode="chain",
    )
    # 3 批（10/10/5）：第一批无回复，后续每批回复上一批的最后一条消息。
    assert reply_log == [None, 10, 20], reply_log
    assert main.message_id == 1
    assert len(sent) == 25


@pytest.mark.asyncio
async def test_post_mode_replies_to_main_post():
    items = [{"kind": "photo", "file_id": f"f{i}", "filename": f"{i}.jpg"} for i in range(25)]
    counter, reply_log = [0], []
    sent, main = await publish._run_item_batches(
        items,
        caption="c",
        album_size=10,
        send_one=_send_one,
        send_album=_make_album(counter, reply_log),
        reply_mode="post",
    )
    # 3 批（10/10/5）：第一批无回复（主贴），后续批次都回复主贴第一条（id=1）。
    assert reply_log == [None, 1, 1], reply_log
    assert main.message_id == 1
    assert len(sent) == 25


@pytest.mark.asyncio
async def test_post_mode_with_external_anchor():
    items = [{"kind": "photo", "file_id": f"f{i}", "filename": f"{i}.jpg"} for i in range(15)]
    counter, reply_log = [0], []
    sent, main = await publish._run_item_batches(
        items,
        caption="c",
        album_size=10,
        send_one=_send_one,
        send_album=_make_album(counter, reply_log),
        reply_mode="post",
        anchor_id=77,
    )
    # 有外部锚点时，post 模式所有批次都回复该锚点。
    assert reply_log == [77, 77], reply_log
    assert len(sent) == 15


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,anchor,expected", [
    ("chain", None, [None, 1, 2, 3]),
    ("post", None, [None, 1, 1, 1]),
    ("post", 77, [77, 77, 77, 77]),
])
async def test_album_fallback_preserves_reply_layout(mode, anchor, expected):
    replies = []
    captions = []

    async def send_album(*args):
        raise TelegramError("album rejected")

    async def send_one(item, cap, reply_to):
        replies.append(reply_to)
        captions.append(cap)
        return _Msg(len(replies))

    await publish._run_item_batches(
        [{"kind": "photo", "file_id": str(i)} for i in range(4)],
        caption="caption", album_size=3, send_one=send_one,
        send_album=send_album, reply_mode=mode, anchor_id=anchor,
    )
    assert replies == expected
    assert captions == ["caption", None, None, None]


@pytest.mark.asyncio
async def test_discussion_mode_keeps_cover_in_channel_and_replies_with_rest(monkeypatch):
    bot = AsyncMock()
    bot.get_chat.return_value = SimpleNamespace(id=-1001, linked_chat_id=-1002)
    bot.send_photo.return_value = _Msg(10, -1001)
    bot.send_media_group.return_value = [_Msg(20, -1002), _Msg(21, -1002)]
    monkeypatch.setattr(
        publish, "_wait_for_discussion_forward", AsyncMock(return_value=(-1002, 77))
    )

    sent, main = await publish.deliver_items_to_chat(
        bot,
        -1001,
        [{"kind": "photo", "file_id": str(i)} for i in range(3)],
        caption="caption",
        timeout_kwargs={},
        reply_mode="discussion",
    )

    assert main.message_id == 10
    assert [message.message_id for message in sent] == [10, 20, 21]
    assert publish._channel_message_ids(sent, main) == [10]
    assert bot.send_photo.await_args.kwargs["chat_id"] == -1001
    assert bot.send_media_group.await_args.kwargs["chat_id"] == -1002
    assert bot.send_media_group.await_args.kwargs["reply_to_message_id"] == 77


@pytest.mark.asyncio
async def test_discussion_forward_is_correlated_before_update_queue_runs():
    publish._discussion_forwards.clear()
    message = SimpleNamespace(
        is_automatic_forward=True,
        forward_origin=SimpleNamespace(
            chat=SimpleNamespace(id=-1001), message_id=10
        ),
        chat=SimpleNamespace(id=-1002),
        message_id=77,
    )

    publish.capture_discussion_forward(SimpleNamespace(message=message))

    assert await publish._wait_for_discussion_forward(-1001, 10) == (-1002, 77)
