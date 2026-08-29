from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers import publish


@pytest.mark.asyncio
async def test_publish_keeps_file_handle_open_and_unbuffered(monkeypatch, tmp_path):
    media_path = tmp_path / "large.jpg"
    media_path.write_bytes(b"stream-me")
    captured = {}

    async def send_media_group(*, chat_id, media, **_kwargs):
        upload = media[0].media
        captured["handle"] = upload.input_file_content
        captured["open_during_send"] = not upload.input_file_content.closed
        return [
            SimpleNamespace(
                message_id=101,
                photo=(SimpleNamespace(file_id="telegram-file-id"),),
            )
        ]

    bot = SimpleNamespace(send_media_group=send_media_group)
    monkeypatch.setattr(publish, "CHANNEL_ID", "@channel")
    monkeypatch.setattr(publish, "save_published_post", AsyncMock())

    result = await publish.publish_from_files(
        bot,
        [{"path": str(media_path), "kind": "photo", "filename": "large.jpg"}],
        tags="#Pixiv",
        user_id=1,
        username="tester",
    )

    assert result["status"] == "published"
    assert captured["open_during_send"] is True
    assert captured["handle"].closed is True
    assert not media_path.exists()
