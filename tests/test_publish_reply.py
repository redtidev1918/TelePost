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
async def test_discussion_mode_fills_root_capacity_then_replies_with_overflow(monkeypatch):
    bot = AsyncMock()
    bot.get_chat.return_value = SimpleNamespace(id=-1001, linked_chat_id=-1002)
    bot.send_media_group.return_value = [_Msg(i, -1001) for i in range(10, 20)]
    bot.send_photo.return_value = _Msg(20, -1002)
    monkeypatch.setattr(
        publish, "_wait_for_discussion_forward", AsyncMock(return_value=(-1002, 77))
    )

    sent, main = await publish.deliver_items_to_chat(
        bot,
        -1001,
        [{"kind": "photo", "file_id": str(i)} for i in range(11)],
        caption="caption",
        timeout_kwargs={},
        reply_mode="discussion",
    )

    assert main.message_id == 10
    assert [message.message_id for message in sent] == list(range(10, 21))
    assert publish._channel_message_ids(sent, main) == list(range(10, 20))
    assert len(bot.send_media_group.await_args.kwargs["media"]) == 10
    assert bot.send_media_group.await_args.kwargs["chat_id"] == -1001
    assert bot.send_media_group.await_args.kwargs["media"][0].caption == "caption"
    assert all(m.caption is None for m in bot.send_media_group.await_args.kwargs["media"][1:])
    assert bot.send_photo.await_args.kwargs["chat_id"] == -1002
    assert bot.send_photo.await_args.kwargs["reply_to_message_id"] == 77
    assert bot.send_photo.await_args.kwargs["caption"] is None


@pytest.mark.asyncio
async def test_discussion_mode_at_capacity_needs_no_overflow_anchor(monkeypatch):
    bot = AsyncMock()
    bot.get_chat.return_value = SimpleNamespace(id=-1001, linked_chat_id=-1002)
    bot.send_media_group.return_value = [_Msg(i, -1001) for i in range(1, 11)]
    waiter = AsyncMock(side_effect=AssertionError("no overflow, no anchor wait"))
    monkeypatch.setattr(publish, "_wait_for_discussion_forward", waiter)

    sent, main = await publish.deliver_items_to_chat(
        bot, -1001,
        [{"kind": "photo", "file_id": str(i)} for i in range(10)],
        caption="caption", timeout_kwargs={}, reply_mode="discussion",
    )

    assert main.message_id == 1
    assert len(sent) == 10
    waiter.assert_not_awaited()


@pytest.mark.asyncio
async def test_discussion_mixed_media_splits_image_and_file_threads(monkeypatch):
    bot = AsyncMock()
    bot.get_chat.return_value = SimpleNamespace(id=-1001, linked_chat_id=-1002)
    bot.send_media_group.side_effect = [
        [_Msg(i, -1001) for i in range(1, 11)],   # channel visual root
        [_Msg(i, -1001) for i in range(11, 14)],  # channel file root (3 docs)
    ]
    bot.send_photo.return_value = _Msg(14, -1002)     # discussion image overflow
    waiter = AsyncMock(side_effect=[(-1002, 77), (-1002, 88)])
    monkeypatch.setattr(publish, "_wait_for_discussion_forward", waiter)

    items = (
        [{"kind": "photo", "file_id": f"p{i}"} for i in range(11)] +
        [{"kind": "document", "file_id": f"d{i}", "filename": f"{i}.txt"}
         for i in range(3)]
    )
    sent, main = await publish.deliver_items_to_chat(
        bot, -1001, items,
        caption="caption", timeout_kwargs={}, reply_mode="discussion",
    )

    channel_ids = publish._channel_message_ids(sent, main)
    assert channel_ids == list(range(1, 14))  # 10 images + 3 files on channel
    assert bot.send_photo.await_args.kwargs["chat_id"] == -1002
    assert bot.send_photo.await_args.kwargs["reply_to_message_id"] == 77
    root_docs = bot.send_media_group.await_args_list[1].kwargs
    assert root_docs["chat_id"] == -1001
    assert root_docs["reply_to_message_id"] == 10
    assert waiter.await_args_list[0].args == (-1001, 10)
    assert waiter.await_args_list[1].args == (-1001, 13)


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


@pytest.mark.asyncio
async def test_discussion_mode_raises_without_linked_chat_before_sending():
    bot = AsyncMock()
    bot.get_chat.return_value = SimpleNamespace(id=-1001, linked_chat_id=None)

    with pytest.raises(RuntimeError, match="未关联讨论组"):
        await publish.deliver_items_to_chat(
            bot,
            -1001,
            [{"kind": "photo", "file_id": str(i)} for i in range(3)],
            caption="caption",
            timeout_kwargs={},
            reply_mode="discussion",
        )

    bot.send_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_discussion_mode_keeps_channel_root_when_forward_wait_times_out(monkeypatch):
    bot = AsyncMock()
    bot.get_chat.return_value = SimpleNamespace(id=-1001, linked_chat_id=-1002)
    bot.send_media_group.return_value = [_Msg(i, -1001) for i in range(10, 20)]
    monkeypatch.setattr(
        publish,
        "_wait_for_discussion_forward",
        AsyncMock(side_effect=asyncio.TimeoutError),
    )

    # §discussion-failure-isolation: the channel root (cover) is the confirmed
    # Publication; a linked-discussion follow-up failure must NOT delete it nor
    # re-run the whole operation (which would duplicate the root).
    sent, main = await publish.deliver_items_to_chat(
        bot,
        -1001,
        [{"kind": "photo", "file_id": str(i)} for i in range(11)],
        caption="caption",
        timeout_kwargs={},
        reply_mode="discussion",
    )

    assert main.message_id == 10
    # The channel root survives with the overflow dropped.
    assert [m.message_id for m in sent] == list(range(10, 20))
    # Cover NOT deleted / no whole re-run / no overflow sent.
    assert bot.delete_message.await_args_list == []
    assert bot.send_media_group.await_count == 1
    bot.send_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_discussion_rest_album_network_error_is_uncertain_and_not_retried(monkeypatch):
    # 评论相册响应丢失可能已部分送达：标记 uncertain、不自动重试（避免重复相册）。
    from telegram.error import NetworkError

    bot = AsyncMock()
    bot.get_chat.return_value = SimpleNamespace(id=-1001, linked_chat_id=-1002)
    root = [_Msg(i, -1001) for i in range(10, 20)]
    bot.send_media_group.side_effect = [root, NetworkError("read error")]
    monkeypatch.setattr(
        publish, "_wait_for_discussion_forward", AsyncMock(return_value=(-1002, 77))
    )

    # §discussion-failure-isolation: the confirmed channel root (cover 10-19) is
    # kept even when the discussion-overflow response is lost/errors; no delete
    # of the root and no whole re-run (which would duplicate the cover).
    sent, main = await publish.deliver_items_to_chat(
        bot, -1001,
        [{"kind": "photo", "file_id": str(i)} for i in range(12)],
        caption="caption", timeout_kwargs={}, reply_mode="discussion",
    )
    assert main.message_id == 10
    assert [m.message_id for m in sent] == list(range(10, 20))
    # Cover kept; the lost rest is never deleted-blindly nor re-run as a whole.
    assert bot.delete_message.await_args_list == []
    # First call = cover group; second = the rest group that lost its response.
    assert bot.send_media_group.await_count == 2


@pytest.mark.asyncio
async def test_discussion_forward_timeout_keeps_root_and_does_not_rerun_all(monkeypatch):
    # §discussion-failure-isolation: once the channel root (cover) is confirmed,
    # a follow-up forward-wait timeout keeps the root and does NOT re-run the
    # whole operation (which would duplicate the cover).
    bot = AsyncMock()
    bot.get_chat.return_value = SimpleNamespace(id=-1001, linked_chat_id=-1002)
    bot.send_media_group.return_value = [_Msg(i, -1001) for i in range(10, 20)]
    monkeypatch.setattr(
        publish, "_wait_for_discussion_forward",
        AsyncMock(side_effect=asyncio.TimeoutError),
    )

    sent, main = await publish.deliver_items_to_chat(
        bot, -1001,
        [{"kind": "photo", "file_id": str(i)} for i in range(12)],
        caption="caption", timeout_kwargs={}, reply_mode="discussion",
    )

    assert main.message_id == 10  # first cover becomes root (kept)
    assert bot.send_media_group.await_count == 1  # no whole re-run
    assert [m.message_id for m in sent] == list(range(10, 20))
    assert bot.delete_message.await_args_list == []
    bot.send_photo.assert_not_awaited()
