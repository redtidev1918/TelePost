"""底部菜单文本必须只产生一条回复。

回归背景（生产实测）：
用户点击 ReplyKeyboard 的「ℹ️ 关于」，收到「关于」卡片后**又**收到一条
「🤔 这条消息我没看懂」。

原因是 PTB 的分组语义：``Application.process_update`` 会顺序执行**每一个**
group，只有处理器**抛出** ``ApplicationHandlerStop`` 才会中断后续 group
（``telegram/ext/_application.py`` 中 ``except ApplicationHandlerStop: break``），
仅仅 ``return`` 不会。``handle_menu_shortcuts``(group=998) 当时只 return，
于是 group=1000 的 ``catch_all`` 又兜底回复了一次。

本文件用真实的 ``Application`` + 真实 group 编号复现该链路，并锁死两类不变量：
1. 菜单按钮文本只回一条，且不是兜底文案；
2. 真正的未知文本仍然能拿到兜底指引（不要为了修 bug 把兜底关掉）。
"""
import inspect
from pathlib import Path

import pytest
from telegram import Chat, Message, MessageEntity, Update, User
from telegram.ext import Application, MessageHandler, filters

from handlers.command_handlers import catch_all, handle_menu_shortcuts

PROJECT_ROOT = Path(__file__).parents[1]


class _RecordingBot:
    """记录所有出站消息的最小 Bot 替身。"""

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

    def _msg(self):
        from unittest.mock import MagicMock

        self._mid += 1
        m = MagicMock()
        m.message_id = self._mid
        return m

    async def send_message(self, chat_id, text, **kw):
        self.outbox.append(text)
        return self._msg()

    async def send_photo(self, chat_id, **kw):
        self.outbox.append("<photo>")
        return self._msg()

    async def edit_message_text(self, *a, **kw):
        return self._msg()


def _text_update(text, user_id=42):
    user = User(id=user_id, is_bot=False, first_name="T", username="tester")
    chat = Chat(id=user_id, type="private")
    kw = dict(message_id=1, date=0, chat=chat, from_user=user, text=text)
    if text.startswith("/"):
        kw["entities"] = (
            MessageEntity(type="bot_command", offset=0, length=len(text.split()[0])),
        )
    return Update(update_id=1, message=Message(**kw))


def _build_app(bot):
    """按 main.py 的生产分组注册：菜单 998，兜底 1000。"""
    app = Application.builder().bot(bot).build()
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_shortcuts), group=998
    )
    app.add_handler(MessageHandler(filters.ALL, catch_all), group=1000)
    return app


async def _process(app, bot, update):
    update.set_bot(bot)
    if update.message:
        update.message.set_bot(bot)
    await app.process_update(update)


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["ℹ️ 关于", "🔍 搜索"])
async def test_menu_button_replies_exactly_once(monkeypatch, label):
    """点击菜单按钮只回一条，且不是兜底文案。"""
    monkeypatch.setattr(
        "handlers.command_handlers.MessageFormatter.about_message",
        staticmethod(lambda: "ℹ️ 关于投稿机器人"),
    )
    bot = _RecordingBot()
    app = _build_app(bot)
    await app.initialize()
    try:
        await _process(app, bot, _text_update(label))
    finally:
        await app.shutdown()

    assert len(bot.outbox) == 1, f"「{label}」应只回一条，实际 {len(bot.outbox)} 条: {bot.outbox}"
    assert "我没看懂" not in bot.outbox[0], f"「{label}」不应触发 catch_all 兜底: {bot.outbox}"


@pytest.mark.asyncio
async def test_unrelated_text_still_gets_fallback(monkeypatch):
    """未知文本仍然拿到兜底指引——修复不能把兜底整体关掉。"""
    bot = _RecordingBot()
    app = _build_app(bot)
    await app.initialize()
    try:
        await _process(app, bot, _text_update("今天天气不错", user_id=99))
    finally:
        await app.shutdown()

    assert len(bot.outbox) == 1, f"未知文本应回一条兜底，实际: {bot.outbox}"
    assert "我没看懂" in bot.outbox[0]


def test_no_handler_returns_application_handler_stop():
    """ApplicationHandlerStop 只能 raise，不能 return。

    return 一个异常实例不会中断 group（PTB 只看是否抛出），
    历史上 main.py 三处都误写成 return，导致「黑名单/会话超时/会话外媒体」
    的提示后面还会再跟一条兜底回复。
    """
    for rel in ("main.py", "handlers/command_handlers.py"):
        source = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
        assert "return ApplicationHandlerStop" not in source, f"{rel} 中出现了无效的 return ApplicationHandlerStop"


def test_menu_shortcuts_stops_chain_after_handling():
    """handle_menu_shortcuts 命中后必须抛出 ApplicationHandlerStop。"""
    source = inspect.getsource(handle_menu_shortcuts)
    assert "raise ApplicationHandlerStop" in source
