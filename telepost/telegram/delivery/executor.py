"""Batch execution orchestration, independent of PTB.

Given a :class:`DeliveryPlan` and a :class:`Sender`, walks the batches, applies
the chain/post reply topology, puts the caption on the very first message only
and distinguishes three outcomes:

* confirmed success                 → ``DeliveryResult.delivered``;
* certain failure                   → ``DeliveryResult.failed``; the caller sees
                                       every message that already landed;
* network uncertainty during an     → ``DeliveryResult.uncertain`` – Telegram
  album / single send                 might have accepted it, so the caller must
                                       NEVER blindly resend it.

An album failure that is *not* a network error falls back to one-by-one sends
(memory-safety for small machines), preserving the historical behaviour.
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import tempfile
import urllib.request
from typing import Callable, List, Optional, Protocol

from ...domain.delivery import (
    DeliveredMessage,
    DeliveryResult,
    LocalFile,
    MediaItem,
    MediaKind,
    RemoteUrl,
    ReplyMode,
)
from .planner import Batch, DeliveryPlan
from .preparation import (
    PHOTO_MAX_BYTES,
    is_photo_constraint_error,
    retry_photo_derivative,
)

logger = logging.getLogger(__name__)

#: Telegram's own URL fetcher failed to download a remote media source.
REMOTE_FETCH_ERROR_MARKERS = (
    "webpage_curl_failed",
    "failed to fetch",
    "failed to download",
    "source url",
    "url_fetch",
)


class NetworkFailure(Exception):
    """A Telegram response was lost (timeout/network) after the request was sent.

    Resending after this can duplicate an album Telegram actually received.
    ``original`` preserves the transport exception (e.g. PTB TimedOut) so the
    outer facade can re-raise the same type to callers.
    """

    def __init__(self, *args, original: Optional[BaseException] = None):
        super().__init__(*args)
        self.original = original


class Sender(Protocol):
    """PTB-side transport. The gateway implements this."""

    async def send_album(
        self, batch: Batch, *, reply_to: Optional[int], caption: Optional[str]
    ) -> List[DeliveredMessage]: ...

    async def send_single(
        self, item, *, reply_to: Optional[int], caption: Optional[str]
    ) -> DeliveredMessage: ...


OnSent = Callable[[List[DeliveredMessage]], None]


def is_remote_fetch_error(error: object) -> bool:
    """True when Telegram itself could not fetch an external media URL."""
    text = str(error or "").lower()
    return any(marker in text for marker in REMOTE_FETCH_ERROR_MARKERS)


def _download_remote(url: str, max_bytes: int = PHOTO_MAX_BYTES) -> str:
    """Stream one remote media source to a bounded temp file.

    Runs in a worker thread (blocking urllib I/O); Telegram's User-Agent is
    deliberately reused so proxy/CDN policy treats TelePost like Telegram.
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "TelegramBot-LinkPreview/0.1"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
        if content_type and not content_type.startswith(
            ("image/", "video/", "audio/", "application/")
        ):
            raise ValueError(f"remote media has unsupported type {content_type!r}: {url[:120]}")
        suffix = mimetypes.guess_extension(content_type) or ".bin"
        fd, path = tempfile.mkstemp(prefix="tp-remote-", suffix=suffix)
        total = 0
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(
                            f"remote media exceeds {max_bytes} bytes: {url[:120]}"
                        )
                    out.write(chunk)
        except Exception:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
    return path


async def materialize_remote(item: MediaItem) -> Optional[MediaItem]:
    """Turn one RemoteUrl item into a bounded temporary LocalFile."""
    if not item.is_remote:
        return item
    try:
        path = await asyncio.to_thread(_download_remote, str(item.source.url))
    except Exception as exc:  # noqa: BLE001 - bounded remote fallback must fail the item cleanly
        logger.warning("remote media materialization failed: %s", exc)
        return None
    name = getattr(item.source, "filename", None) or os.path.basename(item.source.url) or "remote"
    return MediaItem(
        item.kind,
        LocalFile(path, name, temporary=True),
        item.spoiler,
    )


async def _recover_rejected_photo(sender: "Sender", item, *,
                                  reply_to: Optional[int],
                                  caption: Optional[str]):
    """ONE bounded re-process of a photo Telegram itself rejected.

    Telegram's verdict is the authority (the artifact already passed local
    validation), so a refused photo gets exactly one smaller derivative and one
    retry as a photo; only if that is refused too does the artwork ship as a
    document instead of failing the whole delivery. Any other error propagates
    untouched. May raise :class:`NetworkFailure` from the retry itself.
    """
    path = item.local_path
    if item.kind is not MediaKind.PHOTO or not path:
        return None
    derivative = retry_photo_derivative(path)
    if derivative is None:
        return None
    name = f"{os.path.splitext(item.filename or 'image')[0]}.jpg"
    retry_item = MediaItem(
        MediaKind.PHOTO,
        LocalFile(derivative, name, original_path=path, temporary=True),
        item.spoiler,
    )
    try:
        try:
            return await sender.send_single(retry_item, reply_to=reply_to,
                                            caption=caption)
        except NetworkFailure:
            raise
        except Exception:
            document = MediaItem(
                MediaKind.DOCUMENT,
                LocalFile(path, item.filename or "file", original_path=path),
                False,
            )
            return await sender.send_single(document, reply_to=reply_to,
                                            caption=caption)
    finally:
        try:
            os.unlink(derivative)
        except OSError:
            pass


async def _send_with_remote_fallback(
    sender: "Sender",
    item: MediaItem,
    *,
    reply_to: Optional[int],
    caption: Optional[str],
) -> Optional[DeliveredMessage]:
    """One bounded remote→local retry when Telegram cannot fetch a URL."""
    materialized = await materialize_remote(item)
    if materialized is None:
        return None
    try:
        return await sender.send_single(
            materialized, reply_to=reply_to, caption=caption
        )
    finally:
        path = materialized.local_path
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


async def execute_plan(
    plan: DeliveryPlan,
    sender: Sender,
    *,
    caption: Optional[str] = None,
    on_sent: Optional[OnSent] = None,
) -> DeliveryResult:
    if not plan.batches:
        return DeliveryResult.failed("nothing to deliver", retryable=False)

    sent: List[DeliveredMessage] = []
    main_message: Optional[DeliveredMessage] = None
    previous_id: Optional[int] = None

    for batch in plan.batches:
        if plan.reply_mode is ReplyMode.POST:
            reply_to = plan.anchor_message_id or (
                main_message.message_id if main_message else None
            )
        else:
            reply_to = (
                previous_id if previous_id is not None else plan.anchor_message_id
            )

        # The caption belongs to the first message of the whole delivery only.
        batch_caption = caption if main_message is None else None
        messages: Optional[List[DeliveredMessage]] = None
        album_error: Optional[BaseException] = None

        if batch.is_album:
            try:
                messages = await sender.send_album(
                    batch, reply_to=reply_to, caption=batch_caption
                )
            except NetworkFailure as exc:
                result = DeliveryResult.uncertain(
                    "album send response lost; Telegram may have accepted it",
                    known_messages=sent,
                )
                result.error = getattr(exc, "original", None) or exc
                return result
            except Exception as exc:
                # Certain album failure (bad request etc.): degrade to singles.
                album_error = exc
                messages = None

        if messages is not None and len(messages) != len(batch.items):
            return DeliveryResult.uncertain(
                f"album returned {len(messages)} messages for "
                f"{len(batch.items)} items",
                known_messages=sent + messages,
            )

        if messages is None:
            messages = []
            for index, item in enumerate(batch.items):
                if index == 0:
                    item_reply = reply_to
                elif plan.reply_mode is ReplyMode.POST:
                    item_reply = (
                        reply_to if reply_to is not None else messages[0].message_id
                    )
                else:
                    item_reply = messages[-1].message_id
                item_caption = batch_caption if index == 0 else None
                try:
                    messages.append(
                        await sender.send_single(
                            item, reply_to=item_reply, caption=item_caption
                        )
                    )
                except NetworkFailure as exc:
                    if item.is_remote and is_remote_fetch_error(exc):
                        remote_fallback = await _send_with_remote_fallback(
                            sender, item, reply_to=item_reply, caption=item_caption
                        )
                        if remote_fallback is not None:
                            messages.append(remote_fallback)
                            continue
                    result = DeliveryResult.uncertain(
                        "message send response lost; Telegram may have accepted it",
                        known_messages=sent + messages,
                    )
                    result.error = getattr(exc, "original", None) or exc
                    return result
                except Exception as exc:
                    recovered = None
                    if is_photo_constraint_error(exc):
                        try:
                            recovered = await _recover_rejected_photo(
                                sender, item, reply_to=item_reply,
                                caption=item_caption,
                            )
                        except NetworkFailure as net_exc:
                            result = DeliveryResult.uncertain(
                                "photo retry response lost; Telegram may have "
                                "accepted it",
                                known_messages=sent + messages,
                            )
                            result.error = (getattr(net_exc, "original", None)
                                            or net_exc)
                            return result
                    if recovered is not None:
                        messages.append(recovered)
                        continue
                    if item.is_remote and is_remote_fetch_error(exc):
                        remote_fallback = await _send_with_remote_fallback(
                            sender, item, reply_to=item_reply, caption=item_caption
                        )
                        if remote_fallback is not None:
                            messages.append(remote_fallback)
                            continue
                        return DeliveryResult.failed(
                            f"remote media unavailable: {exc}",
                            retryable=True,
                            known_messages=sent + messages,
                        )
                    # Certain failure with known landed messages; the caller
                    # decides rollback/retry without guessing delivery state.
                    return DeliveryResult.failed(
                        str(exc), retryable=True,
                        known_messages=sent + messages,
                    )

        for message in messages:
            sent.append(message)
            if main_message is None:
                main_message = message
            previous_id = message.message_id
        if on_sent is not None:
            on_sent(messages)

    _ = album_error  # fallback already handled; kept for debugger introspection
    return DeliveryResult.delivered(sent, main_message)
