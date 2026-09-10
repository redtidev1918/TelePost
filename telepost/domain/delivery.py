"""Telegram delivery domain models.

PTB-free value objects shared by the delivery planner/executor and the
application layer. ``MediaSource`` is a tagged union over the three ways a
piece of media can be supplied:

* ``TelegramFileId``  – media already hosted by Telegram (zero re-upload);
* ``LocalFile``       – a path on this machine (multipart API / PixivFlow);
* ``RemoteUrl``       – a public URL Telegram may fetch itself.

The domain never imports ``python-telegram-bot``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Union


class MediaKind(str, Enum):
    PHOTO = "photo"
    VIDEO = "video"
    ANIMATION = "animation"
    AUDIO = "audio"
    DOCUMENT = "document"

    @classmethod
    def coerce(cls, value: "str | MediaKind") -> "MediaKind":
        if isinstance(value, MediaKind):
            return value
        return cls(str(value).strip().lower())


class ReplyMode(str, Enum):
    """How successive batches relate to each other in the chat."""

    CHAIN = "chain"       # each batch replies to the previous one
    POST = "post"         # every batch replies to the first post / anchor
    DISCUSSION = "discussion"  # cover goes to the channel, rest to discussion


@dataclass(frozen=True)
class TelegramFileId:
    file_id: str
    filename: Optional[str] = None


@dataclass(frozen=True)
class LocalFile:
    path: str
    filename: str


@dataclass(frozen=True)
class RemoteUrl:
    url: str
    filename: Optional[str] = None


MediaSource = Union[TelegramFileId, LocalFile, RemoteUrl]


@dataclass
class MediaItem:
    """One unit of media inside a delivery request."""

    kind: MediaKind
    source: MediaSource
    spoiler: bool = False

    # ---- convenience constructors / accessors used by adapters ----
    @staticmethod
    def file_id(kind: str, file_id: str, *, spoiler: bool = False,
                filename: Optional[str] = None) -> "MediaItem":
        return MediaItem(
            MediaKind.coerce(kind), TelegramFileId(file_id, filename), spoiler
        )

    @staticmethod
    def local(kind: str, path: str, filename: str, *, spoiler: bool = False) -> "MediaItem":
        return MediaItem(MediaKind.coerce(kind), LocalFile(path, filename), spoiler)

    @property
    def filename(self) -> Optional[str]:
        return getattr(self.source, "filename", None)

    @property
    def telegram_file_id(self) -> Optional[str]:
        return self.source.file_id if isinstance(self.source, TelegramFileId) else None

    @property
    def local_path(self) -> Optional[str]:
        return self.source.path if isinstance(self.source, LocalFile) else None

    @property
    def is_local(self) -> bool:
        return isinstance(self.source, LocalFile)

    @property
    def is_file_id(self) -> bool:
        return isinstance(self.source, TelegramFileId)


@dataclass
class DeliveryRequest:
    """Everything the delivery engine needs for one publication.

    ``caption`` is attached to the very first message only.
    """

    chat_id: Union[int, str]
    items: List[MediaItem]
    caption: Optional[str] = None
    spoiler: bool = False
    reply_mode: ReplyMode = ReplyMode.CHAIN
    reply_to_message_id: Optional[int] = None
    album_size: int = 10
    # Discussion-only: linked discussion chat id, resolved by the caller when
    # known; the strategy resolves it from the channel when left unset.
    discussion_chat_id: Optional[int] = None


@dataclass(frozen=True)
class DeliveredMessage:
    """A confirmed Telegram message produced by a delivery.

    ``raw`` is an opaque transport object (the PTB ``Message``) kept for
    legacy adapters that still need it; the domain/application layers must
    never read it and it carries no type dependency on PTB.
    """

    chat_id: int
    message_id: int
    kind: MediaKind
    file_id: Optional[str] = None
    thumbnail_file_id: Optional[str] = None
    raw: object = None


class DeliveryState(str, Enum):
    DELIVERED = "delivered"
    UNCERTAIN = "uncertain"
    FAILED = "failed"


@dataclass
class DeliveryResult:
    """Formal outcome of a delivery.

    The application layer must branch on ``state`` instead of catching generic
    exceptions or string-matching error text.
    """

    state: DeliveryState
    messages: List[DeliveredMessage] = field(default_factory=list)
    main_message: Optional[DeliveredMessage] = None
    reason: str = ""
    retryable: bool = False
    error: object = None
    # Messages known to have landed even though the overall result is not
    # confirmed (uncertain / failed). Drives conservative rollback decisions.
    known_messages: List[DeliveredMessage] = field(default_factory=list)

    # ---- canonical constructors ----
    @staticmethod
    def delivered(messages: List[DeliveredMessage],
                  main_message: Optional[DeliveredMessage] = None) -> "DeliveryResult":
        if not messages:
            return DeliveryResult.failed("delivery returned no messages", retryable=False)
        return DeliveryResult(
            DeliveryState.DELIVERED,
            messages=list(messages),
            main_message=main_message or messages[0],
        )

    @staticmethod
    def uncertain(reason: str, *, known_messages: Optional[List[DeliveredMessage]] = None,
                  retryable: bool = False) -> "DeliveryResult":
        return DeliveryResult(
            DeliveryState.UNCERTAIN,
            reason=reason,
            retryable=retryable,
            known_messages=list(known_messages or []),
            messages=list(known_messages or []),
            main_message=(known_messages[0] if known_messages else None),
        )

    @staticmethod
    def failed(reason: str, *, retryable: bool = True,
               known_messages: Optional[List[DeliveredMessage]] = None) -> "DeliveryResult":
        known = list(known_messages or [])
        return DeliveryResult(
            DeliveryState.FAILED,
            reason=reason,
            retryable=retryable,
            known_messages=known,
            messages=known,
            main_message=(known[0] if known else None),
        )

    @property
    def ok(self) -> bool:
        return self.state is DeliveryState.DELIVERED

    @property
    def is_uncertain(self) -> bool:
        return self.state is DeliveryState.UNCERTAIN

    def channel_message_ids(self, channel_chat_id: Optional[int] = None) -> List[int]:
        """Message ids that belong to the main chat (never discussion-chat ids)."""
        if channel_chat_id is None and self.main_message is not None:
            channel_chat_id = self.main_message.chat_id
        if channel_chat_id is None:
            return [m.message_id for m in self.messages]
        return [m.message_id for m in self.messages if m.chat_id == channel_chat_id]
