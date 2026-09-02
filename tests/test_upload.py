"""上传阶段 handlers.upload 测试（重构后取代原 media/document handler 测试）。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from models.state import STATE


@pytest.mark.asyncio
@pytest.mark.unit
async def test_photo_appends_media(mock_telegram_update, mock_telegram_context):
    photo = MagicMock(file_id="f1")
    mock_telegram_update.message.photo = [photo]
    mock_telegram_update.message.reply_text = AsyncMock()

    with patch("handlers.upload.append_entry", new=AsyncMock(return_value=1)):
        from handlers.upload import handle_upload
        result = await handle_upload(mock_telegram_update, mock_telegram_context)

    assert result == STATE["UPLOAD"]
    assert "已接收媒体" in mock_telegram_update.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_document_appends_file_in_mixed(mock_telegram_update, mock_telegram_context):
    mock_telegram_update.message.photo = None
    mock_telegram_update.message.video = None
    mock_telegram_update.message.animation = None
    mock_telegram_update.message.audio = None
    doc = MagicMock(file_id="d1", file_name="a.pdf", mime_type="application/pdf")
    mock_telegram_update.message.document = doc
    mock_telegram_update.message.reply_text = AsyncMock()

    with patch("handlers.upload.BOT_MODE", "MIXED"), \
         patch("handlers.upload._file_validator.validate", return_value=(True, "")), \
         patch("handlers.upload.append_entry", new=AsyncMock(return_value=1)):
        from handlers.upload import handle_upload
        result = await handle_upload(mock_telegram_update, mock_telegram_context)

    assert result == STATE["UPLOAD"]
    assert "已接收文件" in mock_telegram_update.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_media_mode_rejects_document(mock_telegram_update, mock_telegram_context):
    mock_telegram_update.message.photo = None
    mock_telegram_update.message.video = None
    mock_telegram_update.message.animation = None
    mock_telegram_update.message.audio = None
    mock_telegram_update.message.document = MagicMock(file_id="d1", file_name="a.pdf", mime_type="application/pdf")
    mock_telegram_update.message.reply_text = AsyncMock()

    with patch("handlers.upload.BOT_MODE", "MEDIA"):
        from handlers.upload import handle_upload
        result = await handle_upload(mock_telegram_update, mock_telegram_context)

    assert result == STATE["UPLOAD"]
    assert "文件附件" in mock_telegram_update.message.reply_text.await_args.args[0]


@pytest.mark.unit
def test_classify_message():
    from types import SimpleNamespace
    from handlers.upload import classify_message

    m = SimpleNamespace(photo=[SimpleNamespace(file_id="p1")], video=None, animation=None, audio=None, document=None)
    assert classify_message(m) == "photo:p1"

    m = SimpleNamespace(photo=None, video=None, animation=None, audio=None,
                        document=SimpleNamespace(file_id="g", file_name="x.gif", mime_type="image/gif"))
    assert classify_message(m) == "animation:g"

    m = SimpleNamespace(photo=None, video=None, animation=None, audio=None,
                        document=SimpleNamespace(file_id="d", file_name="x.pdf", mime_type="application/pdf"))
    assert classify_message(m) == "document:d:x.pdf"

    m = SimpleNamespace(photo=None, video=None, animation=None, audio=None, document=None)
    assert classify_message(m) is None
