"""Submission domain: content under review/publication, independent of storage.

Distinguishes a *Submission* (the user-supplied content) from a *Publication*
(the confirmed channel result). Mirrors the existing SQLite columns without
coupling callers to a ``sqlite3.Row`` / ``user_data`` / JSON payload dict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from .delivery import MediaItem


class SubmissionSource(str, Enum):
    CHAT = "chat"
    API = "api"
    MCP = "mcp"


class SubmissionDisposition(str, Enum):
    """How a submission is routed to its final target (§submission-disposition).

    Admission is decided by SOURCE TRUST, not by the entry form:
    * ``api``     — automated/third-party entry: ALWAYS REVIEW_REQUIRED.
    * ``miniapp`` — verified human Mini App session: configured via
      MINIAPP_REVIEW_REQUIRED (default true, preserving current production
      behavior); never shares a switch with the API path.
    * ``chat`` / ``chat_direct`` — native Telegram chat: DIRECT_PUBLISH unless
      CHAT_REVIEW_REQUIRED=true.
    """

    DIRECT_PUBLISH = "direct_publish"
    REVIEW_REQUIRED = "review_required"


def entry_disposition(source: str) -> SubmissionDisposition:
    """Admission policy → decision. Source trust decides the flow.

    The API entry point may pass the concrete entry source (``api`` for service
    API tokens, ``miniapp`` for verified Mini App sessions); anything else falls
    back to the native chat disposition.
    """
    source = (source or "").strip().lower()
    if source in ("api", "api_direct"):
        # Automated/third-party: always gated by human review.
        return SubmissionDisposition.REVIEW_REQUIRED
    if source == "miniapp":
        from config.settings import MINIAPP_REVIEW_REQUIRED

        return (
            SubmissionDisposition.REVIEW_REQUIRED
            if MINIAPP_REVIEW_REQUIRED
            else SubmissionDisposition.DIRECT_PUBLISH
        )
    return chat_disposition()


def chat_disposition() -> SubmissionDisposition:
    """Native Telegram chat routing: DIRECT_PUBLISH unless the operator opted
    into review (CHAT_REVIEW_REQUIRED=true)."""
    from config.settings import CHAT_REVIEW_REQUIRED

    return (
        SubmissionDisposition.REVIEW_REQUIRED
        if CHAT_REVIEW_REQUIRED
        else SubmissionDisposition.DIRECT_PUBLISH
    )


def miniapp_disposition() -> SubmissionDisposition:
    """Verified-human Mini App routing (independent of the API path)."""
    return entry_disposition("miniapp")


def api_disposition() -> SubmissionDisposition:
    """HTTP service/API-token routing: automated sources always go to review."""
    return SubmissionDisposition.REVIEW_REQUIRED


class SubmissionStatus(str, Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    PUBLISHED = "published"
    FAILED = "failed"


@dataclass
class SubmissionContent:
    """Caption metadata + ordered media that make up one submission."""

    tags: str = ""
    title: str = ""
    note: str = ""
    link: str = ""
    anonymous: bool = False
    spoiler: bool = False
    items: List[MediaItem] = field(default_factory=list)

    def require_media(self) -> None:
        if not self.items:
            raise ValueError("submission has no media or documents")

    def require_tags(self) -> None:
        if not (self.tags or "").strip():
            raise ValueError("tags are required before publishing")


@dataclass(frozen=True)
class Publication:
    """Confirmed result of delivering a submission to the channel."""

    message_id: int
    link: str
    all_message_ids: List[int]
    media_count: int
    document_count: int
