"""Submission review queue for API and Telegram chat submissions.

Uploads are staged in a private Telegram review chat.  The database keeps
Telegram ``file_id`` values rather than local paths, so pending reviews survive
Fly Machine restarts without retaining the original files on disk.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Optional

import aiosqlite
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
)
from telegram.error import RetryAfter

from config.settings import ADMIN_IDS, REVIEW_CHAT_ID
from database.db_manager import get_db
from handlers.publish import _file_id_of, publish_from_file_ids
from utils.helper_functions import build_caption


logger = logging.getLogger(__name__)

REVIEW_PREVIEW_INTERVAL_SECONDS = max(
    0.0, float(os.getenv("REVIEW_PREVIEW_INTERVAL_SECONDS", "0.75"))
)
REVIEW_PREVIEW_TIMEOUT_SECONDS = max(
    5.0, float(os.getenv("REVIEW_PREVIEW_TIMEOUT_SECONDS", "120"))
)
REVIEW_PREVIEW_MAX_ATTEMPTS = 5
# 审核群预览是否回复上一条消息（形成回复链，多页图集在群内视觉上连成一组）。
# 默认开启；置 0/false 恢复为全部平铺发送。
REVIEW_PREVIEW_THREAD = str(
    os.getenv("REVIEW_PREVIEW_THREAD", "1")
).strip().lower() in {"1", "true", "yes", "on"}
# Telegram media group 每组最多 10 个媒体；可通过环境变量下调（小内存机器上
# 一次性打包太多大图会让 bot 进程 RSS 飙升、健康检查失败甚至 OOM，512 MiB
# 机型建议 4~5；相册发送失败时会自动降级为逐张发送兜底）。
REVIEW_ALBUM_SIZE = max(1, min(10, int(os.getenv("REVIEW_ALBUM_SIZE", "5"))))


def _review_keyboard(review_id: int, link: str = "") -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton("✅ 发布到频道", callback_data=f"review_approve:{review_id}"),
        InlineKeyboardButton("❌ 拒绝", callback_data=f"review_reject:{review_id}"),
    ]]
    if link:
        rows.append([InlineKeyboardButton("🔗 查看原链接", url=link)])
    return InlineKeyboardMarkup(rows)


def _caption_data(*, tags, title, note, link, anonymous, spoiler, user_id, username):
    return {
        "tags": tags,
        "title": title,
        "note": note,
        "link": link,
        "anonymous": "true" if anonymous else "false",
        "spoiler": "true" if spoiler else "false",
        "user_id": user_id,
        "username": username,
    }


def _result_from_row(row) -> dict:
    status = "pending_review" if row["status"] == "pending" else row["status"]
    result = {
        "status": status,
        "review_id": row["id"],
        "media_count": len(json.loads(row["media_json"] or "[]")),
        "document_count": len(json.loads(row["documents_json"] or "[]")),
    }
    if row["published_message_id"]:
        result["message_id"] = row["published_message_id"]
    return result


def _source_label(source: str) -> str:
    return "Telegram 聊天" if source == "chat" else "HTTP API"


async def _find_review(idempotency_key: str):
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT * FROM pending_reviews WHERE idempotency_key=?",
            (idempotency_key,),
        )
        return await cursor.fetchone()


async def _delete_messages(bot, message_ids):
    for message_id in message_ids:
        try:
            await bot.delete_message(chat_id=REVIEW_CHAT_ID, message_id=message_id)
        except Exception:
            logger.debug("清理审核群预览消息失败: %s", message_id, exc_info=True)


def _cleanup_local_files(files):
    directories = set()
    for item in files:
        path = item.get("path")
        if not path:
            continue
        directories.add(os.path.dirname(path))
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("删除 API 临时文件失败: %s", path, exc_info=True)
    for directory in directories:
        try:
            os.rmdir(directory)
        except OSError:
            pass


def _review_timeout_kwargs() -> dict:
    return {
        "read_timeout": REVIEW_PREVIEW_TIMEOUT_SECONDS,
        "write_timeout": REVIEW_PREVIEW_TIMEOUT_SECONDS,
        "connect_timeout": min(REVIEW_PREVIEW_TIMEOUT_SECONDS, 30.0),
        "pool_timeout": min(REVIEW_PREVIEW_TIMEOUT_SECONDS, 30.0),
    }


def _make_media_item(kind: str, media, *, caption=None, spoiler=False, filename=None):
    """Build an InputMedia* for a media group (album).

    - photo/video carry has_spoiler for the R-18 blur cover;
    - documents/audio do not support spoilers;
    - caption is attached to the first item of the first album only.
    """
    parse_mode = "HTML" if caption else None
    if kind == "photo":
        return InputMediaPhoto(media=media, caption=caption, parse_mode=parse_mode, has_spoiler=spoiler)
    if kind == "video":
        return InputMediaVideo(media=media, caption=caption, parse_mode=parse_mode, has_spoiler=spoiler)
    if kind == "audio":
        return InputMediaAudio(media=media, caption=caption, parse_mode=parse_mode)
    if filename:
        return InputMediaDocument(media=media, filename=filename, caption=caption, parse_mode=parse_mode)
    return InputMediaDocument(media=media, caption=caption, parse_mode=parse_mode)


def _album_family(kind: str) -> Optional[str]:
    """Return the compatible Telegram media-group family for a kind.

    Photos and videos may share one album. Audio and documents each require
    their own homogeneous album. Animations are not accepted by
    ``sendMediaGroup`` and therefore stay standalone.
    """
    if kind in {"photo", "video"}:
        return "visual"
    if kind in {"audio", "document"}:
        return kind
    return None


async def _send_local_preview_album(bot, chunk, caption, spoiler, *, reply_to_message_id=None):
    """Send a chunk of local files as one Telegram media group (album)."""
    async def _factory():
        # RetryAfter must create fresh InputFile objects and reopen every file;
        # reusing handles after an attempted upload can resend empty bodies.
        open_handles = []
        try:
            media_group = []
            for index, item in enumerate(chunk):
                handle = open(item["path"], "rb")
                open_handles.append(handle)
                media = InputFile(handle, filename=item["filename"], read_file_handle=False)
                media_group.append(
                    _make_media_item(
                        item["kind"], media,
                        caption=caption if index == 0 else None,
                        spoiler=spoiler,
                        filename=item["filename"],
                    )
                )
            kwargs = dict(
                chat_id=REVIEW_CHAT_ID,
                media=media_group,
                **_review_timeout_kwargs(),
            )
            if reply_to_message_id is not None:
                kwargs["reply_to_message_id"] = reply_to_message_id
            return await bot.send_media_group(**kwargs)
        finally:
            for handle in open_handles:
                try:
                    handle.close()
                except Exception:
                    logger.debug("关闭预览文件句柄失败", exc_info=True)

    return await _send_preview_throttled(_factory)


async def _send_local_preview_single(bot, item, caption, spoiler, *, reply_to_message_id=None):
    """Send one local file as a standalone message (1-item fallback / odd file)."""
    common = {"chat_id": REVIEW_CHAT_ID, **_review_timeout_kwargs()}
    if reply_to_message_id is not None:
        common["reply_to_message_id"] = reply_to_message_id
    kind = item["kind"]

    async def _factory():
        handle = open(item["path"], "rb")
        try:
            media = InputFile(handle, filename=item["filename"], read_file_handle=False)
            if kind == "photo":
                return await bot.send_photo(photo=media, caption=caption, parse_mode="HTML" if caption else None, has_spoiler=spoiler, **common)
            if kind == "video":
                return await bot.send_video(video=media, caption=caption, parse_mode="HTML" if caption else None, has_spoiler=spoiler, **common)
            if kind == "animation":
                return await bot.send_animation(animation=media, caption=caption, parse_mode="HTML" if caption else None, has_spoiler=spoiler, **common)
            if kind == "audio":
                return await bot.send_audio(audio=media, caption=caption, parse_mode="HTML" if caption else None, **common)
            return await bot.send_document(document=media, filename=item.get("filename"), caption=caption, parse_mode="HTML" if caption else None, **common)
        finally:
            handle.close()

    return await _send_preview_throttled(_factory)


async def _send_file_id_preview_album(bot, chunk, caption, spoiler, *, reply_to_message_id=None):
    """Send a chunk of existing file_ids as one Telegram media group (album)."""
    media_group = []
    for index, item in enumerate(chunk):
        item_caption = caption if index == 0 else None
        media_group.append(
            _make_media_item(
                item["kind"], item["file_id"],
                caption=item_caption,
                spoiler=spoiler,
                filename=item.get("filename"),
            )
        )
    kwargs = dict(chat_id=REVIEW_CHAT_ID, media=media_group, **_review_timeout_kwargs())
    if reply_to_message_id is not None:
        kwargs["reply_to_message_id"] = reply_to_message_id
    return await _send_preview_throttled(lambda: bot.send_media_group(**kwargs))


async def _send_file_id_preview_single(bot, item, caption, spoiler, *, reply_to_message_id=None):
    """Send one existing file_id as a standalone message."""
    common = {"chat_id": REVIEW_CHAT_ID, **_review_timeout_kwargs()}
    if reply_to_message_id is not None:
        common["reply_to_message_id"] = reply_to_message_id
    kind = item["kind"]
    item_caption = caption
    parse_mode = "HTML" if item_caption else None
    kw = dict(caption=item_caption, parse_mode=parse_mode, **common)

    if kind == "photo":
        return await _send_preview_throttled(
            lambda: bot.send_photo(photo=item["file_id"], has_spoiler=spoiler, **kw)
        )
    if kind == "video":
        return await _send_preview_throttled(
            lambda: bot.send_video(video=item["file_id"], has_spoiler=spoiler, **kw)
        )
    if kind == "animation":
        return await _send_preview_throttled(
            lambda: bot.send_animation(animation=item["file_id"], has_spoiler=spoiler, **kw)
        )
    if kind == "audio":
        return await _send_preview_throttled(
            lambda: bot.send_audio(audio=item["file_id"], **kw)
        )
    return await _send_preview_throttled(
        lambda: bot.send_document(
            document=item["file_id"], filename=item.get("filename"), **kw
        )
    )


def _retry_after_seconds(exc: RetryAfter) -> float:
    value = getattr(exc, "retry_after", 5) or 5
    if hasattr(value, "total_seconds"):
        value = value.total_seconds()
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return 5.0


async def _send_preview_throttled(send_factory: Callable[[], Awaitable]):
    """Send one review preview (album or message) with flood-control backoff.

    Staging a multi-page album (e.g. a 24-page Pixiv work) sends media groups
    to the review chat in a row; without pacing, Telegram answers with
    RetryAfter / flood control. Pause briefly between sends and wait out
    RetryAfter explicitly.
    """
    last_error = None
    for attempt in range(REVIEW_PREVIEW_MAX_ATTEMPTS):
        try:
            # A factory is required here: an awaited coroutine cannot be
            # reused after RetryAfter. It also reopens local files per retry.
            return await send_factory()
        except RetryAfter as exc:
            last_error = exc
            wait = _retry_after_seconds(exc) + 1.0
            logger.warning("审核预览触发 Telegram 限流，等待 %.1fs 后重试", wait)
        except Exception as exc:
            msg = str(exc).lower()
            if not any(k in msg for k in ("flood", "retry after", "too many")):
                raise
            last_error = exc
            wait = 5.0

        if attempt + 1 >= REVIEW_PREVIEW_MAX_ATTEMPTS:
            raise last_error
        await asyncio.sleep(wait)

    raise RuntimeError("审核预览重试次数已耗尽")


async def _pace_review_preview(index: int) -> None:
    if index > 0 and REVIEW_PREVIEW_INTERVAL_SECONDS > 0:
        await asyncio.sleep(REVIEW_PREVIEW_INTERVAL_SECONDS)


async def _stage_items(bot, items, caption, spoiler, message_ids, *, local):
    """Stage files as media-group albums with a reply chain between groups.

    Items are partitioned by Telegram-compatible album families. Each run of
    up to REVIEW_ALBUM_SIZE compatible files becomes one album; a run of one,
    and every animation, is sent standalone (so a single novel keeps caption).
    When REVIEW_PREVIEW_THREAD is on, every following album / standalone
    message replies to the last sent message, forming one visual thread.

    Memory safety for small (512 MiB) machines: an album send that fails
    (timeout / network / flood) automatically falls back to sending the chunk
    one file at a time, so a large Pixiv set never fails the whole submission
    and the RSS peak stays bounded by one file per request.
    """
    staged_media: list = []
    staged_documents: list = []

    # Partition into maximal compatible runs, chunked to the album cap.
    ordered_runs: list[tuple[Optional[str], list]] = []
    for item in items:
        kind = item["kind"] if local else item["type"]
        family = _album_family(kind)
        if (
            family is not None
            and ordered_runs
            and ordered_runs[-1][0] == family
            and len(ordered_runs[-1][1]) < REVIEW_ALBUM_SIZE
        ):
            ordered_runs[-1][1].append(item)
        else:
            # None families (animations/unknowns) always become standalone.
            ordered_runs.append((family, [item]))

    async def _send_album(chunk, album_caption, reply_to):
        if local:
            return await _send_local_preview_album(
                bot, chunk, album_caption, spoiler, reply_to_message_id=reply_to
            )
        return await _send_file_id_preview_album(
            bot, chunk, album_caption, spoiler, reply_to_message_id=reply_to
        )

    async def _send_single(item, item_caption, reply_to):
        if local:
            return await _send_local_preview_single(
                bot, item, item_caption, spoiler, reply_to_message_id=reply_to
            )
        return await _send_file_id_preview_single(
            bot, item, item_caption, spoiler, reply_to_message_id=reply_to
        )

    last_message_id = None
    album_index = 0

    for family, chunk in ordered_runs:
        reply_to = last_message_id if (REVIEW_PREVIEW_THREAD and last_message_id is not None) else None
        is_album = family is not None and len(chunk) > 1
        chunk_caption = caption if album_index == 0 else None

        messages = None
        if is_album:
            await _pace_review_preview(album_index)
            try:
                messages = await _send_album(chunk, chunk_caption, reply_to)
            except Exception as exc:
                # 相册一次性打包多张大图时，小内存机器可能超时/连接中断；
                # 降级为逐张发送，RSS 峰值只与单张文件相关，整份投稿不失败。
                logger.warning(
                    "审核相册发送失败（%s），降级为逐张发送 %d 个文件",
                    exc, len(chunk),
                )
                messages = None

        if messages is not None and len(messages) != len(chunk):
            # Record the partial album first so the caller's cleanup deletes
            # whatever Telegram actually received, then fail loudly.
            for message in messages:
                message_ids.append(message.message_id)
            # Never persist a partial review: the caller deletes every known
            # preview and temporary upload file on this exception.
            raise RuntimeError(
                f"审核相册返回消息数 {len(messages)} 与文件数 {len(chunk)} 不一致"
            )

        if messages is None:
            # Fallback / standalone path: one message per file.
            messages = []
            for within, item in enumerate(chunk):
                if is_album:
                    # 降级逐张：每张之间按常规预览间隔限速
                    await _pace_review_preview(1 if within > 0 else 0)
                item_reply = reply_to if within == 0 else (
                    last_message_id if (REVIEW_PREVIEW_THREAD and last_message_id is not None) else None
                )
                single_caption = chunk_caption if within == 0 else None
                single_msg = await _send_single(item, single_caption, item_reply)
                messages.append(single_msg)
                for m in [single_msg]:
                    message_ids.append(m.message_id)
                last_message_id = single_msg.message_id
        else:
            for message in messages:
                message_ids.append(message.message_id)
            last_message_id = messages[-1].message_id

        album_index += 1

        for message, item in zip(messages, chunk):
            file_id = _file_id_of(message)
            if not file_id:
                raise RuntimeError("审核群预览未返回 Telegram file_id")
            item_kind = item["kind"] if local else item["type"]
            if item_kind == "document":
                staged_documents.append({
                    "file_id": file_id,
                    "filename": item.get("filename") or "file",
                })
            else:
                staged_media.append({"type": item_kind, "file_id": file_id})

    return staged_media, staged_documents


async def _stage_local_files(bot, files, caption: str, spoiler: bool, message_ids):
    return await _stage_items(
        bot, files, caption, spoiler, message_ids, local=True
    )


async def _stage_file_ids(bot, media, documents, caption: str, spoiler: bool, message_ids):
    items = []
    for item in media:
        items.append({"kind": item["type"], "type": item["type"], "file_id": item["file_id"]})
    for item in documents:
        items.append({
            "kind": "document", "type": "document",
            "file_id": item["file_id"], "filename": item.get("filename") or "file",
        })
    return await _stage_items(
        bot, items, caption, spoiler, message_ids, local=False
    )


async def _create_review(
    bot,
    *,
    media,
    documents,
    review_message_ids,
    idempotency_key,
    source,
    tags,
    title,
    note,
    link,
    anonymous,
    spoiler,
    user_id,
    username,
):
    now = time.time()
    try:
        async with get_db() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO pending_reviews (
                    idempotency_key, source, status, user_id, username, title, tags, note, link,
                    anonymous, spoiler, media_json, documents_json, review_chat_id,
                    review_message_ids, created_at, updated_at
                ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    idempotency_key,
                    source,
                    user_id,
                    username,
                    title,
                    tags,
                    note,
                    link,
                    int(anonymous),
                    int(spoiler),
                    json.dumps(media),
                    json.dumps(documents),
                    str(REVIEW_CHAT_ID),
                    json.dumps(review_message_ids),
                    now,
                    now,
                ),
            )
            review_id = cursor.lastrowid
    except aiosqlite.IntegrityError:
        await _delete_messages(bot, review_message_ids)
        existing = await _find_review(idempotency_key)
        if existing is None:
            raise
        return _result_from_row(existing)

    control_text = (
        f"🕵️ 投稿待审核 #{review_id}\n"
        f"投稿方式：{_source_label(source)}\n"
        f"投稿人：{username}\n"
        f"标题：{title or '（无）'}\n"
        f"标签：{tags or '（无）'}\n"
        f"文件：{len(media)} 个媒体 / {len(documents)} 个文档"
    )
    try:
        control = await bot.send_message(
            chat_id=REVIEW_CHAT_ID,
            text=control_text,
            reply_to_message_id=(
                review_message_ids[-1]
                if REVIEW_PREVIEW_THREAD and review_message_ids
                else None
            ),
            reply_markup=_review_keyboard(review_id, link),
            disable_web_page_preview=True,
        )
        async with get_db() as conn:
            await conn.execute(
                "UPDATE pending_reviews SET control_message_id=?, updated_at=? WHERE id=?",
                (control.message_id, time.time(), review_id),
            )
    except Exception:
        async with get_db() as conn:
            await conn.execute("DELETE FROM pending_reviews WHERE id=?", (review_id,))
        await _delete_messages(bot, review_message_ids)
        raise

    logger.info(
        "%s 投稿已进入审核队列: review_id=%s user=%s",
        _source_label(source), review_id, user_id,
    )
    return {
        "status": "pending_review",
        "review_id": review_id,
        "media_count": len(media),
        "document_count": len(documents),
    }


def _normalized_idempotency_key(user_id: int, value: str, source: str) -> str:
    raw = (value or "").strip()[:240]
    return f"{source}:{user_id}:{raw or uuid.uuid4().hex}"


async def queue_review_from_files(
    bot,
    files,
    *,
    tags="",
    title="",
    note="",
    link="",
    anonymous=False,
    spoiler=False,
    user_id,
    username="",
    idempotency_key="",
    source="api",
) -> dict:
    """Stage multipart API files and create a durable pending review."""
    key = _normalized_idempotency_key(user_id, idempotency_key, source)
    existing = await _find_review(key)
    if existing is not None:
        _cleanup_local_files(files)
        return _result_from_row(existing)

    data = _caption_data(
        tags=tags, title=title, note=note, link=link,
        anonymous=anonymous, spoiler=spoiler, user_id=user_id, username=username,
    )
    preview_ids = []
    try:
        media, documents = await _stage_local_files(
            bot, files, build_caption(data), spoiler, preview_ids
        )
        return await _create_review(
            bot,
            media=media,
            documents=documents,
            review_message_ids=preview_ids,
            idempotency_key=key,
            source=source,
            tags=tags,
            title=title,
            note=note,
            link=link,
            anonymous=anonymous,
            spoiler=spoiler,
            user_id=user_id,
            username=username,
        )
    except Exception:
        await _delete_messages(bot, preview_ids)
        raise
    finally:
        _cleanup_local_files(files)


async def queue_review_from_file_ids(
    bot,
    media,
    documents,
    *,
    tags="",
    title="",
    note="",
    link="",
    anonymous=False,
    spoiler=False,
    user_id,
    username="",
    idempotency_key="",
    source="api",
) -> dict:
    """Stage an API file_id submission and create a durable pending review."""
    key = _normalized_idempotency_key(user_id, idempotency_key, source)
    existing = await _find_review(key)
    if existing is not None:
        return _result_from_row(existing)

    data = _caption_data(
        tags=tags, title=title, note=note, link=link,
        anonymous=anonymous, spoiler=spoiler, user_id=user_id, username=username,
    )
    preview_ids = []
    try:
        staged_media, staged_documents = await _stage_file_ids(
            bot, media, documents, build_caption(data), spoiler, preview_ids
        )
        return await _create_review(
            bot,
            media=staged_media,
            documents=staged_documents,
            review_message_ids=preview_ids,
            idempotency_key=key,
            source=source,
            tags=tags,
            title=title,
            note=note,
            link=link,
            anonymous=anonymous,
            spoiler=spoiler,
            user_id=user_id,
            username=username,
        )
    except Exception:
        await _delete_messages(bot, preview_ids)
        raise


async def _answer(query, text=None, **kwargs):
    try:
        await query.answer(text=text, **kwargs)
    except Exception:
        logger.debug("回应审核按钮失败", exc_info=True)


async def _notify_chat_submitter(bot, row, text: str):
    """Best-effort decision notice for interactive chat submitters only."""
    if row["source"] != "chat":
        return
    try:
        await bot.send_message(chat_id=row["user_id"], text=text)
    except Exception:
        logger.warning(
            "通知聊天投稿人审核结果失败: review_id=%s", row["id"], exc_info=True
        )


async def approve_review(update, context):
    query = update.callback_query
    if update.effective_user.id not in ADMIN_IDS:
        await _answer(query, "你没有审核权限", show_alert=True)
        return
    await _answer(query)

    try:
        review_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ 无效的审核记录")
        return

    async with get_db() as conn:
        cursor = await conn.execute(
            """
            UPDATE pending_reviews
            SET status='publishing', updated_at=?, error=''
            WHERE id=? AND status IN ('pending', 'failed')
            """,
            (time.time(), review_id),
        )
        claimed = cursor.rowcount == 1
        cursor = await conn.execute("SELECT * FROM pending_reviews WHERE id=?", (review_id,))
        row = await cursor.fetchone()

    if row is None:
        await query.edit_message_text("❌ 审核记录不存在")
        return
    if not claimed:
        await query.edit_message_text(f"ℹ️ 该投稿当前状态：{row['status']}")
        return

    try:
        result = await publish_from_file_ids(
            context.bot,
            json.loads(row["media_json"] or "[]"),
            json.loads(row["documents_json"] or "[]"),
            tags=row["tags"],
            title=row["title"],
            note=row["note"],
            link=row["link"],
            anonymous=bool(row["anonymous"]),
            spoiler=bool(row["spoiler"]),
            user_id=row["user_id"],
            username=row["username"],
        )
    except Exception as error:
        logger.error("审核通过后发布失败: review_id=%s", review_id, exc_info=True)
        async with get_db() as conn:
            await conn.execute(
                "UPDATE pending_reviews SET status='failed', updated_at=?, error=? WHERE id=?",
                (time.time(), str(error)[:500], review_id),
            )
        await query.edit_message_text(
            f"⚠️ 审核 #{review_id} 发布失败，可重试：\n{str(error)[:200]}",
            reply_markup=_review_keyboard(review_id, row["link"]),
        )
        return

    now = time.time()
    async with get_db() as conn:
        await conn.execute(
            """
            UPDATE pending_reviews
            SET status='published', updated_at=?, decided_at=?, decided_by=?,
                published_message_id=?, error=''
            WHERE id=?
            """,
            (now, now, update.effective_user.id, result["message_id"], review_id),
        )
    await _notify_chat_submitter(
        context.bot,
        row,
        f"✅ 你的投稿已通过审核并发布到频道。\n{result.get('link', '')}",
    )
    await query.edit_message_text(
        f"✅ 审核 #{review_id} 已发布\n{result.get('link', '')}",
        disable_web_page_preview=True,
    )


async def reject_review(update, context):
    query = update.callback_query
    if update.effective_user.id not in ADMIN_IDS:
        await _answer(query, "你没有审核权限", show_alert=True)
        return
    await _answer(query)

    try:
        review_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ 无效的审核记录")
        return

    now = time.time()
    async with get_db() as conn:
        cursor = await conn.execute(
            """
            UPDATE pending_reviews
            SET status='rejected', updated_at=?, decided_at=?, decided_by=?, error=''
            WHERE id=? AND status IN ('pending', 'failed')
            """,
            (now, now, update.effective_user.id, review_id),
        )
        changed = cursor.rowcount == 1
        cursor = await conn.execute("SELECT * FROM pending_reviews WHERE id=?", (review_id,))
        row = await cursor.fetchone()

    if row is None:
        await query.edit_message_text("❌ 审核记录不存在")
    elif changed:
        await _notify_chat_submitter(
            context.bot,
            row,
            "❌ 你的投稿未通过审核。如需了解原因，请联系频道管理员。",
        )
        await query.edit_message_text(f"❌ 审核 #{review_id} 已拒绝")
    else:
        await query.edit_message_text(f"ℹ️ 该投稿当前状态：{row['status']}")
