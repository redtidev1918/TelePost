"""Submission review queue — Telegram adapter (thin facade).

Business rules (state machine, atomic claim, idempotency, DTOs) live in
:mod:`services.review_service`; enqueue orchestration lives in
:mod:`telepost.application.review_queue`; the review-chat preview upload
(flood pacing, albums, file_id staging) lives in
:class:`telepost.telegram.review_stager.TelegramReviewStager`.

This module keeps the historical public names (used by tests, the API server,
main.py and the callback router) and wires them to those components. The
database stores Telegram ``file_id`` values, so pending reviews survive
restarts without retaining uploaded files.
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from typing import Optional

from config.settings import ADMIN_IDS, REVIEW_CHAT_ID
from services.review_service import (
    PublishFailedError,
    ReviewBusyError,
    ReviewError,
    ReviewNotFoundError,
    ReviewService,
    ReviewStateError,
)
from telepost.application.review_queue import (
    PUBLISHED_DEDUP_WINDOW_SECONDS,
    QueueCommand,
    ReviewQueueService,
    normalize_idempotency_key,
    pixiv_id_from_link as _pixiv_id_impl,
)
from telepost.telegram.delivery.preparation import PHOTO_MAX_BYTES
from telepost.telegram.delivery.sender import file_id_of as _file_id_of
from telepost.telegram.review_stager import TelegramReviewStager
from telepost.telegram import review_keyboard

logger = logging.getLogger(__name__)

# ---- configuration (module attributes stay monkeypatchable) --------------
REVIEW_PREVIEW_INTERVAL_SECONDS = max(
    0.0, float(os.getenv("REVIEW_PREVIEW_INTERVAL_SECONDS", "0.75"))
)
REVIEW_PREVIEW_TIMEOUT_SECONDS = max(
    5.0, float(os.getenv("REVIEW_PREVIEW_TIMEOUT_SECONDS", "120"))
)
REVIEW_PREVIEW_MAX_ATTEMPTS = 5
# 发布中途进程崩溃会把记录卡在 publishing；超过该秒数视为僵尸，允许重新认领。
from services.review_service import PUBLISHING_STALE_SECONDS  # noqa: E402
REVIEW_PREVIEW_THREAD = str(
    os.getenv("REVIEW_PREVIEW_THREAD", "1")
).strip().lower() in {"1", "true", "yes", "on"}
REVIEW_ALBUM_SIZE = max(
    1, min(10, int(os.getenv("REVIEW_ALBUM_SIZE", "5")))
)
PENDING_REVIEW_RETENTION_DAYS = max(
    0, int(os.getenv("PENDING_REVIEW_RETENTION_DAYS", "0"))
)
PENDING_REVIEW_CLEANUP_BATCH_SIZE = max(
    1, min(200, int(os.getenv("PENDING_REVIEW_CLEANUP_BATCH_SIZE", "100")))
)
# 被替换（superseded）的旧审核卡保留天数；超过后由定期维护删除 Telegram
# 预览/控制消息与行，血缘（attempt / seen）保留。0 = 不启用该清理。
SUPERSEDED_RETENTION_DAYS = max(
    0, int(os.getenv("SUPERSEDED_RETENTION_DAYS", "30"))
)
# 重抓进展看门狗：受理后超过 REMIND 分钟仍未到终态，向审核群发一次“仍在处理”
# 提醒（每个 attempt 最多一次）；超过 STALE 分钟仍无
# 终态时先查 PixivFlow durable cell；不能把仍在投递的 admitted attempt
# 凭本地时间判失败。两者为 0 时关闭对应行为。
REFETCH_PROGRESS_REMIND_MINUTES = max(
    0, int(os.getenv("REFETCH_PROGRESS_REMIND_MINUTES", "5"))
)
REFETCH_STALE_TIMEOUT_MINUTES = max(
    0, int(os.getenv("REFETCH_STALE_TIMEOUT_MINUTES", "45"))
)
REFETCH_TIMEOUT_SECONDS = 120

_PIXIV_ID_RE = re.compile(r"pixiv\.net/(?:artworks/|novel/show\.php\?id=)(\d+)")


def _pixiv_id_from_link(link: str) -> str:
    return _pixiv_id_impl(link)


# ---- service singletons ---------------------------------------------------
async def publish_from_file_ids(*args, **kwargs):
    """Compatibility seam: tests/deployment monkey-patch this name.

    Resolved lazily to avoid the handlers.publish ↔ handlers.review import
    cycle; patching ``handlers.review.publish_from_file_ids`` replaces this
    whole function, which is exactly the seam the tests rely on.
    """
    from handlers.publish import publish_from_file_ids as _impl
    return await _impl(*args, **kwargs)


async def _publish_from_file_ids(*args, **kwargs):
    return await publish_from_file_ids(*args, **kwargs)


review_service = ReviewService(_publish_from_file_ids)
queue_service = ReviewQueueService()


def _stager(bot) -> TelegramReviewStager:
    # Read globals at call time so tests monkeypatching
    # handlers.review.REVIEW_CHAT_ID / REVIEW_ALBUM_SIZE take effect.
    stager = TelegramReviewStager(
        bot,
        globals()["REVIEW_CHAT_ID"],
        album_size=globals()["REVIEW_ALBUM_SIZE"],
        preview_interval=globals()["REVIEW_PREVIEW_INTERVAL_SECONDS"],
        preview_timeout=globals()["REVIEW_PREVIEW_TIMEOUT_SECONDS"],
        preview_max_attempts=globals()["REVIEW_PREVIEW_MAX_ATTEMPTS"],
        thread=globals()["REVIEW_PREVIEW_THREAD"],
        photo_max_bytes=globals()["PHOTO_MAX_BYTES"],
        chat_id_getter=lambda: globals()["REVIEW_CHAT_ID"],
    )
    # Tests patch handlers.review.asyncio.sleep to fast-forward throttle waits.
    stager._sleep = asyncio.sleep
    return stager


# ---- back-compat UI names -------------------------------------------------
def _review_keyboard(review_id, link="", *, spoiler=False, source="api",
                     pixiv_id="", failed=False):
    return review_keyboard.review_keyboard(
        review_id, link, spoiler=spoiler, source=source,
        pixiv_id=pixiv_id, failed=failed,
    )


def _caption_data(*, tags, title, note, link, anonymous, spoiler, user_id, username):
    return {
        "tags": tags, "title": title, "note": note, "link": link,
        "anonymous": "true" if anonymous else "false",
        "spoiler": "true" if spoiler else "false",
        "user_id": user_id, "username": username,
    }


def _result_from_row(row, *, reused: bool = False, reuse_reason: str = "") -> dict:
    return ReviewQueueService.result_from_row(
        row, reused=reused, reuse_reason=reuse_reason
    )


def _source_label(source: str) -> str:
    return "Telegram 聊天" if source == "chat" else "HTTP API"


# ---- back-compat preview staging delegates -------------------------------
async def _stage_file_ids(bot, media, documents, caption: str, spoiler: bool,
                          message_ids):
    stager = _stager(bot)
    staged_media, staged_documents, preview_ids = await stager.stage_file_ids(
        media, documents, caption=caption, spoiler=spoiler
    )
    message_ids.extend(preview_ids)
    return staged_media, staged_documents


async def _stage_local_files(bot, files, caption: str, spoiler: bool, message_ids):
    stager = _stager(bot)
    staged_media, staged_documents, preview_ids, _decisions = await stager.stage_local(
        files, caption=caption, spoiler=spoiler
    )
    message_ids.extend(preview_ids)
    return staged_media, staged_documents


def _make_media_item(kind, media, *, caption=None, spoiler=False, filename=None):
    return _stager(None)._make_media_item(
        kind, media, caption=caption, spoiler=spoiler, filename=filename
    )


def _album_family(kind: str):
    return _stager(None)._family(kind)


async def _send_local_preview_single(bot, item, caption, spoiler, *,
                                     timeout_kwargs=None):
    """Back-compat single local preview send (used by tests/older callers)."""
    from telegram import InputFile
    from telepost.telegram.delivery.sender import timeout_kwargs as _tk
    timeouts = timeout_kwargs if timeout_kwargs is not None else _tk(
        globals()["REVIEW_PREVIEW_TIMEOUT_SECONDS"]
    )
    handle = open(item["path"], "rb")
    media = InputFile(handle, filename=item["filename"],
                      read_file_handle=False, attach=True)
    kwargs = dict(chat_id=REVIEW_CHAT_ID, caption=caption,
                  parse_mode="HTML" if caption else None,
                  reply_to_message_id=None, **timeouts)
    try:
        if item["kind"] == "photo":
            return await bot.send_photo(photo=media, has_spoiler=spoiler, **kwargs)
        if item["kind"] == "video":
            return await bot.send_video(video=media, has_spoiler=spoiler, **kwargs)
        if item["kind"] == "animation":
            return await bot.send_animation(animation=media, has_spoiler=spoiler,
                                            **kwargs)
        if item["kind"] == "audio":
            return await bot.send_audio(audio=media, **kwargs)
        return await bot.send_document(document=media, filename=item["filename"],
                                       **kwargs)
    finally:
        handle.close()


def _review_timeout_kwargs() -> dict:
    from telepost.telegram.delivery.sender import timeout_kwargs
    return timeout_kwargs(REVIEW_PREVIEW_TIMEOUT_SECONDS)


def _review_message_ids(row) -> list:
    import json as _json
    try:
        ids = [int(v) for v in _json.loads(row["review_message_ids"] or "[]")]
    except (TypeError, ValueError, _json.JSONDecodeError):
        ids = []
    control = row["control_message_id"]
    if control:
        ids.append(int(control))
    return list(dict.fromkeys(ids))


async def _delete_messages(bot, message_ids):
    await _stager(bot).delete_preview_messages(message_ids)


def _cleanup_local_files(files):
    import os as _os
    directories = set()
    for item in files or []:
        path = item.get("path")
        if not path:
            continue
        directories.add(_os.path.dirname(path))
        try:
            _os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("删除 API 临时文件失败: %s", path, exc_info=True)
    for directory in directories:
        try:
            _os.rmdir(directory)
        except OSError:
            pass


# ---- enqueue --------------------------------------------------------------
async def queue_review_from_file_ids(
    bot, media, documents, *, tags="", title="", note="", link="",
    anonymous=False, spoiler=False, user_id, username="",
    idempotency_key="", source="api", target_id="", source_label="",
    source_ref="", scheduled_at="", work_type="", pixiv_id="",
    refetch_request_id="", submitter_user_id=None, submitter_username="",
    actor_kind="user", actor_subject="",
) -> dict:
    """Stage a file_id submission and create a durable pending review."""
    key = normalize_idempotency_key(user_id, idempotency_key, source)
    command = QueueCommand(
        user_id=user_id, username=username, tags=tags, title=title, note=note,
        link=link, anonymous=anonymous, spoiler=spoiler, source=source,
        idempotency_key=key, target_id=target_id, work_type=work_type,
        pixiv_id=pixiv_id, source_label=source_label, source_ref=source_ref,
        scheduled_at=scheduled_at, review_chat_id=str(REVIEW_CHAT_ID),
        refetch_request_id=refetch_request_id,
        submitter_user_id=submitter_user_id,
        submitter_username=submitter_username,
        actor_kind=actor_kind, actor_subject=actor_subject,
    )
    return await queue_service.enqueue(
        command, _stager(bot), media=media, documents=documents
    )


async def queue_review_from_files(
    bot, files, *, tags="", title="", note="", link="",
    anonymous=False, spoiler=False, user_id, username="",
    idempotency_key="", source="api", target_id="", source_label="",
    source_ref="", scheduled_at="", work_type="", pixiv_id="",
    refetch_request_id="", submitter_user_id=None, submitter_username="",
    actor_kind="user", actor_subject="",
) -> dict:
    """Stage multipart API files and create a durable pending review."""
    key = normalize_idempotency_key(user_id, idempotency_key, source)
    resolved_pixiv = pixiv_id or _pixiv_id_from_link(link or "")
    command = QueueCommand(
        user_id=user_id, username=username, tags=tags, title=title, note=note,
        link=link, anonymous=anonymous, spoiler=spoiler, source=source,
        idempotency_key=key, target_id=target_id, work_type=work_type,
        pixiv_id=resolved_pixiv, source_label=source_label,
        source_ref=source_ref, scheduled_at=scheduled_at,
        review_chat_id=str(REVIEW_CHAT_ID),
        refetch_request_id=refetch_request_id,
        submitter_user_id=submitter_user_id,
        submitter_username=submitter_username,
        actor_kind=actor_kind, actor_subject=actor_subject,
    )
    return await queue_service.enqueue(
        command, _stager(bot), files=files
    )


# ---- legacy reuse/expire helpers (used by maintenance job + tests) --------
async def _find_review(idempotency_key: str):
    from telepost.storage.sqlite.reviews import ReviewRepository
    return await ReviewRepository().find_active_by_key(
        idempotency_key, PUBLISHED_DEDUP_WINDOW_SECONDS
    )


async def _find_published_work(target_id, work_type, pixiv_id):
    from telepost.storage.sqlite.reviews import ReviewRepository
    return await ReviewRepository().find_published_work(
        target_id, work_type, pixiv_id, PUBLISHED_DEDUP_WINDOW_SECONDS
    )


async def _notify_chat_submitter(bot, row, text: str):
    if row["source"] != "chat":
        return
    try:
        await bot.send_message(chat_id=row["user_id"], text=text)
    except Exception:
        logger.warning("通知聊天投稿人审核结果失败: review_id=%s", row["id"],
                       exc_info=True)


async def expire_stale_reviews(bot, *, now: Optional[float] = None) -> int:
    """Expire stale pending reviews and remove their Telegram preview messages."""
    if PENDING_REVIEW_RETENTION_DAYS <= 0:
        return 0
    from telepost.storage.sqlite.reviews import ReviewRepository

    current_time = time.time() if now is None else now
    cutoff = current_time - PENDING_REVIEW_RETENTION_DAYS * 86400
    error = (
        f"pending review expired after {PENDING_REVIEW_RETENTION_DAYS} days"
    )
    rows = await ReviewRepository().expire_pending(
        cutoff=cutoff, now=current_time,
        batch_size=PENDING_REVIEW_CLEANUP_BATCH_SIZE, error=error,
    )
    for row in rows:
        await _delete_messages(bot, _review_message_ids(row))
        await _notify_chat_submitter(
            bot, row,
            f"⌛ 你的投稿超过 {PENDING_REVIEW_RETENTION_DAYS} 天未审核，已自动过期。",
        )
    if rows:
        logger.info("已过期并清理 %d 条待审核投稿（保留 %d 天）",
                    len(rows), PENDING_REVIEW_RETENTION_DAYS)
    return len(rows)


async def cleanup_superseded_reviews(bot, *, now: Optional[float] = None) -> int:
    """Delete old superseded (replaced) review cards and their rows.

    A superseded review is an OLD version of a review chain; once it is older
    than ``SUPERSEDED_RETENTION_DAYS`` it has no live value — the new review
    owns the chain. The sweep deletes its Telegram preview/control messages and
    the row, but NEVER the lineage (``refetch_attempts`` /
    ``refetch_seen_candidates``): the audit trail survives card cleanup.
    ``0`` disables the sweep. Follows the same oldest-first batch shape as
    ``expire_stale_reviews`` (guarded, idempotent deletes).
    """
    if SUPERSEDED_RETENTION_DAYS <= 0:
        return 0
    from telepost.storage.sqlite.reviews import ReviewRepository

    current_time = time.time() if now is None else now
    cutoff = current_time - SUPERSEDED_RETENTION_DAYS * 86400
    rows = await ReviewRepository().list_old_superseded(
        cutoff=cutoff, limit=PENDING_REVIEW_CLEANUP_BATCH_SIZE
    )
    for row in rows:
        try:
            await _delete_messages(bot, _review_message_ids(row))
        except Exception:
            logger.debug("删除旧审核卡消息失败: review_id=%s",
                         row["id"], exc_info=True)
        await ReviewRepository().delete(row["id"])
    if rows:
        logger.info("已清理 %d 条被替换的旧审核卡（保留 %d 天）",
                    len(rows), SUPERSEDED_RETENTION_DAYS)
    return len(rows)


async def monitor_refetch_progress(bot, *, now: Optional[float] = None) -> int:
    """重抓崩溃兜底：一次进展提醒 + 超时失败通知。

    Scans durable refetch attempts that are still active (requested/admitted):

    * older than REFETCH_PROGRESS_REMIND_MINUTES and not reminded recently →
      send one 「仍在处理中」 reminder to the review group per attempt;
    * older than REFETCH_STALE_TIMEOUT_MINUTES → fail only unadmitted requests;
      admitted attempts are reconciled against PixivFlow's durable cell. A
      pending delivery or unavailable remote stays active for remote recovery.

    Returns the number of attempts acted on. Disabled when both knobs are 0.
    """
    from telepost.storage.sqlite.refetch import RefetchRepository

    if REFETCH_PROGRESS_REMIND_MINUTES <= 0 and REFETCH_STALE_TIMEOUT_MINUTES <= 0:
        return 0
    current_time = time.time() if now is None else now
    remind_seconds = REFETCH_PROGRESS_REMIND_MINUTES * 60
    stale_seconds = REFETCH_STALE_TIMEOUT_MINUTES * 60
    cutoff_remind = current_time - remind_seconds
    cutoff_fail = current_time - stale_seconds

    repo = RefetchRepository()
    acted = 0
    for row, kind in await repo.active_since(
        cutoff_remind=cutoff_remind, cutoff_fail=cutoff_fail
    ):
        review_id = row["source_review_id"]
        minutes = int((current_time - row["created_at"]) // 60)
        if kind == "stale":
            if stale_seconds <= 0:
                continue
            if row["state"] == "requested":
                if await repo.mark_failed(row["request_id"], "admission_timeout"):
                    acted += 1
                    try:
                        await context_bot_send(
                            bot, f"⚠️ 审核 #{review_id} 重抓请求超时，当前稿件未变，请稍后重试。"
                        )
                    except Exception:
                        logger.debug("发送重抓超时通知失败: review_id=%s",
                                     review_id, exc_info=True)
                continue
            from telepost.storage.sqlite.reviews import ReviewRepository
            source = await ReviewRepository().get(review_id)
            if source is None or source["status"] != "pending":
                _, _, changed = await repo.apply_outcome(
                    row["request_id"], "failed", reason="source_review_resolved",
                )
                acted += int(changed)
                continue
            try:
                remote_state = await asyncio.to_thread(
                    _read_pixivflow_refetch_status,
                    source["target_id"], row["request_id"],
                )
            except Exception as exc:
                # A durable outbox may still be delivering after 45 minutes.
                # Transport failure is not evidence that the work failed.
                logger.warning("重抓远端状态不可用: review_id=%s error=%s",
                               review_id, type(exc).__name__)
                remote_state = ""
            if remote_state in {"no_candidate", "duplicate", "failed", "submitted"}:
                disposition = ("no_alternative" if remote_state in {"no_candidate", "duplicate"}
                               else "failed")
                reason = ("delivery_uncorrelated" if remote_state == "submitted"
                          else "remote_failed" if remote_state == "failed" else "")
                _, applied, changed = await repo.apply_outcome(
                    row["request_id"], disposition, reason=reason,
                )
                if changed:
                    acted += 1
                    if applied == "no_alternative":
                        message = f"📭 审核 #{review_id} 没有找到新的可替换作品，当前稿件保持不变。"
                    elif applied == "failed":
                        message = f"⚠️ 审核 #{review_id} 重抓失败，当前稿件未变，请稍后重试。"
                    else:
                        message = ""
                    if message:
                        try:
                            await context_bot_send(bot, message)
                        except Exception:
                            logger.debug("发送重抓终态通知失败: review_id=%s",
                                         review_id, exc_info=True)
                continue
            # Active remote state or unknown transport result: one reminder,
            # then wait for the PixivFlow terminal callback/recovery owner.
        if remind_seconds <= 0:
            continue
        last = row["last_progress_notified_at"] or 0
        if last:
            continue  # one delayed reminder per attempt; terminal result is authoritative
        await repo.bump_progress_notified(row["request_id"], current_time)
        acted += 1
        try:
            await context_bot_send(
                bot,
                f"🔄 审核 #{review_id} 重抓仍在处理中（已运行约 {minutes} 分钟），"
                "有新结果会第一时间在本群通知。",
            )
        except Exception:
            logger.debug("发送重抓进展提醒失败: review_id=%s",
                         review_id, exc_info=True)
    return acted


async def context_bot_send(bot, text: str) -> None:
    await bot.send_message(chat_id=REVIEW_CHAT_ID, text=text)


async def reconcile_incomplete_reviews(bot, *, stale_seconds: float = 60.0) -> int:
    """Repair crash-left review rows by restoring a durable control message."""
    repaired = await queue_service.reconcile_incomplete(
        _stager(bot), stale_seconds=stale_seconds
    )
    if repaired:
        logger.info("已修复 %d 条缺少控制消息的审核记录", repaired)
    return repaired


async def _load_review_for_action(query, review_id):
    return await review_service.get_row(review_id)


# ---- callback handlers ----------------------------------------------------
async def _answer(query, text=None, **kwargs):
    try:
        await query.answer(text=text, **kwargs)
    except Exception:
        logger.debug("回应审核按钮失败", exc_info=True)


async def toggle_review_spoiler(update, context):
    query = update.callback_query
    if update.effective_user.id not in ADMIN_IDS:
        await _answer(query, "你没有审核权限", show_alert=True)
        return
    try:
        review_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await _answer(query, "无效的审核记录", show_alert=True)
        return

    try:
        await review_service.toggle_spoiler(
            review_id, actor=update.effective_user.id
        )
    except ReviewNotFoundError:
        await _answer(query, "审核记录不存在", show_alert=True)
        return
    except ReviewError as error:
        await _answer(query, "该投稿已处理，无法修改遮罩", show_alert=True)
        logger.debug("审核遮罩切换失败: %s", error)
        return

    row = await _load_review_for_action(query, review_id)
    new_spoiler = bool(row["spoiler"])
    await _answer(query, f"遮罩已{'开启' if new_spoiler else '关闭'}")
    try:
        await query.edit_message_reply_markup(
            reply_markup=_review_keyboard(
                review_id, row["link"], spoiler=new_spoiler,
                source=row["source"],
                pixiv_id=_pixiv_id_from_link(row["link"] or ""),
            )
        )
    except Exception:
        logger.debug("刷新审核键盘失败（遮罩已入库）: review_id=%s", review_id)


def _submit_pixivflow_refetch(target_id: str, request_id: str,
                              correlation_id: str = "") -> dict:
    base = os.environ["PIXIVFLOW_REFETCH_BASE_URL"].rstrip("/")
    token = os.environ["PIXIVFLOW_REFETCH_TOKEN"]
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("无效的 PixivFlow 重抓地址")
    url = f"{base}/internal/targets/{quote(target_id, safe='')}/refetch"
    body = {"requestId": request_id}
    if correlation_id:
        body["correlationId"] = correlation_id
    request = Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=REFETCH_TIMEOUT_SECONDS) as response:
            result = json.load(response)
            if response.status != 202 or result.get("status") != "accepted":
                raise RuntimeError(f"PixivFlow 拒绝重抓（HTTP {response.status}）")
            return result
    except HTTPError as error:
        raise RuntimeError(f"PixivFlow 拒绝重抓（HTTP {error.code}）") from error


def _read_pixivflow_refetch_status(target_id: str, request_id: str) -> str:
    """Read the durable remote cell before declaring an admitted attempt stale."""
    base = os.environ["PIXIVFLOW_REFETCH_BASE_URL"].rstrip("/")
    token = os.environ["PIXIVFLOW_REFETCH_TOKEN"]
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("无效的 PixivFlow 重抓地址")
    url = (f"{base}/internal/targets/{quote(target_id, safe='')}/refetch/"
           f"{quote(request_id, safe='')}")
    request = Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")
    with urlopen(request, timeout=10) as response:
        result = json.load(response)
        if response.status != 200 or result.get("requestId") != request_id:
            raise ValueError("PixivFlow 重抓状态不匹配")
        return str(result.get("state") or "")


def _classify_refetch_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "http 401" in lowered or "http 403" in lowered:
        return "unauthorized"
    if "http 404" in lowered:
        return "not_found"
    if "http 5" in lowered:
        return "remote_error"
    if "timed out" in lowered or "timeout" in lowered or "超时" in message:
        return "timeout"
    return "network_error"


async def _record_refetch_event(event: str, *, review_id: int, request_id: str,
                                chain_id: str, generation: int, actor: Optional[int],
                                **fields) -> None:
    from telepost.observability import audit
    try:
        await audit.record_event(
            event, review_id=review_id, actor=f"telegram_user:{actor}" if actor else None,
            execution_id=request_id, detail={
                "request_id": request_id,
                "review_chain_id": chain_id,
                "generation": generation,
                **fields,
            },
        )
    except Exception:
        logger.debug("记录重抓审计事件失败: %s", event, exc_info=True)


def _refetch_replay_text(row) -> str:
    state = row["state"]
    return {
        "requested": "正在重抓，请稍候",
        "admitted": "正在重抓，请稍候",
        "replaced": "重抓已完成，新候选已进入审核队列",
        "no_alternative": "没有找到新的可替换作品，当前稿件保持不变。稍后有新候选时可以再次重抓。",
        "failed": "重抓失败，当前稿件未变，请稍后重试",
        "obsolete": "该审核稿已结束，请操作最新审核稿",
    }.get(state, "正在重抓，请稍候")


async def refetch_review(update, context):
    """审核群「重抓/换一张」：把当前 pending 审核稿替换为另一个候选。

    Thin Telegram adapter over the shared application command
    (:func:`telepost.application.refetch.request_refetch`) — the Mini App API
    calls the exact same service, so Bot and Mini App share one state machine,
    one idempotency and one audit trail (§39, §4).
    """
    query = update.callback_query
    if update.effective_user.id not in ADMIN_IDS:
        await _answer(query, "你没有审核权限", show_alert=True)
        return
    try:
        review_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await _answer(query, "无效的审核记录", show_alert=True)
        return

    from telepost.application.refetch import (
        RefetchAlreadyRunningError,
        RefetchError,
        RefetchNotConfiguredError,
        RefetchNotFoundError,
        RefetchStateError,
        request_refetch,
    )

    # Stable per-click identity: the Telegram callback_query.id is unique per
    # press and stable across webhook redelivery of that same press, so a
    # transport retry converges on ONE attempt (and ONE request id) while a NEW
    # intentional click (new id) starts a new generation.
    callback_id = getattr(query, "id", None)
    callback_key = (
        f"cb:{review_id}:{callback_id}"
        if callback_id is not None
        else f"cb:{review_id}:{uuid.uuid4().hex}"
    )

    async def _notify_review_group(outcome: str, payload) -> None:
        if outcome == "accepted":
            text = (
                f"🔄 审核 #{review_id} 已提交重抓，PixivFlow 正在查找新的候选作品；"
                "有新作品时会替换进审核队列。"
            )
        else:
            text = f"⚠️ 审核 #{review_id} 重抓失败，当前稿件未变，请稍后重试。"
        try:
            await context.bot.send_message(chat_id=REVIEW_CHAT_ID, text=text)
        except Exception:
            logger.debug("发送重抓提示失败: review_id=%s", review_id, exc_info=True)

    try:
        result = await request_refetch(
            review_id, actor=update.effective_user.id,
            surface="telegram_bot", callback_key=callback_key,
            on_remote_result=_notify_review_group,
        )
    except RefetchNotFoundError:
        await _answer(query, "审核记录不存在", show_alert=True)
        return
    except RefetchStateError as exc:
        await _answer(query, str(exc), show_alert=True)
        return
    except RefetchAlreadyRunningError:
        await _answer(query, "正在重抓，请稍候", show_alert=True)
        return
    except RefetchNotConfiguredError as exc:
        await _answer(query, str(exc), show_alert=True)
        return
    except RefetchError as exc:
        await _answer(query, str(exc)[:200], show_alert=True)
        return

    if result.get("replayed"):
        # Same click redelivered: answer with the attempt's current state.
        await _answer(query, _refetch_replay_text(
            {"state": result["state"]} if result["state"] else {}), show_alert=True)
        return

    await _answer(query, "已提交重抓，找到新候选后会替换进本群", show_alert=True)


async def approve_review(update, context):
    query = update.callback_query
    if update.effective_user.id not in ADMIN_IDS:
        await _answer(query, "你没有审核权限", show_alert=True)
        return
    await _answer(query)
    try:
        review_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ 无效的审核记录")
        return

    async def _show_publishing(row):
        try:
            from telegram import InlineKeyboardMarkup
            await query.edit_message_text(
                f"🚀 审核 #{review_id} 正在发布…多图+评论串可能要几十秒，请勿重复点击。",
                reply_markup=InlineKeyboardMarkup([]),
            )
        except Exception:
            pass

    try:
        result = await review_service.approve(
            context.bot, review_id,
            actor=update.effective_user.id, source="telegram",
            on_claim=_show_publishing,
        )
    except ReviewNotFoundError:
        await query.edit_message_text("❌ 审核记录不存在")
        return
    except ReviewBusyError:
        await query.edit_message_text("ℹ️ 该投稿当前状态：publishing")
        return
    except ReviewStateError as error:
        await query.edit_message_text(f"ℹ️ {error.message}")
        return
    except PublishFailedError as error:
        row = await _load_review_for_action(query, review_id)
        await query.edit_message_text(
            f"⚠️ 审核 #{review_id} {error.retry_hint}：\n{error.message}",
            reply_markup=_review_keyboard(
                review_id, row["link"],
                spoiler=bool(row["spoiler"]), source=row["source"],
                pixiv_id=_pixiv_id_from_link(row["link"] or ""),
                failed=True,
            ),
        )
        return

    await query.edit_message_text(
        f"✅ 审核 #{review_id} 已发布\n{result.link or ''}",
        disable_web_page_preview=True,
    )


async def reject_review(update, context):
    query = update.callback_query
    if update.effective_user.id not in ADMIN_IDS:
        await _answer(query, "你没有审核权限", show_alert=True)
        return
    await _answer(query)
    try:
        review_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ 无效的审核记录")
        return

    try:
        await review_service.reject(
            context.bot, review_id,
            actor=update.effective_user.id, source="telegram",
        )
    except ReviewNotFoundError:
        await query.edit_message_text("❌ 审核记录不存在")
        return
    except ReviewStateError as error:
        await query.edit_message_text(f"ℹ️ {error.message}")
        return

    await query.edit_message_text(f"❌ 审核 #{review_id} 已拒绝")
