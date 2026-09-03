from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers import publish


@pytest.mark.asyncio
async def test_publish_keeps_file_handle_open_and_unbuffered(monkeypatch, tmp_path):
    media_path = tmp_path / "large.jpg"
    media_path.write_bytes(b"stream-me")
    captured = {}

    async def send_photo(*, chat_id, photo, **_kwargs):
        # 单张照片走 send_photo；本地文件以 attach 模式的 InputFile 传入
        upload = photo
        captured["handle"] = upload.input_file_content
        captured["open_during_send"] = not upload.input_file_content.closed
        return SimpleNamespace(
            message_id=101,
            photo=(SimpleNamespace(file_id="telegram-file-id"),),
        )

    bot = SimpleNamespace(send_photo=send_photo)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,filename", [
    ("photo", "222_page_1.jpg"),
    ("document", "111_novel.txt"),
])
async def test_publish_local_album_uses_attach_uri(
    monkeypatch, tmp_path, kind, filename
):
    """直发（API_REVIEW_REQUIRED=false）相册/文档里的本地 InputFile 必须是
    attach 模式，否则 PTB 序列化时丢掉 media 字段，Telegram 报 media not found
    ——表现为 API 投稿的图片发不出去、小说文档丢失。"""
    from telegram.request._requestparameter import RequestParameter

    suffix = ".jpg" if kind == "photo" else ".txt"
    paths = []
    files = []
    for i in range(2):
        p = tmp_path / f"f{i}{suffix}"
        p.write_bytes(b"fake-bytes")
        paths.append(p)
        files.append({"path": str(p), "kind": kind, "filename": f"{i}{suffix}"})

    captured = {}

    async def send_media_group(*, chat_id, media, **_kwargs):
        captured["media"] = media
        out = []
        for m in media:
            if kind == "photo":
                out.append(SimpleNamespace(
                    message_id=200 + len(out),
                    photo=(SimpleNamespace(file_id=f"ph{len(out)}"),),
                    document=None,
                ))
            else:
                out.append(SimpleNamespace(
                    message_id=300 + len(out),
                    photo=(),
                    document=SimpleNamespace(file_id=f"doc{len(out)}"),
                ))
        return out

    bot = SimpleNamespace(send_media_group=send_media_group)
    monkeypatch.setattr(publish, "CHANNEL_ID", "@channel")
    monkeypatch.setattr(publish, "save_published_post", AsyncMock())

    await publish.publish_from_files(
        bot, files, tags="#Pixiv", user_id=1, username="tester"
    )

    group = captured["media"]
    for item in group:
        assert item.media.attach_name is not None
        assert item.media.attach_uri == f"attach://{item.media.attach_name}"
    param = RequestParameter.from_input("media", group)
    assert "attach://" in param.json_value


@pytest.mark.asyncio
async def test_publish_animation_goes_standalone_not_album(monkeypatch, tmp_path):
    """GIF(animation) 不是 sendMediaGroup 支持的成员，混进相册会被 Telegram
    400 拒绝；必须逐条 send_animation 发送。"""
    gif = tmp_path / "x.gif"
    gif.write_bytes(b"gif-bytes")

    async def send_media_group(**_kwargs):
        raise AssertionError("GIF 不应进入 send_media_group")

    async def send_animation(*, chat_id, animation, **_kwargs):
        assert animation.attach_uri is not None
        return SimpleNamespace(message_id=401, animation=SimpleNamespace(file_id="a1"))

    bot = SimpleNamespace(
        send_media_group=send_media_group,
        send_animation=send_animation,
    )
    monkeypatch.setattr(publish, "CHANNEL_ID", "@channel")
    monkeypatch.setattr(publish, "save_published_post", AsyncMock())

    result = await publish.publish_from_files(
        bot,
        [{"path": str(gif), "kind": "animation", "filename": "x.gif"}],
        tags="#Pixiv", user_id=1, username="tester",
    )
    assert result["status"] == "published"
    assert result["media_count"] == 1


@pytest.mark.unit
def test_oversized_photo_is_compressed_not_demoted(tmp_path):
    """超大本地图片应压缩到可发范围、保持 photo（频道直接看图），
    而不是降级成 document 文件。Pillow 缺失时跳过。"""
    PIL = pytest.importorskip("PIL")
    from PIL import Image

    src = tmp_path / "huge.bmp"
    img = Image.effect_noise((2200, 2200), 128).convert("RGB")
    img.save(src, "BMP")  # BMP 无压缩，2200*2200*3 ≈ 14.5MB > 阈值
    assert src.stat().st_size > publish.PHOTO_MAX_BYTES, "测试图应超过阈值"

    items = publish.reclassify_oversized_photos(
        [{"kind": "photo", "path": str(src), "filename": "huge.bmp"}]
    )

    assert items[0]["kind"] == "photo"
    assert items[0]["filename"] == "huge.jpg"
    assert src.stat().st_size <= publish.PHOTO_MAX_BYTES
