"""Telegram adapter that stages review previews in the private review chat.

Uploads incoming local files / file_ids as ordered albums+singles, applies
flood-control pacing/backoff, and returns the now-hosted Telegram ``file_id``
values. Implements :class:`telepost.application.review_queue.StagingPort`.

PTB lives here and in ``sender.py`` only; the application service never sees it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import List, Optional, Tuple

from telegram import (
    InputFile,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
)
from telegram.error import RetryAfter

from ..application.review_queue import pixiv_id_from_link
from .delivery.preparation import (
    PHOTO_MAX_BYTES,
    cleanup_prepared_dicts,
    reclassify_oversized_dicts as reclassify_oversized,
)
from .delivery.sender import file_id_of, timeout_kwargs
from . import review_keyboard

logger = logging.getLogger(__name__)


def _thumbnail_file_id(message) -> str:
    for attr in ("video", "animation", "document", "audio"):
        value = getattr(message, attr, None)
        thumbnail = getattr(value, "thumbnail", None) or getattr(value, "thumb", None)
        if thumbnail and getattr(thumbnail, "file_id", ""):
            return thumbnail.file_id
    return ""


def _review_message_ids(row) -> List[int]:
    import json
    try:
        ids = [int(v) for v in json.loads(row["review_message_ids"] or "[]")]
    except (TypeError, ValueError, json.JSONDecodeError):
        ids = []
    control = row["control_message_id"]
    if control:
        ids.append(int(control))
    return list(dict.fromkeys(ids))


def _retry_after_seconds(exc: RetryAfter) -> float:
    value = getattr(exc, "retry_after", 5) or 5
    if hasattr(value, "total_seconds"):
        value = value.total_seconds()
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return 5.0


class TelegramReviewStager:
    def __init__(self, bot, review_chat_id, *,
                 album_size: int = 5,
                 preview_interval: float = 0.75,
                 preview_timeout: float = 120.0,
                 preview_max_attempts: int = 5,
                 thread: bool = True,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep,
                 photo_max_bytes: int = PHOTO_MAX_BYTES,
                 chat_id_getter: Optional[Callable[[], object]] = None):
        self._bot = bot
        self._chat_id = review_chat_id
        self._chat_id_getter = chat_id_getter
        self._album_size = album_size
        self._interval = preview_interval
        self._max_attempts = preview_max_attempts
        self._thread = thread
        self._sleep = sleep
        self._photo_max_bytes = photo_max_bytes
        self._preview_timeout = preview_timeout
        self._timeouts = timeout_kwargs(preview_timeout)
        # Monotonic deadline of the CURRENT staging call, or None when the
        # caller does not require bounded staging (background repair / tests).
        self._staging_deadline: Optional[float] = None

    # ---- StagingPort ---------------------------------------------------
    async def stage_local(self, files, *, caption: str, spoiler: bool,
                          message_ids: Optional[List[int]] = None
                          ) -> Tuple[list, list, List[int], list]:
        ids: List[int] = message_ids if message_ids is not None else []
        prepared, decisions = reclassify_oversized(
            list(files), max_bytes=self._photo_max_bytes, use_preview=True
        )
        staged_items = []
        original_documents = []
        for index, item in enumerate(prepared):
            if item.get("preparation_reason") == "use_preview":
                preview = dict(item)
                preview["staging_only"] = True
                staged_items.append(preview)
                original_documents.append({
                    "kind": "document",
                    "path": item["original_path"],
                    "filename": files[index].get("filename")
                    or item.get("filename") or "image",
                })
            else:
                staged_items.append(item)
        staged_items.extend(original_documents)
        try:
            media, documents = await self._stage(
                staged_items, caption, spoiler, ids, local=True
            )
            return media, documents, ids, decisions
        finally:
            cleanup_prepared_dicts(prepared)

    async def stage_file_ids(self, media, documents, *, caption: str, spoiler: bool,
                             message_ids: Optional[List[int]] = None
                             ) -> Tuple[list, list, List[int]]:
        items = [
            {"kind": item["type"], "type": item["type"], "file_id": item["file_id"]}
            for item in media
        ] + [
            {"kind": "document", "type": "document", "file_id": item["file_id"],
             "filename": item.get("filename") or "file"}
            for item in documents
        ]
        ids: List[int] = message_ids if message_ids is not None else []
        staged_media, staged_documents = await self._stage(
            items, caption, spoiler, ids, local=False
        )
        return staged_media, staged_documents, ids

    @property
    def chat_id(self):
        # Late lookup of the module global so monkeypatched review-chat ids
        # (tests / runtime config) are always current.
        return self._chat_id_getter() if self._chat_id_getter else self._chat_id

    async def delete_preview_messages(self, message_ids: List[int]) -> None:
        for message_id in message_ids or []:
            try:
                await self._bot.delete_message(
                    chat_id=self.chat_id, message_id=message_id
                )
            except Exception:
                logger.debug("清理审核群预览消息失败: %s", message_id, exc_info=True)

    async def cleanup_files(self, files) -> None:
        directories = set()
        for item in files or []:
            for path in (item.get("path"), item.get("preview_path")):
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

    async def notify_reused(self, row) -> None:
        text = review_keyboard.reused_notice_text(row)
        kwargs = {
            "chat_id": self.chat_id, "text": text,
            "disable_web_page_preview": True, **self._timeouts_now(),
        }
        message_ids = _review_message_ids(row)
        if str(row["review_chat_id"]) == str(self.chat_id) and message_ids:
            kwargs.update(
                reply_to_message_id=message_ids[-1],
                allow_sending_without_reply=True,
            )
        await self._send_throttled(lambda: self._bot.send_message(**kwargs))
        logger.info("重复投稿已在审核群提示: review_id=%s status=%s",
                    row["id"], row["status"])

    async def send_control_message_id(self, *, review_id, command,
                                      preview_message_ids,
                                      media_count: int = 0,
                                      document_count: int = 0) -> int:
        text = review_keyboard.control_text(
            review_id=review_id, command=command,
            media_count=media_count, document_count=document_count,
        )
        reply_to = (
            preview_message_ids[-1]
            if self._thread and preview_message_ids else None
        )
        message = await self._send_throttled(lambda: self._bot.send_message(
            chat_id=self.chat_id,
            text=text,
            reply_to_message_id=reply_to,
            reply_markup=review_keyboard.review_keyboard(
                review_id,
                command.link,
                spoiler=bool(command.spoiler),
                source=command.source,
                pixiv_id=(command.pixiv_id
                          or pixiv_id_from_link(command.link or "")),
            ),
            disable_web_page_preview=True,
            **self._timeouts_now(),
        ))
        return message.message_id

    # ---- staging budget -------------------------------------------------
    def set_staging_deadline(self, deadline: Optional[float]) -> None:
        """Bound how long the CURRENT staging call may take (monotonic clock).

        Submissions are staged inline inside the HTTP request, while the routing
        layer answers 502 for anything slower than its own timeout
        (``ROUTER_TIMEOUT_SECONDS``). Unbounded retries therefore handed callers
        a synthetic 502 for a submission that was still running, which makes
        "not processed" and "processed and failed" indistinguishable. Every
        remaining Telegram call is capped to the remaining budget here, so the
        request always answers by itself before the router gives up.
        """
        self._staging_deadline = deadline

    def _budget_left(self) -> Optional[float]:
        if self._staging_deadline is None:
            return None
        return self._staging_deadline - time.monotonic()

    def _timeouts_now(self) -> dict:
        left = self._budget_left()
        if left is None or left >= self._preview_timeout:
            return self._timeouts
        return timeout_kwargs(max(left, 1.0))

    def _require_budget(self) -> None:
        left = self._budget_left()
        if left is not None and left <= 0:
            raise RuntimeError(
                "审核预览暂存超时（submission staging timeout），本次投稿未完成"
            )

    # ---- throttle ------------------------------------------------------
    async def _send_throttled(self, factory: Callable[[], Awaitable]):
        last_error = None
        for attempt in range(self._max_attempts):
            self._require_budget()
            try:
                return await factory()
            except RetryAfter as exc:
                last_error = exc
                # First backoff is exactly retry_after+1 (matches tests and
                # Telegram's flood guidance); later attempts grow geometrically.
                wait = min(
                    _retry_after_seconds(exc) + 1.0 if attempt == 0
                    else (_retry_after_seconds(exc) * (2 ** attempt)) + 1.0,
                    60.0,
                )
                logger.warning(
                    "审核预览触发 Telegram 限流，等待 %.1fs 后重试（第 %d/%d 次）",
                    wait, attempt + 1, self._max_attempts,
                )
            except Exception as exc:
                msg = str(exc).lower()
                if not any(k in msg for k in ("flood", "retry after", "too many")):
                    raise
                last_error = exc
                wait = 5.0
            if attempt + 1 >= self._max_attempts:
                raise last_error
            left = self._budget_left()
            if left is not None:
                # Never sleep past the staging deadline: the next attempt would
                # be rejected anyway, and the request must answer in time.
                wait = min(wait, max(left, 0.0))
            await self._sleep(wait)
        raise RuntimeError("审核预览重试次数已耗尽")

    # ---- media construction -------------------------------------------
    def _make_media_item(self, kind, media, *, caption, spoiler, filename=None):
        parse_mode = "HTML" if caption else None
        if kind == "photo":
            return InputMediaPhoto(media=media, caption=caption,
                                  parse_mode=parse_mode, has_spoiler=spoiler)
        if kind == "video":
            return InputMediaVideo(media=media, caption=caption,
                                  parse_mode=parse_mode, has_spoiler=spoiler)
        if kind == "audio":
            return InputMediaAudio(media=media, caption=caption,
                                   parse_mode=parse_mode)
        if filename:
            return InputMediaDocument(media=media, filename=filename,
                                      caption=caption, parse_mode=parse_mode)
        return InputMediaDocument(media=media, caption=caption,
                                  parse_mode=parse_mode)

    def _family(self, kind) -> Optional[str]:
        if kind in {"photo", "video"}:
            return "visual"
        if kind in {"audio", "document"}:
            return kind
        return None

    # ---- senders -------------------------------------------------------
    async def _local_album(self, chunk, caption, spoiler, reply_to):
        async def factory():
            # Handles MUST stay open until the awaited send finishes (PTB reads
            # them during serialization). Closing them in a finally before the
            # caller finishes awaiting produced "can't be used in await" races.
            handles = [open(item["path"], "rb") for item in chunk]
            group = []
            try:
                for index, item in enumerate(chunk):
                    media = InputFile(
                        handles[index], filename=item["filename"],
                        read_file_handle=False, attach=True,
                    )
                    group.append(self._make_media_item(
                        item["kind"], media,
                        caption=caption if index == 0 else None,
                        spoiler=spoiler, filename=item["filename"],
                    ))
                kwargs = dict(chat_id=self.chat_id, media=group, **self._timeouts_now())
                if reply_to is not None:
                    kwargs["reply_to_message_id"] = reply_to
                return await self._bot.send_media_group(**kwargs)
            finally:
                for handle in handles:
                    try:
                        handle.close()
                    except Exception:
                        logger.debug("关闭预览文件句柄失败", exc_info=True)
        return await self._send_throttled(factory)

    async def _local_single(self, item, caption, spoiler, reply_to):
        common = {"chat_id": self.chat_id, **self._timeouts_now()}
        if reply_to is not None:
            common["reply_to_message_id"] = reply_to
        kind = item["kind"]

        async def factory():
            handle = open(item["path"], "rb")
            try:
                media = InputFile(handle, filename=item["filename"],
                                  read_file_handle=False, attach=True)
                if kind == "photo":
                    return await self._bot.send_photo(
                        photo=media, caption=caption,
                        parse_mode="HTML" if caption else None,
                        has_spoiler=spoiler, **common)
                if kind == "video":
                    return await self._bot.send_video(
                        video=media, caption=caption,
                        parse_mode="HTML" if caption else None,
                        has_spoiler=spoiler, **common)
                if kind == "animation":
                    return await self._bot.send_animation(
                        animation=media, caption=caption,
                        parse_mode="HTML" if caption else None,
                        has_spoiler=spoiler, **common)
                if kind == "audio":
                    return await self._bot.send_audio(
                        audio=media, caption=caption,
                        parse_mode="HTML" if caption else None, **common)
                return await self._bot.send_document(
                    document=media, filename=item.get("filename"),
                    caption=caption, parse_mode="HTML" if caption else None,
                    **common)
            finally:
                handle.close()
        return await self._send_throttled(factory)

    async def _file_id_album(self, chunk, caption, spoiler, reply_to):
        group = [
            self._make_media_item(
                item["kind"], item["file_id"],
                caption=caption if index == 0 else None,
                spoiler=spoiler, filename=item.get("filename"),
            )
            for index, item in enumerate(chunk)
        ]
        kwargs = dict(chat_id=self.chat_id, media=group, **self._timeouts_now())
        if reply_to is not None:
            kwargs["reply_to_message_id"] = reply_to
        return await self._send_throttled(
            lambda: self._bot.send_media_group(**kwargs)
        )

    async def _file_id_single(self, item, caption, spoiler, reply_to):
        common = {"chat_id": self.chat_id, **self._timeouts_now()}
        if reply_to is not None:
            common["reply_to_message_id"] = reply_to
        kind = item["kind"]
        parse = "HTML" if caption else None
        kw = dict(caption=caption, parse_mode=parse, **common)

        if kind == "photo":
            return await self._send_throttled(
                lambda: self._bot.send_photo(
                    photo=item["file_id"], has_spoiler=spoiler, **kw)
            )
        if kind == "video":
            return await self._send_throttled(
                lambda: self._bot.send_video(
                    video=item["file_id"], has_spoiler=spoiler, **kw)
            )
        if kind == "animation":
            return await self._send_throttled(
                lambda: self._bot.send_animation(
                    animation=item["file_id"], has_spoiler=spoiler, **kw)
            )
        if kind == "audio":
            return await self._send_throttled(
                lambda: self._bot.send_audio(audio=item["file_id"], **kw)
            )
        return await self._send_throttled(
            lambda: self._bot.send_document(
                document=item["file_id"], filename=item.get("filename"), **kw)
        )

    # ---- core staging --------------------------------------------------
    async def _stage(self, items, caption, spoiler, message_ids, *, local):
        runs: List[tuple] = []
        for item in items:
            kind = item["kind"] if local else item["type"]
            family = self._family(kind)
            if (family is not None and runs and runs[-1][0] == family
                    and len(runs[-1][1]) < self._album_size):
                runs[-1][1].append(item)
            else:
                runs.append((family, [item]))

        staged_media: list = []
        staged_documents: list = []
        last_message_id = None
        album_index = 0

        for family, chunk in runs:
            reply_to = (
                last_message_id
                if (self._thread and last_message_id is not None) else None
            )
            is_album = family is not None and len(chunk) > 1
            chunk_caption = caption if album_index == 0 else None
            messages = None

            if is_album:
                if album_index > 0 and self._interval > 0:
                    await self._sleep(self._interval)
                try:
                    messages = await (
                        self._local_album(chunk, chunk_caption, spoiler, reply_to)
                        if local else
                        self._file_id_album(chunk, chunk_caption, spoiler, reply_to)
                    )
                except Exception as exc:
                    # TimedOut / network errors must never degrade to singles:
                    # Telegram may have accepted the album before the response
                    # was lost, and resending would duplicate it. RetryAfter is
                    # already handled (with backoff) inside _send_throttled.
                    from telegram.error import TimedOut
                    if isinstance(exc, TimedOut):
                        raise
                    logger.warning(
                        "审核相册发送失败（%s），降级为逐张发送 %d 个文件",
                        exc, len(chunk),
                    )
                    messages = None

            if is_album and messages is not None and len(messages) != len(chunk):
                for message in messages:
                    message_ids.append(message.message_id)
                raise RuntimeError(
                    f"返回消息数 {len(messages)} 与文件数 {len(chunk)} 不一致"
                )

            if messages is None:
                messages = []
                for within, item in enumerate(chunk):
                    if is_album:
                        if within > 0 and self._interval > 0:
                            await self._sleep(self._interval)
                    item_reply = reply_to if within == 0 else (
                        last_message_id
                        if (self._thread and last_message_id is not None) else None
                    )
                    single_caption = chunk_caption if within == 0 else None
                    single = await (
                        self._local_single(item, single_caption, spoiler, item_reply)
                        if local else
                        self._file_id_single(item, single_caption, spoiler, item_reply)
                    )
                    messages.append(single)
                    message_ids.append(single.message_id)
                    last_message_id = single.message_id
            else:
                for message in messages:
                    message_ids.append(message.message_id)
                last_message_id = messages[-1].message_id

            for message, item in zip(messages, chunk):
                fid = file_id_of(message)
                if not fid:
                    raise RuntimeError("审核群预览未返回 Telegram file_id")
                item_kind = item["kind"] if local else item["type"]
                if item.get("staging_only"):
                    continue
                if item_kind == "document":
                    staged_documents.append({
                        "file_id": fid,
                        "filename": item.get("filename") or "file",
                    })
                else:
                    staged = {"type": item_kind, "file_id": fid}
                    thumb = _thumbnail_file_id(message)
                    if thumb:
                        staged["thumbnail_file_id"] = thumb
                    staged_media.append(staged)

            album_index += 1

        return staged_media, staged_documents
