"""频道多相册投递的回复层级测试（chain vs post）。"""
import asyncio

import pytest

from handlers import publish


class _Msg:
    def __init__(self, message_id):
        self.message_id = message_id


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
