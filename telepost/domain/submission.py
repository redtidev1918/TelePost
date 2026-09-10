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
