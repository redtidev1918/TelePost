"""Publication application service.

Single business entry point used by every adapter (Telegram chat flow, review
approval, HTTP API, future MCP). It owns:

* idempotency (explicit key + cross-intent work dedupe);
* delivery via the :class:`TelegramDeliveryPort` (the PTB gateway is injected,
  so this module never imports python-telegram-bot);
* persisting the confirmed channel post;
* a formal result object – adapters never infer state from exceptions or
  re-query the database.

Success is returned only after a *confirmed* delivery. A request whose
Telegram response was lost returns :class:`Uncertain`; nothing is retried
blindly and no "published" ACK is produced.
"""
from __future__ import annotations

import asyncio

import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Protocol

from ..domain.delivery import (
    DeliveredMessage,
    DeliveryRequest,
    DeliveryResult,
    MediaItem,
)
from ..observability import audit
from ..observability.errors import classify as classify_error
from ..storage.sqlite.ledger import DeliveryLedgerRepository, LedgerEntry

logger = logging.getLogger(__name__)

PUBLISHED_DEDUP_WINDOW_SECONDS = 7 * 86400


# ---- formal results -------------------------------------------------------

@dataclass
class PublicationOutcome:
    status: str                      # published | idempotent_replay | duplicate_existing
    message_id: Optional[int] = None
    link: str = ""
    media_count: int = 0
    document_count: int = 0
    delivery_status: str = "published"
    reused: bool = False
    reuse_reason: str = ""
    matched_idempotency_key: Optional[str] = None
    reason: str = ""
    retryable: bool = False
    uncertain: bool = False
    known_messages: List[DeliveredMessage] = field(default_factory=list)
    error: object = None

    @property
    def ok(self) -> bool:
        return self.status == "published" or self.reused

    def as_ack(self) -> dict:
        """Stable wire DTO for HTTP / chat adapters."""
        data = {
            "status": self.status,
            "message_id": self.message_id,
            "link": self.link,
            "media_count": self.media_count,
            "document_count": self.document_count,
            "delivery_status": self.delivery_status,
        }
        if self.reused:
            data.update(
                reused=True,
                reuse_reason=self.reuse_reason,
                matched_idempotency_key=self.matched_idempotency_key,
            )
        return {k: v for k, v in data.items() if v is not None}


@dataclass
class PublishCommand:
    chat_id: object
    items: List[MediaItem]
    caption_data: dict                # tags/title/note/link/username/...
    user_id: int
    spoiler: bool = False
    username: str = ""
    idempotency_key: str = ""
    target_id: str = ""
    work_type: str = ""
    pixiv_id: str = ""
    reply_mode: object = None         # domain ReplyMode; default CHAIN
    reply_to_message_id: Optional[int] = None
    album_size: int = 10


# ---- ports ----------------------------------------------------------------

class TelegramDeliveryPort(Protocol):
    async def deliver(self, request: DeliveryRequest) -> DeliveryResult: ...


PostSink = Callable[..., object]
LinkBuilder = Callable[[int], str]


class PublicationService:
    def __init__(self, *, delivery: TelegramDeliveryPort,
                 ledger: Optional[DeliveryLedgerRepository] = None,
                 record_post: Optional[PostSink] = None,
                 link_builder: Optional[LinkBuilder] = None,
                 dedup_window_seconds: int = PUBLISHED_DEDUP_WINDOW_SECONDS):
        self._delivery = delivery
        self._ledger = ledger or DeliveryLedgerRepository()
        self._record_post = record_post  # injected by the wiring layer
        self._link_builder = link_builder
        self._dedup_window = dedup_window_seconds
        # Same-process serialization for concurrent identical keys: the second
        # caller waits for the first to finish sending+recording, then replays.
        self._key_locks: dict = {}

    def _link(self, message_id: int, channel) -> str:
        if self._link_builder is not None:
            return self._link_builder(message_id)
        channel = str(channel)
        if channel.startswith("@"):
            return f"https://t.me/{channel.lstrip('@')}/{message_id}"
        return f"https://t.me/c/{channel.replace('-100', '')}/{message_id}"

    async def publish(self, command: PublishCommand) -> PublicationOutcome:
        key = (command.idempotency_key or "").strip()[:240]
        if key:
            lock = self._key_locks.setdefault(key, asyncio.Lock())
            async with lock:
                try:
                    return await self._publish(command, key)
                finally:
                    self._key_locks.pop(key, None)
        return await self._publish(command, key)

    async def _publish(self, command: PublishCommand, key: str) -> PublicationOutcome:
        from ..domain.delivery import ReplyMode

        pid = (command.pixiv_id or "").strip()
        event_fields = self._event_fields(command, key)
        # Review-approved publishes use review:<id>:<original> keys. Their
        # publish.* audit is owned by ReviewService (which keeps the durable
        # event linked to the review row); emitting here too would double it.
        audit_publish = not (key.startswith("review:") and event_fields["review_id"])

        replay = await self._ledger.find_by_key(key)
        if replay is not None:
            return self._replay(replay, reason="idempotent_replay")

        historical = await self._ledger.find_work(
            command.target_id, command.work_type, pid, self._dedup_window
        )
        if historical is not None and historical.idempotency_key != key:
            return self._replay(historical, reason="duplicate_existing")

        request = DeliveryRequest(
            chat_id=command.chat_id,
            items=command.items,
            caption=self._caption(command.caption_data),
            spoiler=command.spoiler,
            reply_mode=command.reply_mode or ReplyMode.CHAIN,
            reply_to_message_id=command.reply_to_message_id,
            album_size=command.album_size,
        )
        if audit_publish:
            await audit.record_event("publish.started", **event_fields)
        result = await self._delivery.deliver(request)

        if result.is_uncertain:
            return PublicationOutcome(
                status="uncertain",
                reason=result.reason,
                retryable=False,
                uncertain=True,
                known_messages=result.known_messages,
                delivery_status="uncertain",
                error=getattr(result, "error", None),
            )
        if not result.ok or result.main_message is None:
            error = getattr(result, "error", None)
            if audit_publish:
                await audit.record_event(
                    "publish.failed",
                    error_class=classify_error(error),
                    detail={"reason": result.reason or "delivery failed"},
                    **event_fields,
                )
            return PublicationOutcome(
                status="failed",
                reason=result.reason or "delivery failed",
                retryable=result.retryable,
                known_messages=result.known_messages,
                delivery_status="failed",
                error=error,
            )

        main = result.main_message
        channel_ids = result.channel_message_ids(main.chat_id)
        media_count = sum(1 for m in result.messages if m.kind.value != "document")
        document_count = sum(1 for m in result.messages if m.kind.value == "document")

        if self._record_post is not None:
            await self._record_post(command, result, media_count, document_count)

        recorded = await self._ledger.record_published(
            key,
            target_id=command.target_id,
            pixiv_id=pid,
            work_type=command.work_type,
            message_id=main.message_id,
            related_message_ids=channel_ids,
            user_id=command.user_id,
        )
        if not recorded:
            # Concurrent same-key request won the UNIQUE race. Telegram only
            # received this attempt because both passed the pre-send lookup, so
            # surface it as an uncertain duplicate rather than pretending the
            # returned message is the canonical replay.
            replay = await self._ledger.find_by_key(key)
            if replay is not None:
                return PublicationOutcome(
                    status="uncertain",
                    reason="idempotency key raced with another in-flight request; "
                           "both may have been sent",
                    uncertain=True,
                    known_messages=[main],
                    delivery_status="uncertain",
                )

        if audit_publish:
            await audit.record_event(
                "publish.completed",
                detail={"message_id": main.message_id,
                        "media": media_count, "documents": document_count},
                **event_fields,
            )
        return PublicationOutcome(
            status="published",
            message_id=main.message_id,
            link=self._link(main.message_id, command.chat_id),
            media_count=media_count,
            document_count=document_count,
        )

    @staticmethod
    def _event_fields(command: PublishCommand, key: str) -> dict:
        # Review-approved publishes use keys shaped review:<id>:<original>.
        review_id = None
        parts = key.split(":", 2)
        if len(parts) == 3 and parts[0] == "review" and parts[1].isdigit():
            review_id = int(parts[1])
        return {
            "review_id": review_id,
            "pixiv_id": (command.pixiv_id or "").strip() or None,
            "work_type": command.work_type or None,
            "target_id": command.target_id or None,
            "idempotency_key": key or None,
            "actor": f"telegram_user:{command.user_id}" if command.user_id else "api",
        }

    def _replay(self, entry: LedgerEntry, *, reason: str) -> PublicationOutcome:
        key = entry.idempotency_key or ""
        review_id = None
        parts = key.split(":", 2)
        is_review_key = (
            len(parts) == 3 and parts[0] == "review" and parts[1].isdigit()
        )
        if is_review_key:
            review_id = int(parts[1])
        # review-keyed replays are audited by ReviewService (or short-circuited
        # before the publisher entirely) — never double-emit from the service.
        if not is_review_key:
            try:
                asyncio.get_running_loop().create_task(audit.record_event(
                    "publish.duplicate_suppressed",
                    pixiv_id=entry.pixiv_id or None,
                    work_type=entry.work_type or None,
                    target_id=entry.target_id or None,
                    idempotency_key=key or None,
                    detail={"reuse_reason": reason,
                            "matched_idempotency_key": entry.idempotency_key},
                ))
            except RuntimeError:
                pass
        message_id = entry.message_id or 0
        return PublicationOutcome(
            status="published",
            message_id=message_id or None,
            link=self._link(message_id, None) if (message_id and self._link_builder)
            else _legacy_link(message_id),
            delivery_status="published",
            reused=True,
            reuse_reason=reason,
            matched_idempotency_key=entry.idempotency_key,
        )

    @staticmethod
    def _caption(data: dict) -> str:
        from utils.helper_functions import build_caption
        return build_caption(data)


def _legacy_link(message_id: int) -> str:
    """Replay rows predate command context; resolve channel from live config."""
    try:
        from config.settings import CHANNEL_ID
        channel = str(CHANNEL_ID)
        if channel.startswith("@"):
            return f"https://t.me/{channel.lstrip('@')}/{message_id}"
        return f"https://t.me/c/{channel.replace('-100', '')}/{message_id}"
    except Exception:
        return ""
