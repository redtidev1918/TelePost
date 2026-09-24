"""Public post media access.

This is deliberately scoped to ``published_posts``. It must never become a
second path into private review media. Telegram file_ids stay server-side; the
Mini App only receives bounded image bytes.
"""
from __future__ import annotations

import io
import json
import logging
import os
from dataclasses import dataclass
from typing import List, Optional

from ..application.hot import HotRepository
from services.review_service import MEDIA_DOWNLOAD_MAX_BYTES, ReviewService

logger = logging.getLogger(__name__)

_MEDIA_KINDS = {"photo", "video", "animation", "audio", "document"}


@dataclass(frozen=True)
class MediaEntry:
    kind: str
    file_id: str
    filename: str = ""


@dataclass(frozen=True)
class MediaResult:
    message_id: int
    index: int
    kind: str
    variant: str
    mime_type: str
    filename: str
    size: int
    data: bytes


class PostMediaError(Exception):
    code = "post_media_error"
    http_status = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class PostMediaNotFoundError(PostMediaError):
    code = "post_not_found"
    http_status = 404


class PostMediaUnavailableError(PostMediaError):
    code = "post_media_unavailable"
    http_status = 415


def parse_post_media(raw: object) -> List[MediaEntry]:
    """Decode the historical compact ``kind:file_id[:filename]`` storage."""
    if raw is None:
        return []
    if isinstance(raw, list):
        values = raw
    else:
        text = str(raw).strip()
        if not text:
            return []
        try:
            decoded = json.loads(text)
            values = decoded if isinstance(decoded, list) else [decoded]
        except (json.JSONDecodeError, TypeError, ValueError):
            values = [text]
    entries: List[MediaEntry] = []
    for value in values:
        if isinstance(value, dict):
            kind = str(value.get("kind") or value.get("type") or "photo")
            file_id = str(value.get("file_id") or "")
            filename = str(value.get("filename") or "")
        else:
            parts = str(value).split(":", 2)
            kind = parts[0] if len(parts) > 1 else "photo"
            file_id = parts[1] if len(parts) > 1 else parts[0]
            filename = parts[2] if len(parts) > 2 else ""
        kind = kind if kind in _MEDIA_KINDS else "photo"
        if file_id:
            entries.append(MediaEntry(kind, file_id, filename))
    return entries


class PostMediaService:
    def __init__(self, repository: Optional[HotRepository] = None):
        self.repository = repository or HotRepository()

    async def get(
        self, bot, message_id: int, index: int, variant: str = "preview"
    ) -> MediaResult:
        if variant not in {"thumbnail", "preview", "original"}:
            raise PostMediaError("invalid variant")
        post = await self.repository.get(int(message_id))
        if post is None:
            raise PostMediaNotFoundError("帖子不存在")
        media = parse_post_media(post.file_ids)
        if index < 0 or index >= len(media):
            raise PostMediaNotFoundError("媒体不存在")
        entry = media[index]
        if not entry.file_id:
            raise PostMediaNotFoundError("媒体 file_id 不存在")

        tg_file = await bot.get_file(entry.file_id)
        size = int(getattr(tg_file, "file_size", 0) or 0)
        if size > MEDIA_DOWNLOAD_MAX_BYTES:
            raise PostMediaUnavailableError("媒体超过安全下载大小")
        buffer = io.BytesIO()
        await tg_file.download_to_memory(buffer)
        data = buffer.getvalue()
        if not data or len(data) > MEDIA_DOWNLOAD_MAX_BYTES:
            raise PostMediaUnavailableError("媒体不可用或超过安全下载大小")

        remote_path = getattr(tg_file, "file_path", "") or ""
        filename = (
            entry.filename or remote_path.rsplit("/", 1)[-1] or f"media-{index}"
        )
        if variant == "original":
            mime_type = ReviewService._mime_for(entry.kind, filename, data)
            return MediaResult(int(message_id), index, entry.kind, variant,
                               mime_type, filename, len(data), data)

        image_data, mime_type = ReviewService._image_preview(data, variant)
        return MediaResult(
            int(message_id), index, "image", variant, mime_type,
            f"{os.path.splitext(filename)[0]}.jpg", len(image_data), data=image_data,
        )
