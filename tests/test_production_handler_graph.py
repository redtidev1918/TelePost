"""Submission-owned updates must stop at the production handler graph."""

from unittest.mock import MagicMock

import pytest
from telegram import CallbackQuery, Chat, Message, MessageEntity, PhotoSize, Update, User
from telegram.ext import Application, ConversationHandler

from main import setup_application
from models.state import STATE


class _RecordingBot:
    def __init__(self):
        self.id = 777
        self.username = self.name = "testbot"
        self.outbox = []
        self._mid = 100

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    def _message(self):
        self._mid += 1
        message = MagicMock()
        message.message_id = self._mid
        return message

    async def send_message(self, chat_id, text, **kwargs):
        self.outbox.append(text)
        return self._message()

    async def edit_message_text(self, text, **kwargs):
        self.outbox.append(text)
        return self._message()

    async def answer_callback_query(self, **kwargs):
        return True


def _message_update(text=None, *, photo=False, update_id=1):
    user = User(id=42, is_bot=False, first_name="T", username="tester")
    chat = Chat(id=42, type="private")
    kwargs = dict(message_id=update_id, date=0, chat=chat, from_user=user, text=text)
    if text and text.startswith("/"):
        kwargs["entities"] = (
            MessageEntity(type="bot_command", offset=0, length=len(text.split()[0])),
        )
    if photo:
        kwargs["photo"] = (
            PhotoSize(file_id="ph1", file_unique_id="u1", width=10, height=10),
        )
    return Update(update_id=update_id, message=Message(**kwargs))


def _callback_update(data, update_id=10):
    user = User(id=42, is_bot=False, first_name="T", username="tester")
    chat = Chat(id=42, type="private")
    message = Message(message_id=99, date=0, chat=chat, from_user=user, text="preview")
    query = CallbackQuery(
        id=f"cq{update_id}", from_user=user, chat_instance="ci", message=message, data=data
    )
    return Update(update_id=update_id, callback_query=query)


def _inject(update, bot):
    update.set_bot(bot)
    if update.message:
        update.message.set_bot(bot)
    if update.callback_query:
        update.callback_query.set_bot(bot)
        update.callback_query.message.set_bot(bot)


@pytest.fixture
async def production_app(monkeypatch, tmp_path):
    from database import db_manager
    import handlers.mode_selection as mode_selection

    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "submission.db"))
    monkeypatch.setattr(mode_selection, "is_blacklisted", lambda _user_id: False)
    await db_manager.init_db()

    bot = _RecordingBot()
    app = Application.builder().bot(bot).build()
    setup_application(app)
    await app.initialize()
    try:
        yield app, bot
    finally:
        await app.shutdown()


async def _process(app, bot, update):
    _inject(update, bot)
    await app.process_update(update)


def _submission_conversation(app):
    return next(handler for handler in app.handlers[2] if isinstance(handler, ConversationHandler))


@pytest.mark.asyncio
async def test_start_submission_is_owned_once_by_production_graph(production_app):
    app, bot = production_app

    await _process(app, bot, _message_update("📝 开始投稿"))

    assert sum("请直接上传内容" in text for text in bot.outbox) == 1
    assert not any("我没看懂" in text for text in bot.outbox)
    assert _submission_conversation(app)._conversations[(42, 42)] == STATE["UPLOAD"]


@pytest.mark.asyncio
async def test_edit_tag_is_owned_once_by_production_graph(production_app):
    app, bot = production_app
    await _process(app, bot, _message_update("📝 开始投稿"))
    await _process(app, bot, _message_update(photo=True, update_id=2))
    await _process(app, bot, _message_update("/done_media", update_id=3))
    bot.outbox.clear()

    await _process(app, bot, _callback_update("edit_tag"))

    assert sum("请发送新的标签" in text for text in bot.outbox) == 1
    assert not any("未知操作" in text for text in bot.outbox)
    assert _submission_conversation(app)._conversations[(42, 42)] == STATE["EDIT"]

    await _process(app, bot, _message_update("#新标签, 测试", update_id=11))
    assert any("标签已更新" in text for text in bot.outbox)
    assert _submission_conversation(app)._conversations[(42, 42)] == STATE["PREVIEW"]
