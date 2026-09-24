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
    MEDIA_GROUP_CAPACITY,
    DeliveredMessage,
    DeliveryRequest,
    DeliveryResult,
    MediaItem,
    MediaKind,
    RemoteUrl,
    SubmissionText,
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
    album_size: int = MEDIA_GROUP_CAPACITY


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
                 dedup_window_seconds: int = PUBLISHED_DEDUP_WINDOW_SECONDS,
                 novel_preview: Optional[object] = None,
                 txt_fetch: Optional[object] = None):
        self._delivery = delivery
        self._ledger = ledger or DeliveryLedgerRepository()
        self._record_post = record_post  # injected by the wiring layer
        self._link_builder = link_builder
        self._dedup_window = dedup_window_seconds
        # Optional publication enrichment: a novel TXT attachment may also be
        # readable online through Telegraph. It is an enrichment, never a
        # success prerequisite — the authoritative artifact remains the
        # Telegram TXT document, and a failure/timeout here must not change the
        # publication outcome. ``txt_fetch`` resolves a file_id attachment's
        # bytes through the Telegram adapter (local files read from disk).
        self._novel_preview = novel_preview
        self._txt_fetch = txt_fetch
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

    @staticmethod
    def _media_delivery_strategy(items: List[MediaItem]) -> str:
        """Summarize which transport sources this publication actually used."""
        if not items:
            return "empty"
        sources = {
            item.source.__class__.__name__ for item in items
            if item.kind is not MediaKind.TEXT
        }
        if len(sources) == 1:
            return {
                "TelegramFileId": "telegram_file_id",
                "LocalFile": "local_upload",
                "RemoteUrl": "remote_url",
            }.get(next(iter(sources)), "unknown")
        return "mixed"

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
        from ..telegram.delivery.planner import plan_delivery

        pid = (command.pixiv_id or "").strip()
        event_fields = self._event_fields(command, key)
        # Review-approved publishes use review:<id>:<original> keys. Their
        # publish.* audit is owned by ReviewService (which keeps the durable
        # event linked to the review row); emitting here too would double it.
        audit_publish = not (key.startswith("review:") and event_fields["review_id"])

        replay = await self._ledger.find_by_key(key)
        if replay is not None and replay.status == "published":
            return await self._replay(replay, reason="idempotent_replay")
        if replay is not None and replay.status == "uncertain":
            return PublicationOutcome(
                status="uncertain",
                reason="previous delivery is uncertain; verify Telegram before retrying",
                retryable=False,
                uncertain=True,
                known_messages=self._progress_messages(replay),
                delivery_status="uncertain",
            )

        historical = await self._ledger.find_work(
            command.target_id, command.work_type, pid, self._dedup_window
        )
        if historical is not None and historical.idempotency_key != key:
            return await self._replay(historical, reason="duplicate_existing")

        mode = command.reply_mode or ReplyMode.CHAIN
        base_plan = plan_delivery(
            command.items,
            album_size=command.album_size,
            reply_mode=mode,
        )
        base_items = [item for batch in base_plan.batches for item in batch.items]
        prior = self._progress_messages(replay) if replay is not None else []
        # Optional publication enrichment: a TXT novel in the FINAL snapshot may
        # gain a Telegraph "read online" link BEFORE the channel caption is
        # built. Failure/timeout/absence only means "no extra link"; the TXT
        # document is always still delivered and the publication outcome is
        # decided by Telegram delivery alone. The enrichment is idempotent per
        # publication key, so a delivery retry never creates a second page.
        preview_url = ""
        if key and not prior and self._novel_preview is not None:
            try:
                preview = await self._novel_preview.enrich(
                    publication_key=key,
                    title=dict(command.caption_data or {}).get("title", ""),
                    items=base_items,
                    fetch=self._txt_fetch,
                )
                if preview.succeeded:
                    preview_url = preview.url
            except Exception as exc:
                # Enrichment must never break the TXT publication path.
                logger.warning("novel preview enrichment skipped: %s",
                               type(exc).__name__)
        caption = self._caption(command, preview_url=preview_url)
        ordered_items = base_items
        # A multi-document submission's caption is submission metadata. Send it as
        # the final message instead of attaching it to the first document, so
        # readers do not mistake it for that one file's label. Visual media
        # keeps the existing root-caption UX.
        text_as_caption = bool(caption and len(base_items) > 1 and all(
            item.kind is MediaKind.DOCUMENT for item in base_items
        ))
        if text_as_caption:
            ordered_items = base_items + [MediaItem(
                MediaKind.TEXT, SubmissionText(caption)
            )]
        plan = plan_delivery(
            ordered_items,
            album_size=command.album_size,
            reply_mode=mode,
        )
        ordered_items = [item for batch in plan.batches for item in batch.items]
        if replay is not None and replay.status != "partial":
            return PublicationOutcome(
                status="uncertain",
                reason=f"unsupported delivery ledger state: {replay.status}",
                retryable=False,
                uncertain=True,
                known_messages=prior,
                delivery_status="uncertain",
            )
        if replay is not None and replay.status == "partial" and not prior:
            return PublicationOutcome(
                status="uncertain",
                reason="delivery checkpoint is unreadable; verify Telegram before retrying",
                retryable=False,
                uncertain=True,
                delivery_status="uncertain",
            )
        if prior and mode is ReplyMode.DISCUSSION:
            return PublicationOutcome(
                status="uncertain",
                reason="partial discussion delivery requires manual verification",
                retryable=False,
                uncertain=True,
                known_messages=prior,
                delivery_status="uncertain",
            )
        if any(
            delivered.kind is not item.kind
            for delivered, item in zip(prior, ordered_items)
        ):
            return PublicationOutcome(
                status="uncertain",
                reason="delivery checkpoint does not match the current request",
                retryable=False,
                uncertain=True,
                known_messages=prior,
                delivery_status="uncertain",
            )
        if len(prior) > len(ordered_items):
            return PublicationOutcome(
                status="uncertain",
                reason="delivery checkpoint does not match the current request",
                retryable=False,
                uncertain=True,
                known_messages=prior,
                delivery_status="uncertain",
            )

        reply_to = command.reply_to_message_id
        if prior:
            if mode is ReplyMode.POST:
                reply_to = command.reply_to_message_id or prior[0].message_id
            else:
                reply_to = prior[-1].message_id
        request = DeliveryRequest(
            chat_id=command.chat_id,
            items=ordered_items[len(prior):],
            caption=(None if prior or text_as_caption else caption),
            spoiler=command.spoiler,
            reply_mode=mode,
            reply_to_message_id=reply_to,
            album_size=command.album_size,
        )
        if audit_publish:
            await audit.record_event(
                "publish.started",
                detail={"media_delivery_strategy": self._media_delivery_strategy(ordered_items)},
                **event_fields,
            )
        result = (
            DeliveryResult.delivered(prior, prior[0] if prior else None)
            if not request.items else await self._delivery.deliver(request)
        )

        if result.is_uncertain:
            await self._ledger.record_partial(
                key,
                target_id=command.target_id,
                pixiv_id=pid,
                work_type=command.work_type,
                user_id=command.user_id,
                messages=prior + result.known_messages,
                uncertain=True,
            )
            return PublicationOutcome(
                status="uncertain",
                reason=result.reason,
                retryable=False,
                uncertain=True,
                known_messages=prior + result.known_messages,
                delivery_status="uncertain",
                error=getattr(result, "error", None),
            )
        if not result.ok or result.main_message is None:
            error = getattr(result, "error", None)
            known = prior + result.known_messages
            if known:
                await self._ledger.record_partial(
                    key,
                    target_id=command.target_id,
                    pixiv_id=pid,
                    work_type=command.work_type,
                    user_id=command.user_id,
                    messages=known,
                )
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
                known_messages=known,
                delivery_status="failed",
                error=error,
            )

        messages = prior + result.messages
        main = messages[0]
        result = DeliveryResult.delivered(messages, main)
        channel_ids = result.channel_message_ids(main.chat_id)
        media_count = sum(
            1 for m in messages
            if m.kind.value not in {"document", "text"}
        )
        document_count = sum(1 for m in messages if m.kind.value == "document")

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
                        "media": media_count, "documents": document_count,
                        "media_delivery_strategy": self._media_delivery_strategy(ordered_items)},
                **event_fields,
            )
        return PublicationOutcome(
            status="published",
            message_id=main.message_id,
            link=self._link(main.message_id, command.chat_id),
            media_count=media_count,
            document_count=document_count,
            known_messages=messages,
        )

    @staticmethod
    def _progress_messages(entry: Optional[LedgerEntry]) -> List[DeliveredMessage]:
        if entry is None:
            return []
        messages = []
        try:
            for item in entry.progress:
                messages.append(DeliveredMessage(
                    chat_id=item["chat_id"],
                    message_id=item["message_id"],
                    kind=MediaKind.coerce(item["kind"]),
                    file_id=item.get("file_id"),
                    thumbnail_file_id=item.get("thumbnail_file_id"),
                    file_unique_id=item.get("file_unique_id"),
                ))
        except (KeyError, TypeError, ValueError):
            return []
        return messages

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

    async def _replay(self, entry: LedgerEntry, *, reason: str) -> PublicationOutcome:
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
            await audit.record_event(
                "publish.duplicate_suppressed",
                pixiv_id=entry.pixiv_id or None,
                work_type=entry.work_type or None,
                target_id=entry.target_id or None,
                idempotency_key=key or None,
                detail={"reuse_reason": reason,
                        "matched_idempotency_key": entry.idempotency_key},
            )
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
    def _caption(command, preview_url: str = "") -> str:
        """Channel caption. Attachment kinds come from the REAL delivery items,
        so the media presentation (the spoiler "点击查看" hint) always reflects
        what is actually published — a multi-document publication never
        advertises a media view (§publication-presentation). An optional novel
        preview link (Telegraph "read online") is appended when the enrichment
        succeeded; its absence never alters the presentation of the TXT
        document, which remains the authoritative downloadable artifact."""
        from telepost.domain import presentation
        data = dict(command.caption_data or {})
        if not data.get("media_types"):
            data["media_types"] = presentation.media_kinds_from_items(
                command.items or []
            )
        if preview_url:
            data["novel_preview_url"] = preview_url
        return channel_caption(data)


def channel_caption(caption_data: dict) -> str:
    """Shared channel caption builder for REAL publication: body + navigation footer.

    ONE implementation for every publication path (chat direct, API direct,
    review approval, editorial, PixivFlow/service). The channel footer is a
    Publication Presentation concern — it is never assembled per-handler
    (§submission-entrypoint). The footer carries each navigation action
    exactly once: READ_ONLINE (Telegraph preview), BOT_SUBMIT
    (`?start=submit`) and MINI_APP_SUBMIT (`?startapp=submit`) when the owning
    bot enables it. Caption budgeting stays here so the footer can never
    overflow Telegram's limit.
    """
    from utils.helper_functions import build_caption
    footer_html = ""
    items = _publication_navigation(data := dict(caption_data or {}))
    if items:
        import html as _html
        footer_html = "\n\n" + " | ".join(
            f'<a href="{_html.escape(item.url, quote=True)}">'
            f"{_html.escape(item.label, quote=False)}</a>"
            for item in items
        )
    body_data = dict(data)
    if footer_html and body_data.get("novel_preview_url"):
        # READ_ONLINE lives in the navigation footer once; no duplicate body
        # block on real publications (review/preview surfaces keep the old
        # `🔗 在线阅读` line via build_caption directly).
        body_data["novel_preview_url"] = ""
    max_length = 1024 - len(footer_html) if footer_html else 1024
    return build_caption(body_data, max_length=max_length) + footer_html


def _publication_navigation(caption_data: dict):
    """Typed footer actions for the OWNING bot, or ``[]`` when unconfigured.

    Reads bot/runtime config (CHANNEL_FOOTER_LINK + MINIAPP_SUBMIT_CTA):
    * BOT_SUBMIT       always when a valid owning-bot link is configured;
    * MINI_APP_SUBMIT  additionally when ``MINIAPP_SUBMIT_CTA`` is enabled;
    * READ_ONLINE      when this publication has a Telegraph ``novel_preview_url``.
    Missing/invalid config never produces a malformed link.
    """
    from telepost.domain.navigation import (
        BOT_SUBMIT_ACTION,
        BOT_SUBMIT_LABEL,
        MINI_APP_SUBMIT_ACTION,
        MINI_APP_SUBMIT_LABEL,
        READ_ONLINE_ACTION,
        READ_ONLINE_LABEL,
        NavigationItem,
        bot_submission_url,
        miniapp_submission_url,
    )
    try:
        from config.settings import (
            CHANNEL_FOOTER_LINK,
            MINIAPP_SHORT_NAME,
            MINIAPP_SUBMIT_CTA,
        )
    except Exception:
        return []
    link = (CHANNEL_FOOTER_LINK or "").strip()
    if not link or not link.startswith(("http://", "https://")):
        return []

    data = dict(caption_data or {})
    items = []
    preview_url = data.get("novel_preview_url")
    if preview_url and str(preview_url).startswith(("http://", "https://")):
        items.append(NavigationItem(READ_ONLINE_ACTION, READ_ONLINE_LABEL, str(preview_url)))
    bot_url = bot_submission_url(link)
    if bot_url:
        items.append(NavigationItem(BOT_SUBMIT_ACTION, BOT_SUBMIT_LABEL, bot_url))
    if MINIAPP_SUBMIT_CTA:
        mini_url = miniapp_submission_url(link, short_name=MINIAPP_SHORT_NAME)
        if mini_url:
            items.append(NavigationItem(MINI_APP_SUBMIT_ACTION, MINI_APP_SUBMIT_LABEL, mini_url))
    return items


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
