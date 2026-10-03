"""搜索关闭时，所有搜索入口都要给出「尚未启用」提示，而不是静默或报错。

背景：``SEARCH_ENABLED=false``（多 bot 下是 ``BOT{n}_SEARCH_ENABLED=false``）
时搜索引擎根本不会初始化，但 ``/search`` 和底部菜单「🔍 搜索」此前仍会照常
进入流程：命令会打到 ``get_search_engine()``，菜单则会弹出一组点了也没用的
搜索选项键盘。用户看到的是卡住或空结果，而不是「这个功能没开」。

本文件锁死两件事：
1. 关闭时，命令 / 菜单 / 内联搜索按钮都只回一条「尚未启用」说明；
2. 不牵连不依赖搜索引擎的入口——「我的投稿 / 标签云 / 热门内容」照旧可用，
   且搜索输入模式不会被无限期留在 user_data 里。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Chat, Message, MessageEntity, Update, User
from telegram.ext import Application, ApplicationHandlerStop, MessageHandler, filters

from handlers import command_handlers
from handlers.callback_handlers import handle_search_action, handle_tag_search
from handlers.search_handlers import (
    SEARCH_DISABLED_MESSAGE,
    search_posts,
    handle_search_input,
)


def _message_update(text, user_id=42):
    user = User(id=user_id, is_bot=False, first_name="T", username="tester")
    chat = Chat(id=user_id, type="private")
    kw = dict(message_id=1, date=0, chat=chat, from_user=user, text=text)
    if text.startswith("/"):
        kw["entities"] = (
            MessageEntity(type="bot_command", offset=0, length=len(text.split()[0])),
        )
    return Update(update_id=1, message=Message(**kw))


def _command_update(args=None):
    """/search 命令：message 用 AsyncMock，避免真的走 Bot API。"""
    update = MagicMock()
    update.callback_query = None
    update.message = AsyncMock()
    update.message.text = "/search" + (" " + " ".join(args) if args else "")
    update.message.chat.type = "private"
    update.channel_post = None
    update.edited_channel_post = None
    update.effective_user = MagicMock(id=42)
    return update


def _callback_update(data):
    query = AsyncMock()
    query.data = data
    query.message = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.message = None
    return update, query


class _RecordingBot:
    """记录出站文本的最小 Bot 替身（与菜单回归测试同一套路）。"""

    def __init__(self):
        self.outbox = []
        self._mid = 1000
        self.id = 777
        self.username = "testbot"
        self.name = "testbot"

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def send_message(self, chat_id, text, **kw):
        self.outbox.append(text)
        return MagicMock(message_id=self._mid)

    async def edit_message_text(self, *a, **kw):
        return MagicMock(message_id=self._mid)


def _menu_app(bot):
    app = Application.builder().bot(bot).build()
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, command_handlers.handle_menu_shortcuts),
        group=998,
    )
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [None, ["python"]])
async def test_search_command_when_disabled(monkeypatch, args):
    """/search（带不带关键词都一样）只回一条未启用提示，且不碰搜索引擎。"""
    monkeypatch.setattr("config.settings.SEARCH_ENABLED", False)
    engine_calls = []
    monkeypatch.setattr(
        "handlers.search_handlers.get_search_engine",
        lambda *a, **kw: engine_calls.append(1),
    )

    update = _command_update(args)
    await search_posts(update, MagicMock(args=args or []))

    update.message.reply_text.assert_awaited_once()
    assert update.message.reply_text.await_args.args[0] == SEARCH_DISABLED_MESSAGE
    assert "尚未启用" in update.message.reply_text.await_args.args[0]
    assert engine_calls == [], "关闭时不该再去取搜索引擎实例"


@pytest.mark.asyncio
async def test_search_command_when_enabled_still_answers_help(monkeypatch):
    """开启时保持原行为：无参数仍给搜索帮助，不是未启用提示。"""
    monkeypatch.setattr("config.settings.SEARCH_ENABLED", True)

    update = _command_update(None)
    await search_posts(update, MagicMock(args=[]))

    sent = update.message.reply_text.await_args.args[0]
    assert "尚未启用" not in sent
    assert "搜索帮助" in sent


@pytest.mark.asyncio
async def test_menu_search_button_when_disabled(monkeypatch):
    """底部菜单「🔍 搜索」关闭时只回一条未启用提示（不落到 catch_all 兜底）。"""
    monkeypatch.setattr("config.settings.SEARCH_ENABLED", False)
    bot = _RecordingBot()
    app = _menu_app(bot)
    await app.initialize()
    try:
        update = _message_update("🔍 搜索")
        update.set_bot(bot)
        update.message.set_bot(bot)
        await app.process_update(update)
    finally:
        await app.shutdown()

    assert len(bot.outbox) == 1, f"应只回一条，实际 {bot.outbox}"
    assert "尚未启用" in bot.outbox[0]
    assert "我没看懂" not in bot.outbox[0]


@pytest.mark.asyncio
async def test_menu_search_button_when_enabled(monkeypatch):
    """开启时菜单照旧引导输入关键词。"""
    monkeypatch.setattr("config.settings.SEARCH_ENABLED", True)
    bot = _RecordingBot()
    app = _menu_app(bot)
    await app.initialize()
    try:
        update = _message_update("🔍 搜索")
        update.set_bot(bot)
        update.message.set_bot(bot)
        await app.process_update(update)
    finally:
        await app.shutdown()

    assert len(bot.outbox) == 1
    assert "请输入搜索关键词" in bot.outbox[0]
    assert "尚未启用" not in bot.outbox[0]


@pytest.mark.asyncio
async def test_menu_search_button_raises_handler_stop_when_disabled(monkeypatch):
    """提示之后仍然要中断后续 group，否则用户会额外收到兜底文案。"""
    monkeypatch.setattr("config.settings.SEARCH_ENABLED", False)
    bot = _RecordingBot()
    update = _message_update("🔍 搜索")
    update.set_bot(bot)
    update.message.set_bot(bot)
    context = MagicMock()
    context.user_data = {}

    with pytest.raises(ApplicationHandlerStop):
        await command_handlers.handle_menu_shortcuts(update, context)

    assert bot.outbox == [SEARCH_DISABLED_MESSAGE]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["search_fulltext", "search_tag", "search_time"])
async def test_inline_search_actions_when_disabled(monkeypatch, data):
    """内联搜索按钮关闭时原地改写为未启用提示。"""
    monkeypatch.setattr("config.settings.SEARCH_ENABLED", False)
    update, query = _callback_update(data)

    await handle_search_action(update, MagicMock(user_data={}))

    query.edit_message_text.assert_awaited_once()
    assert query.edit_message_text.await_args.args[0] == SEARCH_DISABLED_MESSAGE


@pytest.mark.asyncio
async def test_inline_myposts_unaffected_when_disabled(monkeypatch):
    """「我的投稿」走数据库，搜索关闭时也必须照常可用。"""
    monkeypatch.setattr("config.settings.SEARCH_ENABLED", False)
    called = {}

    async def _fake_myposts(update, context):
        called["ok"] = True

    # handle_search_action 内部是函数级 import，patch 源头即可。
    monkeypatch.setattr("handlers.search_handlers.get_my_posts", _fake_myposts)

    update, query = _callback_update("search_myposts")
    await handle_search_action(update, MagicMock(user_data={}))

    assert called.get("ok"), "我的投稿不应被搜索开关拦下"
    query.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_tag_search_callback_when_disabled(monkeypatch):
    """标签搜索回调关闭时给提示，不去取搜索引擎。"""
    monkeypatch.setattr("config.settings.SEARCH_ENABLED", False)
    update, query = _callback_update("tag_search_python")

    await handle_tag_search(update, MagicMock(user_data={}))

    query.edit_message_text.assert_awaited_once()
    assert query.edit_message_text.await_args.args[0] == SEARCH_DISABLED_MESSAGE


@pytest.mark.asyncio
async def test_search_input_exits_mode_when_disabled(monkeypatch):
    """开关在用户进入搜索模式后才关闭时：提示并退出搜索模式，避免卡住。"""
    monkeypatch.setattr("config.settings.SEARCH_ENABLED", False)
    update = MagicMock()
    update.callback_query = None
    update.message = AsyncMock()
    update.message.text = "python"
    update.message.chat.type = "private"
    update.channel_post = None
    update.edited_channel_post = None
    context = MagicMock()
    context.user_data = {"search_mode": "fulltext"}

    await handle_search_input(update, context)

    assert context.user_data["search_mode"] is None, "必须退出搜索输入模式"
    update.message.reply_text.assert_awaited_once()
    assert update.message.reply_text.await_args.args[0] == SEARCH_DISABLED_MESSAGE
