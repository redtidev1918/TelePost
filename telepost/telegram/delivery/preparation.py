"""Canonical, decode-budgeted media preparation.

Every local-photo path (review staging, direct/channel publishing, discussion
publishing and replay) reaches this module before Telegram I/O.  Probing reads
only file metadata.  A photo whose estimated working set exceeds the configured
budget is never decoded: an optional preview is used, otherwise the immutable
original is sent as a document.

Pure filesystem + Pillow, no PTB imports.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from ...domain.delivery import LocalFile, MediaItem, MediaKind

logger = logging.getLogger(__name__)

#: Telegram photo (single and in-album) hard limit, with safety margin.
PHOTO_MAX_BYTES = int((10.0 - 0.5) * 1024 * 1024)
IMAGE_DECODE_BUDGET_BYTES = max(
    1, int(os.getenv("TELEPOST_IMAGE_DECODE_BUDGET_MB", "64"))
) * 1024 * 1024

_MODE_BYTES_PER_PIXEL = {
    "1": 1,
    "L": 1,
    "P": 1,
    "LA": 2,
    "RGB": 3,
    "RGBA": 4,
    "RGBa": 4,
    "CMYK": 4,
    "I": 4,
    "F": 4,
}


class PreparationDecision(str, Enum):
    PASS_THROUGH = "pass_through"
    SAFE_COMPRESS = "safe_compress"
    USE_PREVIEW = "use_preview"
    DOCUMENT_FALLBACK = "document_fallback"


@dataclass(frozen=True)
class MediaProbe:
    filesize: int
    format: str
    width: int
    height: int
    mode: str
    frames: int


@dataclass(frozen=True)
class MediaResourceEstimate:
    estimated_decode_bytes: int
    estimated_peak_bytes: int


@dataclass(frozen=True)
class PreparedMedia:
    original_source: str
    delivery_source: str
    kind: MediaKind
    reason: PreparationDecision
    temporary: bool = False
    decision: Optional[Dict[str, Any]] = None


def log_media_decision(payload: Dict[str, Any]) -> None:
    """Emit the single structured decision line for one classified item."""
    logger.info("media decision %s", json.dumps(payload, ensure_ascii=False,
                                                default=str))


def _decision_payload(path: str, *, size: int, decision: str, reason: str,
                      probe: Optional[MediaProbe] = None,
                      estimate: Optional[MediaResourceEstimate] = None,
                      policy: Optional["MediaPreparationPolicy"] = None,
                      delivery: Optional[str] = None,
                      preview_available: bool = False) -> Dict[str, Any]:
    mime = mimetypes.guess_type(path)[0] or ""
    payload: Dict[str, Any] = {
        "filename": os.path.basename(path),
        "mime": mime,
        "file_bytes": size,
        "width": probe.width if probe else None,
        "height": probe.height if probe else None,
        "mode": probe.mode if probe else None,
        "estimated_decode_bytes": (
            estimate.estimated_decode_bytes if estimate else None
        ),
        "decode_budget_bytes": (
            policy.decode_budget_bytes if policy else IMAGE_DECODE_BUDGET_BYTES
        ),
        "telegram_photo_limit": policy.max_bytes if policy else PHOTO_MAX_BYTES,
        "preview_available": preview_available,
        "decision": decision,
        "reason": reason,
        "original_bytes": size,
    }
    if delivery and delivery != path:
        try:
            payload["prepared_bytes"] = os.stat(delivery).st_size
        except OSError:
            pass
    return payload


def probe_image(path: str) -> MediaProbe:
    """Read image header metadata without materializing pixels."""
    from PIL import Image

    size = os.stat(path).st_size
    with Image.open(path) as image:
        width, height = image.size
        return MediaProbe(
            filesize=size,
            format=str(image.format or ""),
            width=int(width),
            height=int(height),
            mode=str(image.mode or ""),
            frames=max(1, int(getattr(image, "n_frames", 1) or 1)),
        )


def estimate_resources(probe: MediaProbe) -> MediaResourceEstimate:
    pixels = probe.width * probe.height
    decode = pixels * _MODE_BYTES_PER_PIXEL.get(probe.mode, 4)
    conversion = 0 if probe.mode in {"RGB", "L"} else pixels * 3
    return MediaResourceEstimate(decode, decode + conversion)


class MediaPreparationPolicy:
    def __init__(self, *, max_bytes: int = PHOTO_MAX_BYTES,
                 decode_budget_bytes: int = IMAGE_DECODE_BUDGET_BYTES):
        self.max_bytes = max_bytes
        self.decode_budget_bytes = decode_budget_bytes

    def prepare(self, path: str, *, preview_path: Optional[str] = None) -> PreparedMedia:
        try:
            size = os.stat(path).st_size
        except OSError:
            return self._fallback(path, preview_path, reason="compression_failed_fallback")

        # Telegram can accept this exact file as a photo; no Pillow import or
        # decode is needed. This also keeps normal operation working without PIL.
        if size <= self.max_bytes:
            payload = _decision_payload(
                path, size=size, decision="photo_passthrough",
                reason="already_within_limits", policy=self,
            )
            log_media_decision(payload)
            return PreparedMedia(path, path, MediaKind.PHOTO,
                                 PreparationDecision.PASS_THROUGH,
                                 decision=payload)

        try:
            probe = probe_image(path)
            estimate = estimate_resources(probe)
        except Exception:
            return self._fallback(path, preview_path)

        if probe.frames > 1 or estimate.estimated_peak_bytes > self.decode_budget_bytes:
            return self._fallback(
                path, preview_path, probe=probe, estimate=estimate,
                reason="decode_budget_exceeded",
            )

        derivative = compress_photo(path, self.max_bytes)
        if derivative:
            payload = _decision_payload(
                path, size=size, decision="safe_compress",
                reason="photo_size_exceeded_but_decode_within_budget",
                probe=probe, estimate=estimate, policy=self,
                delivery=derivative,
                preview_available=bool(preview_path),
            )
            log_media_decision(payload)
            return PreparedMedia(path, derivative, MediaKind.PHOTO,
                                 PreparationDecision.SAFE_COMPRESS, True,
                                 decision=payload)
        return self._fallback(
            path, preview_path, probe=probe, estimate=estimate,
            reason="compression_failed_fallback",
        )

    def _fallback(self, path: str, preview_path: Optional[str], *,
                  reason: str = "decode_budget_exceeded",
                  probe: Optional[MediaProbe] = None,
                  estimate: Optional[MediaResourceEstimate] = None
                  ) -> PreparedMedia:
        try:
            size = os.stat(path).st_size
        except OSError:
            size = 0
        if preview_path:
            try:
                if os.stat(preview_path).st_size <= self.max_bytes:
                    payload = _decision_payload(
                        path, size=size, decision="use_preview",
                        reason="preview_preferred"
                        if reason == "decode_budget_exceeded"
                        else "compression_failed_fallback",
                        probe=probe, estimate=estimate, policy=self,
                        delivery=preview_path, preview_available=True,
                    )
                    log_media_decision(payload)
                    return PreparedMedia(
                        path, preview_path, MediaKind.PHOTO,
                        PreparationDecision.USE_PREVIEW, decision=payload,
                    )
            except OSError:
                pass
        payload = _decision_payload(
            path, size=size, decision="document_fallback", reason=reason,
            probe=probe, estimate=estimate, policy=self,
            preview_available=bool(preview_path),
        )
        log_media_decision(payload)
        return PreparedMedia(path, path, MediaKind.DOCUMENT,
                             PreparationDecision.DOCUMENT_FALLBACK,
                             decision=payload)


def compress_photo(path: str, max_bytes: int) -> Optional[str]:
    """Create a bounded JPEG derivative; the original is never modified."""
    try:
        from PIL import Image
    except ImportError:
        return None

    derivative = ""
    try:
        with Image.open(path) as source:
            image = source
            converted = None
            # Shrink before RGB conversion so RGBA does not create a second
            # full-size pixel buffer. Only budget-approved images reach here.
            if max(image.size) > 4096:
                image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
            if image.mode not in ("RGB", "L"):
                converted = image.convert("RGB")
                image = converted
            try:
                for quality in (85, 70, 55):
                    fd, derivative = tempfile.mkstemp(
                        prefix="telepost-prepared-", suffix=".jpg",
                        dir=os.path.dirname(os.path.abspath(path)),
                    )
                    os.close(fd)
                    image.save(derivative, "JPEG", quality=quality)
                    if os.stat(derivative).st_size <= max_bytes:
                        return derivative
                    os.unlink(derivative)
                    derivative = ""
            finally:
                if converted is not None:
                    converted.close()
    except Exception as exc:  # decoding/IO failure → caller falls back to document
        logger.warning("图片压缩失败，回退为文档发送: %s", exc)
    if derivative:
        try:
            os.unlink(derivative)
        except OSError:
            pass
    return None


def cleanup_prepared(items: List[MediaItem]) -> None:
    for item in items:
        source = item.source
        if isinstance(source, LocalFile) and source.temporary:
            try:
                os.unlink(source.path)
            except OSError:
                pass


def cleanup_prepared_dicts(items: list) -> None:
    for item in items:
        if item.get("temporary"):
            try:
                os.unlink(item["path"])
            except OSError:
                pass


def reclassify_oversized_dicts(items: list, *,
                                max_bytes: int = PHOTO_MAX_BYTES,
                                use_preview: bool = False) -> tuple:
    """Legacy dict facade over the canonical preparation policy.

    Returns (items, decisions); decisions are the structured media-decision
    payloads (one per classified photo) for the durable media.prepared audit.
    """
    policy = MediaPreparationPolicy(max_bytes=max_bytes)
    out: list = []
    decisions: list = []
    for item in items:
        if item.get("kind") == "photo" and item.get("path"):
            prepared = policy.prepare(
                item["path"],
                preview_path=item.get("preview_path") if use_preview else None,
            )
            item = dict(item)
            item["path"] = prepared.delivery_source
            item["kind"] = prepared.kind.value
            item["preparation_reason"] = prepared.reason.value
            item["temporary"] = prepared.temporary
            item["original_path"] = prepared.original_source
            if prepared.reason is PreparationDecision.SAFE_COMPRESS:
                base = os.path.splitext(item.get("filename") or "image")[0]
                item["filename"] = f"{base}.jpg"
            elif prepared.reason is PreparationDecision.USE_PREVIEW:
                item["filename"] = os.path.basename(prepared.delivery_source)
            if prepared.decision is not None:
                decision = dict(prepared.decision)
                if item.get("filename"):
                    decision["filename"] = item["filename"]
                decisions.append(decision)
        out.append(item)
    return out, decisions


def reclassify_oversized(items: List[MediaItem], *,
                         max_bytes: int = PHOTO_MAX_BYTES) -> List[MediaItem]:
    """Return canonical prepared items without modifying original artifacts."""
    policy = MediaPreparationPolicy(max_bytes=max_bytes)
    out: List[MediaItem] = []
    for item in items:
        if item.kind is MediaKind.PHOTO:
            source = item.source
            if isinstance(source, LocalFile):
                prepared = policy.prepare(
                    source.path
                )
                filename = source.filename
                if prepared.reason is PreparationDecision.SAFE_COMPRESS:
                    filename = f"{os.path.splitext(filename)[0]}.jpg"
                item = MediaItem(
                    prepared.kind,
                    LocalFile(
                        prepared.delivery_source,
                        filename,
                        preview_path=source.preview_path,
                        original_path=prepared.original_source,
                        temporary=prepared.temporary,
                    ),
                    item.spoiler if prepared.kind is MediaKind.PHOTO else False,
                )
                if prepared.decision is not None:
                    payload = dict(prepared.decision)
                    if filename:
                        payload["filename"] = filename
                    log_media_decision(payload)
                else:
                    logger.info(
                        "图片准备决策=%s source=%s delivery=%s",
                        prepared.reason.value, prepared.original_source,
                        prepared.delivery_source,
                    )
        out.append(item)
    return out
