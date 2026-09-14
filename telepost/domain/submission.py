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

    Chat submissions default to DIRECT_PUBLISH: native Telegram chat flow is
    "发布到频道". Review routing is an explicit, configurable policy
    (CHAT_REVIEW_REQUIRED). Mini App / API submissions default to
    REVIEW_REQUIRED in production (API_REVIEW_REQUIRED=true). The two entry
    points share one domain/service but MAY have different defaults — they are
    not bound to each other, and refactoring one must never silently change the
    other's default.
    """

    DIRECT_PUBLISH = "direct_publish"
    REVIEW_REQUIRED = "review_required"


def chat_disposition() -> SubmissionDisposition:
    """Native Telegram chat routing: DIRECT_PUBLISH unless the operator opted
    into review (CHAT_REVIEW_REQUIRED=true)."""
    from config.settings import CHAT_REVIEW_REQUIRED

    return (
        SubmissionDisposition.REVIEW_REQUIRED
        if CHAT_REVIEW_REQUIRED
        else SubmissionDisposition.DIRECT_PUBLISH
    )


def api_disposition() -> SubmissionDisposition:
    """HTTP API (Mini App / service) routing, driven by API_REVIEW_REQUIRED.

    Production sets API_REVIEW_REQUIRED=true so Mini App and automatic
    submissions always pass the review queue before reaching the channel.
    """
    from config.settings import API_REVIEW_REQUIRED

    return (
        SubmissionDisposition.REVIEW_REQUIRED
        if API_REVIEW_REQUIRED
        else SubmissionDisposition.DIRECT_PUBLISH
    )


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
