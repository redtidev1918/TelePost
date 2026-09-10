"""投稿发布模块（薄 facade）。

历史上本模块同时承载了 Telegram 发布引擎（相册规划、回复链、评论区策略、
不确定投递语义）、API 直投编排、幂等账本和会话发布。这些职责现在分别属于：

* :mod:`telepost.telegram.delivery`  —— planner / executor / sender /
  gateway / discussion / registry（PTB 发布引擎，含 uncertain 语义）；
* :mod:`telepost.application.publication` —— PublicationService（业务编排、
  幂等、正式 PublicationOutcome）；
* :mod:`telepost.application.posts` —— 已发布帖子落库 + 搜索索引；
* :mod:`telepost.storage.sqlite` —— Repository。

本文件只保留：
1. 会话式投稿 handler（解析 update → 调 application → 渲染回复）；
2. 兼容旧调用方/测试的同名 re-export 与紧凑字符串格式转换。

旧式 ``"kind:file_id[:filename]"`` 输入与 raw PTB Message 输出在此边界转换，
新代码内部一律使用 domain 的 MediaItem / DeliveryResult。
"""
import json
import logging
import os
import time

from telegram import Update
from telegram.ext import ConversationHandler, CallbackContext

from config.settings import (
    CHANNEL_ID,
    CHAT_REVIEW_REQUIRED,
    NOTIFY_OWNER,
    OWNER_ID,
)
from database.db_manager import get_db, cleanup_old_data
from models.state import STATE
from utils.helper_functions import build_caption

# --- delivery engine (moved) ----------------------------------------------
from telepost.domain.delivery import (
    DeliveredMessage,
    DeliveryRequest,
    DeliveryResult,
    MediaItem,
    MediaKind,
    ReplyMode,
    TelegramFileId,
    LocalFile,
)
from telepost.telegram.delivery.preparation import (
    PHOTO_MAX_BYTES,
    compress_photo as _compress_photo,
    reclassify_oversized as _reclassify_oversized_items,
)
from telepost.telegram.delivery.sender import (
    PTBSender,
    file_id_of as _file_id_of,
    thumbnail_file_id_of as _thumbnail_file_id,
    timeout_kwargs as _timeout_kwargs,
)
from telepost.telegram.delivery import legacy_runner
from telepost.telegram.delivery.legacy_runner import run_item_batches as _new_run_item_batches
from telepost.telegram.delivery.discussion import (
    DiscussionDeliveryError as DiscussionPublishError,
    DiscussionStrategy,
)
from telepost.telegram.delivery.gateway import PTBTelegramDeliveryGateway
from telepost.telegram.delivery.registry import default_registry

logger = logging.getLogger(__name__)

TELEGRAM_SEND_TIMEOUT_SECONDS = max(
    5.0,
    float(os.getenv("TELEGRAM_SEND_TIMEOUT_SECONDS",
                    os.getenv("REVIEW_PREVIEW_TIMEOUT_SECONDS", "120"))),
)
CHANNEL_ALBUM_SIZE = 10
CHANNEL_ALBUM_REPLY = os.getenv("CHANNEL_ALBUM_REPLY", "chain").strip().lower()
DISCUSSION_FORWARD_TIMEOUT_SECONDS = max(
    1.0, float(os.getenv("DISCUSSION_FORWARD_TIMEOUT_SECONDS", "10"))
)

# Process-local discussion forward bookkeeping. The authoritative store is now
# ``telepost.telegram.delivery.registry.default_registry``; these module-level
# views remain as test/back-compat seams.
_discussion_forwards = default_registry._forwards
_discussion_waiters = default_registry._waiters
_recent_forwards = default_registry._recent


def _telegram_timeout_kwargs():
    return _timeout_kwargs(TELEGRAM_SEND_TIMEOUT_SECONDS)


# ---- re-exports of the legacy dict engine (shared with review previews) ----
def _is_local_item(item: dict) -> bool:
    return legacy_runner._is_local_item(item)


def _local_input_file(path: str, filename: str):
    return legacy_runner._local_input_file(path, filename)


def _close_item_handle(media):
    legacy_runner._close_item_handle(media)


def _compress_photo_impl(src_path: str, max_bytes: int) -> bool:
    return _compress_photo(src_path, max_bytes)


# Public name kept for tests/importers.
def reclassify_oversized_photos(items: list, *, max_bytes: int = PHOTO_MAX_BYTES) -> list:
    """Oversized dict-items → compress, else reclassify as document.

    Accepts/returns the legacy dict shape
    ``{"kind", "path"?, "file_id"?, "filename"?}``.
    """
    domain_items = _reclassify_oversized_items(_items_from_dicts(items), max_bytes=max_bytes)
    return _dicts_from_items(domain_items, preserve=items)


def _media_kwargs(item: dict, caption) -> dict:
    return legacy_runner._media_kwargs(item, caption)


def _album_input_media(item: dict, caption):
    return legacy_runner._album_input_media(item, caption)


def _item_batches(items: list, album_size: int):
    return legacy_runner.item_batches(items, album_size)


async def _run_item_batches(items, *, caption, album_size,
                            send_one, send_album, fallback_single=True,
                            anchor_id=None, reply_mode="chain", on_sent=None):
    return await legacy_runner.run_item_batches(
        items,
        caption=caption,
        album_size=album_size,
        send_one=send_one,
        send_album=send_album,
        fallback_single=fallback_single,
        anchor_id=anchor_id,
        reply_mode=reply_mode,
        on_sent=on_sent,
    )


# ---- compact string ↔ domain item conversions ----------------------------
def _review_items(media_list, doc_list):
    """Compact session file_id format → review payload dicts."""
    media = []
    for item in media_list:
        kind, file_id = item.split(":", 1)
        media.append({"type": kind, "file_id": file_id})
    documents = []
    for item in doc_list:
        parts = item.split(":", 2)
        file_id = parts[1] if len(parts) >= 2 else parts[0]
        filename = parts[2] if len(parts) >= 3 else "file"
        documents.append({"file_id": file_id, "filename": filename})
    return media, documents


def _normalize_chat_items(media_list, doc_list):
    """Compact "kind:file_id[:filename]" strings → unified item dicts."""
    items = []
    for entry in media_list:
        kind, file_id = entry.split(":", 1)
        items.append({"kind": kind, "file_id": file_id,
                      "spoiler_key": kind in ("photo", "video", "animation")})
    for entry in doc_list:
        parts = entry.split(":", 2)
        file_id = parts[1] if len(parts) >= 2 else parts[0]
        filename = parts[2] if len(parts) >= 3 else "file"
        items.append({"kind": "document", "file_id": file_id, "filename": filename})
    return items


def _items_from_dicts(items):
    out = []
    for it in items:
        kind = MediaKind.coerce(it["kind"])
        if it.get("path"):
            source = LocalFile(it["path"], it.get("filename") or "file")
        elif it.get("file_id") is not None:
            source = TelegramFileId(it["file_id"], it.get("filename"))
        else:
            source = TelegramFileId("", it.get("filename"))
        out.append(MediaItem(kind, source, bool(it.get("spoiler", False))))
    return out


def _dicts_from_items(items, *, preserve=None):
    by_kind_name = {}
    return [
        _item_to_dict(item) for item in items
    ]


def _item_to_dict(item: MediaItem) -> dict:
    out = {"kind": item.kind.value, "spoiler": item.spoiler}
    if item.is_local:
        out["path"] = item.source.path
        out["filename"] = item.source.filename
    else:
        out["file_id"] = item.source.file_id
        if item.source.filename:
            out["filename"] = item.source.filename
    return out


def _channel_message_ids(messages, main_message):
    """Only main-channel message ids; never discussion-chat ids."""
    main_chat_id = getattr(getattr(main_message, "chat", None), "id", None)
    return [
        message.message_id for message in messages
        if main_chat_id is None
        or getattr(getattr(message, "chat", None), "id", main_chat_id) == main_chat_id
    ]


# ---- discussion forward capture (registry backed) -------------------------
def capture_discussion_forward(update):
    """Remember a channel post's auto-forward into the linked discussion."""
    from telepost.telegram.capture import capture_discussion_forward as _cap
    _cap(update)


def _pop_recent_forward(channel_id, channel_msg_id):
    found = default_registry.pop_recent(channel_id, channel_msg_id)
    return found  # (discussion_chat_id, discussion_msg_id) or None


async def _wait_for_discussion_forward(channel_id, message_id):
    return await default_registry.wait_for_forward(
        channel_id, message_id, DISCUSSION_FORWARD_TIMEOUT_SECONDS
    )


async def _scan_recent_forward(channel_id):
    found = await default_registry.scan_recent(channel_id)
    if found is None:
        return None
    source_msg_id, dchat, dmsg = found
    return source_msg_id, dchat, dmsg


async def _delete_message(bot, chat_id, message_id):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except Exception as exc:
        msg = str(exc).lower()
        if "not found" in msg or "message can't be deleted" in msg:
            return True
        logger.exception("回滚删除消息失败 chat=%s msg=%s", chat_id, message_id)
        return False


async def _discussion_rollback(bot, sent):
    clean = True
    for chat_id, msg_id in sent.get("rest", []) + sent.get("anchor", []) + sent.get("cover", []):
        clean = await _delete_message(bot, chat_id, msg_id) and clean
    return clean


# ---- unified delivery entry ----------------------------------------------
def _build_gateway(bot, *, timeout_kwargs=None):
    return PTBTelegramDeliveryGateway(
        bot,
        send_timeout=TELEGRAM_SEND_TIMEOUT_SECONDS,
        album_size=CHANNEL_ALBUM_SIZE,
    )


def _reply_mode_from(reply_mode):
    reply_mode = (reply_mode or CHANNEL_ALBUM_REPLY) or "chain"
    return ReplyMode(reply_mode) if isinstance(reply_mode, str) else reply_mode


async def deliver_items_to_chat(bot, chat_id, items, *, caption, spoiler=False,
                                album_size=CHANNEL_ALBUM_SIZE, timeout_kwargs=None,
                                reply_to_message_id=None, reply_mode=None,
                                on_sent=None):
    """统一投递入口（频道发布与审核群预览共用）。

    items: [{"kind": photo|video|animation|audio|document,
             本地文件加 "path"+"filename"；Telegram 资源加 "file_id"(+"filename")}]
    caption 只挂在整条投递第一条消息；返回 (sent_messages, main_message)，
    其中元素是 raw PTB Message（兼容旧调用方）。
    """
    mode = _reply_mode_from(reply_mode)

    if mode is ReplyMode.DISCUSSION and len(items) > 1:
        channel = await bot.get_chat(chat_id)
        if not channel.linked_chat_id:
            raise RuntimeError("频道未关联讨论组，无法把其余图片发到主贴评论区")
        return await _deliver_discussion(
            bot, channel, items, caption=caption, spoiler=spoiler,
            album_size=album_size, timeout_kwargs=timeout_kwargs,
        )

    domain_items = _items_from_dicts(
        [dict(item, spoiler=item.get("spoiler", spoiler)) for item in items]
    )
    gateway = _build_gateway(bot, timeout_kwargs=timeout_kwargs)
    request = DeliveryRequest(
        chat_id=chat_id,
        items=domain_items,
        caption=caption,
        spoiler=spoiler,
        reply_mode=mode,
        reply_to_message_id=reply_to_message_id,
        album_size=album_size,
    )
    result = await _execute_with_on_sent(gateway, request, on_sent)
    raw_messages = [m.raw for m in result.messages if m.raw is not None]
    if not result.ok:
        if result.is_uncertain:
            # Preserve the original PTB exception type when known: callers
            # (review approval / tests) distinguish TimedOut from NetworkError
            # and must never retry a possibly-accepted album.
            original = getattr(result, "error", None)
            if original is not None:
                raise original
            from telegram.error import NetworkError
            raise NetworkError(result.reason)
        original = getattr(result, "error", None)
        if original is not None:
            raise original
        raise RuntimeError(result.reason or "delivery failed")
    return raw_messages, result.main_message.raw


async def _execute_with_on_sent(gateway, request, on_sent):
    """Deliver while mirroring raw messages to the legacy on_sent callback."""
    if on_sent is None:
        return await gateway.deliver(request)

    def _collect(messages):
        on_sent([m.raw for m in messages if m.raw is not None])

    # Chain path: on_sent belongs to the low-level executor; inject via the
    # gateway's chain delivery using the shared executor.
    from telepost.telegram.delivery.executor import execute_plan
    from telepost.telegram.delivery.planner import PlanningOrder, plan_delivery

    plan = plan_delivery(
        request.items,
        album_size=request.album_size,
        reply_mode=request.reply_mode,
        anchor_message_id=request.reply_to_message_id,
        ordering=PlanningOrder.FAMILY,
    )
    sender = PTBSender(gateway._bot, request.chat_id,
                       timeouts=gateway._timeouts())
    return await execute_plan(plan, sender, caption=request.caption,
                              on_sent=_collect)


async def _deliver_discussion(bot, channel, items, *, caption, spoiler,
                              album_size, timeout_kwargs):
    """Legacy-seamed discussion strategy: module globals stay monkeypatchable."""
    gateway = _build_gateway(bot, timeout_kwargs=timeout_kwargs)
    strategy = DiscussionStrategy(
        gateway, bot,
        registry=default_registry,
        forward_timeout=DISCUSSION_FORWARD_TIMEOUT_SECONDS,
        waiter=lambda cid, mid: _wait_for_discussion_forward(cid, mid),
        scanner=lambda cid: _scan_recent_forward(cid),
        rollback=lambda sent: _discussion_rollback(bot, sent),
    )

    async def cover(req_items, *, cap, reply_mode=ReplyMode.POST):
        dict_items = _dicts_from_items(req_items)
        sent, main = await deliver_items_to_chat(
            bot, channel.id, dict_items, caption=cap, spoiler=spoiler,
            album_size=album_size, timeout_kwargs=timeout_kwargs,
            reply_mode=reply_mode.value,
        )
        return sent, main

    # Re-implement via the old two-phase flow to preserve exact semantics and
    # the monkeypatched _wait_for_discussion_forward/_scan_recent_forward seams.
    from telegram.error import NetworkError

    linked = channel.linked_chat_id

    async def attempt():
        sent = {"cover": [], "anchor": [], "rest": []}
        first_sent = None
        try:
            first_sent, main = await deliver_items_to_chat(
                bot, channel.id, items[:1],
                caption=caption, spoiler=spoiler, album_size=album_size,
                timeout_kwargs=timeout_kwargs, reply_mode="post",
            )
        except NetworkError:
            found = await _scan_recent_forward(channel.id)
            if found is not None:
                cover_id, dchat, dmsg = found
                sent["cover"] = [(channel.id, cover_id)]
                sent["anchor"] = [(dchat, dmsg)]
                raise DiscussionPublishError(
                    "频道首贴发送响应丢失，已反查到帖子并回滚",
                    uncertain=False, sent=sent,
                )
            raise DiscussionPublishError(
                "频道首贴发送响应丢失，未在讨论区发现转发，重发一次",
                uncertain=False, sent=sent,
            )
        except Exception as exc:
            raise DiscussionPublishError(
                f"频道首贴发送失败：{exc}", uncertain=False, sent=sent
            )
        sent["cover"] = [(m.chat.id, m.message_id) for m in first_sent]

        try:
            dchat, dmsg = await _wait_for_discussion_forward(
                channel.id, main.message_id
            )
        except Exception:
            raise DiscussionPublishError(
                "等待频道帖转发到讨论组超时", uncertain=False, sent=sent
            )
        if dchat != linked:
            raise DiscussionPublishError(
                "频道自动转发落到了非预期讨论组", uncertain=False, sent=sent
            )
        sent["anchor"] = [(dchat, dmsg)]

        rest_collected = []
        try:
            rest_sent, _ = await deliver_items_to_chat(
                bot, dchat, items[1:],
                caption=None, spoiler=spoiler, album_size=album_size,
                timeout_kwargs=timeout_kwargs, reply_to_message_id=dmsg,
                reply_mode="post",
                on_sent=lambda msgs: rest_collected.extend(
                    (m.chat.id, m.message_id) for m in msgs),
            )
        except NetworkError as exc:
            sent["rest"] = rest_collected
            raise DiscussionPublishError(
                "评论区相册发送响应丢失，可能已部分送达，不自动重试",
                uncertain=True, sent=sent,
            ) from exc
        except Exception as exc:
            sent["rest"] = rest_collected
            raise DiscussionPublishError(
                f"评论区相册发送失败：{exc}", uncertain=False, sent=sent
            )
        sent["rest"] = rest_collected
        return first_sent + rest_sent, main

    import asyncio
    last = None
    for try_no in (1, 2):
        try:
            return await attempt()
        except DiscussionPublishError as exc:
            last = exc
            if exc.uncertain:
                await _discussion_rollback(bot, exc.sent)
                raise
            if not await _discussion_rollback(bot, exc.sent):
                raise DiscussionPublishError(
                    f"{exc}；且回滚未能删净，请人工检查",
                    uncertain=True, sent=exc.sent,
                )
            if try_no == 2:
                raise
            await asyncio.sleep(2.0)
    raise last or DiscussionPublishError("评论区发布失败", uncertain=True)


# ---------------------------------------------------------------------------
# Published-post archival (facade over telepost.application.posts)
# ---------------------------------------------------------------------------
async def save_published_post(user_id, message_id, data, media_list, doc_list,
                              all_message_ids=None):
    """保存已发布帖子到 DB + 搜索索引（兼容旧签名，data 为 row/dict）。"""
    from telepost.application.posts import PublishedPostInput, record_published_post

    def col(name, default=""):
        try:
            if name in data.keys():
                return data[name] if data[name] is not None else default
        except (AttributeError, TypeError):
            if isinstance(data, dict) and name in data:
                return data[name] if data[name] is not None else default
        return default

    username = col("username") or f"user{user_id}"
    post = PublishedPostInput(
        user_id=user_id,
        username=username,
        main_message_id=message_id,
        title=col("title"),
        tags=col("tags"),
        note=col("note"),
        link=col("link"),
        media_compact=list(media_list or []),
        document_compact=list(doc_list or []),
        all_message_ids=list(all_message_ids or []),
    )
    try:
        return await record_published_post(post)
    except Exception as exc:
        logger.error("保存帖子信息到数据库失败: %s", exc)
        return None


# ---------------------------------------------------------------------------
# API direct publish (publication service seam)
# ---------------------------------------------------------------------------
# Circular seam: handlers.review imports this module's publish_from_file_ids,
# so the review-side helper is resolved lazily where it is needed.
PUBLISHED_DEDUP_WINDOW_SECONDS = 7 * 86400


def _pixiv_id_from_link(link: str) -> str:
    from handlers.review import _pixiv_id_from_link as _impl
    return _impl(link)


def _link_of(message_id: int) -> str:
    channel = str(CHANNEL_ID)
    if channel.startswith("@"):
        return f"https://t.me/{channel.lstrip('@')}/{message_id}"
    return f"https://t.me/c/{channel.replace('-100', '')}/{message_id}"


def _ledger_replay(row) -> dict:
    def _get(name):
        if isinstance(row, dict):
            return row.get(name)
        try:
            return row[name]
        except (TypeError, KeyError, IndexError):
            return getattr(row, name, None)

    message_id = _get("message_id")
    return {
        "status": "published",
        "message_id": message_id,
        "link": _link_of(message_id) if message_id else "",
        "reused": True,
        "reuse_reason": "idempotent_replay",
        "matched_idempotency_key": _get("idempotency_key"),
        "delivery_status": "published",
    }


# Legacy ledger module-level helpers (kept as seams; delegate to repository).
async def _ledger_find_by_key(idempotency_key: str):
    from telepost.storage.sqlite.ledger import DeliveryLedgerRepository
    entry = await DeliveryLedgerRepository().find_by_key(idempotency_key)
    return _ledger_entry_to_row(entry) if entry else None


async def _ledger_find_work(target_id, work_type, pixiv_id, window_seconds):
    from telepost.storage.sqlite.ledger import DeliveryLedgerRepository
    entry = await DeliveryLedgerRepository().find_work(
        target_id, work_type, pixiv_id, window_seconds
    )
    return _ledger_entry_to_row(entry) if entry else None


async def _ledger_record(idempotency_key, *, target_id, pixiv_id, work_type,
                         message_id, related_message_ids, user_id):
    from telepost.storage.sqlite.ledger import DeliveryLedgerRepository
    return await DeliveryLedgerRepository().record_published(
        idempotency_key, target_id=target_id, pixiv_id=pixiv_id,
        work_type=work_type, message_id=message_id,
        related_message_ids=related_message_ids, user_id=user_id,
    )


def _ledger_entry_to_row(entry):
    from types import SimpleNamespace
    return SimpleNamespace(
        idempotency_key=entry.idempotency_key,
        message_id=entry.message_id,
        target_id=entry.target_id,
        pixiv_id=entry.pixiv_id,
        work_type=entry.work_type,
        status=entry.status,
    )


async def publish_from_files(bot, files, *, tags="", title="", note="", link="",
                             anonymous=False, spoiler=False, user_id, username="",
                             idempotency_key="", target_id="", work_type="",
                             pixiv_id="") -> dict:
    """API 本地文件直投核心：不经 Telegram 会话，直接发频道。"""
    import os as _os
    from telepost.application.publication import (
        PublicationService, PublishCommand,
    )
    from telepost.telegram.delivery.gateway import PTBTelegramDeliveryGateway

    key = idempotency_key.strip()[:240]
    pid = (pixiv_id or _pixiv_id_from_link(link or "")).strip()

    ledger = _LedgerBridge()
    replay = await ledger.find_by_key(key)
    if replay is not None:
        for fobj in files:
            try:
                _os.remove(fobj["path"])
            except OSError:
                pass
        return replay
    historical = await ledger.find_work(
        target_id, work_type, pid, PUBLISHED_DEDUP_WINDOW_SECONDS
    )
    if historical is not None and historical.get("matched_idempotency_key") != key:
        for fobj in files:
            try:
                _os.remove(fobj["path"])
            except OSError:
                pass
        historical["reuse_reason"] = "duplicate_existing"
        return historical

    items = _items_from_dicts(
        [{"kind": f["kind"], "path": f["path"], "filename": f["filename"],
          "spoiler": spoiler} for f in files]
    )
    data = {
        "tags": tags, "title": title, "note": note, "link": link,
        "spoiler": "true" if spoiler else "false",
        "anonymous": "true" if anonymous else "false",
        "user_id": user_id, "username": username,
    }

    service = PublicationService(
        delivery=_LegacyDeliveryPort(bot, reclassify_local=True),
        link_builder=_link_of,
        record_post=_make_post_recorder(data, local=True),
    )
    command = PublishCommand(
        chat_id=CHANNEL_ID,
        items=items,
        caption_data=data,
        user_id=user_id,
        spoiler=spoiler,
        username=username,
        idempotency_key=key,
        target_id=target_id,
        work_type=work_type,
        pixiv_id=pid,
        album_size=CHANNEL_ALBUM_SIZE,
    )
    outcome = await service.publish(command)
    for fobj in files:
        try:
            _os.remove(fobj["path"])
        except OSError:
            pass
    return _outcome_to_legacy(outcome, raise_on_failure=True)


async def publish_from_file_ids(bot, media, documents, *, tags="", title="",
                                note="", link="", anonymous=False, spoiler=False,
                                user_id, username="", idempotency_key="",
                                target_id="", work_type="", pixiv_id="") -> dict:
    """API file_id 直投核心：素材已在 Telegram 服务器，零媒体重传。"""
    from telepost.application.publication import (
        PublicationService, PublishCommand,
    )
    from telepost.telegram.delivery.gateway import PTBTelegramDeliveryGateway

    key = idempotency_key.strip()[:240]
    pid = (pixiv_id or _pixiv_id_from_link(link or "")).strip()

    ledger = _LedgerBridge()
    replay = await ledger.find_by_key(key)
    if replay is not None:
        return replay
    historical = await ledger.find_work(
        target_id, work_type, pid, PUBLISHED_DEDUP_WINDOW_SECONDS
    )
    if historical is not None and historical.get("matched_idempotency_key") != key:
        historical["reuse_reason"] = "duplicate_existing"
        return historical

    items = _items_from_dicts([
        {"kind": m["type"], "file_id": m["file_id"], "spoiler": spoiler}
        for m in media
    ] + [
        {"kind": "document", "file_id": d["file_id"],
         "filename": d.get("filename") or "file"}
        for d in documents
    ])
    data = {
        "tags": tags, "title": title, "note": note, "link": link,
        "spoiler": "true" if spoiler else "false",
        "anonymous": "true" if anonymous else "false",
        "user_id": user_id, "username": username,
    }
    media_compact = [f"{m['type']}:{m['file_id']}" for m in media]
    doc_compact = [
        f"document:{d['file_id']}:{d.get('filename', 'file')}" for d in documents
    ]
    service = PublicationService(
        delivery=_LegacyDeliveryPort(bot),
        link_builder=_link_of,
        record_post=_make_post_recorder(
            data, local=False, media_compact=media_compact,
            doc_compact=doc_compact,
        ),
    )
    command = PublishCommand(
        chat_id=CHANNEL_ID,
        items=items,
        caption_data=data,
        user_id=user_id,
        spoiler=spoiler,
        username=username,
        idempotency_key=key,
        target_id=target_id,
        work_type=work_type,
        pixiv_id=pid,
        album_size=CHANNEL_ALBUM_SIZE,
    )
    outcome = await service.publish(command)
    return _outcome_to_legacy(outcome, raise_on_failure=True)


def _outcome_to_legacy(outcome, *, raise_on_failure):
    if outcome.uncertain:
        # Preserve NetworkError semantics: caller must not blind-retry.
        from telegram.error import NetworkError
        raise NetworkError(outcome.reason or "delivery uncertain")
    if outcome.status == "failed":
        raise RuntimeError(outcome.reason or "所有消息发送失败")
    result = {
        "status": "published",
        "message_id": outcome.message_id,
        "link": outcome.link,
        "media_count": outcome.media_count,
        "document_count": outcome.document_count,
        "delivery_status": outcome.delivery_status,
    }
    if outcome.reused:
        result["reused"] = True
        result["reuse_reason"] = outcome.reuse_reason
        result["matched_idempotency_key"] = outcome.matched_idempotency_key
    return result


class _LegacyDeliveryPort:
    """Adapt the application-layer DeliveryRequest to the module-level
    ``deliver_items_to_chat`` facade.

    Going through the *module global* (rather than constructing a gateway
    directly) keeps the historical monkeypatch seam working and ensures every
    adapter — chat, review approval and HTTP API — shares the same engine and
    its success / certain-failure / uncertain semantics.
    """

    def __init__(self, bot, *, reclassify_local: bool = False):
        self._bot = bot
        self._reclassify_local = reclassify_local

    async def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        dict_items = _dicts_from_items(list(request.items))
        if self._reclassify_local:
            dict_items = reclassify_oversized_photos(dict_items)
            # Keep domain items aligned with reclassified dict items.
            request.items = _items_from_dicts(dict_items)
        try:
            raw_messages, raw_main = await deliver_items_to_chat(
                self._bot, request.chat_id, dict_items,
                caption=request.caption,
                spoiler=request.spoiler,
                album_size=request.album_size,
                reply_to_message_id=request.reply_to_message_id,
                reply_mode=request.reply_mode.value,
            )
        except Exception as exc:
            uncertain = (
                getattr(exc, "uncertain", False)
                or exc.__class__.__name__ == "NetworkError"
                or "timed out" in str(exc).lower()
                or "network" in str(exc).lower()
            )
            if uncertain:
                return DeliveryResult.uncertain(str(exc))
            return DeliveryResult.failed(str(exc), retryable=True)

        fallback_chat = getattr(getattr(raw_main, "chat", None), "id", None)
        messages = [
            DeliveredMessage(
                chat_id=getattr(getattr(m, "chat", None), "id", None) or fallback_chat
                        or request.chat_id,
                message_id=m.message_id,
                kind=MediaKind.coerce(_kind_of_raw_message(m)),
                file_id=_file_id_of(m),
                thumbnail_file_id=_thumbnail_file_id(m),
                raw=m,
            )
            for m in raw_messages
        ]
        main = next((m for m in messages if m.raw is raw_main), messages[0] if messages else None)
        return DeliveryResult.delivered(messages, main)


def _kind_of_raw_message(message) -> str:
    if getattr(message, "photo", None):
        return "photo"
    for attr in ("video", "animation", "audio", "document"):
        if getattr(message, attr, None):
            return attr
    return "document"


class _LedgerBridge:
    """Replay dictionaries in the exact legacy shape callers expect."""

    async def find_by_key(self, key):
        row = await _ledger_find_by_key(key)
        return _ledger_replay(row) if row is not None else None

    async def find_work(self, target_id, work_type, pid, window):
        row = await _ledger_find_work(target_id, work_type, pid, window)
        if row is None:
            return None
        out = _ledger_replay(row)
        out["reuse_reason"] = "duplicate_existing"
        return out


def _make_post_recorder(data, *, local, files=None, media_compact=None,
                        doc_compact=None):
    async def _record(command, result, media_count, document_count):
        if local:
            media_list, doc_list = [], []
            ordered_items = sorted(
                command.items,
                key=lambda it: 0 if it.kind is not MediaKind.DOCUMENT else 1,
            )
            for delivered, item in zip(result.messages, ordered_items):
                fid = delivered.file_id
                if item.kind is MediaKind.DOCUMENT:
                    doc_list.append(f"document:{fid}:{item.filename or 'file'}")
                else:
                    media_list.append(f"{item.kind.value}:{fid}")
        else:
            media_list, doc_list = media_compact or [], doc_compact or []
        await save_published_post(
            command.user_id, result.main_message.message_id, data,
            media_list, doc_list,
            result.channel_message_ids(result.main_message.chat_id),
        )
    return _record


# ---------------------------------------------------------------------------
# Chat-session publish handlers (legacy "kind:file_id" compact format)
# ---------------------------------------------------------------------------
async def handle_media_publish(context, media_list, caption, spoiler_flag):
    """聊天投稿：发布 file_id 媒体到频道。返回 (主消息, 消息ID列表)。"""
    items = _normalize_chat_items(media_list, [])
    try:
        sent, main = await deliver_items_to_chat(
            context.bot, CHANNEL_ID, items, caption=caption, spoiler=spoiler_flag
        )
    except Exception as exc:
        logger.error("发送媒体失败: %s", exc, exc_info=True)
        return (None, [])
    if not sent:
        return (None, [])
    return (main, [m.message_id for m in sent])


async def handle_document_publish(context, doc_list, caption=None,
                                  reply_to_message_id=None):
    """聊天投稿：发布文档到频道。返回主消息对象或 None。"""
    items = _normalize_chat_items([], doc_list)
    try:
        sent, main = await deliver_items_to_chat(
            context.bot, CHANNEL_ID, items, caption=caption,
            reply_to_message_id=reply_to_message_id,
        )
    except Exception as exc:
        logger.error("发送文档失败: %s", exc, exc_info=True)
        return None
    return main


def InputMediaDocumentFactory(file_handle, filename, caption):
    """兼容旧调用：本地文档文件 → InputMediaDocument（attach 模式）。"""
    from telegram import InputMediaDocument, InputFile
    return InputMediaDocument(
        media=InputFile(file_handle, filename=filename,
                        read_file_handle=False, attach=True),
        caption=caption, parse_mode="HTML" if caption else None,
        filename=filename,
    )


async def publish_submission(update: Update, context: CallbackContext) -> int:
    """聊天会话发布/入审核队列 handler（薄层）。"""
    user_id = update.effective_user.id
    publish_success = False
    is_callback = update.callback_query is not None

    async def _reply_to_user(text: str):
        if is_callback:
            try:
                await update.callback_query.answer()
            except Exception:
                pass
            try:
                await update.callback_query.edit_message_text(text)
            except Exception:
                try:
                    await update.effective_message.reply_text(text)
                except Exception as exc:
                    logger.error("发送结果通知失败: %s", exc)
        else:
            await update.message.reply_text(text)

    try:
        async with get_db() as conn:
            c = await conn.cursor()
            await c.execute("SELECT * FROM submissions WHERE user_id=?", (user_id,))
            data = await c.fetchone()

        if not data:
            await _reply_to_user("❌ 数据异常，请重新发送 /start")
            return ConversationHandler.END

        if not (data["tags"] or "").strip():
            if is_callback:
                await update.callback_query.answer("请先填写标签", show_alert=True)
            else:
                await _reply_to_user("⚠️ 发布前必须填写标签")
            return STATE['PREVIEW']

        caption = build_caption(data)
        media_list, doc_list = [], []
        try:
            if data["image_id"]:
                media_list = json.loads(data["image_id"])
        except (json.JSONDecodeError, TypeError):
            logger.warning("解析媒体数据失败，user_id: %s", user_id)
        try:
            if data["document_id"]:
                doc_list = json.loads(data["document_id"])
        except (json.JSONDecodeError, TypeError):
            logger.warning("解析文档数据失败，user_id: %s", user_id)

        if not media_list and not doc_list:
            await _reply_to_user("❌ 未检测到任何上传文件，请重新发送 /start")
            async with get_db() as conn:
                await conn.execute(
                    "DELETE FROM submissions WHERE user_id=?", (user_id,)
                )
            return ConversationHandler.END

        spoiler_value = (
            data["spoiler"] if "spoiler" in data.keys() and data["spoiler"] else "false"
        )
        spoiler_flag = spoiler_value.lower() == "true"

        if CHAT_REVIEW_REQUIRED:
            from handlers.review import queue_review_from_file_ids

            review_media, review_documents = _review_items(media_list, doc_list)
            username = (
                data["username"]
                if "username" in data.keys() and data["username"]
                else (update.effective_user.username or f"user{user_id}")
            )
            anonymous_value = (
                data["anonymous"]
                if "anonymous" in data.keys() and data["anonymous"]
                else "false"
            )
            review_result = await queue_review_from_file_ids(
                context.bot,
                review_media,
                review_documents,
                tags=data["tags"] or "",
                title=data["title"] or "",
                note=data["note"] or "",
                link=data["link"] or "",
                anonymous=str(anonymous_value).lower() == "true",
                spoiler=spoiler_flag,
                user_id=user_id,
                username=username,
                idempotency_key=f"submission:{data['timestamp']}",
                source="chat",
            )
            await _reply_to_user(
                f"✅ 投稿已进入审核队列（#{review_result['review_id']}）。\n"
                "审核完成后机器人会通知你。"
            )
            publish_success = True
            return ConversationHandler.END

        chat_items = _normalize_chat_items(media_list, doc_list)
        sent_message = None
        all_message_ids = []
        if chat_items:
            try:
                sent_messages, sent_message = await deliver_items_to_chat(
                    context.bot, CHANNEL_ID, chat_items,
                    caption=caption, spoiler=spoiler_flag,
                )
                all_message_ids = _channel_message_ids(sent_messages, sent_message)
            except Exception as exc:
                logger.error("发布到频道失败: %s", exc, exc_info=True)
                sent_message, all_message_ids = None, []

        if not sent_message:
            await _reply_to_user(
                "❌ 内容发送失败。\n"
                "您的投稿数据已保留，请稍后重新发送 /submit 并完成相同步骤，或联系管理员处理。"
            )
            return ConversationHandler.END

        if str(CHANNEL_ID).startswith('@'):
            channel_username = str(CHANNEL_ID).lstrip('@')
            submission_link = (
                f"https://t.me/{channel_username}/{sent_message.message_id}"
            )
        else:
            submission_link = "频道无公开链接"

        await _reply_to_user(
            f"🎉 投稿已成功发布到频道！\n点击以下链接查看投稿：\n{submission_link}"
        )
        publish_success = True

        await save_published_post(
            user_id, sent_message.message_id, data,
            media_list, doc_list, all_message_ids,
        )

        if NOTIFY_OWNER and OWNER_ID:
            try:
                username = data["username"] if "username" in data.keys() else f"user{user_id}"
            except (KeyError, TypeError):
                username = f"user{user_id}"
            user = update.effective_user
            real_username = user.username or username
            notification_text = (
                "📨 新投稿通知\n\n"
                "👤 投稿人信息:\n"
                f"  • ID: {user_id}\n"
                f"  • 用户名: {('@' + real_username) if user.username else real_username}\n"
                f"  • 昵称: {user.first_name}{f' {user.last_name}' if user.last_name else ''}\n\n"
                f"🔗 查看投稿: {submission_link}\n\n"
                "⚙️ 管理操作:\n"
                f"封禁此用户: /blacklist_add {user_id} 违规内容\n"
                "查看黑名单: /blacklist_list"
            )
            try:
                await context.bot.send_message(
                    chat_id=OWNER_ID, text=notification_text
                )
            except Exception:
                logger.warning("⚠️ 投稿已发布，但无法确认管理员通知是否送达")

    except Exception as exc:
        logger.error("发布投稿失败: %s", exc, exc_info=True)
        try:
            await _reply_to_user("❌ 发布失败，您的投稿数据已保留，请稍后重试或联系管理员。")
        except Exception as notify_err:
            logger.error("发送失败通知时出错: %s", notify_err)
    finally:
        if not publish_success:
            logger.warning("用户 %s 投稿未完成，会话数据已保留待恢复或超时清理", user_id)
        else:
            try:
                async with get_db() as conn:
                    await conn.execute(
                        "DELETE FROM submissions WHERE user_id=?", (user_id,)
                    )
            except Exception as exc:
                logger.error("删除数据错误: %s", exc)
        try:
            await cleanup_old_data()
        except Exception as exc:
            logger.error("清理过期数据失败: %s", exc)

    return ConversationHandler.END
