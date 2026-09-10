"""Pre-send media preparation driven by Telegram limits.

* oversized local photos are compressed in place to stay under the 10 MiB
  photo cap (preferred: they remain inline images in the channel);
* if compression is impossible (Pillow missing / undecodable) the item is
  reclassified as a document (documents accept up to 50 MiB);
* ``file_id`` media is Telegram-hosted and therefore never size-limited here.

Pure filesystem + Pillow, no PTB imports.
"""
from __future__ import annotations

import io
import logging
import os
from typing import List

from ...domain.delivery import MediaItem, MediaKind

logger = logging.getLogger(__name__)

#: Telegram photo (single and in-album) hard limit, with safety margin.
PHOTO_MAX_BYTES = int((10.0 - 0.5) * 1024 * 1024)


def compress_photo(path: str, max_bytes: int) -> bool:
    """Compress a local image in place to <= max_bytes (JPEG, step-down)."""
    try:
        from PIL import Image
    except ImportError:
        return False

    try:
        image = Image.open(path)
        try:
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            if max(image.size) > 4096:
                image.thumbnail((4096, 4096), Image.LANCZOS)
            for quality in (92, 85, 78, 70, 60):
                buf = io.BytesIO()
                image.save(buf, "JPEG", quality=quality,
                           optimize=True, progressive=True)
                if buf.tell() <= max_bytes:
                    tmp = path + ".compressed"
                    with open(tmp, "wb") as fh:
                        fh.write(buf.getvalue())
                    os.replace(tmp, path)
                    return True
            return False
        finally:
            image.close()
    except Exception as exc:  # decoding/IO failure → caller falls back to document
        logger.warning("图片压缩失败，回退为文档发送: %s", exc)
        return False


def reclassify_oversized_dicts(items: list, *,
                                max_bytes: int = PHOTO_MAX_BYTES) -> list:
    """Dict-item variant (``{"kind", "path", "filename"}``); mutates paths."""
    out: list = []
    for item in items:
        if item.get("kind") == "photo" and item.get("path"):
            path = item["path"]
            try:
                if os.path.getsize(path) > max_bytes:
                    if compress_photo(path, max_bytes):
                        base = os.path.splitext(item.get("filename") or "image")[0]
                        item = dict(item)
                        item["filename"] = f"{base}.jpg"
                    else:
                        item = dict(item)
                        item["kind"] = "document"
            except OSError:
                pass
        out.append(item)
    return out


def reclassify_oversized(items: List[MediaItem], *,
                         max_bytes: int = PHOTO_MAX_BYTES) -> List[MediaItem]:
    """Return a new list with oversized local photos compressed/reclassified."""
    out: List[MediaItem] = []
    for item in items:
        if item.kind is MediaKind.PHOTO:
            path = item.local_path
            if path:
                try:
                    if os.path.getsize(path) > max_bytes:
                        if compress_photo(path, max_bytes):
                            logger.info("图片压缩到 %d 字节内，仍按图片发送: %s",
                                        max_bytes, item.filename)
                            source = item.source
                            if source.filename and not source.filename.lower().endswith(".jpg"):
                                base = os.path.splitext(source.filename)[0]
                                # LocalFile is frozen; rebuild with .jpg name.
                                from ...domain.delivery import LocalFile
                                source = LocalFile(source.path, f"{base}.jpg")
                            item = MediaItem(item.kind, source, item.spoiler)
                        else:
                            logger.info("图片超过 %d 字节且无法压缩，按文档发送: %s",
                                        max_bytes, item.filename)
                            item = MediaItem(MediaKind.DOCUMENT, item.source, False)
                except OSError:
                    pass
        out.append(item)
    return out
