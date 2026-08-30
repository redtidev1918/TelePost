"""API/chat review queue and approval callbacks."""

import json
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from telegram.error import RetryAfter
from telegram import InputFile

from database import db_manager
from handlers import review


def _photo_message(message_id=10, file_id="STAGED_PHOTO"):
    message = MagicMock()
    message.message_id = message_id
    message.photo = [MagicMock(file_id=file_id)]
    message.video = None
    message.animation = None
    message.audio = None
    message.document = None
    return message


def _document_message(message_id=30, file_id="STAGED_DOC", filename=None):
    message = MagicMock()
    message.message_id = message_id
    message.photo = None
    message.video = None
    message.animation = None
    message.audio = None
    message.document = MagicMock(file_id=file_id, file_name=filename or "novel.txt")
    return message


def _animation_message(message_id=40, file_id="STAGED_ANIMATION"):
    message = MagicMock()
    message.message_id = message_id
    message.photo = None
    message.video = None
    message.animation = MagicMock(file_id=file_id)
    message.audio = None
    message.document = None
    return message


def test_file_id_extraction_accepts_tuple_photo_sizes():
    from handlers.publish import _file_id_of

    message = _photo_message()
    message.photo = (
        MagicMock(file_id="SMALL_PHOTO"),
        MagicMock(file_id="LARGE_PHOTO"),
    )

    assert _file_id_of(message) == "LARGE_PHOTO"


def _callback_update(data, user_id=123456789):
    update = MagicMock()
    update.effective_user.id = user_id
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


@pytest.fixture
async def review_db(monkeypatch, tmp_path):
    db_path = str(tmp_path / "reviews.db")
    monkeypatch.setattr(db_manager, "DB_PATH", db_path)
    monkeypatch.setattr(review, "REVIEW_CHAT_ID", -100123)
    monkeypatch.setattr(review, "ADMIN_IDS", [123456789])
    await db_manager.init_db()
    return db_path


@pytest.mark.asyncio
async def test_file_id_submission_is_durable_and_idempotent(review_db):
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    control = MagicMock()
    control.message_id = 11
    bot.send_message.return_value = control

    first = await review.queue_review_from_file_ids(
        bot,
        [{"type": "photo", "file_id": "ORIGINAL"}],
        [],
        tags="#pixiv",
        title="Artwork",
        link="https://www.pixiv.net/artworks/123",
        user_id=7,
        username="pixivflow",
        idempotency_key="pixiv:123",
    )
    second = await review.queue_review_from_file_ids(
        bot,
        [{"type": "photo", "file_id": "ORIGINAL"}],
        [],
        tags="#pixiv",
        user_id=7,
        username="pixivflow",
        idempotency_key="pixiv:123",
    )

    assert first["status"] == "pending_review"
    assert second["review_id"] == first["review_id"]
    assert bot.send_photo.await_count == 1

    async with db_manager.get_db() as conn:
        cursor = await conn.execute("SELECT * FROM pending_reviews")
        row = await cursor.fetchone()
    assert row["status"] == "pending"
    assert json.loads(row["media_json"])[0]["file_id"] == "STAGED_PHOTO"
    assert row["control_message_id"] == 11


@pytest.mark.asyncio
async def test_stale_pending_reviews_expire_and_delete_review_messages(
    review_db, monkeypatch
):
    now = 1_800_000_000.0
    monkeypatch.setattr(review, "PENDING_REVIEW_RETENTION_DAYS", 7)
    monkeypatch.setattr(review, "PENDING_REVIEW_CLEANUP_BATCH_SIZE", 100)
    bot = AsyncMock()

    async with db_manager.get_db() as conn:
        await conn.executemany(
            """
            INSERT INTO pending_reviews (
                idempotency_key, source, status, user_id, review_chat_id,
                review_message_ids, control_message_id, created_at, updated_at
            ) VALUES (?, 'api', 'pending', 7, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "old",
                    str(review.REVIEW_CHAT_ID),
                    json.dumps([101, 102]),
                    103,
                    now - 8 * 86400,
                    now - 8 * 86400,
                ),
                (
                    "fresh",
                    str(review.REVIEW_CHAT_ID),
                    json.dumps([201]),
                    202,
                    now - 6 * 86400,
                    now - 6 * 86400,
                ),
            ],
        )

    expired = await review.expire_stale_reviews(bot, now=now)

    assert expired == 1
    assert bot.delete_message.await_args_list == [
        call(chat_id=review.REVIEW_CHAT_ID, message_id=101),
        call(chat_id=review.REVIEW_CHAT_ID, message_id=102),
        call(chat_id=review.REVIEW_CHAT_ID, message_id=103),
    ]
    async with db_manager.get_db() as conn:
        cursor = await conn.execute(
            "SELECT idempotency_key, status, error FROM pending_reviews ORDER BY id"
        )
        rows = await cursor.fetchall()
    assert [(row["idempotency_key"], row["status"]) for row in rows] == [
        ("old", "expired"),
        ("fresh", "pending"),
    ]
    assert "expired after 7 days" in rows[0]["error"]


@pytest.mark.asyncio
async def test_rejected_review_does_not_block_same_key_resubmission(review_db, monkeypatch):
    """同一 idempotency_key 的旧记录已被拒绝时，新投稿应创建新审核记录。"""
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message(message_id=1, file_id="A")
    control = MagicMock(message_id=2)
    bot.send_message.return_value = control
    monkeypatch.setattr(review, "REVIEW_PREVIEW_INTERVAL_SECONDS", 0.0)

    first = await review.queue_review_from_file_ids(
        bot,
        [{"type": "photo", "file_id": "ORIG"}],
        [],
        tags="#a",
        user_id=7,
        username="u",
        idempotency_key="reject-me",
    )
    assert first["status"] == "pending_review"

    # 审核员拒绝该投稿
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE pending_reviews SET status='rejected' WHERE id=?",
            (first["review_id"],),
        )

    # 同一 key 再次投稿（例如 PixivFlow 次日又选中同一作品）
    second = await review.queue_review_from_file_ids(
        bot,
        [{"type": "photo", "file_id": "ORIG"}],
        [],
        tags="#b",
        user_id=7,
        username="u",
        idempotency_key="reject-me",
    )

    assert second["status"] == "pending_review"
    assert second["review_id"] != first["review_id"]
    async with db_manager.get_db() as conn:
        cursor = await conn.execute("SELECT * FROM pending_reviews WHERE id=?", (second["review_id"],))
        row = await cursor.fetchone()
    assert row["tags"] == "#b"
    # 旧记录被替换，同 key 只保留一行
    async with db_manager.get_db() as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM pending_reviews WHERE idempotency_key=?",
            ("api:7:reject-me",),
        )
        assert (await cursor.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_failed_local_staging_deletes_uploaded_preview(review_db, tmp_path):
    source = tmp_path / "preview.png"
    source.write_bytes(b"not-a-real-image")
    message = _photo_message(message_id=77)
    message.photo = ()
    bot = AsyncMock()
    bot.send_photo.return_value = message

    with pytest.raises(RuntimeError, match="file_id"):
        await review.queue_review_from_files(
            bot,
            [{"kind": "photo", "path": str(source), "filename": "preview.png"}],
            tags="#test",
            user_id=7,
            username="tester",
        )

    bot.delete_message.assert_awaited_once_with(chat_id=-100123, message_id=77)
    assert not source.exists()


@pytest.mark.asyncio
async def test_multi_page_review_paces_preview_sends(review_db, monkeypatch):
    """多页图片按每批 ≤10 打包成 media group（相册）发送。"""
    bot = AsyncMock()
    bot.send_media_group.return_value = [
        _photo_message(message_id=10, file_id="PAGE_1"),
        _photo_message(message_id=11, file_id="PAGE_2"),
    ]
    sleep = AsyncMock()
    monkeypatch.setattr(review.asyncio, "sleep", sleep)
    monkeypatch.setattr(review, "REVIEW_PREVIEW_INTERVAL_SECONDS", 0.75)

    media, documents = await review._stage_file_ids(
        bot,
        [
            {"type": "photo", "file_id": "ORIGINAL_1"},
            {"type": "photo", "file_id": "ORIGINAL_2"},
        ],
        [],
        "caption",
        False,
        [],
    )

    assert [item["file_id"] for item in media] == ["PAGE_1", "PAGE_2"]
    assert documents == []
    bot.send_media_group.assert_awaited_once()
    # 首个相册不产生组间间隔（只有 1 个 chunk，pace 在 index==0 时不 sleep）
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_multi_album_review_threads_and_paces(review_db, monkeypatch):
    """29 页作品拆成 3 个相册；后续相册回复上一相册的最后一条消息。"""
    bot = AsyncMock()
    # 3 个相册：10 + 10 + 9
    bot.send_media_group.side_effect = [
        [_photo_message(message_id=100 + i, file_id=f"P1_{i}") for i in range(10)],
        [_photo_message(message_id=110 + i, file_id=f"P2_{i}") for i in range(10)],
        [_photo_message(message_id=120 + i, file_id=f"P3_{i}") for i in range(9)],
    ]
    sleep = AsyncMock()
    monkeypatch.setattr(review.asyncio, "sleep", sleep)
    monkeypatch.setattr(review, "REVIEW_PREVIEW_INTERVAL_SECONDS", 0.75)
    monkeypatch.setattr(review, "REVIEW_PREVIEW_THREAD", True)
    monkeypatch.setattr(review, "REVIEW_ALBUM_SIZE", 10)

    media, documents = await review._stage_file_ids(
        bot,
        [{"type": "photo", "file_id": f"ID_{i}"} for i in range(29)],
        [],
        "caption",
        True,
        [],
    )

    assert len(media) == 29
    assert documents == []
    assert bot.send_media_group.await_count == 3
    calls = [call.kwargs for call in bot.send_media_group.await_args_list]
    # 第一个相册无 reply_to；第二、三个相册回复前一相册最后一条消息
    assert calls[0].get("reply_to_message_id") is None
    assert calls[1]["reply_to_message_id"] == 109
    assert calls[2]["reply_to_message_id"] == 119
    # caption 只挂在第一个相册；spoiler 打开
    first_media = calls[0]["media"][0]
    assert first_media.caption == "caption"
    assert first_media.has_spoiler is True
    assert calls[1]["media"][0].caption is None


@pytest.mark.asyncio
async def test_multi_page_review_threads_previews(review_db, monkeypatch):
    """同一批多个相册时，后续相册回复上一条消息（回复链）。"""
    bot = AsyncMock()
    bot.send_media_group.side_effect = [
        [
            _photo_message(message_id=10, file_id="PAGE_1"),
            _photo_message(message_id=11, file_id="PAGE_2"),
        ],
        [
            _photo_message(message_id=12, file_id="PAGE_3"),
            _photo_message(message_id=13, file_id="PAGE_4"),
        ],
    ]
    monkeypatch.setattr(review, "REVIEW_PREVIEW_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(review, "REVIEW_PREVIEW_THREAD", True)
    monkeypatch.setattr(review, "REVIEW_ALBUM_SIZE", 2)

    await review._stage_file_ids(
        bot,
        [
            {"type": "photo", "file_id": "ORIGINAL_1"},
            {"type": "photo", "file_id": "ORIGINAL_2"},
            {"type": "photo", "file_id": "ORIGINAL_3"},
            {"type": "photo", "file_id": "ORIGINAL_4"},
        ],
        [],
        "caption",
        False,
        [],
    )

    calls = [call.kwargs for call in bot.send_media_group.await_args_list]
    assert calls[0].get("reply_to_message_id") is None
    assert calls[1]["reply_to_message_id"] == 11


@pytest.mark.asyncio
async def test_multi_page_review_can_disable_threading(review_db, monkeypatch):
    bot = AsyncMock()
    bot.send_media_group.side_effect = [
        [
            _photo_message(message_id=10, file_id="PAGE_1"),
            _photo_message(message_id=11, file_id="PAGE_2"),
        ],
        [
            _photo_message(message_id=12, file_id="PAGE_3"),
            _photo_message(message_id=13, file_id="PAGE_4"),
        ],
    ]
    monkeypatch.setattr(review, "REVIEW_PREVIEW_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(review, "REVIEW_PREVIEW_THREAD", False)
    monkeypatch.setattr(review, "REVIEW_ALBUM_SIZE", 2)

    await review._stage_file_ids(
        bot,
        [
            {"type": "photo", "file_id": "ORIGINAL_1"},
            {"type": "photo", "file_id": "ORIGINAL_2"},
            {"type": "photo", "file_id": "ORIGINAL_3"},
            {"type": "photo", "file_id": "ORIGINAL_4"},
        ],
        [],
        "caption",
        False,
        [],
    )

    calls = [call.kwargs for call in bot.send_media_group.await_args_list]
    assert all(call.get("reply_to_message_id") is None for call in calls)


@pytest.mark.asyncio
async def test_novel_document_stages_with_caption_and_thread(review_db, monkeypatch):
    """小说 .txt 作为 document 单条发送：带 caption，且回复前一条消息。"""
    bot = AsyncMock()
    bot.send_media_group.side_effect = [
        [_photo_message(message_id=10 + i, file_id=f"P_{i}") for i in range(2)],
    ]
    bot.send_document.return_value = _document_message(message_id=30, file_id="NOVEL_TXT")
    monkeypatch.setattr(review, "REVIEW_PREVIEW_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(review, "REVIEW_PREVIEW_THREAD", True)

    media, documents = await review._stage_file_ids(
        bot,
        [
            {"type": "photo", "file_id": "COVER_1"},
            {"type": "photo", "file_id": "COVER_2"},
        ],
        [{"file_id": "NOVEL_ORIG", "filename": "123_novel.txt"}],
        "caption",
        True,
        [],
    )

    assert len(media) == 2
    assert len(documents) == 1 and documents[0]["filename"] == "123_novel.txt"
    # document 单独一条发送，回复到相册最后一条
    kwargs = bot.send_document.await_args.kwargs
    assert kwargs["reply_to_message_id"] == 11
    assert kwargs["document"] == "NOVEL_ORIG"


@pytest.mark.asyncio
async def test_animations_stay_standalone_and_threaded(review_db, monkeypatch):
    """Telegram sendMediaGroup 不支持 animation；GIF 必须逐条发并串成回复链。"""
    bot = AsyncMock()
    bot.send_animation.side_effect = [
        _animation_message(message_id=40, file_id="GIF_1"),
        _animation_message(message_id=41, file_id="GIF_2"),
    ]
    monkeypatch.setattr(review, "REVIEW_PREVIEW_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(review, "REVIEW_PREVIEW_THREAD", True)

    media, documents = await review._stage_file_ids(
        bot,
        [
            {"type": "animation", "file_id": "ORIGINAL_GIF_1"},
            {"type": "animation", "file_id": "ORIGINAL_GIF_2"},
        ],
        [],
        "caption",
        True,
        [],
    )

    assert [item["file_id"] for item in media] == ["GIF_1", "GIF_2"]
    assert documents == []
    bot.send_media_group.assert_not_awaited()
    calls = [call.kwargs for call in bot.send_animation.await_args_list]
    assert calls[0].get("reply_to_message_id") is None
    assert calls[1]["reply_to_message_id"] == 40


@pytest.mark.asyncio
async def test_album_message_count_mismatch_fails_instead_of_dropping_pages(review_db):
    bot = AsyncMock()
    bot.send_media_group.return_value = [
        _photo_message(message_id=50, file_id="ONLY_ONE"),
    ]

    with pytest.raises(RuntimeError, match="消息数 1 与文件数 2"):
        await review.queue_review_from_file_ids(
            bot,
            [
                {"type": "photo", "file_id": "ORIGINAL_1"},
                {"type": "photo", "file_id": "ORIGINAL_2"},
            ],
            [],
            tags="#test",
            user_id=7,
        )

    bot.delete_message.assert_awaited_once_with(chat_id=-100123, message_id=50)


@pytest.mark.asyncio
async def test_album_local_files_keep_attach_uri(review_db, monkeypatch, tmp_path):
    """相册里每个本地文件必须是 attach 模式，否则 InputMedia 的 media 字段
    会被 python-telegram-bot 丢弃，Telegram 报 media not found。"""
    from telegram.request._requestparameter import RequestParameter

    files = []
    for i in range(2):
        p = tmp_path / f"page_{i}.png"
        p.write_bytes(b"fake-image-bytes")
        files.append({"kind": "photo", "path": str(p), "filename": p.name})

    bot = AsyncMock()
    bot.send_media_group.return_value = [
        _photo_message(message_id=10, file_id="P0"),
        _photo_message(message_id=11, file_id="P1"),
    ]
    monkeypatch.setattr(review, "REVIEW_PREVIEW_INTERVAL_SECONDS", 0.0)

    media, documents = await review._stage_local_files(
        bot, files, "caption", False, []
    )

    assert len(media) == 2
    kwargs = bot.send_media_group.await_args.kwargs
    group = kwargs["media"]
    assert len(group) == 2
    # 每个 InputMedia 的 media 必须是带 attach_name 的 InputFile
    for item in group:
        assert item.media.attach_name is not None
        assert item.media.attach_uri == f"attach://{item.media.attach_name}"
    # 序列化后 media 字段仍保留 attach:// 引用（否则 Telegram 无法解析）
    param = RequestParameter.from_input("media", group)
    assert "attach://" in param.json_value


@pytest.mark.asyncio
async def test_oversized_photo_stages_as_document(review_db, monkeypatch, tmp_path):
    """超过 Telegram 图片上限（10 MiB）的页面自动按文档发送，投稿不失败。"""
    small = tmp_path / "small.png"
    small.write_bytes(b"x" * 1024)
    big = tmp_path / "big.png"
    big.write_bytes(b"y" * (2 * 1024 * 1024))
    monkeypatch.setattr(review, "PHOTO_MAX_BYTES", 1024)  # 任何 >1KB 都算超限

    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message(message_id=10, file_id="SMALL_PHOTO")
    bot.send_document.return_value = _document_message(
        message_id=20, file_id="BIG_DOC", filename="big.png"
    )
    monkeypatch.setattr(review, "REVIEW_PREVIEW_INTERVAL_SECONDS", 0.0)

    media, documents = await review._stage_local_files(
        bot,
        [
            {"kind": "photo", "path": str(small), "filename": "small.png"},
            {"kind": "photo", "path": str(big), "filename": "big.png"},
        ],
        "caption",
        False,
        [],
    )

    assert [m["file_id"] for m in media] == ["SMALL_PHOTO"]
    assert len(documents) == 1 and documents[0]["filename"] == "big.png"
    # 大图走 document 发送（InputFile 指向大图文件）
    bot.send_document.assert_awaited_once()
    sent = bot.send_document.await_args.kwargs["document"]
    assert isinstance(sent, InputFile)
    assert sent.filename == "big.png"


@pytest.mark.asyncio
async def test_album_failure_falls_back_to_single_sends(review_db, monkeypatch):
    """相册发送失败（小内存机器超时）时自动降级逐张发送，整份投稿不失败。"""
    bot = AsyncMock()
    # 相册第一次抛异常；随后逐张发送成功
    bot.send_media_group.side_effect = RuntimeError("timeout / memory pressure")
    bot.send_photo.side_effect = [
        _photo_message(message_id=70, file_id="SINGLE_1"),
        _photo_message(message_id=71, file_id="SINGLE_2"),
    ]
    monkeypatch.setattr(review, "REVIEW_PREVIEW_INTERVAL_SECONDS", 0.0)

    media, documents = await review._stage_file_ids(
        bot,
        [
            {"type": "photo", "file_id": "ORIGINAL_1"},
            {"type": "photo", "file_id": "ORIGINAL_2"},
        ],
        [],
        "caption",
        False,
        [],
    )

    # 降级后每张仍被暂存为可用的 file_id
    assert [item["file_id"] for item in media] == ["SINGLE_1", "SINGLE_2"]
    assert documents == []
    # 相册尝试了 1 次，然后逐张 2 次
    assert bot.send_media_group.await_count == 1
    assert bot.send_photo.await_count == 2
    # 第一张带 caption 且无 reply；第二张回复第一张
    calls = [call.kwargs for call in bot.send_photo.await_args_list]
    assert calls[0]["caption"] == "caption"
    assert calls[0].get("reply_to_message_id") is None
    assert calls[1].get("reply_to_message_id") == 70


@pytest.mark.asyncio
async def test_album_size_is_configurable(review_db, monkeypatch):
    """REVIEW_ALBUM_SIZE 控制每组媒体数；最后不足一组的单张走独立消息。"""
    bot = AsyncMock()
    bot.send_media_group.return_value = [
        _photo_message(message_id=80, file_id="P0"),
        _photo_message(message_id=81, file_id="P1"),
    ]
    bot.send_photo.return_value = _photo_message(message_id=82, file_id="P2")
    monkeypatch.setattr(review, "REVIEW_PREVIEW_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(review, "REVIEW_ALBUM_SIZE", 2)

    media, _ = await review._stage_file_ids(
        bot,
        [{"type": "photo", "file_id": f"ID{i}"} for i in range(3)],
        [],
        "caption",
        False,
        [],
    )

    assert len(media) == 3
    assert [item["file_id"] for item in media] == ["P0", "P1", "P2"]
    # 第一组 2 张走相册；剩余 1 张独立发送
    assert bot.send_media_group.await_count == 1
    assert bot.send_photo.await_count == 1


@pytest.mark.asyncio
async def test_control_message_replies_to_last_album_item(review_db):
    bot = AsyncMock()
    bot.send_media_group.return_value = [
        _photo_message(message_id=60, file_id="PAGE_1"),
        _photo_message(message_id=61, file_id="PAGE_2"),
    ]
    control = MagicMock(message_id=62)
    bot.send_message.return_value = control

    await review.queue_review_from_file_ids(
        bot,
        [
            {"type": "photo", "file_id": "ORIGINAL_1"},
            {"type": "photo", "file_id": "ORIGINAL_2"},
        ],
        [],
        tags="#test",
        user_id=7,
    )

    assert bot.send_message.await_args.kwargs["reply_to_message_id"] == 61


@pytest.mark.asyncio
async def test_local_album_retry_reopens_files(review_db, monkeypatch, tmp_path):
    first = tmp_path / "one.png"
    second = tmp_path / "two.png"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    bot = AsyncMock()
    seen = []

    async def send_media_group(**kwargs):
        handle = kwargs["media"][0].media.input_file_content
        seen.append((id(handle), handle.closed, handle.read()))
        if len(seen) == 1:
            raise RetryAfter(1)
        return [
            _photo_message(message_id=70, file_id="PAGE_1"),
            _photo_message(message_id=71, file_id="PAGE_2"),
        ]

    bot.send_media_group.side_effect = send_media_group
    sleep = AsyncMock()
    monkeypatch.setattr(review.asyncio, "sleep", sleep)

    media, _ = await review._stage_local_files(
        bot,
        [
            {"kind": "photo", "path": str(first), "filename": first.name},
            {"kind": "photo", "path": str(second), "filename": second.name},
        ],
        "caption",
        False,
        [],
    )

    assert len(media) == 2
    assert [(closed, body) for _, closed, body in seen] == [
        (False, b"one"),
        (False, b"one"),
    ]
    assert seen[0][0] != seen[1][0]
    sleep.assert_awaited_once_with(2.0)


@pytest.mark.asyncio
async def test_review_preview_uses_large_upload_timeouts(review_db, monkeypatch, tmp_path):
    source = tmp_path / "large-page.png"
    source.write_bytes(b"image")
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    monkeypatch.setattr(review, "REVIEW_PREVIEW_TIMEOUT_SECONDS", 120.0)

    await review._send_local_preview_single(
        bot,
        {"kind": "photo", "path": str(source), "filename": source.name},
        "caption",
        True,
    )

    kwargs = bot.send_photo.await_args.kwargs
    assert kwargs["read_timeout"] == 120.0
    assert kwargs["write_timeout"] == 120.0
    assert kwargs["connect_timeout"] == 30.0
    assert kwargs["pool_timeout"] == 30.0


@pytest.mark.asyncio
async def test_retry_after_creates_a_fresh_send_coroutine(review_db, monkeypatch):
    bot = AsyncMock()
    bot.send_photo.side_effect = [
        RetryAfter(2),
        _photo_message(message_id=12, file_id="RETRIED"),
    ]
    sleep = AsyncMock()
    monkeypatch.setattr(review.asyncio, "sleep", sleep)

    media, _ = await review._stage_file_ids(
        bot,
        [{"type": "photo", "file_id": "ORIGINAL"}],
        [],
        "caption",
        False,
        [],
    )

    assert media[0]["file_id"] == "RETRIED"
    assert bot.send_photo.await_count == 2
    sleep.assert_awaited_once_with(3.0)


@pytest.mark.asyncio
async def test_owner_can_approve_once(review_db):
    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    control = MagicMock()
    control.message_id = 11
    bot.send_message.return_value = control
    queued = await review.queue_review_from_file_ids(
        bot,
        [{"type": "photo", "file_id": "ORIGINAL"}],
        [],
        tags="#pixiv",
        user_id=7,
        username="pixivflow",
        idempotency_key="pixiv:approve",
    )

    update = _callback_update(f"review_approve:{queued['review_id']}")
    context = MagicMock()
    context.bot = bot
    publish_result = {
        "status": "published",
        "message_id": 99,
        "link": "https://t.me/c/1/99",
        "media_count": 1,
        "document_count": 0,
    }
    with patch("handlers.review.publish_from_file_ids", AsyncMock(return_value=publish_result)) as publish:
        await review.approve_review(update, context)
        await review.approve_review(update, context)

    publish.assert_awaited_once()
    async with db_manager.get_db() as conn:
        cursor = await conn.execute(
            "SELECT status, published_message_id FROM pending_reviews WHERE id=?",
            (queued["review_id"],),
        )
        row = await cursor.fetchone()
    assert row["status"] == "published"
    assert row["published_message_id"] == 99


@pytest.mark.asyncio
async def test_chat_submission_enters_review_and_notifies_after_approval(
    review_db, monkeypatch
):
    from handlers import publish

    async with db_manager.get_db() as conn:
        await conn.execute(
            """
            INSERT INTO submissions (
                user_id, timestamp, mode, image_id, document_id, tags, link,
                title, note, spoiler, anonymous, username
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                7,
                1234.5,
                "media",
                json.dumps(["photo:ORIGINAL"]),
                json.dumps([]),
                "#chat",
                "",
                "Chat artwork",
                "",
                "false",
                "false",
                "chat_user",
            ),
        )

    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message()
    control = MagicMock()
    control.message_id = 11
    bot.send_message.return_value = control

    update = _callback_update("publish", user_id=7)
    update.effective_user.username = "chat_user"
    context = MagicMock()
    context.bot = bot
    monkeypatch.setattr(publish, "CHAT_REVIEW_REQUIRED", True)

    await publish.publish_submission(update, context)

    assert bot.send_photo.await_args.kwargs["chat_id"] == -100123
    assert "进入审核队列" in update.callback_query.edit_message_text.await_args.args[0]
    async with db_manager.get_db() as conn:
        cursor = await conn.execute("SELECT * FROM pending_reviews")
        row = await cursor.fetchone()
        cursor = await conn.execute("SELECT * FROM submissions WHERE user_id=7")
        session = await cursor.fetchone()
    assert row["source"] == "chat"
    assert session is None

    bot.send_message.reset_mock()
    approval = _callback_update(f"review_approve:{row['id']}")
    with patch(
        "handlers.review.publish_from_file_ids",
        AsyncMock(return_value={
            "status": "published",
            "message_id": 99,
            "link": "https://t.me/test/99",
        }),
    ):
        await review.approve_review(approval, context)
    bot.send_message.assert_awaited_once_with(
        chat_id=7,
        text="✅ 你的投稿已通过审核并发布到频道。\nhttps://t.me/test/99",
    )


@pytest.mark.asyncio
async def test_non_admin_cannot_reject(review_db):
    update = _callback_update("review_reject:1", user_id=999)
    context = MagicMock()
    context.bot = AsyncMock()
    await review.reject_review(update, context)
    update.callback_query.answer.assert_awaited_once_with(
        text="你没有审核权限", show_alert=True
    )


@pytest.mark.asyncio
async def test_single_file_id_uses_send_photo_not_media_group(monkeypatch):
    from handlers import publish

    bot = AsyncMock()
    bot.send_photo.return_value = _photo_message(message_id=55, file_id="PHOTO")
    monkeypatch.setattr(publish, "save_published_post", AsyncMock())

    result = await publish.publish_from_file_ids(
        bot,
        [{"type": "photo", "file_id": "PHOTO"}],
        [],
        tags="#pixiv",
        user_id=7,
        username="pixivflow",
    )

    assert result["message_id"] == 55
    bot.send_photo.assert_awaited_once()
    bot.send_media_group.assert_not_called()
