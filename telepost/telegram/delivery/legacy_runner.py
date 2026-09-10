"""Compatibility copy of the legacy dict-based batch engine.

The new code path uses :mod:`telepost.telegram.delivery.planner` +
``executor`` with formal :class:`MediaItem`/:class:`DeliveryResult` objects.
The historical chat/preview code (and its pinned tests) drives batches with
plain ``{"kind", "file_id"|"path"}`` dicts and injected ``send_one`` /
``send_album`` callables returning raw PTB messages. That orchestration is kept
here verbatim so ``handlers.publish`` becomes a thin re-export facade rather
than owning the engine.
"""
from __future__ import annotations

import logging

from telegram import InputMediaPhoto, InputMediaVideo, InputMediaDocument
from telegram.error import NetworkError

logger = logging.getLogger(__name__)


def _is_local_item(item: dict) -> bool:
    return bool(item.get("path"))


def _local_input_file(path: str, filename: str):
    from telegram import InputFile

    return InputFile(
        open(path, "rb"), filename=filename,
        read_file_handle=False, attach=True,
    )


def _close_item_handle(media) -> None:
    handle = getattr(media, "input_file_content", None)
    if handle and hasattr(handle, "close"):
        try:
            handle.close()
        except Exception:
            pass


def _media_kwargs(item: dict, caption) -> dict:
    kind = item["kind"]
    media = (
        _local_input_file(item["path"], item["filename"])
        if _is_local_item(item) else item["file_id"]
    )
    kw = {"caption": caption, "parse_mode": "HTML" if caption else None}
    if kind == "photo":
        return {"method": "send_photo", "photo": media, **kw,
                "has_spoiler": item.get("spoiler", False)}
    if kind == "video":
        return {"method": "send_video", "video": media, **kw,
                "has_spoiler": item.get("spoiler", False)}
    if kind == "animation":
        return {"method": "send_animation", "animation": media, **kw,
                "has_spoiler": item.get("spoiler", False)}
    if kind == "audio":
        return {"method": "send_audio", "audio": media, **kw}
    return {"method": "send_document", "document": media,
            "filename": item.get("filename") or "file", **kw}


def _album_input_media(item: dict, caption):
    kind = item["kind"]
    media = (
        _local_input_file(item["path"], item["filename"])
        if _is_local_item(item) else item["file_id"]
    )
    parse = "HTML" if caption else None
    if kind == "photo":
        return InputMediaPhoto(media=media, caption=caption, parse_mode=parse,
                               has_spoiler=item.get("spoiler", False))
    if kind == "video":
        return InputMediaVideo(media=media, caption=caption, parse_mode=parse,
                               has_spoiler=item.get("spoiler", False))
    return InputMediaDocument(media=media, caption=caption, parse_mode=parse,
                              filename=item.get("filename") or "file")


def item_batches(items: list, album_size: int):
    """Split into ``(family, items)`` runs in stable family order."""
    def family(kind):
        if kind in ("photo", "video"):
            return "visual"
        return kind  # animation / audio / document

    order = {"visual": 0, "animation": 1, "audio": 2, "document": 3}
    ordered = sorted(items, key=lambda item: order.get(family(item["kind"]), 9))

    runs = []
    for item in ordered:
        fam = family(item["kind"])
        if (runs and runs[-1][0] == fam and fam in ("visual", "document")
                and len(runs[-1][1]) < album_size):
            runs[-1][1].append(item)
        else:
            runs.append((fam, [item]))
    return runs


async def run_item_batches(items, *, caption, album_size,
                           send_one, send_album, fallback_single=True,
                           anchor_id=None, reply_mode="chain", on_sent=None):
    """Legacy dict-based orchestration shared by channel post and review preview.

    ``send_one(item, caption, reply_to) -> Message``
    ``send_album(media_built_list, reply_to) -> [Message]``
    Returns ``(sent_messages, main_message)``.
    """
    sent_messages = []
    previous_id = None
    main_message = None

    for fam, batch in item_batches(items, album_size):
        can_album = fam in ("visual", "document") and len(batch) > 1
        if reply_mode == "post":
            reply_to = anchor_id if anchor_id is not None else (
                main_message.message_id if main_message is not None else None
            )
        else:
            reply_to = previous_id if previous_id is not None else anchor_id
        batch_caption = caption if main_message is None else None
        messages = None

        if can_album:
            media_group = None
            try:
                media_group = [
                    _album_input_media(item, batch_caption if i == 0 else None)
                    for i, item in enumerate(batch)
                ]
                messages = await send_album(media_group, reply_to)
                if messages is not None and len(messages) != len(batch):
                    raise RuntimeError(
                        f"返回消息数 {len(messages)} 与文件数 {len(batch)} 不一致"
                    )
                for member in media_group:
                    _close_item_handle(member.media)
            except NetworkError:
                if media_group:
                    for member in media_group:
                        _close_item_handle(member.media)
                # Telegram may have accepted a request before the response was
                # lost. Falling back here can duplicate an entire album.
                raise
            except Exception as exc:
                if media_group:
                    for member in media_group:
                        _close_item_handle(member.media)
                logger.warning("相册发送失败（%s），降级为逐条发送 %d 个文件",
                               exc, len(batch))
                messages = None
        if messages is None:
            messages = []
            for index, item in enumerate(batch):
                item_caption = batch_caption if index == 0 else None
                if index == 0:
                    item_reply = reply_to
                elif reply_mode == "post":
                    item_reply = reply_to if reply_to is not None else messages[0].message_id
                else:
                    item_reply = messages[-1].message_id
                messages.append(await send_one(item, item_caption, item_reply))

        for message in messages:
            sent_messages.append(message)
            if main_message is None:
                main_message = message
            previous_id = message.message_id
        if on_sent:
            on_sent(messages)

    return sent_messages, main_message
