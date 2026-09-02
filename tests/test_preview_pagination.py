"""
发布前预览/快速编辑 与 分页导航 测试
"""
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from models.state import STATE


class _FakeDB:
    """模拟 get_db()：支持 async with get_db() as conn，fetchone 固定返回 row"""

    def __init__(self, row):
        self._row = row

    def __call__(self):
        return self

    async def __aenter__(self):
        cursor = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=self._row)
        self._conn = MagicMock()
        self._conn.cursor = AsyncMock(return_value=cursor)
        return self._conn

    async def __aexit__(self, *args):
        return False


def _make_update(is_callback=False):
    update = MagicMock()
    update.callback_query = None
    update.effective_message.reply_text = AsyncMock()
    update.message.reply_text = AsyncMock()
    update.effective_user.id = 42
    if is_callback:
        query = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.data = ""
        update.callback_query = query
    return update


def _submission_row():
    return {
        "image_id": '["photo:abc"]',
        "document_id": "[]",
        "tags": "#测试",
        "link": "",
        "title": "标题",
        "note": "简介",
        "spoiler": "false",
        "anonymous": "false",
        "timestamp": 1.0,
    }


class TestSubmissionPreview:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_preview_returns_publish_state(self):
        from handlers.preview_handlers import show_submission_preview

        update = _make_update()
        with patch("handlers.preview_handlers.get_db", _FakeDB(_submission_row())):
            result = await show_submission_preview(update, None)

        assert result == STATE["PUBLISH"]
        update.effective_message.reply_text.assert_called_once()
        kwargs = update.effective_message.reply_text.call_args.kwargs
        assert kwargs.get("reply_markup") is not None

    @pytest.mark.unit
    def test_missing_tags_routes_primary_button_to_tag_editor(self):
        from handlers.preview_handlers import _build_preview_keyboard

        row = _submission_row() | {"tags": ""}
        button = _build_preview_keyboard(row).inline_keyboard[0][0]
        assert button.callback_data == "edit_tag"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_preview_expired_session(self):
        from handlers.preview_handlers import show_submission_preview
        from telegram.ext import ConversationHandler

        update = _make_update()
        with patch("handlers.preview_handlers.get_db", _FakeDB(None)):
            result = await show_submission_preview(update, None)

        assert result == ConversationHandler.END

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_done_media_opens_preview(self):
        from handlers.media_handlers import done_media

        update = _make_update()
        row = _submission_row() | {"mode": "media"}
        with (
            patch("handlers.media_handlers.get_db", _FakeDB(row)),
            patch("handlers.preview_handlers.get_db", _FakeDB(row)),
        ):
            result = await done_media.__wrapped__(update, None)

        assert result == STATE["PUBLISH"]
        assert "发布预览" in update.effective_message.reply_text.await_args.args[0]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_mixed_document_only_opens_preview(self):
        from handlers.media_handlers import done_media

        update = _make_update()
        row = _submission_row() | {
            "mode": "mixed",
            "image_id": "[]",
            "document_id": '["document:file:work.pdf"]',
        }
        with (
            patch("handlers.media_handlers.get_db", _FakeDB(row)),
            patch("handlers.preview_handlers.get_db", _FakeDB(row)),
        ):
            result = await done_media.__wrapped__(update, None)

        assert result == STATE["PUBLISH"]


class TestQuickEdit:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_edit_tag_updates_and_returns_publish(self):
        from handlers.preview_handlers import handle_edit_tag

        update = _make_update()
        update.message.text = "#新标签, 另一个"

        with patch("handlers.preview_handlers.get_db", _FakeDB(_submission_row())):
            result = await handle_edit_tag(update, None)

        assert result == STATE["PUBLISH"]
        calls = [str(c) for c in update.message.reply_text.call_args_list]
        assert any("已更新" in t for t in calls)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_edit_tag_invalid_keeps_state(self):
        from handlers.preview_handlers import handle_edit_tag

        update = _make_update()
        update.message.text = "   "

        with patch("handlers.preview_handlers.get_db", _FakeDB(_submission_row())):
            result = await handle_edit_tag(update, None)

        assert result == STATE["EDIT_TAG"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_edit_note_updates_and_returns_publish(self):
        from handlers.preview_handlers import handle_edit_note

        update = _make_update()
        update.message.text = "新的简介内容"

        with patch("handlers.preview_handlers.get_db", _FakeDB(_submission_row())):
            result = await handle_edit_note(update, None)

        assert result == STATE["PUBLISH"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_edit_link_rejects_invalid_url(self):
        from handlers.preview_handlers import handle_edit_link

        update = _make_update()
        update.message.text = "example.com"
        result = await handle_edit_link(update, None)
        assert result == STATE["EDIT_LINK"]


class TestEditButtons:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_edit_field_callback_routes_states(self):
        from handlers.preview_handlers import handle_edit_field_callback

        for data, expected in [
            ("edit_tag", STATE["EDIT_TAG"]),
            ("edit_title", STATE["EDIT_TITLE"]),
            ("edit_note", STATE["EDIT_NOTE"]),
            ("edit_link", STATE["EDIT_LINK"]),
            ("edit_media", STATE["EDIT_MEDIA"]),
        ]:
            update = _make_update(is_callback=True)
            update.callback_query.data = data
            result = await handle_edit_field_callback(update, None)
            assert result == expected, data


class TestPublishRequirements:
    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_publish_without_tags_stays_in_preview(self):
        from handlers.publish import publish_submission

        update = _make_update(is_callback=True)
        row = _submission_row() | {"tags": ""}
        with (
            patch("handlers.publish.get_db", _FakeDB(row)),
            patch("handlers.publish.cleanup_old_data", AsyncMock()),
        ):
            result = await publish_submission(update, MagicMock())

        assert result == STATE["PUBLISH"]
        update.callback_query.answer.assert_awaited_once_with("请先填写标签", show_alert=True)


class TestPagination:
    @pytest.mark.unit
    def test_page_nav_keyboard(self):
        from ui.keyboards import Keyboards

        kb = Keyboards.page_nav(1, 3)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        datas = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "📄 1/3" in texts
        assert "下一页 ➡️" in texts and "page_2" in datas
        assert "⬅️ 上一页" not in texts  # 第一页没有上一页

        kb2 = Keyboards.page_nav(3, 3)
        datas2 = [b.callback_data for row in kb2.inline_keyboard for b in row]
        assert "page_2" in datas2 and "page_4" not in datas2

    @pytest.mark.unit
    def test_page_nav_keeps_base_rows(self):
        from ui.keyboards import Keyboards
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        base = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ 1", callback_data="delete_post_1")]])
        kb = Keyboards.page_nav(2, 5, base=base)
        flat = [b for row in kb.inline_keyboard for b in row]
        assert any(b.callback_data == "delete_post_1" for b in flat)
        assert any(b.callback_data == "page_1" for b in flat)
        assert any(b.callback_data == "page_3" for b in flat)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_pagination_dispatcher_page_info(self):
        from handlers.callback_handlers import handle_pagination

        query = MagicMock()
        query.answer = AsyncMock()
        query.data = "page_info"
        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.user_data = {}

        await handle_pagination(update, context)
        query.answer.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_pagination_without_context_is_safe(self):
        from handlers.callback_handlers import handle_pagination

        query = MagicMock()
        query.answer = AsyncMock()
        query.data = "page_2"
        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.user_data = {}

        await handle_pagination(update, context)
        query.answer.assert_called()  # 无翻页上下文时安全应答，不抛异常
