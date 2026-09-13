"""Canonical, decode-budgeted media preparation.

Every local-photo path (review staging, direct/channel publishing, discussion
publishing and replay) reaches this module before Telegram I/O.  The pipeline is:

    probe (header only, no pixel decode)
      -> decide the target kind from the PROBE + source metadata
      -> transform only when necessary (bounded, memory-safe)
      -> validate the FINAL artifact (bytes AND dimensions AND ratio AND format)
      -> re-classify from the FINAL artifact
      -> PreparedMedia with an explicit fallback reason

Telegram photo constraints, from https://core.telegram.org/bots/api#sendphoto :

* "The photo must be at most 10 MB in size."
* "The photo's width and height must not exceed 10000 in total."
* "Width and height ratio must be at most 20."

``sendMediaGroup`` re-uses the same photo pipeline (2-10 items, documents and
audio only alongside their own type); ``sendAnimation``/``sendDocument`` allow
"up to 50 MB in size".  Those numbers are the only source of truth here — a
dimension violation is *not* a reason to send a document, it is a reason to
produce a smaller photo.

Pure filesystem + Pillow, no PTB imports.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...domain.delivery import LocalFile, MediaItem, MediaKind

logger = logging.getLogger(__name__)

#: Telegram photo byte limit with a safety margin (see module docstring).
PHOTO_MAX_BYTES = int((10.0 - 0.5) * 1024 * 1024)
#: Telegram rejects a photo when width + height exceeds this sum, even when the
#: file is well under the byte limit ("Photo_invalid_dimensions").
PHOTO_MAX_DIMENSION_SUM = int(os.getenv("TELEPOST_PHOTO_MAX_DIMENSION_SUM", "10000"))
#: Telegram also rejects extreme panoramas: width/height ratio must be <= 20.
PHOTO_MAX_ASPECT_RATIO = float(os.getenv("TELEPOST_PHOTO_MAX_ASPECT_RATIO", "20"))
#: Long-edge cap for a photo derivative; keeps one encode bounded on a small box.
PHOTO_MAX_EDGE = int(os.getenv("TELEPOST_PHOTO_MAX_EDGE", "4096"))
#: sendAnimation / sendDocument: "Bots can currently send files ... of up to
#: 50 MB in size".
MEDIA_MAX_BYTES = int(float(os.getenv("TELEPOST_MEDIA_MAX_MB", "50")) * 1024 * 1024)
IMAGE_DECODE_BUDGET_BYTES = max(
    1, int(os.getenv("TELEPOST_IMAGE_DECODE_BUDGET_MB", "64"))
) * 1024 * 1024
#: How many images may be prepared at once. 1 = strictly sequential (low-memory
#: default, one decoded image in RAM at a time). Higher values only shorten wall
#: time; they never raise the per-image peak.
MEDIA_PREPARE_CONCURRENCY = max(
    1, int(os.getenv("TELEPOST_MEDIA_PREPARE_CONCURRENCY", "1"))
)

#: Formats Telegram accepts for a photo upload.
PHOTO_ARTIFACT_FORMATS = frozenset({"JPEG", "MPO", "PNG", "WEBP", "BMP"})
#: Formats Pillow can decode at a reduced scale (Image.draft), which is what
#: keeps the peak working set bounded for the huge-JPEG case.
DRAFT_DECODE_FORMATS = frozenset({"JPEG", "MPO"})
#: Quality ladder, highest first: never ship a lower quality than necessary.
JPEG_QUALITIES = (85, 75, 65, 55)
#: Long-edge ladder used after the dimension-derived cap.
PHOTO_EDGE_LADDER = (4096, 3200, 2560, 2048, 1600)
#: Marker substrings of the Telegram errors that mean "this photo is not a
#: legal photo" (dimensions / ratio / size). Only those justify one bounded
#: re-process; everything else is a real failure.
PHOTO_CONSTRAINT_ERROR_MARKERS = (
    "photo_invalid_dimensions",
    "invalid_dimensions",
    "photo dimensions",
    "width and height",
    "aspect ratio",
    "photo_invalid_ratio",
    "photo_invalid_size",
    "photo_too_big",
    "file is too big",
    "file is too large",
    "photo is too big",
    "image_process_failed",
)

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
    ANIMATION_PRESERVED = "animation_preserved"
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
class PhotoLimits:
    """The documented Telegram photo constraints plus the local decode budget."""

    max_bytes: int = PHOTO_MAX_BYTES
    max_dimension_sum: int = PHOTO_MAX_DIMENSION_SUM
    max_aspect_ratio: float = PHOTO_MAX_ASPECT_RATIO
    max_edge: int = PHOTO_MAX_EDGE
    decode_budget_bytes: int = IMAGE_DECODE_BUDGET_BYTES


@dataclass(frozen=True)
class MediaArtifact:
    """A concrete file on disk, exactly as Telegram would receive it.

    Classification is always decided from an artifact (source or derivative),
    never from a mixture of the two.
    """

    path: str
    size: int
    width: int
    height: int
    format: str

    @property
    def dimension_sum(self) -> int:
        return self.width + self.height

    @property
    def aspect_ratio(self) -> float:
        shorter = max(1, min(self.width, self.height))
        return max(self.width, self.height) / shorter

    def photo_violation(self, limits: PhotoLimits) -> Optional[str]:
        """Return the first violated photo constraint, or None when it is legal."""
        if self.format.upper() not in PHOTO_ARTIFACT_FORMATS:
            return "photo_format_unsupported"
        if self.dimension_sum > limits.max_dimension_sum:
            return "photo_dimensions_exceeded"
        if self.aspect_ratio > limits.max_aspect_ratio:
            return "photo_aspect_ratio_exceeded"
        if self.size > limits.max_bytes:
            return "photo_bytes_exceeded"
        return None


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
                      preview_available: bool = False,
                      final: Optional[MediaArtifact] = None,
                      target_kind: Optional[str] = None,
                      violation: Optional[str] = None,
                      fallback_reason: Optional[str] = None) -> Dict[str, Any]:
    """One log line that can reconstruct the whole classification."""
    mime = mimetypes.guess_type(path)[0] or ""
    if final is None and delivery and delivery != path:
        try:
            final = artifact_of(delivery)
        except Exception:
            final = None
    resized = bool(
        final is not None and probe is not None
        and (final.width, final.height) != (probe.width, probe.height)
    )
    payload: Dict[str, Any] = {
        "filename": os.path.basename(path),
        "mime": mime,
        "file_bytes": size,
        "width": probe.width if probe else None,
        "height": probe.height if probe else None,
        "mode": probe.mode if probe else None,
        "frames": probe.frames if probe else None,
        "original_format": probe.format if probe else None,
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
        # ---- final-artifact verdict ----
        "target_kind": target_kind,
        "final_bytes": final.size if final else None,
        "final_width": final.width if final else None,
        "final_height": final.height if final else None,
        "final_format": final.format if final else None,
        "resized": resized,
        "violation": violation,
        "fallback_reason": fallback_reason,
    }
    if final is not None and final.path != path:
        payload["prepared_bytes"] = final.size
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


def artifact_of(path: str, *, probe: Optional[MediaProbe] = None) -> MediaArtifact:
    """Header-only view of a file as Telegram would receive it."""
    probe = probe or probe_image(path)
    return MediaArtifact(
        path=path,
        size=os.stat(path).st_size,
        width=probe.width,
        height=probe.height,
        format=probe.format,
    )


def estimate_resources(probe: MediaProbe) -> MediaResourceEstimate:
    pixels = probe.width * probe.height
    decode = pixels * _MODE_BYTES_PER_PIXEL.get(probe.mode, 4)
    conversion = 0 if probe.mode in {"RGB", "L"} else pixels * 3
    return MediaResourceEstimate(decode, decode + conversion)


def is_photo_constraint_error(error: object) -> bool:
    """True when a Telegram error means "this file is not a legal photo"."""
    text = str(error or "").lower()
    return any(marker in text for marker in PHOTO_CONSTRAINT_ERROR_MARKERS)


def _unlink(path: Optional[str]) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def _artifact_is_compliant(path: str, limits: PhotoLimits) -> bool:
    try:
        return artifact_of(path).photo_violation(limits) is None
    except Exception:
        return False


class MediaPreparationPolicy:
    def __init__(self, *, max_bytes: int = PHOTO_MAX_BYTES,
                 decode_budget_bytes: int = IMAGE_DECODE_BUDGET_BYTES,
                 limits: Optional[PhotoLimits] = None):
        self.limits = limits or PhotoLimits(
            max_bytes=max_bytes, decode_budget_bytes=decode_budget_bytes
        )
        self.max_bytes = self.limits.max_bytes
        self.decode_budget_bytes = self.limits.decode_budget_bytes

    def prepare(self, path: str, *, preview_path: Optional[str] = None) -> PreparedMedia:
        try:
            size = os.stat(path).st_size
        except OSError:
            return self._fallback(path, preview_path, reason="source_unreadable")

        try:
            probe = probe_image(path)
        except Exception:
            probe = None
        estimate = estimate_resources(probe) if probe is not None else None

        # ---- 1. animations are never photo material -------------------
        if probe is not None and probe.frames > 1:
            return self._animated(path, size=size, probe=probe, estimate=estimate)

        # ---- 2. header-only decision on the immutable original --------
        if probe is None:
            if size <= self.max_bytes:
                # Unreadable header: keep the historical lenient pass-through
                # (also keeps normal operation working without PIL).
                return self._emit(
                    path, path, MediaKind.PHOTO, PreparationDecision.PASS_THROUGH,
                    size=size, decision="photo_passthrough",
                    reason="already_within_limits", target_kind=MediaKind.PHOTO.value,
                )
            return self._fallback(path, preview_path, reason="probe_failed")

        original = MediaArtifact(path, size, probe.width, probe.height, probe.format)
        violation = original.photo_violation(self.limits)
        if violation is None:
            return self._emit(
                path, path, MediaKind.PHOTO, PreparationDecision.PASS_THROUGH,
                size=size, decision="photo_passthrough",
                reason="already_within_limits", probe=probe, estimate=estimate,
                final=original, target_kind=MediaKind.PHOTO.value,
            )

        # ---- 3. bounded, memory-safe transform ------------------------
        if not self._decode_is_bounded(probe, estimate):
            # Peak working set cannot be bounded for this format (no reduced
            # decode available) — a routing decision, not a silent loss.
            return self._fallback(
                path, preview_path, probe=probe, estimate=estimate,
                reason="decode_budget_exceeded", violation=violation,
            )

        if violation == "photo_aspect_ratio_exceeded":
            # A uniform downscale preserves the ratio, so no compliant photo is
            # reachable without cropping the artwork.
            return self._fallback(
                path, preview_path, probe=probe, estimate=estimate,
                reason="photo_aspect_ratio_unfixable", violation=violation,
            )

        derivative = compress_photo(
            path, self.max_bytes, probe=probe, limits=self.limits
        )

        # ---- 4. re-classify from the FINAL artifact -------------------
        final: Optional[MediaArtifact] = None
        if derivative:
            try:
                final = artifact_of(derivative)
            except Exception:
                final = None
            if final is None or final.photo_violation(self.limits) is not None:
                _unlink(derivative)
                derivative, final = None, None
        if derivative and final is not None:
            reason = (
                "photo_size_exceeded_but_decode_within_budget"
                if violation == "photo_bytes_exceeded"
                else "photo_dimensions_too_large_resized"
            )
            return self._emit(
                path, derivative, MediaKind.PHOTO,
                PreparationDecision.SAFE_COMPRESS, size=size,
                decision="safe_compress", reason=reason, probe=probe,
                estimate=estimate, final=final,
                violation=violation, target_kind=MediaKind.PHOTO.value,
                preview_available=bool(preview_path), temporary=True,
            )
        return self._fallback(
            path, preview_path, probe=probe, estimate=estimate,
            reason="photo_transform_failed", violation=violation,
        )

    # ---- helpers -------------------------------------------------------
    def _decode_is_bounded(self, probe: MediaProbe,
                           estimate: MediaResourceEstimate) -> bool:
        if estimate.estimated_peak_bytes <= self.decode_budget_bytes:
            return True
        # Image.draft() drops DCT blocks before allocating pixels, so a huge
        # JPEG still decodes inside the budget at a reduced scale.
        return probe.format.upper() in DRAFT_DECODE_FORMATS

    def _animated(self, path: str, *, size: int, probe: MediaProbe,
                  estimate: MediaResourceEstimate) -> PreparedMedia:
        """Keep the animation: never route animated content through the JPEG ladder."""
        if size <= MEDIA_MAX_BYTES:
            return self._emit(
                path, path, MediaKind.ANIMATION,
                PreparationDecision.ANIMATION_PRESERVED, size=size,
                decision="animation_kept", reason="animated_never_static",
                probe=probe, estimate=estimate,
                target_kind=MediaKind.ANIMATION.value,
                final=MediaArtifact(path, size, probe.width, probe.height,
                                    probe.format),
            )
        return self._emit(
            path, path, MediaKind.DOCUMENT, PreparationDecision.DOCUMENT_FALLBACK,
            size=size, decision="document_fallback",
            reason="animation_exceeds_animation_byte_limit", probe=probe,
            estimate=estimate, target_kind=MediaKind.DOCUMENT.value,
            fallback_reason="animation_exceeds_animation_byte_limit",
        )

    def _emit(self, source: str, delivery: str, kind: MediaKind,
              decision_enum: PreparationDecision, *, size: int, decision: str,
              reason: str, probe: Optional[MediaProbe] = None,
              estimate: Optional[MediaResourceEstimate] = None,
              final: Optional[MediaArtifact] = None,
              preview_available: bool = False, violation: Optional[str] = None,
              fallback_reason: Optional[str] = None,
              target_kind: Optional[str] = None,
              temporary: bool = False) -> PreparedMedia:
        payload = _decision_payload(
            source, size=size, decision=decision, reason=reason, probe=probe,
            estimate=estimate, policy=self, delivery=delivery,
            preview_available=preview_available,
            final=final, target_kind=target_kind,
            violation=violation, fallback_reason=fallback_reason,
        )
        log_media_decision(payload)
        return PreparedMedia(source, delivery, kind, decision_enum, temporary,
                             decision=payload)

    def _fallback(self, path: str, preview_path: Optional[str], *,
                  reason: str = "decode_budget_exceeded",
                  probe: Optional[MediaProbe] = None,
                  estimate: Optional[MediaResourceEstimate] = None,
                  violation: Optional[str] = None) -> PreparedMedia:
        try:
            size = os.stat(path).st_size
        except OSError:
            size = 0
        if preview_path:
            try:
                if os.stat(preview_path).st_size <= self.max_bytes:
                    preview_probe = None
                    try:
                        preview_probe = probe_image(preview_path)
                    except Exception:
                        preview_probe = None
                    preview_final = None
                    if preview_probe is not None:
                        preview_final = MediaArtifact(
                            preview_path, os.stat(preview_path).st_size,
                            preview_probe.width, preview_probe.height,
                            preview_probe.format,
                        )
                    return self._emit(
                        path, preview_path, MediaKind.PHOTO,
                        PreparationDecision.USE_PREVIEW, size=size,
                        decision="use_preview",
                        reason="preview_preferred"
                        if reason == "decode_budget_exceeded"
                        else "compression_failed_fallback",
                        probe=probe, estimate=estimate, preview_available=True,
                        final=preview_final, violation=violation,
                        target_kind=MediaKind.PHOTO.value,
                    )
            except OSError:
                pass
        return self._emit(
            path, path, MediaKind.DOCUMENT, PreparationDecision.DOCUMENT_FALLBACK,
            size=size, decision="document_fallback", reason=reason, probe=probe,
            estimate=estimate, preview_available=bool(preview_path),
            violation=violation, fallback_reason=reason,
            target_kind=MediaKind.DOCUMENT.value,
        )


def _scaled_size(size: Tuple[int, int], cap: int) -> Tuple[int, int]:
    width, height = size
    longest = max(width, height)
    if cap >= longest:
        return width, height
    factor = cap / longest
    return max(1, int(width * factor)), max(1, int(height * factor))


def photo_dimension_caps(probe: MediaProbe, limits: PhotoLimits) -> List[int]:
    """Long-edge caps to try, largest (highest quality) first.

    The first cap is the largest one that still satisfies the documented
    dimension-sum rule, so a high-resolution-but-small-bytes image is resized
    just enough instead of being dropped to the smallest ladder step.
    """
    longest = max(probe.width, probe.height)
    first = min(longest, limits.max_edge)
    if probe.width + probe.height > limits.max_dimension_sum:
        allowed = (limits.max_dimension_sum * longest) // (probe.width + probe.height)
        first = min(first, max(1, allowed))
    ladder = [first]
    for cap in PHOTO_EDGE_LADDER:
        if cap < first and cap not in ladder:
            ladder.append(cap)
    return ladder


def _save_jpeg(image, directory: str, quality: int) -> Optional[str]:
    fd, derivative = tempfile.mkstemp(
        prefix="telepost-prepared-", suffix=".jpg", dir=directory
    )
    os.close(fd)
    try:
        image.save(derivative, "JPEG", quality=quality)
    except Exception as exc:
        logger.warning("图片压缩失败: %s", exc)
        _unlink(derivative)
        return None
    return derivative


def compress_photo(path: str, max_bytes: int, *,
                   probe: Optional[MediaProbe] = None,
                   limits: Optional[PhotoLimits] = None,
                   dimension_caps: Optional[Sequence[int]] = None) -> Optional[str]:
    """Bounded quality → dimension → format ladder; the original is never modified.

    Returns a JPEG derivative whose FINAL artifact satisfies every Telegram photo
    constraint (bytes, dimension sum, ratio, format), or ``None`` when no
    compliant derivative is reachable.  One image is decoded at a time and every
    intermediate buffer is released before the next attempt.
    """
    try:
        from PIL import Image
    except ImportError:
        return None

    limits = limits or PhotoLimits(max_bytes=max_bytes)
    if probe is None:
        try:
            probe = probe_image(path)
        except Exception:
            return None

    caps = list(dimension_caps) if dimension_caps else photo_dimension_caps(probe, limits)
    directory = os.path.dirname(os.path.abspath(path))
    for cap in caps:
        if cap < 1:
            continue
        try:
            source = Image.open(path)
        except Exception as exc:
            logger.warning("图片解码失败，回退为文档发送: %s", exc)
            return None
        try:
            if str(source.format or "").upper() in DRAFT_DECODE_FORMATS:
                # Reduced-scale JPEG decode: throw away DCT blocks first so the
                # peak working set follows the cap instead of the source size.
                # Over budget we ask for half the cap, which forces the decoder
                # into a power-of-two reduction instead of a full-size decode.
                draft_cap = cap
                if estimate_resources(probe).estimated_peak_bytes > limits.decode_budget_bytes:
                    draft_cap = max(1, cap // 2)
                source.draft("RGB", (draft_cap, draft_cap))
            converted = None
            resized = None
            try:
                image = source
                # Shrink before RGB conversion so RGBA does not create a second
                # full-size pixel buffer at the source resolution.
                if max(image.size) > cap:
                    resized = image.resize(
                        _scaled_size(image.size, cap), Image.Resampling.LANCZOS
                    )
                    image = resized
                if image.mode not in ("RGB", "L"):
                    converted = image.convert("RGB")
                    image = converted
                for quality in JPEG_QUALITIES:
                    derivative = _save_jpeg(image, directory, quality)
                    if derivative is None:
                        continue
                    if _artifact_is_compliant(derivative, limits):
                        return derivative
                    _unlink(derivative)
            finally:
                for buffer in (resized, converted):
                    if buffer is not None:
                        buffer.close()
        finally:
            source.close()
    return None


def retry_photo_derivative(path: str, *,
                           max_bytes: int = PHOTO_MAX_BYTES,
                           limits: Optional[PhotoLimits] = None) -> Optional[str]:
    """ONE bounded, more aggressive re-process for a photo Telegram rejected.

    Halves the long edge (still dimension-sum compliant) and walks the quality
    ladder from the top; returns a compliant derivative, or ``None``. The caller
    must try this at most once, then fall back to a document.
    """
    limits = limits or PhotoLimits(max_bytes=max_bytes)
    try:
        probe = probe_image(path)
    except Exception:
        return None
    longest = max(probe.width, probe.height)
    cap = max(1, longest // 2)
    if probe.width + probe.height > limits.max_dimension_sum:
        allowed = (limits.max_dimension_sum * longest) // (probe.width + probe.height)
        cap = min(cap, max(1, allowed))
    cap = min(cap, limits.max_edge)
    return compress_photo(
        path, limits.max_bytes, probe=probe, limits=limits, dimension_caps=[cap]
    )


def _prepare_many(entries: Sequence[Tuple[str, Optional[str]]],
                  policy: MediaPreparationPolicy) -> List[PreparedMedia]:
    """Prepare images one at a time (bounded concurrency), never a whole gallery."""
    if len(entries) > 1 and MEDIA_PREPARE_CONCURRENCY > 1:
        with ThreadPoolExecutor(max_workers=MEDIA_PREPARE_CONCURRENCY) as pool:
            return list(pool.map(
                lambda entry: policy.prepare(entry[0], preview_path=entry[1]),
                entries,
            ))
    return [
        policy.prepare(path, preview_path=preview) for path, preview in entries
    ]


def cleanup_prepared(items: List[MediaItem]) -> None:
    for item in items:
        source = item.source
        if isinstance(source, LocalFile) and source.temporary:
            _unlink(source.path)


def cleanup_prepared_dicts(items: list) -> None:
    for item in items:
        if item.get("temporary"):
            _unlink(item["path"])


def reclassify_oversized_dicts(items: list, *,
                                max_bytes: int = PHOTO_MAX_BYTES,
                                use_preview: bool = False) -> tuple:
    """Legacy dict facade over the canonical preparation policy.

    Returns (items, decisions); decisions are the structured media-decision
    payloads (one per classified photo) for the durable media.prepared audit.
    """
    policy = MediaPreparationPolicy(max_bytes=max_bytes)
    entries = [
        (item["path"], item.get("preview_path") if use_preview else None)
        for item in items
        if item.get("kind") == "photo" and item.get("path")
    ]
    prepared_list = iter(_prepare_many(entries, policy))
    out: list = []
    decisions: list = []
    for item in items:
        if item.get("kind") == "photo" and item.get("path"):
            prepared = next(prepared_list)
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
    photo_indexes = [
        index for index, item in enumerate(items)
        if item.kind is MediaKind.PHOTO and isinstance(item.source, LocalFile)
    ]
    prepared_by_index: Dict[int, PreparedMedia] = {}
    if photo_indexes:
        prepared = _prepare_many(
            [(items[index].source.path, None) for index in photo_indexes], policy
        )
        prepared_by_index = dict(zip(photo_indexes, prepared))

    out: List[MediaItem] = []
    for index, item in enumerate(items):
        prepared = prepared_by_index.get(index)
        if prepared is not None:
            source = item.source
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
