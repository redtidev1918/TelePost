"""
本机实地测试：用真实 ConversationHandler + 真实 DB 跑完整投稿流程。

流程：/submit → 发媒体 → 发文档 → /done_media → 编辑标签 → 确认发布。
"""
import os

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update, Message, User, Chat, PhotoSize, Document, MessageEntity
from telegram.ext import Application


class FakeBot:
    """记录所有出站消息；实现 PTB shortcut 与发布所需方法。"""

    def __init__(self):
        self.outbox = []
        self._mid = 1000
        self.username = "testbot"
        self.name = "testbot"

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    def _msg(self, kind="photo"):
        self._mid += 1
        m = MagicMock()
        m.message_id = self._mid
        m.photo = (MagicMock(file_id=f"P{self._mid}"),) if kind == "photo" else None
        m.document = MagicMock(file_id=f"D{self._mid}") if kind == "document" else None
        m.video = m.animation = m.audio = None
        return m

    async def send_message(self, chat_id, text, **kw):
        self.outbox.append(("text", text))
        return self._msg()

    async def send_photo(self, chat_id, **kw):
        self.outbox.append(("photo", None))
        return self._msg("photo")

    async def send_video(self, chat_id, **kw):
        self.outbox.append(("video", None))
        return self._msg()

    async def send_animation(self, chat_id, **kw):
        self.outbox.append(("animation", None))
        return self._msg()

    async def send_audio(self, chat_id, **kw):
        self.outbox.append(("audio", None))
        return self._msg()

    async def send_document(self, chat_id, **kw):
        self.outbox.append(("document", None))
        return self._msg("document")

    async def send_media_group(self, chat_id, media, **kw):
        self.outbox.append(("media_group", len(media)))
        return [self._msg() for _ in media]

    async def answer_callback_query(self, **kw):
        return True

    async def edit_message_text(self, *a, **kw):
        return self._msg()


def _text_update(text):
    user = User(id=42, is_bot=False, first_name="T", username="tester")
    chat = Chat(id=42, type="private")
    kw = dict(message_id=1, date=0, chat=chat, from_user=user, text=text)
    if text.startswith("/"):
        kw["entities"] = (MessageEntity(type="bot_command", offset=0, length=len(text.split()[0])),)
    msg = Message(**kw)
    return Update(update_id=1, message=msg)


def _photo_update(fid="ph1"):
    user = User(id=42, is_bot=False, first_name="T", username="tester")
    chat = Chat(id=42, type="private")
    msg = Message(message_id=2, date=0, chat=chat, from_user=user,
                  photo=(PhotoSize(file_id=fid, file_unique_id="u", width=10, height=10),))
    return Update(update_id=2, message=msg)


def _doc_update():
    user = User(id=42, is_bot=False, first_name="T", username="tester")
    chat = Chat(id=42, type="private")
    msg = Message(message_id=3, date=0, chat=chat, from_user=user,
                  document=Document(file_id="d1", file_unique_id="du", file_name="a.pdf",
                                    mime_type="application/pdf"))
    return Update(update_id=3, message=msg)


def _callback_update(data):
    user = User(id=42, is_bot=False, first_name="T", username="tester")
    chat = Chat(id=42, type="private")
    msg = Message(message_id=99, date=0, chat=chat, from_user=user, text="预览")
    from telegram import CallbackQuery
    cq = CallbackQuery(id="cq1", from_user=user, chat_instance="ci", message=msg, data=data)
    return Update(update_id=4, callback_query=cq)


@pytest.mark.asyncio
async def test_full_submission_conversation(monkeypatch, tmp_path):
    """/submit → 媒体 → 文档 → /done_media → 编辑标签 → 发布 全链路。"""
    monkeypatch.chdir(tmp_path)
    from database import db_manager
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "submissions.db"))
    await db_manager.init_db()

    from handlers.conversation import build_submission_conversation
    from handlers import publish
    import handlers.mode_selection as ms

    bot = FakeBot()
    app = Application.builder().bot(bot).build()
    app.add_handler(build_submission_conversation())
    await app.initialize()

    monkeypatch.setattr(ms, "is_blacklisted", lambda uid: False)
    # 发布落地不依赖搜索索引/统计，仅验证状态流转与消息
    monkeypatch.setattr(publish, "save_published_post", AsyncMock())

    def _inject(u):
        u.set_bot(bot)
        if u.message:
            u.message.set_bot(bot)
        if u.callback_query:
            u.callback_query.set_bot(bot)
            if u.callback_query.message:
                u.callback_query.message.set_bot(bot)

    async def process(u):
        _inject(u)
        await app.process_update(u)

    # 1) /submit
    await process(_text_update("/submit"))
    assert any("直接上传" in t for k, t in bot.outbox if k == "text"), "应发上传提示"

    # 2) 发媒体
    await process(_photo_update())
    assert any("已接收媒体" in t for k, t in bot.outbox if k == "text")

    # 3) 发文档
    await process(_doc_update())
    assert any("已接收文件" in t for k, t in bot.outbox if k == "text")

    # 4) 完成上传 → 预览
    await process(_text_update("/done_media"))
    assert any("发布预览" in t for k, t in bot.outbox if k == "text")

    # 5) 编辑标签（回调 → EDIT → 文本 → 回 PREVIEW）
    await process(_callback_update("edit_tag"))
    await process(_text_update("#新标签, 测试"))
    assert any("标签已更新" in t for k, t in bot.outbox if k == "text")

    # 6) 确认发布
    await process(_callback_update("publish"))
    kinds = [k for k, _ in bot.outbox]
    assert "photo" in kinds and "document" in kinds, f"发布应包含媒体与文档: {kinds}"

    await app.shutdown()
