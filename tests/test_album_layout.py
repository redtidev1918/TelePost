"""Album topology through the production PTB adapter, using synthetic media only."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest, TimedOut

from handlers import publish
from telepost.observability import audit


CHANNEL, DISCUSSION, ANCHOR = -100100, -100200, 800


def bot_for_layout():
    bot = AsyncMock()
    bot.get_chat.return_value = SimpleNamespace(id=CHANNEL, linked_chat_id=DISCUSSION)
    counter = 0

    def message(chat_id, group=None, reply=None):
        nonlocal counter
        counter += 1
        return SimpleNamespace(
            message_id=counter, chat=SimpleNamespace(id=chat_id),
            photo=[SimpleNamespace(file_id=f"synthetic-{counter}")],
            media_group_id=group,
            reply_to_message=SimpleNamespace(message_id=reply) if reply else None,
        )

    async def album(**kwargs):
        group = f"album-{counter + 1}"
        return [message(kwargs["chat_id"], group, kwargs["reply_to_message_id"])
                for _ in kwargs["media"]]

    async def single(**kwargs):
        return message(kwargs["chat_id"], reply=kwargs["reply_to_message_id"])

    bot.send_media_group.side_effect = album
    bot.send_photo.side_effect = single
    return bot


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 2, 5, 10, 11, 15, 21])
@pytest.mark.parametrize("mode", ["chain", "post", "discussion"])
async def test_complete_layout(monkeypatch, tmp_path, count, mode):
    from PIL import Image
    bot = bot_for_layout()
    waiter = AsyncMock(return_value=(DISCUSSION, ANCHOR))
    monkeypatch.setattr(publish, "_wait_for_discussion_forward", waiter)
    items = []
    for index in range(count):
        path = tmp_path / f"synthetic-{index}.jpg"
        Image.new("RGB", (32, 32), (index, 20, 40)).save(path)
        items.append({"kind": "photo", "path": str(path), "filename": path.name})
    sent, main = await publish.deliver_items_to_chat(
        bot, CHANNEL, items, caption="Synthetic gallery", spoiler=True,
        reply_mode=mode, album_size=10,
    )
    assert len(sent) == count and main.message_id == 1
    calls = [c.kwargs for c in bot.send_media_group.await_args_list]
    sizes = [min(10, count - i) for i in range(0, count, 10)]
    assert [len(c["media"]) for c in calls] == [n for n in sizes if n > 1]
    assert bot.send_photo.await_count == sizes.count(1)
    for i, c in enumerate(calls):
        assert c["chat_id"] == (DISCUSSION if mode == "discussion" and i else CHANNEL)
        expected_reply = None if i == 0 else ANCHOR if mode == "discussion" else 1 if mode == "post" else i * 10
        assert c["reply_to_message_id"] == expected_reply
        assert all(m.has_spoiler for m in c["media"])
        assert [m.caption for m in c["media"]] == ["Synthetic gallery" if i == 0 else None] + [None] * (len(c["media"]) - 1)
    if bot.send_photo.await_count:
        single = bot.send_photo.await_args.kwargs
        assert single["chat_id"] == (DISCUSSION if mode == "discussion" and count > 10 else CHANNEL)
        assert single["reply_to_message_id"] == (None if count == 1 else ANCHOR if mode == "discussion" else 1 if mode == "post" else 20 if count == 21 else 10)
        assert single["caption"] == ("Synthetic gallery" if count == 1 else None)
    assert [m.media_group_id for m in sent[:min(count, 10)]] == (["album-1"] * min(count, 10) if count > 1 else [None])
    bot.delete_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [BadRequest("synthetic rejection"), TimedOut()])
async def test_rejected_album_never_becomes_channel_singles(monkeypatch, failure, caplog):
    bot = bot_for_layout()
    bot.send_media_group.side_effect = failure
    with pytest.raises(type(failure)):
        await publish.deliver_items_to_chat(
            bot, CHANNEL, [{"kind": "photo", "file_id": f"synthetic-{i}"} for i in range(5)],
            caption="Synthetic", reply_mode="chain",
        )
    assert bot.send_media_group.await_count == 1
    bot.send_photo.assert_not_awaited()
    bot.delete_message.assert_not_awaited()
    if isinstance(failure, BadRequest):
        assert "fallback=disabled" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [BadRequest("synthetic rejection"), TimedOut()])
async def test_partial_publish_restart_does_not_repeat_root(monkeypatch, tmp_path, failure):
    from database import db_manager
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "ledger.db"))
    await db_manager.init_db()
    monkeypatch.setattr(publish, "CHANNEL_ID", CHANNEL)
    monkeypatch.setattr(publish, "CHANNEL_ALBUM_REPLY", "post")
    bot = bot_for_layout()
    send_album = bot.send_media_group.side_effect
    async def fail_overflow(**kwargs):
        if kwargs["reply_to_message_id"]:
            raise failure
        return await send_album(**kwargs)
    bot.send_media_group.side_effect = fail_overflow
    media = [{"type": "photo", "file_id": f"synthetic-{i}"} for i in range(15)]
    with pytest.raises(TimedOut if isinstance(failure, TimedOut) else RuntimeError):
        await publish.publish_from_file_ids(bot, media, [], user_id=1, idempotency_key="review:77:synthetic")
    # publish_from_file_ids constructs a new service/repository, using only DB progress.
    bot.send_media_group.side_effect = send_album
    if isinstance(failure, TimedOut):
        from telegram.error import NetworkError
        with pytest.raises(NetworkError, match="previous delivery is uncertain"):
            await publish.publish_from_file_ids(bot, media, [], user_id=1, idempotency_key="review:77:synthetic")
        assert bot.send_media_group.await_count == 2
    else:
        retry = await publish.publish_from_file_ids(bot, media, [], user_id=1, idempotency_key="review:77:synthetic")
        assert retry["status"] == "published"
        assert bot.send_media_group.await_count == 3
        call = bot.send_media_group.await_args.kwargs
        assert len(call["media"]) == 5 and call["reply_to_message_id"] == 1
        assert all(m.caption is None for m in call["media"])
        replay = await publish.publish_from_file_ids(bot, media, [], user_id=1, idempotency_key="review:77:synthetic")
        assert replay["reused"] and bot.send_media_group.await_count == 3
    bot.send_photo.assert_not_awaited()
    events = await audit.list_events(review_id=77)
    layouts = [audit.detail_value(e["detail"]) for e in events if e["event"] == "publish.layout"]
    assert layouts
    first = layouts[-1]
    assert first["confirmed_messages"] == 10
    assert first["layout"] == ("uncertain" if isinstance(failure, TimedOut) else "failed")
    assert first["groups"][0] == [CHANNEL, 1, "album-1", None, 10]


def test_production_topology_and_child_config(tmp_path, monkeypatch):
    import tomllib
    from pathlib import Path
    from run import build_bot_env
    monkeypatch.chdir(tmp_path)
    # Binary mode keeps the UTF-8 Chinese comments in fly.toml readable on
    # Windows, matching tests/test_fly_deploy_contract.py.
    with (Path(__file__).parents[1] / "fly.toml").open("rb") as file:
        config = tomllib.load(file)["env"]
    for index in (1, 2):
        env = build_bot_env(index, config)
        assert env["CHANNEL_ALBUM_REPLY"] == "discussion"
        assert env["MEDIA_GROUP_CAPACITY"] == "10"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [BadRequest("synthetic discussion rejection"), TimedOut(), "anchor"])
async def test_discussion_failure_records_incomplete_layout_and_keeps_root(monkeypatch, tmp_path, failure):
    from database import db_manager
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "ledger.db"))
    await db_manager.init_db()
    monkeypatch.setattr(publish, "CHANNEL_ID", CHANNEL)
    monkeypatch.setattr(publish, "CHANNEL_ALBUM_REPLY", "discussion")
    waiter = AsyncMock(return_value=(DISCUSSION, ANCHOR))
    if failure == "anchor":
        waiter.side_effect = TimeoutError()
    monkeypatch.setattr(publish, "_wait_for_discussion_forward", waiter)
    bot = bot_for_layout()
    send_album = bot.send_media_group.side_effect
    async def fail_discussion(**kwargs):
        if kwargs["chat_id"] == DISCUSSION:
            raise failure
        return await send_album(**kwargs)
    bot.send_media_group.side_effect = fail_discussion
    media = [{"type": "photo", "file_id": f"synthetic-{i}"} for i in range(15)]
    first = await publish.publish_from_file_ids(bot, media, [], user_id=1, idempotency_key="review:78:synthetic")
    replay = await publish.publish_from_file_ids(bot, media, [], user_id=1, idempotency_key="review:78:synthetic")
    assert first["status"] == "published" and replay["reused"]
    assert bot.send_media_group.await_count == (1 if failure == "anchor" else 2)
    bot.send_photo.assert_not_awaited()
    bot.delete_message.assert_not_awaited()
    layouts = [audit.detail_value(e["detail"]) for e in await audit.list_events(review_id=78)
               if e["event"] == "publish.layout"]
    assert len(layouts) == 1
    assert layouts[0]["layout"] == "incomplete"
    assert layouts[0]["confirmed_messages"] == 10
    assert layouts[0]["groups"] == [[CHANNEL, 1, "album-1", None, 10]]
