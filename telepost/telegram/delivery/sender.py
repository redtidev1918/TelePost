"""python-telegram-bot transport for the delivery engine.

Only this subpackage touches PTB send calls. It:

* builds ``InputMedia*`` / ``send_*`` keyword arguments from domain
  :class:`MediaItem` (file_id sent verbatim – Telegram chat submissions keep
  the zero-reupload ``file_id`` fast path; local files attach a file handle);
* closes local file handles after the send attempt;
* maps PTB ``NetworkError`` (response possibly lost) to the executor's
  :class:`NetworkFailure`, while every other PTB error remains a certain
  failure that the caller may fall back / roll back on;
* extracts confirmed :class:`DeliveredMessage` objects including the resulting
  Telegram ``file_id`` (and video/animation thumbnail file_id).
"""
from __future__ import annotations

import logging
from typing import List, Optional

from ...domain.delivery import DeliveredMessage, MediaItem, MediaKind
from .executor import NetworkFailure
from .planner import Batch

logger = logging.getLogger(__name__)

SEND_TIMEOUT_KWARG_KEYS = ("read_timeout", "write_timeout", "connect_timeout", "pool_timeout")


def timeout_kwargs(timeout: float) -> dict:
    return {
        "read_timeout": timeout,
        "write_timeout": timeout,
        "connect_timeout": min(timeout, 30.0),
        "pool_timeout": min(timeout, 30.0),
    }


def file_id_of(message) -> Optional[str]:
    """Extract the best file_id from a confirmed PTB Message."""
    for attr in ("photo", "video", "animation", "audio", "document"):
        value = getattr(message, attr, None)
        if value:
            # PTB exposes Message.photo as a tuple; older releases/mocks a list.
            if isinstance(value, (list, tuple)):
                return value[-1].file_id
            return value.file_id
    return None


def thumbnail_file_id_of(message) -> Optional[str]:
    for attr in ("video", "animation", "document", "audio"):
        value = getattr(message, attr, None)
        thumbnail = getattr(value, "thumbnail", None) or getattr(value, "thumb", None)
        if thumbnail and getattr(thumbnail, "file_id", ""):
            return thumbnail.file_id
    return None


def _to_delivered(message, kind: MediaKind, *, fallback_chat_id=None) -> DeliveredMessage:
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    if chat_id is None:
        chat_id = fallback_chat_id
    return DeliveredMessage(
        chat_id=chat_id,
        message_id=message.message_id,
        kind=kind,
        file_id=file_id_of(message),
        thumbnail_file_id=thumbnail_file_id_of(message),
        raw=message,
    )


def _local_input_file(path: str, filename: str, *, attach: bool):
    from telegram import InputFile

    return InputFile(
        open(path, "rb"),
        filename=filename,
        read_file_handle=False,
        attach=attach,
    )


def _close_handle(value) -> None:
    handle = getattr(value, "input_file_content", None)
    if handle and hasattr(handle, "close"):
        try:
            handle.close()
        except Exception:  # pragma: no cover - best effort
            pass


class PTBSender:
    """Executor ``Sender`` backed by a PTB ``Bot`` for one target chat."""

    def __init__(self, bot, chat_id, *, timeouts: Optional[dict] = None):
        self._bot = bot
        self._chat_id = chat_id
        self._timeouts = timeouts or {}

    # ---- construction helpers ---------------------------------------
    def _media_ref(self, item: MediaItem, *, attach: bool):
        # Local files are always multipart attachments (attach://); PTB uses the
        # flag to serialize both album members and single-send arguments.
        source = item.source
        if item.is_local:
            return _local_input_file(source.path, source.filename or "file", attach=True)
        if item.is_file_id:
            return source.file_id
        return source.url  # RemoteUrl: Telegram fetches it itself

    def _input_media(self, item: MediaItem, caption: Optional[str]):
        from telegram import (
            InputMediaPhoto,
            InputMediaVideo,
            InputMediaDocument,
            InputMediaAudio,
        )

        media = self._media_ref(item, attach=True)
        parse = "HTML" if caption else None
        kind = item.kind
        if kind is MediaKind.PHOTO:
            return InputMediaPhoto(media=media, caption=caption, parse_mode=parse,
                                   has_spoiler=item.spoiler)
        if kind is MediaKind.VIDEO:
            return InputMediaVideo(media=media, caption=caption, parse_mode=parse,
                                   has_spoiler=item.spoiler)
        if kind is MediaKind.AUDIO:
            return InputMediaAudio(media=media, caption=caption, parse_mode=parse)
        return InputMediaDocument(
            media=media, caption=caption, parse_mode=parse,
            filename=item.filename or "file",
        )

    def _single_kwargs(self, item: MediaItem, caption: Optional[str]) -> dict:
        ref = self._media_ref(item, attach=False)
        kw = {"caption": caption, "parse_mode": "HTML" if caption else None}
        kind = item.kind
        if kind is MediaKind.PHOTO:
            return {"method": "send_photo", "photo": ref, **kw,
                    "has_spoiler": item.spoiler}
        if kind is MediaKind.VIDEO:
            return {"method": "send_video", "video": ref, **kw,
                    "has_spoiler": item.spoiler}
        if kind is MediaKind.ANIMATION:
            return {"method": "send_animation", "animation": ref, **kw,
                    "has_spoiler": item.spoiler}
        if kind is MediaKind.AUDIO:
            return {"method": "send_audio", "audio": ref, **kw}
        return {"method": "send_document", "document": ref,
                "filename": item.filename or "file", **kw}

    # ---- executor Sender interface ----------------------------------
    async def send_album(self, batch: Batch, *, reply_to: Optional[int],
                         caption: Optional[str]) -> List[DeliveredMessage]:
        from telegram.error import NetworkError

        media_group = [
            self._input_media(item, caption if i == 0 else None)
            for i, item in enumerate(batch.items)
        ]
        kwargs = dict(
            chat_id=self._chat_id,
            media=media_group,
            reply_to_message_id=reply_to,
            **self._timeouts,
        )
        try:
            try:
                messages = await self._bot.send_media_group(**kwargs)
            except NetworkError as exc:
                raise NetworkFailure(str(exc), original=exc) from exc
            finally:
                for member in media_group:
                    _close_handle(member.media)
        except NetworkFailure:
            raise
        except Exception:
            for member in media_group:
                _close_handle(member.media)
            raise
        return [_to_delivered(m, it.kind) for m, it in zip(messages, batch.items)]

    async def send_single(self, item: MediaItem, *, reply_to: Optional[int],
                          caption: Optional[str]) -> DeliveredMessage:
        from telegram.error import NetworkError

        kw = self._single_kwargs(item, caption)
        method_name = kw.pop("method")
        method = getattr(self._bot, method_name)
        try:
            try:
                message = await method(
                    chat_id=self._chat_id,
                    reply_to_message_id=reply_to,
                    **self._timeouts,
                    **kw,
                )
            except NetworkError as exc:
                raise NetworkFailure(str(exc), original=exc) from exc
        finally:
            for value in kw.values():
                _close_handle(value)
        return _to_delivered(message, item.kind)
