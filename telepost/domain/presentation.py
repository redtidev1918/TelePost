"""Publication presentation rules (SSOT, §publication-presentation).

What a published message may LOOK like is a domain decision, not an entrypoint
decision: chat, Mini App, HTTP API and PixivFlow all publish through the same
formatter, and all of them must end up with the same caption semantics.

Two rules live here:

1. **Media actions reflect the real attachment kinds.** A "view the media" hint
   only makes sense when the message actually carries media that Telegram
   renders as previewable content (photo / video / animation). A document-only
   publication already opens/downloads directly in Telegram, so it must never
   grow a media-view hint.

2. **Only explicit submitter identity is authorship.** ``submitter_user_id`` /
   ``submitter_username`` (a verified human) is the ONLY source for a displayed
   submitter. The request actor, the API token name/alias, the credential
   holder, the transport ``source`` and the legacy ``user_id``/``username``
   request identity are provenance or authentication metadata — never the
   submission author.

Surfaces:

``channel``   public publication (what channel readers see)
``miniapp``   public-facing preview (Mini App)
``review``    internal reviewer surface (review group / review card caption)
``system``    bot/system messages (no submitter presentation at all)
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

# Telegram renders these as inline previewable content (the client can show
# them in the message, and they support the spoiler treatment).
PREVIEWABLE_KINDS = frozenset({"photo", "video", "animation"})

# Attachment kinds TelePost accepts at all. ``audio`` deliberately is NOT a
# previewable kind: Telegram has no spoiler/blur treatment for audio, so a
# "点击查看" hint would promise a visual reveal that does not exist.
KNOWN_KINDS = PREVIEWABLE_KINDS | {"audio", "document"}

PUBLIC_SURFACES = frozenset({"channel", "miniapp"})
INTERNAL_SURFACES = frozenset({"review", "system"})

# Provenance labels for internal surfaces. Public surfaces never show these:
# channel readers care about the submission, not about our ingestion pipeline.
SOURCE_LABELS = {
    "chat": "Telegram 聊天",
    "api": "API",
    "pixivflow": "PixivFlow",
    "schedule": "PixivFlow",
    "service": "服务投稿",
}


def normalize_kind(kind: Any) -> str:
    if isinstance(kind, str):
        return kind.strip().lower()
    if isinstance(kind, dict):
        kind = kind.get("type") or kind.get("kind") or ""
    else:
        kind = getattr(kind, "value", None) or getattr(kind, "kind", None)
    return str(kind or "").strip().lower()


def is_previewable_media(kind: Any) -> bool:
    """True when this attachment kind is inline-previewable in Telegram."""
    return normalize_kind(kind) in PREVIEWABLE_KINDS


def has_previewable_media(kinds: Any) -> bool:
    """True when ANY attachment is previewable.

    Accepts a single kind (``"photo"``), an iterable of kinds, or attachment
    descriptors (dicts with ``type``/``kind``, or objects with a ``kind``
    attribute) — callers must not re-implement ``if photos > 0`` checks.
    """
    if kinds is None:
        return False
    if isinstance(kinds, (str, bytes)):
        return is_previewable_media(kinds)
    return any(is_previewable_media(kind) for kind in _iter_kinds(kinds))


def _iter_kinds(items: Iterable[Any]) -> Iterable[Any]:
    for item in items:
        if isinstance(item, dict):
            yield item.get("type") or item.get("kind") or ""
        else:
            yield item


def media_kinds_from_items(items: Any) -> list:
    """Normalize attachment descriptors to a list of kind names.

    Accepts kind strings, chat-session ``kind:file_id`` compact strings, dicts
    (``type``/``kind``) and objects with a ``kind`` attribute.
    """
    if items is None:
        return []
    if isinstance(items, str):
        return [_split_compact(items)]
    if isinstance(items, (bytes,)):
        return [normalize_kind(items)]
    out = []
    for kind in _iter_kinds(items):
        normalized = _split_compact(kind)
        if normalized:
            out.append(normalized)
    return out


def _split_compact(item: Any) -> str:
    """``kind:file_id`` session format → kind; otherwise normalize_kind."""
    normalized = normalize_kind(item)
    if not normalized:
        return ""
    if ":" in normalized:
        return normalized.split(":", 1)[0]
    return normalized


def submitter_display(submitter_user_id: Any,
                      submitter_username: Any = "") -> str:
    """The ONLY accepted source of a displayed submitter.

    Returns an empty string when there is no explicit human submitter — a
    service/API submission (``submitter_user_id IS NULL``) has no human author,
    and no actor/token/source fallback may be substituted.
    """
    if submitter_user_id is None or submitter_user_id == "":
        return ""
    try:
        explicit_id = int(submitter_user_id)
    except (TypeError, ValueError):
        return ""
    if explicit_id <= 0:
        return ""
    username = str(submitter_username or "").strip().lstrip("@")
    return username or f"user{explicit_id}"


def is_anonymous(value: Any) -> bool:
    """Accept bool, ``"true"``/``"false"`` strings and legacy rows."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def source_display(source: Any, source_label: Any = "") -> str:
    """Internal-only provenance label (never a submitter)."""
    label = str(source_label or "").strip()
    if label:
        return label
    return SOURCE_LABELS.get(normalize_kind(source), "")


def is_internal_surface(surface: Optional[str]) -> bool:
    return str(surface or "channel") in INTERNAL_SURFACES
