"""Submission review queue for API and Telegram chat submissions.

Uploads are staged in a private Telegram review chat.  The database keeps
Telegram ``file_id`` values rather than local paths, so pending reviews survive
Fly Machine restarts without retaining the original files on disk.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
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
from handlers.publish import (
    _file_id_of,
    publish_from_file_ids,
    reclassify_oversized_photos,
    PHOTO_MAX_BYTES as _PHOTO_MAX_BYTES,
)
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
# 审核群相册每组媒体数（Telegram 上限 10；可通过环境变量下调，小内存机器上
# 一次性打包太多大图会让 bot 进程 RSS 飙升、健康检查失败甚至 OOM，512 MiB
# 机型建议 4~5；相册发送失败时会自动降级为逐张发送兜底）。
REVIEW_ALBUM_SIZE = max(1, min(10, int(os.getenv("REVIEW_ALBUM_SIZE", "5"))))
# 待审核记录默认永久保留，保证升级后不意外删除部署者的既有队列。受限部署可显式
# 设置天数；清理时只删除 Telegram 审核群预览并把记录标记为 expired，不直接抹掉审计。
PENDING_REVIEW_RETENTION_DAYS = max(
    0, int(os.getenv("PENDING_REVIEW_RETENTION_DAYS", "0"))
)
PENDING_REVIEW_CLEANUP_BATCH_SIZE = max(
    1, min(200, int(os.getenv("PENDING_REVIEW_CLEANUP_BATCH_SIZE", "100")))
)
# Telegram 图片单张上限（含相册）10 MiB，超过按文档发送；与频道发布共用同一阈值
# （handlers.publish.PHOTO_MAX_BYTES），保留别名供本模块其余处引用。
PHOTO_MAX_BYTES = _PHOTO_MAX_BYTES
# 已发布作品在 N 秒内被同一 idempotency_key 再次投递时视为重复（PixivFlow 去重失效
# 会把已发布作品重新投进审核群，造成"审核完了又转发"），跳过而不重建审核记录。
PUBLISHED_DEDUP_WINDOW_SECONDS = 7 * 86400
# "重抓/换一张" 的 run-once 串行跑完所有启用 schedule，每个受 plan.timeout（生产为
# 1800s）watchdog 保护，2 个 schedule 最坏约 3600s；这里给足余量，避免把仍在下载的
# 重抓进程提前掐断误报 "timed out"。
REFETCH_TIMEOUT_SECONDS = 3900


def _review_keyboard(
    review_id: int,
    link: str = "",
    *,
    spoiler: bool = False,
    source: str = "api",
    pixiv_id: str = "",
) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton("✅ 发布到频道", callback_data=f"review_approve:{review_id}"),
        InlineKeyboardButton("❌ 拒绝", callback_data=f"review_reject:{review_id}"),
    ]]
    # 审核员可在发布前决定频道遮罩；初始值沿用投稿者设置。
    rows.append([
        InlineKeyboardButton(
            f"🔇 遮罩：{'开' if spoiler else '关'}",
            callback_data=f"review_spoiler:{review_id}",
        ),
    ])
    # 仅 Pixiv 自动投稿（HTTP API + 携带 pixivId）提供"重抓/换一张"。
    if source == "api" and pixiv_id:
        rows[-1].append(
            InlineKeyboardButton(
                "🔄 重抓/换一张",
                callback_data=f"review_refetch:{review_id}",
            )
        )
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


_PIXIV_ID_RE = re.compile(r"pixiv\.net/(?:artworks/|novel/show\.php\?id=)(\d+)")


def _pixiv_id_from_link(link: str) -> str:
    """Extract the Pixiv work id from a Pixiv link ('' if not a Pixiv link)."""
    if not link:
        return ""
    m = _PIXIV_ID_RE.search(link)
    return m.group(1) if m else ""


async def _find_review(idempotency_key: str):
    # 幂等：
    # - pending / failed：仍在途（可重试），命中同一 key 时复用原记录；
    # - published 且最近（PUBLISHED_DEDUP_WINDOW_SECONDS 内）：视为重复投递，返回
    #   已发布记录让调用方跳过——防止 PixivFlow 去重失效时把已发布作品再次投进审核群
    #   造成"审核完了又转发"；
    # - rejected / 更早的 published：不阻断，允许创建新审核记录（换一张 / 重新考虑）。
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT * FROM pending_reviews "
            "WHERE idempotency_key=? AND ("
            "  status IN ('pending', 'failed')"
            "  OR (status='published' AND decided_at >= ?)"
            ")",
            (idempotency_key, time.time() - PUBLISHED_DEDUP_WINDOW_SECONDS),
        )
        return await cursor.fetchone()


async def _delete_messages(bot, message_ids):
    for message_id in message_ids:
        try:
            await bot.delete_message(chat_id=REVIEW_CHAT_ID, message_id=message_id)
        except Exception:
            logger.debug("清理审核群预览消息失败: %s", message_id, exc_info=True)


def _review_message_ids(row) -> list[int]:
    """Return every Telegram message owned by a review, without duplicates."""
    try:
        message_ids = [int(value) for value in json.loads(row["review_message_ids"] or "[]")]
    except (TypeError, ValueError, json.JSONDecodeError):
        message_ids = []
    control_message_id = row["control_message_id"]
    if control_message_id:
        message_ids.append(int(control_message_id))
    return list(dict.fromkeys(message_ids))


async def expire_stale_reviews(bot, *, now: Optional[float] = None) -> int:
    """Expire stale pending reviews and remove their Telegram review messages.

    The SQLite row is retained as a small audit record and is removed later by
    ``REVIEW_RETENTION_DAYS``.  Claiming rows as ``expired`` before Telegram I/O
    prevents a concurrent approval from publishing an item while it is being
    cleaned up.
    """
    if PENDING_REVIEW_RETENTION_DAYS <= 0:
        return 0

    current_time = time.time() if now is None else now
    cutoff = current_time - PENDING_REVIEW_RETENTION_DAYS * 86400
    rows = []
    async with get_db() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        cursor = await conn.execute(
            "SELECT * FROM pending_reviews "
            "WHERE status='pending' AND created_at < ? "
            "ORDER BY created_at ASC LIMIT ?",
            (cutoff, PENDING_REVIEW_CLEANUP_BATCH_SIZE),
        )
        candidates = await cursor.fetchall()
        for row in candidates:
            cursor = await conn.execute(
                "UPDATE pending_reviews "
                "SET status='expired', updated_at=?, decided_at=?, "
                "error=? WHERE id=? AND status='pending'",
                (
                    current_time,
                    current_time,
                    f"pending review expired after {PENDING_REVIEW_RETENTION_DAYS} days",
                    row["id"],
                ),
            )
            if cursor.rowcount == 1:
                rows.append(row)

    for row in rows:
        await _delete_messages(bot, _review_message_ids(row))
        await _notify_chat_submitter(
            bot,
            row,
            f"⌛ 你的投稿超过 {PENDING_REVIEW_RETENTION_DAYS} 天未审核，已自动过期。",
        )

    if rows:
        logger.info(
            "已过期并清理 %d 条待审核投稿（保留 %d 天）",
            len(rows),
            PENDING_REVIEW_RETENTION_DAYS,
        )
    return len(rows)


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
        # attach=True is REQUIRED for media groups: without it python-telegram-bot
        # drops the "media" field of each InputMedia (no attach:// URI), and
        # Telegram answers "Can't parse inputmedia: media not found".
        open_handles = []
        try:
            media_group = []
            for index, item in enumerate(chunk):
                handle = open(item["path"], "rb")
                open_handles.append(handle)
                media = InputFile(
                    handle,
                    filename=item["filename"],
                    read_file_handle=False,
                    attach=True,
                )
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
    RetryAfter / flood control. Wait out RetryAfter with exponential backoff
    (cap 60 s per pause) so a longer flood window still recovers instead of
    failing the whole submission.
    """
    last_error = None
    for attempt in range(REVIEW_PREVIEW_MAX_ATTEMPTS):
        try:
            # A factory is required here: an awaited coroutine cannot be
            # reused after RetryAfter. It also reopens local files per retry.
            return await send_factory()
        except RetryAfter as exc:
            last_error = exc
            wait = min(
                (_retry_after_seconds(exc) * (2 ** attempt)) + 1.0,
                60.0,
            )
            logger.warning(
                "审核预览触发 Telegram 限流，等待 %.1fs 后重试（第 %d/%d 次）",
                wait, attempt + 1, REVIEW_PREVIEW_MAX_ATTEMPTS,
            )
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

    Oversized photos (> PHOTO_MAX_BYTES) are reclassified as documents before
    partitioning: Telegram's photo uploads (single or media group) reject
    files above 10 MiB, while documents accept up to 50 MiB. Without this a
    single large original PNG page would fail the whole review staging.
    """
    # 超大原图（>9.5 MiB）Telegram 无法作为照片发送，改按文档投递；
    # 与频道发布共用 handlers.publish.reclassify_oversized_photos。
    if local:
        items[:] = reclassify_oversized_photos(
            items, max_bytes=PHOTO_MAX_BYTES
        )

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
    target_id="",
):
    now = time.time()
    review_id = None
    for _attempt in range(2):
        try:
            async with get_db() as conn:
                cursor = await conn.execute(
                    """
                    INSERT INTO pending_reviews (
                        idempotency_key, source, status, user_id, username, title, tags, note, link,
                        anonymous, spoiler, media_json, documents_json, review_chat_id,
                        review_message_ids, target_id, created_at, updated_at
                    ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        target_id,
                        now,
                        now,
                    ),
                )
                review_id = cursor.lastrowid
            break
        except aiosqlite.IntegrityError:
            # 同 key 已存在：pending/failed 复用原记录（新预览随后被清理）；
            # 最近发布的视为重复投递直接跳过；rejected/更早的 published 不阻断——
            # 删除旧记录与旧预览后重试插入，新预览消息继续保留复用。
            async with get_db() as conn:
                cursor = await conn.execute(
                    "SELECT * FROM pending_reviews WHERE idempotency_key=?",
                    (idempotency_key,),
                )
                existing = await cursor.fetchone()
            if existing is None:
                raise
            if existing["status"] in ("pending", "failed"):
                await _delete_messages(bot, review_message_ids)
                return _result_from_row(existing)
            if (
                existing["status"] == "published"
                and (existing["decided_at"] or 0) >= time.time() - PUBLISHED_DEDUP_WINDOW_SECONDS
            ):
                await _delete_messages(bot, review_message_ids)
                logger.info(
                    "跳过已发布作品的重复投递: idempotency_key=%s review_id=%s",
                    idempotency_key, existing["id"],
                )
                return _result_from_row(existing)
            await _delete_messages(bot, json.loads(existing["review_message_ids"] or "[]"))
            if existing["control_message_id"]:
                await _delete_messages(bot, [existing["control_message_id"]])
            async with get_db() as conn:
                await conn.execute("DELETE FROM pending_reviews WHERE id=?", (existing["id"],))
            continue
    if review_id is None:
        raise RuntimeError("创建审核记录失败：幂等键冲突且无法覆盖旧记录")

    control_text = (
        f"🕵️ 投稿待审核 #{review_id}\n"
        f"投稿方式：{_source_label(source)}\n"
        f"投稿人：{username}\n"
        f"标题：{title or '（无）'}\n"
        f"标签：{tags or '（无）'}\n"
        f"文件：{len(media)} 个媒体 / {len(documents)} 个文档"
    )
    try:
        # 控制消息同样走限流退避包装：它是紧跟在媒体相册后的文本发送，
        # 恰好落在 Telegram flood 窗口内；裸 send_message 用 PTB 默认 5s
        # read 超时，Telegram 慢回复（flood 排队/网络抖动）会 ReadTimeout
        # 导致整条投稿回滚（预览已全部发出却被删），客户端只见 502。
        control = await _send_preview_throttled(
            lambda: bot.send_message(
                chat_id=REVIEW_CHAT_ID,
                text=control_text,
                reply_to_message_id=(
                    review_message_ids[-1]
                    if REVIEW_PREVIEW_THREAD and review_message_ids
                    else None
                ),
                reply_markup=_review_keyboard(
                    review_id,
                    link,
                    spoiler=bool(spoiler),
                    source=source,
                    pixiv_id=_pixiv_id_from_link(link or ""),
                ),
                disable_web_page_preview=True,
                **_review_timeout_kwargs(),
            )
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
    target_id="",
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
            target_id=target_id,
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
    target_id="",
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
            target_id=target_id,
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


async def _load_review_for_action(query, review_id):
    """Fetch a pending/failed review row for a reviewer action."""
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT * FROM pending_reviews WHERE id=?", (review_id,)
        )
        return await cursor.fetchone()


async def toggle_review_spoiler(update, context):
    """审核员在发布前翻转该投稿的频道遮罩（初始值沿用投稿者设置）。"""
    query = update.callback_query
    if update.effective_user.id not in ADMIN_IDS:
        await _answer(query, "你没有审核权限", show_alert=True)
        return

    try:
        review_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await _answer(query, "无效的审核记录", show_alert=True)
        return

    async with get_db() as conn:
        cursor = await conn.execute(
            """
            UPDATE pending_reviews SET spoiler = 1 - COALESCE(spoiler, 0), updated_at=?
            WHERE id=? AND status IN ('pending', 'failed')
            """,
            (time.time(), review_id),
        )
        flipped = cursor.rowcount == 1
        cursor = await conn.execute(
            "SELECT spoiler, link, source FROM pending_reviews WHERE id=?",
            (review_id,),
        )
        row = await cursor.fetchone()

    if row is None:
        await _answer(query, "审核记录不存在", show_alert=True)
        return
    if not flipped:
        await _answer(query, "该投稿已处理，无法修改遮罩", show_alert=True)
        return

    new_spoiler = bool(row["spoiler"])
    await _answer(query, f"遮罩已{'开启' if new_spoiler else '关闭'}")
    try:
        await query.edit_message_reply_markup(
            reply_markup=_review_keyboard(
                review_id,
                row["link"],
                spoiler=new_spoiler,
                source=row["source"],
                pixiv_id=_pixiv_id_from_link(row["link"] or ""),
            )
        )
    except Exception:
        logger.debug("刷新审核键盘失败（遮罩已入库）: review_id=%s", review_id)


def _run_pixivflow_refetch(target_id: str = "") -> subprocess.CompletedProcess:
    """触发 PixivFlow 立即重跑；已下载作品自动跳过并选取下一张。

    使用 `run-once`（一次性命令，跑完即退出），而不是 `scheduler run`（守护进程
    别名，永不退出，会在这里超时）。指定 target_id 时只重跑产生该审核的那一个
    target（"换一张"秒级），否则重跑全部启用计划。
    """
    config_path = os.getenv("PIXIVFLOW_CONFIG", "")
    command = ["pixivflow", "run-once"]
    if config_path:
        command += ["--config", config_path]
    if target_id:
        command += ["--target", target_id]
    logger.info("审核群触发 PixivFlow 重抓: %s", " ".join(command))
    return subprocess.run(
        command, cwd="/app", timeout=REFETCH_TIMEOUT_SECONDS, capture_output=True, text=True
    )


async def refetch_review(update, context):
    """审核员点"重抓/换一张"：后台触发 PixivFlow 重跑，新作品会作为新审核稿进入队列。"""
    query = update.callback_query
    if update.effective_user.id not in ADMIN_IDS:
        await _answer(query, "你没有审核权限", show_alert=True)
        return

    try:
        review_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await _answer(query, "无效的审核记录", show_alert=True)
        return

    if os.getenv("PIXIVFLOW_ENABLED", "false").strip().lower() not in {
        "true",
        "1",
        "yes",
    }:
        await _answer(query, "PixivFlow 未启用，无法重抓", show_alert=True)
        return

    row = await _load_review_for_action(query, review_id)
    if row is None:
        await _answer(query, "审核记录不存在", show_alert=True)
        return

    target_id = (row["target_id"] or "").strip() if "target_id" in row.keys() else ""

    # 立刻给审核员反馈，下载在后台进行，完成后新稿会自行进队列。
    await _answer(
        query,
        "已触发重抓，新作品下载投递后会作为新审核稿进入本群（已发布的旧稿不受影响）。",
        show_alert=True,
    )
    try:
        await context.bot.send_message(
            chat_id=REVIEW_CHAT_ID,
            text=f"🔄 审核 #{review_id} 由管理员触发重抓，PixivFlow 正在后台重新下载，请稍候……",
        )
    except Exception:
        logger.debug("发送重抓提示失败", exc_info=True)

    async def _do_refetch():
        try:
            proc = await asyncio.to_thread(_run_pixivflow_refetch, target_id)
            if proc.returncode == 0:
                tail = (proc.stdout or "")[-300:]
                logger.info("PixivFlow 重抓完成: %s", tail)
                await context.bot.send_message(
                    chat_id=REVIEW_CHAT_ID,
                    text="✅ 重抓完成，新投稿已进入审核队列（无新作品时会提示空结果）。",
                )
            else:
                tail = ((proc.stderr or "") + (proc.stdout or ""))[-300:]
                logger.warning("PixivFlow 重抓失败 code=%s: %s", proc.returncode, tail)
                await context.bot.send_message(
                    chat_id=REVIEW_CHAT_ID,
                    text=f"⚠️ 重抓异常退出（{proc.returncode}）：{tail[:200]}",
                )
        except Exception as exc:
            logger.warning("PixivFlow 重抓任务异常: %s", exc, exc_info=True)
            try:
                await context.bot.send_message(
                    chat_id=REVIEW_CHAT_ID,
                    text=f"⚠️ 重抓任务出错：{str(exc)[:200]}",
                )
            except Exception:
                pass

    asyncio.create_task(_do_refetch())


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
            reply_markup=_review_keyboard(
                review_id,
                row["link"],
                spoiler=bool(row["spoiler"]),
                source=row["source"],
                pixiv_id=_pixiv_id_from_link(row["link"] or ""),
            ),
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
