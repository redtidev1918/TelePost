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
from typing import Optional, Tuple

from config.settings import ADMIN_IDS, REVIEW_CHAT_ID
from services.review_service import (
    PublishFailedError,
    ReviewBusyError,
    ReviewError,
    ReviewNotFoundError,
    ReviewService,
    ReviewStateError,
)
from telepost.application import pixivflow_jobs as pixivflow_jobs_port
from telepost.domain import refetch_state as fsm
from telepost.storage.sqlite.refetch import RefetchRepository
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
    1, min(10, int(os.getenv("REVIEW_ALBUM_SIZE", "10")))
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
# 重抓生命周期兜底（P0）。四道闸门各自独立，全部为 0 时才关闭兜底：
#   1. REMIND：每 REMIND 分钟向审核群播报一次进展（阶段 + 已等待 + 任务ID），
#      不再“每个 attempt 只提醒一次”——操作者必须始终看得到它在动；
#   2. STAGE：远端仍在 working 但阶段连续 STAGE 分钟没有前进 → timeout
#      （stalled_no_progress）；远端状态读不出来（空/未知）同样按此判定。
#      绝不能出现“无限 SEARCHING”；
#   3. WAKE：机器不可达且超过 WAKE 分钟时，用同一个 request UUID 幂等唤醒
#      PixivFlow（Fly Proxy 拉起机器，PixivFlow 恢复既有 manual slot）；
#   4. HARD：绝对上限，任何分支超过 HARD 分钟仍未终态 → timeout(stalled)。
#      admission 超时（STALE）只针对还没被远端接受的 requested attempt。
REFETCH_PROGRESS_REMIND_MINUTES = max(
    0, int(os.getenv("REFETCH_PROGRESS_REMIND_MINUTES", "2"))
)
REFETCH_STAGE_TIMEOUT_MINUTES = max(
    0, int(os.getenv("REFETCH_STAGE_TIMEOUT_MINUTES", "15"))
)
REFETCH_STALE_TIMEOUT_MINUTES = max(
    0, int(os.getenv("REFETCH_STALE_TIMEOUT_MINUTES", "20"))
)
# 幂等 wake：机器不可达（停机）且超过 WAKE 分钟无进展时，TelePost 用同一个
# request UUID 再调一次 PixivFlow refetch（Fly Proxy 拉起机器，PixivFlow 找到
# 既有 manual slot 继续执行，绝不新建业务）。
REFETCH_WAKE_MINUTES = max(
    0, int(os.getenv("REFETCH_WAKE_MINUTES", "12"))
)
# 硬性 SLA：超过 HARD 分钟仍未形成任何 terminal outcome 时，TelePost 必须把
# attempt 明确标 timeout(stalled_after_hard_timeout) 并通知审核群，绝不永久 running。
# 生产实测：一个合法重抓槽位可以跑 2–20 分钟（曾排队 10 小时），所以绝对上限必须
# 宽于任何「阶段」预算（STAGE=15、QUEUED=30），只能作为最后的兜底。
REFETCH_HARD_TIMEOUT_MINUTES = max(
    0, int(os.getenv("REFETCH_HARD_TIMEOUT_MINUTES", "90"))
)
REFETCH_TIMEOUT_SECONDS = 120
# 作业心跳循环（P0）：refetch 是一等持久 Job，心跳间隔必须远小于最小超时，
# 300 秒的 cleanup 只作为 force=True 的兜底（同一个实现，绝不出现两个看门狗）。
REFETCH_POLL_INTERVAL_SECONDS = max(
    5, int(os.getenv("REFETCH_POLL_INTERVAL_SECONDS", "30"))
)
# 远端仍报 pending（从未被 worker 领取）超过该时长 → timeout(queued_too_long)。
# 生产实测：worker 忙时排队可长达数小时，因此这是「排队」预算，不是「停摆」。
REFETCH_QUEUED_TIMEOUT_MINUTES = max(
    0, int(os.getenv("REFETCH_QUEUED_TIMEOUT_MINUTES", "30"))
)
# 失败轮询的退避上限（指数退避 base = 轮询间隔，cap = 5 分钟）。
REFETCH_POLL_BACKOFF_MAX_SECONDS = max(
    REFETCH_POLL_INTERVAL_SECONDS,
    int(os.getenv("REFETCH_POLL_BACKOFF_MAX_SECONDS", "300")),
)
# 重启恢复时「最多为多久以前的卡片补发终态通知」：更老的历史行（生产上最长
# 30 天、notify_count=0 的静默终态）只由 doctor 以 WARN 呈现，绝不回头去消息一
# 张早已失效的审核卡片。
REFETCH_RECOVERY_NOTIFY_MAX_AGE_SECONDS = max(
    3600, int(os.getenv("REFETCH_RECOVERY_NOTIFY_MAX_AGE_SECONDS", str(7 * 86400)))
)

# 远端时间戳「新鲜」窗口：宽于一轮轮询，容忍网络抖动与时钟误差。
REFETCH_REMOTE_LIVE_SECONDS = max(
    REFETCH_POLL_INTERVAL_SECONDS * 2,
    int(os.getenv("REFETCH_REMOTE_LIVE_SECONDS", "90")),
)

# 进程内幂等护栏：对同一 attempt 最多发一次 wake（restart 后最多再发一次；
# PixivFlow 端按 request UUID 幂等恢复，重复 wake 无副作用）。
_wake_pinged: set = set()
# 重启恢复扫描「每进程一次」的护栏（in-memory，进程重启即失效——这正是它要的）。
_refetch_recovery_done = False

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
                     pixiv_id="", failed=False, submitter_user_id=None,
                     actor_kind="user", actor_subject=""):
    # §submission-entrypoint: review/staging cards never expose the public
    # submission acquisition CTA (it belongs only to final Channel Publications).
    return review_keyboard.review_keyboard(
        review_id, link, spoiler=spoiler, source=source,
        pixiv_id=pixiv_id, failed=failed,
        submitter_user_id=submitter_user_id, actor_kind=actor_kind,
        actor_subject=actor_subject,
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
                          message_ids, cover_url=None):
    stager = _stager(bot)
    staged_media, staged_documents, preview_ids = await stager.stage_file_ids(
        media, documents, caption=caption, spoiler=spoiler, cover_url=cover_url
    )
    message_ids.extend(preview_ids)
    return staged_media, staged_documents


async def _stage_local_files(bot, files, caption: str, spoiler: bool, message_ids,
                             cover_url=None):
    stager = _stager(bot)
    staged_media, staged_documents, preview_ids, _decisions = await stager.stage_local(
        files, caption=caption, spoiler=spoiler, cover_url=cover_url
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
    submitter_display_name="",
    actor_kind="user", actor_subject="",
    review_chain_id="", generation=0, supersedes_review_id=None,
    data_class="real",
    media_assets=None,
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
        submitter_display_name=submitter_display_name,
        actor_kind=actor_kind, actor_subject=actor_subject,
        review_chain_id=review_chain_id, generation=generation,
        supersedes_review_id=supersedes_review_id,
        data_class=data_class,
        media_assets=tuple(media_assets or ()),
    )
    return await _enqueue_reporting_dropped(
        bot, refetch_request_id,
        lambda: queue_service.enqueue(
            command, _stager(bot), media=media, documents=documents
        ),
    )


async def queue_review_from_files(
    bot, files, *, tags="", title="", note="", link="",
    anonymous=False, spoiler=False, user_id, username="",
    idempotency_key="", source="api", target_id="", source_label="",
    source_ref="", scheduled_at="", work_type="", pixiv_id="",
    refetch_request_id="", submitter_user_id=None, submitter_username="",
    submitter_display_name="",
    actor_kind="user", actor_subject="",
    data_class="real",
    media_assets=None,
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
        submitter_display_name=submitter_display_name,
        actor_kind=actor_kind, actor_subject=actor_subject,
        data_class=data_class,
        media_assets=tuple(media_assets or ()),
    )
    return await _enqueue_reporting_dropped(
        bot, refetch_request_id,
        lambda: queue_service.enqueue(command, _stager(bot), files=files),
    )


async def _enqueue_reporting_dropped(bot, refetch_request_id: str, enqueue):
    """Enqueue a submission; if its refetch attempt already ended, say so once.

    §refetch-terminal-notify — a replacement that arrives after the source review
    was decided is dropped by the refetch state machine (the enqueue raises), and
    the caller only sees an error status. The review group must be told
    explicitly instead of losing the candidate silently.
    """
    try:
        return await enqueue()
    except ValueError as exc:
        message = str(exc)
        if refetch_request_id and ("cancelled" in message or "obsolete" in message
                                   or "unknown" in message):
            await _report_dropped_replacement(bot, refetch_request_id)
        raise


_REFETCH_SOURCE_STATUS_LABELS = {
    "pending": "待审核",
    "approved": "已通过",
    "rejected": "已拒绝",
    "expired": "已过期",
    "published": "已发布",
    "superseded": "已被替换",
}


async def _report_dropped_replacement(bot, refetch_request_id: str) -> None:
    """Best-effort notice that a late replacement was discarded (never raises).

    §refetch-terminal-notify — the delivery intake refuses the replacement with a
    frozen HTTP 400 body, so this is the ONLY thing the moderator ever sees about
    it. The attempt's own audit event is written by the application layer
    (``review_queue._record_dropped_replacement``); this function owns the
    user-visible half.
    """
    try:
        from telepost.storage.sqlite.reviews import ReviewRepository
        attempt = await RefetchRepository().find_by_request_id(refetch_request_id)
        if attempt is None:
            return
        review_id = int(attempt["source_review_id"] or 0)
        created = attempt["created_at"] or 0
        status = ""
        try:
            row = await ReviewRepository().get(review_id)
            status = str(row["status"] or "") if row is not None else ""
        except Exception:
            logger.debug("读取重抓源审核状态失败: review_id=%s", review_id,
                         exc_info=True)
        label = _REFETCH_SOURCE_STATUS_LABELS.get(status, status or "已结束")
        await context_bot_send(
            bot,
            f"🔄 重抓未生效：审核 #{review_id} 已结束（当前状态：{label}），"
            "替换作品已丢弃，当前稿件保持不变。"
            f"\n任务ID：refetch-{review_id}-{int(created)}",
        )
        await refresh_refetch_card(bot, review_id)
    except Exception:
        logger.debug("发送重抓结果丢弃通知失败", exc_info=True)


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


_REFETCH_TERMINAL_OUTCOMES = {"no_candidate", "duplicate", "failed", "submitted"}
_REFETCH_NO_CANDIDATE = {"no_candidate", "duplicate"}


def _refetch_heartbeat_disabled() -> bool:
    """True only when EVERY lifecycle knob is 0 (the documented kill switch)."""
    return max(
        REFETCH_PROGRESS_REMIND_MINUTES, REFETCH_STAGE_TIMEOUT_MINUTES,
        REFETCH_STALE_TIMEOUT_MINUTES, REFETCH_WAKE_MINUTES,
        REFETCH_HARD_TIMEOUT_MINUTES, REFETCH_QUEUED_TIMEOUT_MINUTES,
    ) <= 0


def _refetch_client(*, wake_producer: bool = False):
    """The PixivFlow job port (env is read per call so tests/deploys can swap).

    The HTTP transport is passed in from HERE (``urlopen`` is this module's
    global) so the long-standing test/deploy seam ``review.urlopen`` keeps
    working while the port itself stays the only place that knows URL shapes.

    ``wake_producer`` raises the budget of the capability NEGOTIATION hop ONLY.
    PixivFlow is designed to sit stopped and cold-start on demand, so the manual
    refetch submit is the caller that must absorb a Fly-proxy cold start: on
    2026-09-28 a 10s read budget expired 0.3s before the woken machine answered
    and a legal refetch was written off as a terminal ``timeout`` (审核 #142).
    Status reads keep the tight budget, so a poll can never hold a worker thread
    for minutes waiting on a producer nobody asked to wake.
    """
    return pixivflow_jobs_port.HttpPixivFlowJobClient(
        transport=urlopen,
        negotiation_timeout=(
            pixivflow_jobs_port.NEGOTIATION_TIMEOUT_SECONDS if wake_producer
            else None
        ),
    )


def _refetch_remote_live(remote_heartbeat, now: float) -> bool:
    """Protocol-only liveness: is the remote timestamp recent enough?"""
    if remote_heartbeat is None:
        return False
    try:
        return (now - float(remote_heartbeat)) <= REFETCH_REMOTE_LIVE_SECONDS
    except (TypeError, ValueError):
        return False


def _refetch_backoff_seconds(failures: int) -> float:
    """Capped exponential backoff (base = poll interval, cap = 5 minutes)."""
    base = float(REFETCH_POLL_INTERVAL_SECONDS)
    exponent = max(0, min(int(failures or 1) - 1, 6))
    return min(base * (2 ** exponent), float(REFETCH_POLL_BACKOFF_MAX_SECONDS))


async def _fetch_refetch_snapshot(target_id: str, request_id: str) -> dict:
    """Read one remote job snapshot through the PORT; return protocol fields.

    Only ``state`` plus the liveness timestamps cross this boundary — no
    PixivFlow business concept (slot/disposition/…) is interpreted here.
    Raises :class:`PixivFlowJobError` (error.code vocabulary) on failure.
    """
    def _read():
        # The heartbeat loop reads the protocol view (state + liveness stamps).
        try:
            result = _read_pixivflow_refetch_status(
                target_id, request_id, detail=True,
            )
        except TypeError:
            # A 2-argument seam (tests/deployments patching the old signature)
            # is honoured: it yields the status string only, i.e. no remote
            # liveness — the honour-system fallback of this deployment.
            result = _read_pixivflow_refetch_status(target_id, request_id)
        if isinstance(result, dict):
            return {
                "state": str(result.get("state") or ""),
                "heartbeat": result.get("heartbeat"),
                "job_id": str(result.get("job_id") or ""),
                "error_code": str(result.get("error_code") or ""),
            }
        return {"state": str(result or ""), "heartbeat": None}

    return await asyncio.to_thread(_read)


async def _refetch_poll_notify(bot, text: str, wakeup=None) -> bool:
    """Send one moderator-facing message; never raises (a notify is cosmetic).

    Returns True when the message actually left, so the caller can bump the
    durable ``notify_count`` (the "exactly one user-visible notification"
    counter the stress test and doctor read).
    """
    try:
        await context_bot_send(bot, text)
        return True
    except Exception:
        logger.debug("发送重抓通知失败", exc_info=True)
        return False


async def _refetch_refresh_card(bot, review_id: int, *, minutes=None,
                                stage_label: str = "", task_id: str = "") -> None:
    """Repaint the card; a card that is already gone is not an error."""
    try:
        await refresh_refetch_card(bot, review_id, minutes=minutes,
                                   stage_label=stage_label, task_id=task_id)
    except Exception:
        logger.debug("刷新重抓卡片失败: review_id=%s", review_id, exc_info=True)


async def _refetch_terminal_notify(bot, repo, row, *, review_id: int,
                                   task_id: str, text: str,
                                   stage_label: str = "") -> bool:
    """Guarantee the ONE user-visible notification of a terminal transition.

    Idempotent per attempt via the dedicated ``terminal_notified_at`` clock: a
    state can only become terminal once, so this sends exactly one terminal
    message and never re-notifies on a later tick, retry or process restart.
    Progress reminders do NOT consume this claim — otherwise a terminal state
    arriving after a few reminders would end silently (the production defect).
    Cards older than the recovery window are never re-messaged: a dead card
    must stay dead.
    """
    if int(row["notify_count"] or 0) > 0 and row["terminal_notified_at"]:
        return False
    if not _refetch_card_notifiable(row, task_id=task_id):
        return False
    message = f"{text}\n任务ID：{task_id}"
    sent = await _refetch_poll_notify(bot, message)
    if not sent:
        # A swallowed terminal notice IS the production defect. The attempt is
        # already terminal, so the poller will never revisit it: hand the text
        # to the durable outbox (flushed by the 300s cleanup job) and leave a
        # doctor-visible audit trail. Still claim the one-shot clock below so a
        # concurrent tick cannot enqueue a second copy — the outbox row is the
        # notification, and its idempotency key makes it exactly-once.
        await _enqueue_refetch_terminal_notice(
            row, review_id=review_id, task_id=task_id, text=message,
        )
    await repo.bump_terminal_notified(row["request_id"], time.time())
    await _refetch_refresh_card(bot, review_id, stage_label=stage_label,
                                task_id=task_id)
    return sent


async def _enqueue_refetch_terminal_notice(row, *, review_id: int,
                                           task_id: str, text: str) -> bool:
    """Persist a terminal notice whose direct send failed (§refetch-terminal-notify).

    Never raises: this is itself a best-effort fallback, and the audit event is
    what makes the failure observable when even the outbox write fails.
    """
    from telepost.observability import audit
    from telepost.storage.sqlite.submitter_notifications import (
        SubmitterNotificationRepository,
    )

    request_id = str(row["request_id"] or "")
    queued = False
    try:
        queued = await SubmitterNotificationRepository().enqueue_refetch_terminal(
            request_id, text, review_id=review_id, task_id=task_id,
            chat_id=int(REVIEW_CHAT_ID or 0),
        )
    except Exception:
        logger.warning("重抓终态通知落库失败: request_id=%s", request_id, exc_info=True)
    try:
        await audit.record_event(
            "review.refetch_terminal_notify_undelivered",
            review_id=int(review_id or 0),
            error_class="notify_send_failed",
            actor="service:refetch_watchdog",
            detail={"request_id": request_id, "task_id": task_id,
                    "queued": bool(queued)},
        )
    except Exception:
        logger.warning("重抓终态通知审计写入失败: request_id=%s", request_id,
                       exc_info=True)
    return queued


def _refetch_card_notifiable(row, *, task_id: str) -> bool:
    """False for a historical row whose card is long dead (never re-message).

    Production carried 12 silent terminal rows up to 30 days old; recovering one
    must never message a 30-day-old review card. Historical rows are reported by
    the doctor as legacy instead.
    """
    created = float(row["created_at"] or 0)
    if not created:
        return True
    age = time.time() - created
    if age <= REFETCH_RECOVERY_NOTIFY_MAX_AGE_SECONDS:
        return True
    logger.info(
        "重抓终态通知跳过（历史卡片已过期）: request_id=%s 任务ID=%s age_hours=%.1f",
        row["request_id"], task_id, age / 3600.0,
    )
    return False


def _refetch_terminal_text(review_id: int, *, state: str, failure_code: str,
                           task_id: str, minutes: int,
                           hard_minutes: int) -> str:
    """The moderator-facing terminal sentence (Chinese, always with 任务ID).

    Failure paths must hand back an operable card, so every failure sentence
    ends with 「可以再次重抓」 (§refetch-terminal-notify).
    """
    if failure_code == "queued_too_long":
        return (f"⏱ 审核 #{review_id} 重抓排队超时（PixivFlow 未开始执行，已等待约"
                f" {minutes} 分钟），当前稿件保持不变，可以再次重抓。")
    if failure_code == "stalled_no_progress":
        return (f"❌ 审核 #{review_id} 重抓在阶段「{fsm.label(state)}」停留超过"
                f" {max(1, int(REFETCH_STAGE_TIMEOUT_MINUTES))} 分钟没有进展"
                "（远端无状态变化也无心跳），已判定超时，当前稿件保持不变，"
                "可以再次重抓。")
    if failure_code == "watchdog_no_heartbeat":
        return (f"⚠️ 审核 #{review_id} 重抓看门狗失去心跳超过"
                f" {max(1, int(REFETCH_STALE_TIMEOUT_MINUTES))} 分钟"
                "（本机轮询未再记录到该任务），已标记失败，当前稿件保持不变，"
                "可以再次重抓。")
    if failure_code == "stalled_after_hard_timeout":
        return (f"❌ 审核 #{review_id} 重抓超过 {int(hard_minutes)} 分钟仍未完成，"
                "已自动终止，当前稿件保持不变；请检查 PixivFlow 后重新重抓。")
    if failure_code == "admission_timeout":
        return (f"⚠️ 审核 #{review_id} 重抓请求未被 PixivFlow 接受（已等待约"
                f" {minutes} 分钟），当前稿件未变，请重新点击重抓。")
    return (f"⚠️ 审核 #{review_id} 重抓未能完成（{failure_code}），"
            "当前稿件保持不变，可以再次重抓。")


async def _refetch_recovery_sweep(bot, repo, *, current_time: float) -> int:
    """One recovery pass after a process restart (guarded once per process).

    In-memory state dies with the process; the durable ``refetch_attempts``
    rows are the only truth. Every non-terminal attempt whose LOCAL heartbeat is
    older than two poll intervals is polled immediately, and a remote that
    already reports a terminal state is applied through the state machine.
    Returns the number of attempts that converged.
    """
    global _refetch_recovery_done
    if _refetch_recovery_done:
        return 0
    _refetch_recovery_done = True
    # Imported HERE on purpose: the recovery sweep is the only place that needs
    # the review repository before the regular poll path, and a module-level
    # import would close a cycle (reviews → refetch → handlers).
    from telepost.storage.sqlite.reviews import ReviewRepository
    stale_cutoff = current_time - max(1.0, 2.0 * REFETCH_POLL_INTERVAL_SECONDS)
    recovered = 0
    try:
        rows = await repo.heartbeat_stale(stale_cutoff)
    except Exception:
        logger.warning("重抓重启恢复扫描失败", exc_info=True)
        return 0
    for row in rows:
        request_id = row["request_id"]
        review_id = int(row["source_review_id"] or 0)
        task_id = f"refetch-{review_id}-{int(float(row['created_at'] or 0))}"
        try:
            source = await ReviewRepository().get(review_id)
        except Exception:
            source = None
        # NOTE: the repository returns a ``sqlite3.Row`` (no ``.get``), so the
        # field is read by key with an explicit missing-key guard.
        try:
            target_id = str(source["target_id"] or "") if source is not None else ""
        except (KeyError, IndexError, TypeError):
            target_id = ""
        if not target_id:
            logger.info("重抓重启恢复跳过（无法解析 target）: request_id=%s", request_id)
            continue
        try:
            snapshot = await _fetch_refetch_snapshot(target_id, request_id)
        except Exception as exc:
            # The remote may be stopped; the row stays non-terminal and the
            # regular heartbeat/backoff path will retry it on a later tick.
            logger.warning("重抓重启恢复读取远端失败: request_id=%s error=%s",
                           request_id, type(exc).__name__)
            continue
        status = str(snapshot.get("state") or "")
        moved = False
        if status in _REFETCH_TERMINAL_OUTCOMES:
            disposition = ("no_alternative" if status in {"no_candidate", "duplicate"}
                           else "failed")
            reason = ("delivery_uncorrelated" if status == "submitted"
                      else "remote_failed" if status == "failed" else "")
            _, changed = await apply_refetch_outcome_and_notify(
                bot, repo, row, request_id=request_id,
                disposition=disposition, review_id=review_id,
                task_id=task_id, reason=reason,
            )
            if changed:
                moved = True
                recovered += 1
        elif status:
            stage = fsm.stage_for_remote_state(status)
            if stage and stage != fsm.normalize(row["state"]):
                if await repo.advance_stage(request_id, stage,
                                            remote_state=status,
                                            reason="recovered_after_restart"):
                    moved = True
                    recovered += 1
        if moved and recovered > 0:
            await _record_refetch_event(
                "review.refetch_recovered_after_restart", review_id=review_id,
                request_id=request_id, chain_id=row["review_chain_id"] or "",
                generation=int(row["generation"] or 0), actor=None,
                recovered=recovered, via="heartbeat_sweep",
            )
    logger.info("重抓重启恢复完成: 扫描=%d 收敛=%d", len(rows), recovered)
    if recovered:
        from telepost.observability import audit
        try:
            await audit.record_event(
                "review.refetch_recovered_after_restart",
                detail={"recovered_count": recovered, "scanned": len(rows)},
            )
        except Exception:
            logger.debug("记录重启恢复审计事件失败", exc_info=True)
    return recovered


async def recover_refetch_jobs(bot, *, now: Optional[float] = None) -> int:
    """启动即扫（P0）: one recovery sweep on the real startup path.

    Called by ``main.py`` right after ``reconcile_incomplete_reviews`` and
    BEFORE ``telepost_ready`` flips to True, so a redeploy adopts every
    in-flight refetch attempt durably (never in-memory) instead of waiting for
    the first 30 s heartbeat tick. Guarded once per process, therefore the first
    tick of ``poll_refetch_jobs`` cannot repeat it.
    """
    if _refetch_heartbeat_disabled():
        return 0
    current_time = time.time() if now is None else now
    try:
        return await _refetch_recovery_sweep(
            bot, RefetchRepository(), current_time=current_time,
        )
    except Exception:
        logger.warning("重抓启动恢复失败", exc_info=True)
        return 0


async def flush_refetch_terminal_notifications(bot, *, limit: int = 20) -> int:
    """Deliver refetch terminal notices whose direct send failed.

    Durable retry for the one-message guarantee: a terminal attempt is no longer
    polled, so the poller cannot retry it — the outbox row is the only thing
    standing between a Telegram hiccup and permanent silence. Never raises;
    failures stay in the outbox (attempts/last_error) and the doctor reports them.
    """
    from telepost.storage.sqlite.submitter_notifications import (
        SubmitterNotificationRepository,
    )

    repo = SubmitterNotificationRepository()
    try:
        rows = await repo.pending_refetch_terminal(limit=limit)
    except Exception:
        logger.warning("读取重抓终态通知队列失败", exc_info=True)
        return 0
    delivered = 0
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:
            payload = {}
        text = str(payload.get("text") or "")
        chat_id = int(payload.get("chat_id") or 0) or int(REVIEW_CHAT_ID or 0)
        if not text or not chat_id:
            await repo.record_error(int(row["id"]), "empty text or chat id")
            continue
        try:
            message = await bot.send_message(chat_id=chat_id, text=text)
        except Exception as exc:
            await repo.record_error(int(row["id"]), str(exc))
            continue
        await repo.mark_sent(int(row["id"]), int(getattr(message, "message_id", 0) or 0))
        delivered += 1
    if delivered:
        logger.info("重抓终态通知补发完成: %d 条", delivered)
    return delivered


async def apply_refetch_outcome_and_notify(
    bot, repo, row, *, request_id: str, disposition: str, review_id: int,
    task_id: str, reason: str = "", reason_code: str = "", scanned: int = 0,
    skipped_duplicate: int = 0, skipped_invalid: int = 0,
    skipped_unavailable: int = 0, stage_label: str = "",
) -> Tuple[str, bool]:
    """Apply a remote refetch verdict AND notify through the ONE terminal seam.

    The single decider of "a terminal verdict was applied ⇒ the review group
    hears about it exactly once": the poll loop, the §events reconcile loop, the
    restart recovery sweep and the ``POST /api/v1/refetch/outcomes`` ingress all
    come through here, so no channel can diverge in wording, idempotency or
    delivery. The ingress used to call ``repo.apply_outcome`` itself and then
    hand-roll a ``send_message``: that bypassed the one-shot
    ``terminal_notified_at`` claim (measured NULL for two live attempts),
    omitted the 任务ID line and disagreed with the poll path. A verdict that did
    not move the attempt (already-terminal replay) notifies NOTHING.

    ``reason`` is the bounded human business message (→ ``terminal_reason``);
    ``reason_code`` is the closed-vocabulary protocol code (→ ``failure_code``).

    Returns ``(applied_state, changed)``.
    """
    _, applied, changed = await repo.apply_outcome(
        request_id, disposition, reason=reason, reason_code=reason_code,
        scanned=scanned, skipped_duplicate=skipped_duplicate,
        skipped_invalid=skipped_invalid, skipped_unavailable=skipped_unavailable,
    )
    if not changed:
        # Already terminal: the first path that reached the terminal already
        # claimed the one-shot clock. Never notify again.
        return applied, False
    fresh = await repo.find_by_request_id(request_id)
    await _refetch_terminal_notify(
        bot, repo, fresh if fresh is not None else row,
        review_id=review_id, task_id=task_id,
        text=_refetch_outcome_text(review_id, applied),
        stage_label=stage_label,
    )
    return applied, True


def _refetch_outcome_text(review_id: int, applied: str) -> str:
    """The ONE mapping from an applied verdict to its user-visible sentence.

    Failure sentences always end with 「可以再次重抓」 (the operable card
    contract of §refetch-terminal-notify), and the 任务ID line is appended by
    :func:`_refetch_terminal_notify` rather than by each caller.
    """
    if applied == "no_alternative":
        return (f"📭 审核 #{review_id} 没有找到新的可替换作品，"
                "当前稿件保持不变，可以再次重抓。")
    if applied == "obsolete":
        return (f"🔄 审核 #{review_id} 的重抓已取消：该审核已被处理"
                "（驳回/通过/过期），不会产生替换稿，当前稿件保持不变。")
    if applied == "failed":
        return (f"⚠️ 审核 #{review_id} 重抓失败，当前稿件未变，"
                "可以再次重抓。")
    return f"ℹ️ 审核 #{review_id} 重抓已结束（{applied}）。"


async def _apply_remote_terminal(bot, repo, row, remote_state, *,
                                 request_id: str, review_id: int,
                                 task_id: str) -> bool:
    """Apply one remote terminal verdict through the state machine, exactly once.

    The single seam shared by the poll loop AND the §events reconcile loop: both
    feed a terminal remote-state outcome (``no_candidate`` / ``duplicate`` /
    ``failed`` / ``submitted``) through
    :func:`apply_refetch_outcome_and_notify`, whose idempotency bounds the
    terminal notification to exactly once no matter which channel wins first.
    This is the "terminal event and the poll agree" guarantee for the 不再永久静默
    requirement. The reasons here are already code-shaped
    (``delivery_uncorrelated`` / ``remote_failed``), which is why the
    ``/api/v1/refetch/outcomes`` ingress was the ONLY path that polluted
    ``failure_code`` with remote free text.
    """
    disposition = ("no_alternative" if remote_state in _REFETCH_NO_CANDIDATE
                   else "failed")
    reason = ("delivery_uncorrelated" if remote_state == "submitted"
              else "remote_failed" if remote_state == "failed" else "")
    _, changed = await apply_refetch_outcome_and_notify(
        bot, repo, row, request_id=request_id, disposition=disposition,
        review_id=review_id, task_id=task_id, reason=reason,
    )
    return changed


def _persisted_event_document(evt):
    """Rebuild the persisted Event doc from a normalized protocol Event.

    The raw column stores this verbatim so business handling always decodes from
    it (someone re-reading the row gets the full event, not a partial view).
    """
    return {
        "event_id": evt.event_id,
        "job_id": evt.job_id,
        "type": evt.event_type,
        "at": evt.at,
        "correlation_id": evt.correlation_id,
        "labels": dict(evt.labels or {}),
        "payload": dict(evt.payload or {}),
    }


async def reconcile_refetch_events(bot, *, now: Optional[float] = None) -> int:
    """§events consumer heartbeat: pull unacked events for every in-flight job.

    Runs on the same cadence as ``poll_refetch_jobs`` and makes the event stream
    self-healing — the callback is ACCELERATION, not the only channel. For each
    in-flight attempt it resolves the remote job id, pulls
    ``GET /jobs/{job_id}/events?after=<cursor>&unacked=1``, persists each Event
    (dedup on ``event_id`` so a replay is a no-op), acks the newest durable
    event_id, and feeds any terminal Event through the shared terminal seam
    (:func:`_apply_remote_terminal`) so the callback and the poll can never
    double-notify.

    Returns the number of attempts on which this tick applied a terminal outcome.
    """
    if _refetch_heartbeat_disabled():
        return 0
    current_time = time.time() if now is None else now
    from telepost.storage.sqlite.protocol_events import ProtocolEventRepository
    repo = RefetchRepository()
    events_repo = ProtocolEventRepository()
    try:
        rows = await repo.active_for_poll(current_time)
    except Exception:
        logger.warning("事件调和读取活跃任务失败", exc_info=True)
        return 0
    acted = 0
    for row in rows:
        try:
            acted += await _reconcile_one_refetch_job(
                bot, repo, events_repo, row, current_time=current_time,
            )
        except Exception:
            logger.warning(
                "事件调和单条失败: request_id=%s",
                str(row["request_id"]), exc_info=True,
            )
    return acted


async def _reconcile_one_refetch_job(bot, repo, events_repo, row, *,
                                     current_time: float) -> int:
    """Pull + persist + ack the unacked event tail of ONE in-flight attempt."""
    request_id = str(row["request_id"] or "")
    review_id = int(row["source_review_id"] or 0)
    created = float(row["created_at"] or current_time)
    task_id = f"refetch-{review_id}-{int(created)}"
    client = _refetch_client()
    # Resolve the remote job id: our submit used request_id as the idempotency
    # key, so get() finds the same job the poll reads. A remote that is not yet
    # visible (or is down) returns nothing and this attempt is retried next tick.
    snapshot = await asyncio.to_thread(client.get, request_id)
    job_id = str(snapshot.job_id or "")
    if not job_id:
        return 0
    cursor = await events_repo.ack_through(job_id)
    page = await asyncio.to_thread(
        client.events, job_id, after=cursor, unacked=True,
    )
    if not page.events:
        return 0
    new_events = []
    for evt in page.events:
        result = await events_repo.persist(_persisted_event_document(evt))
        if result["inserted"]:
            new_events.append(evt)
    # Ack the newest event we durably hold for this job. The producer treats an
    # older/unknown cursor as a no-op (never an error, never a state change), so
    # even a stale ack here cannot corrupt the stream.
    newest = await events_repo.newest_event_id(job_id)
    if newest and newest != cursor:
        try:
            await asyncio.to_thread(
                client.ackEvents, job_id, ack_through=newest,
            )
        except Exception:
            logger.warning("事件确认失败: job_id=%s", job_id, exc_info=True)
        await events_repo.set_ack_through(job_id, newest)
    # Feed terminal Events through the shared outcome seam exactly once.
    acted = 0
    for evt in new_events:
        if not evt.is_terminal:
            continue
        remote_state = str(evt.remote_state or "").strip().lower()
        if remote_state not in _REFETCH_TERMINAL_OUTCOMES:
            continue
        if await _apply_remote_terminal(
                bot, repo, row, remote_state, request_id=request_id,
                review_id=review_id, task_id=task_id):
            acted += 1
    return acted


async def poll_refetch_jobs(bot, *, now: Optional[float] = None,
                            force: bool = False) -> int:
    """重抓作业心跳（P0）：每 30 秒轮询一次非终态 attempt 并推进生命周期。

    This is the durable-job driver: one tick polls every non-terminal attempt,
    records OUR heartbeat and the REMOTE liveness clocks, applies the bounded
    budgets, and guarantees that a terminal state is user-visible exactly once.
    ``cleanup_runtime_data`` calls it with ``force=True`` so there is exactly
    one implementation (never a second, divergent watchdog).

    Liveness rule (§refetch-lifecycle): 停摆 = 无进展 **且** 无心跳。A remote that
    keeps sending a fresh heartbeat, or that changes state, resets the stall
    clock — a legitimately long (2–20 minute, once 10-hour) PixivFlow slot is
    never killed for being slow, only for being silent.

    Returns the number of attempts this tick ACTED on (heartbeat written,
    reminder sent or terminal state reached).
    """
    if _refetch_heartbeat_disabled():
        return 0
    current_time = time.time() if now is None else now
    repo = RefetchRepository()
    await _refetch_recovery_sweep(bot, repo, current_time=current_time)
    try:
        rows = await repo.active_for_poll(current_time)
    except Exception:
        logger.warning("重抓轮询读取活跃任务失败", exc_info=True)
        return 0
    acted = 0
    for row in rows:
        try:
            acted += await _poll_one_refetch_job(
                bot, repo, row, current_time=current_time, force=force,
            )
        except Exception:
            # One broken attempt must never stop the rest of the loop.
            logger.warning(
                "重抓轮询单条失败: request_id=%s", row["request_id"], exc_info=True,
            )
    return acted


async def _poll_one_refetch_job(bot, repo, row, *, current_time: float,
                                force: bool) -> int:
    """Poll ONE non-terminal attempt for one tick; returns 0/1 acted."""
    from telepost.storage.sqlite.reviews import ReviewRepository

    review_id = int(row["source_review_id"] or 0)
    request_id = row["request_id"]
    created = float(row["created_at"] or current_time)
    age = current_time - created
    minutes = int(age // 60)
    state = fsm.normalize(row["state"])
    task_id = f"refetch-{review_id}-{int(created)}"
    stage_label = fsm.label(state)
    hard_seconds = REFETCH_HARD_TIMEOUT_MINUTES * 60
    stale_seconds = REFETCH_STALE_TIMEOUT_MINUTES * 60
    wake_seconds = REFETCH_WAKE_MINUTES * 60

    async def _terminate(target: str, reason: str, text: str,
                         remote: str = "") -> int:
        # The terminal STATE is the state machine's decision, never an artefact
        # of this helper: FAILED means our own side is at fault (the watchdog
        # lost the heartbeat), TIMEOUT means the work did not finish within a
        # budget, CANCELLED means the source review moved on first.
        if target == fsm.CANCELLED:
            moved = await repo.mark_cancelled(request_id, reason, remote_state=remote)
        elif target == fsm.FAILED:
            moved = await repo.mark_failed(request_id, reason)
        else:
            moved = await repo.mark_timeout(request_id, reason, remote_state=remote)
        if not moved:
            return 0
        fresh = await repo.find_by_request_id(request_id)
        await _refetch_terminal_notify(
            bot, repo, fresh if fresh is not None else row,
            review_id=review_id, task_id=task_id, text=text,
            stage_label=fsm.label(target),
        )
        return 1

    # 1. The source review owns the attempt: once decided/expired/superseded the
    #    attempt can only be cancelled — and the group must be told (§refetch).
    source = await ReviewRepository().get(review_id)
    if source is None or source["status"] != "pending":
        return await _terminate(
            fsm.CANCELLED, "source_review_resolved",
            f"🔄 审核 #{review_id} 的重抓已取消：该审核已被处理"
            "（驳回/通过/过期），不会产生替换稿，当前稿件保持不变。",
        )

    next_poll = row["next_poll_at"]
    heartbeat_at = row["heartbeat_at"]
    poll_due = (force or next_poll is None or float(next_poll) <= current_time)

    if state == fsm.REQUESTED:
        # 2. Unadmitted request: never polled remotely, never waiting forever.
        #    This branch comes BEFORE the absolute ceiling on purpose: an
        #    unadmitted row must be reported as admission_timeout (the documented
        #    contract) and must never fall through to the hard-ceiling branch,
        #    which would overwrite failure_code.
        #    STALE_MINUTES → admission_timeout; WAKE_MINUTES → idempotent
        #    re-submit with the SAME request UUID (a first submit may have died
        #    with its process). This branch must win over the watchdog branch
        #    below: both share STALE_MINUTES, and letting the watchdog shadow it
        #    would report watchdog_no_heartbeat instead of admission_timeout.
        if stale_seconds > 0 and age >= stale_seconds:
            return await _terminate(
                fsm.TIMEOUT, "admission_timeout",
                _refetch_terminal_text(review_id, state=state,
                                       failure_code="admission_timeout",
                                       task_id=task_id, minutes=minutes,
                                       hard_minutes=int(hard_seconds // 60)),
            )
        if (wake_seconds > 0 and age >= wake_seconds
                and request_id not in _wake_pinged):
            _wake_pinged.add(request_id)
            try:
                submitted = await asyncio.to_thread(
                    _submit_pixivflow_refetch, source["target_id"], request_id)
                await repo.mark_admitted(
                    request_id, str((submitted or {}).get("slot_id") or ""),
                    actor="watchdog:resubmit",
                )
                await _refetch_poll_notify(
                    bot,
                    f"🔄 审核 #{review_id} 重抓任务此前未在 PixivFlow 落地，"
                    "已用同一任务ID重新提交并开始处理，有新结果会第一时间"
                    f"在本群通知。\n任务ID：{task_id}",
                )
                return 1
            except Exception:
                logger.warning("重抓重新提交失败: review_id=%s", review_id,
                               exc_info=True)
        if not poll_due:
            return 0
        # Still record OUR heartbeat so doctor can tell "queued remotely" from
        # "the poller itself is dead".
        await repo.record_poll(
            request_id, now=current_time,
            next_poll_at=current_time + REFETCH_POLL_INTERVAL_SECONDS,
        )
        return 0

    # 3b. Watchdog heartbeat: OUR poller stopped recording for this attempt.
    #     Only ADMITTED attempts reach here (REQUESTED was handled above), and
    #     only a row this process has ACTUALLY polled can be judged — a NULL
    #     heartbeat_at means "never polled by this process" (a row predating the
    #     job model, or one adopted after a restart), which must be POLLED, not
    #     failed. That distinction keeps a redeploy from killing every in-flight
    #     attempt on its first tick.
    if (stale_seconds > 0 and heartbeat_at
            and (current_time - float(heartbeat_at)) >= stale_seconds):
        return await _terminate(
            fsm.FAILED, "watchdog_no_heartbeat",
            _refetch_terminal_text(review_id, state=state,
                                   failure_code="watchdog_no_heartbeat",
                                   task_id=task_id, minutes=minutes,
                                   hard_minutes=int(hard_seconds // 60)),
        )

    # 4. Read the remote through the port (never a second HTTP client).
    if not poll_due:
        return 0
    remote_state = ""
    snapshot: Dict[str, Any] = {}
    poll_failed = False
    try:
        snapshot = await _fetch_refetch_snapshot(source["target_id"], request_id)
        remote_state = str(snapshot.get("state") or "")
    except Exception as exc:
        # Transport failure is not evidence the work failed: the remote may be
        # stopped, and an idempotent wake restarts the resume path.
        poll_failed = True
        logger.warning("重抓远端状态不可用: review_id=%s error=%s",
                       review_id, type(exc).__name__)
    if poll_failed:
        failures = int(row["poll_failures"] or 0) + 1
        interval = _refetch_backoff_seconds(failures)
        await repo.record_poll(
            request_id, now=current_time, failure=True,
            next_poll_at=current_time + interval,
        )
        if hard_seconds > 0 and age >= hard_seconds:
            # Past the absolute ceiling AND unreadable: end it. A remote we
            # cannot reach may never be able to end an attempt by itself.
            return await _terminate(
                fsm.TIMEOUT, "stalled_after_hard_timeout",
                _refetch_terminal_text(review_id, state=state,
                                       failure_code="stalled_after_hard_timeout",
                                       task_id=task_id, minutes=minutes,
                                       hard_minutes=int(hard_seconds // 60)),
                remote=str(row["last_remote_state"] or ""),
            )
        if (wake_seconds > 0 and age >= wake_seconds
                and request_id not in _wake_pinged):
            _wake_pinged.add(request_id)
            try:
                await asyncio.to_thread(
                    _submit_pixivflow_refetch, source["target_id"], request_id)
                if await _refetch_poll_notify(
                    bot,
                    f"🔄 审核 #{review_id} 处理时间较长，已自动恢复任务"
                    "（同一重抓请求），有新结果会第一时间通知。"
                    f"\n任务ID：{task_id}",
                ):
                    await repo.bump_progress_notified(request_id, current_time)
                return 1
            except Exception:
                logger.warning("重抓自动唤醒失败: review_id=%s", review_id)
        # A failed read is not progress, but it must NOT be silent either: fall
        # through to the liveness budgets and the repeating reminder so the
        # moderator still hears "we are waiting" (and a remote that stays
        # unreadable long enough still converges to a terminal state).
        remote_state = ""

    # Protocol-only liveness: a fresh heartbeat/timestamp OR a real state change
    # resets the stall clock (§refetch-lifecycle). A state change means the
    # remote DID make progress, so it is activity even when it carries no
    # timestamp.
    remote_heartbeat = snapshot.get("heartbeat")
    state_changed = bool(remote_state) and remote_state != (row["last_remote_state"] or "")
    protocol_live = state_changed or _refetch_remote_live(remote_heartbeat, current_time)
    # Liveness as of THIS tick, for the stall budget: 「无进展且无心跳才算停摆」.
    # A first observation is NOT progress — an empty ``last_remote_state``
    # turning into ``pending`` must never buy an already-stalled attempt another
    # full window (that is what let a dead search look alive forever) — while a
    # real transition or a fresh remote heartbeat is exactly what keeps a
    # legitimately slow (2–20 minute, once 10 hour) slot safe.
    remote_live_now = (
        (state_changed and bool(row["last_remote_state"] or ""))
        or _refetch_remote_live(remote_heartbeat, current_time)
    )
    # Remote-activity clock: an OBSERVED timestamp (heartbeat/timestamp field)
    # when the remote offers one, otherwise the moment We first observed the
    # current remote state. It is never advanced just because we keep looking,
    # so "we are still polling" can never masquerade as remote progress.
    if remote_heartbeat is not None:
        remote_state_at = float(remote_heartbeat)
    elif state_changed:
        remote_state_at = current_time
    else:
        remote_state_at = row["remote_state_at"]
    # A FAILED read must not be re-recorded as a successful poll: the failure
    # branch above already wrote OUR heartbeat, incremented poll_failures and
    # armed the backoff, and a second "success" write would erase all three
    # (which would hide a permanently unreachable remote from doctor).
    if not poll_failed:
        await repo.record_poll(
            request_id, now=current_time,
            remote_state=remote_state or None,
            remote_heartbeat_at=(float(remote_heartbeat)
                                 if remote_heartbeat is not None else None),
            remote_state_at=remote_state_at,
            next_poll_at=current_time + REFETCH_POLL_INTERVAL_SECONDS,
        )

    # 5. Remote business terminal → apply the outcome through the state machine.
    #    Shared seam (see ``_apply_remote_terminal``): the §events reconcile loop
    #    feeds the SAME apply_outcome/terminal-notify path, so a terminal Event
    #    and this poll agree on the verdict and can never double-notify.
    if remote_state in _REFETCH_TERMINAL_OUTCOMES:
        if await _apply_remote_terminal(
                bot, repo, row, remote_state,
                request_id=request_id, review_id=review_id, task_id=task_id):
            return 1
        return 0

    stage = fsm.stage_for_remote_state(remote_state)
    if stage:
        if await repo.advance_stage(request_id, stage,
                                    remote_state=remote_state,
                                    reason="remote_progress"):
            stage_label = fsm.label(stage)
        state = stage or state

    # 5b. Absolute ceiling: no attempt may outlive HARD_MINUTES. It is judged
    #     AFTER the remote's own verdict (a remote terminal state is the more
    #     authoritative answer and must still reach the moderator) and BEFORE
    #     the local budgets, so the recorded failure_code is unambiguous.
    if hard_seconds > 0 and age >= hard_seconds:
        return await _terminate(
            fsm.TIMEOUT, "stalled_after_hard_timeout",
            _refetch_terminal_text(review_id, state=state,
                                   failure_code="stalled_after_hard_timeout",
                                   task_id=task_id, minutes=minutes,
                                   hard_minutes=int(hard_seconds // 60)),
            remote=remote_state or str(row["last_remote_state"] or ""),
        )

    # 6. Bounded budgets — all tied to liveness, never to silent waiting.
    #    Two DIFFERENT clocks, because they answer two different questions:
    #      * queued clock = when the request was made (``created_at``). A remote
    #        that never claimed the slot is a queue problem, and that wait began
    #        at creation — a fresh attempt is never judged for a wait predating it.
    #      * stall clock  = the last REMOTE progress we can see: an observed
    #        remote timestamp, else the state-transition clock (``updated_at``),
    #        else admission/creation. "We are still polling" deliberately does NOT
    #        advance it, so a dead remote cannot be kept alive merely because this
    #        process keeps looking at it — while a live heartbeat or a real state
    #        change (both of which advance the clock) keep a slow-but-progressing
    #        search safe. That is 「无进展且无心跳才算停摆」.
    stage_seconds = REFETCH_STAGE_TIMEOUT_MINUTES * 60
    stall_clock = (float(row["remote_state_at"]) if row["remote_state_at"]
                   else float(row["updated_at"] or 0)
                   or float(row["started_at"] or created) or created)
    if remote_state == "pending":
        queued_seconds = REFETCH_QUEUED_TIMEOUT_MINUTES * 60
        if queued_seconds > 0 and (current_time - created) >= queued_seconds:
            return await _terminate(
                fsm.TIMEOUT, "queued_too_long",
                _refetch_terminal_text(review_id, state=state,
                                       failure_code="queued_too_long",
                                       task_id=task_id, minutes=minutes,
                                       hard_minutes=int(hard_seconds // 60)),
                remote=remote_state,
            )
    if (stage_seconds > 0 and not remote_live_now
            and (current_time - stall_clock) >= stage_seconds):
        return await _terminate(
            fsm.TIMEOUT, "stalled_no_progress",
            _refetch_terminal_text(review_id, state=state,
                                   failure_code="stalled_no_progress",
                                   task_id=task_id, minutes=minutes,
                                   hard_minutes=int(hard_seconds // 60)),
            remote=remote_state,
        )

    # 6. REPEATING progress reminder (stage + elapsed + 任务ID) — never
    #    once-only, silence reads as "the task died" (§refetch-terminal-notify).
    remind_seconds = REFETCH_PROGRESS_REMIND_MINUTES * 60
    if remind_seconds > 0:
        last = row["last_progress_notified_at"] or 0
        if not last or current_time - last >= remind_seconds:
            await _refetch_poll_notify(
                bot,
                f"🔄 审核 #{review_id} 重抓仍在处理中（当前阶段：{stage_label}，"
                f"已等待约 {minutes} 分钟），有新结果会第一时间在本群通知。"
                f"\n任务ID：{task_id}",
            )
            await repo.bump_progress_notified(request_id, current_time)
            await _refetch_refresh_card(bot, review_id, minutes=minutes,
                                        stage_label=stage_label, task_id=task_id)
            return 1
    return 0


async def monitor_refetch_progress(bot, *, now: Optional[float] = None) -> int:
    """Deprecated alias: the 300-second cleanup path now calls the heartbeat loop.

    Kept as a thin forwarding wrapper (``force=True``) so exactly ONE
    implementation exists — a second, divergent watchdog is the historical root
    cause of the silent refetch failures this change fixes.
    """
    return await poll_refetch_jobs(bot, now=now, force=True)





async def context_bot_send(bot, text: str) -> None:
    await bot.send_message(chat_id=REVIEW_CHAT_ID, text=text)


async def refresh_refetch_card(bot, review_id: int, *,
                               minutes: Optional[int] = None,
                               stage_label: str = "",
                               task_id: str = "") -> bool:
    """Rewrite a review's control card to its CURRENT 重抓 state.

    §refetch-card-state — 重抓 means "the current candidate is rejected", so the
    post the operator actually pressed must change:

    * an ACTIVE attempt → the card becomes 「已提交重抓 / 当前候选已作废」 with the
      publish/reject buttons removed (finding a replacement is the only way
      forward), optionally showing elapsed minutes;
    * NO active attempt AND this review was refetched before → the card stays
      「已作废（视为已拒绝）」 with only 重抓 + original link, so a refetch that
      ended without a replacement does NOT resurrect the publish/reject buttons
      (the operator already rejected this work — the only forward path is a new
      重抓, which fetches a NEW work and recurses);
    * NO active attempt AND never refetched → the normal actionable card is
      rebuilt from the row.

    Cosmetic on purpose: never raises, never touches review state.
    """
    from telepost.storage.sqlite.refetch import RefetchRepository
    from telepost.storage.sqlite.reviews import ReviewRepository

    try:
        row = await ReviewRepository().get(review_id)
    except Exception:
        logger.debug("读取审核记录失败: review_id=%s", review_id, exc_info=True)
        return False
    if row is None:
        return False
    keys = row.keys() if hasattr(row, "keys") else []
    control = row["control_message_id"] if "control_message_id" in keys else None
    if row["status"] != "pending" or not control:
        # Decided / superseded / still-preparing cards are owned by the
        # decision, replacement and reconciliation paths respectively.
        return False
    try:
        chain = (row["review_chain_id"] if "review_chain_id" in keys else "") \
            or f"chain-{review_id}"
        active = await RefetchRepository().find_active_by_chain(chain)
        if active is not None:
            text = review_keyboard.refetch_pending_text(
                review_id=review_id, minutes=minutes,
                stage_label=stage_label, task_id=task_id,
            )
            markup = review_keyboard.refetch_pending_keyboard(
                review_id, row["link"] or "",
            )
        else:
            # No active attempt. The candidate is only "actionable again" if it
            # was NEVER refetched; a refetch limb that ended without a
            # replacement leaves the card voided (rejected) so the operator can
            # only push 重抓 forward to a new work.
            latest = await RefetchRepository().find_latest_by_chain(chain)
            if latest is not None and int(latest["source_review_id"]) == review_id:
                reason = review_keyboard.refetch_failure_reason(latest)
                text = review_keyboard.refetch_voided_text(
                    review_id=review_id, reason=reason, task_id=task_id,
                )
                markup = review_keyboard.refetch_voided_keyboard(
                    review_id, row["link"] or "",
                )
            else:
                text, markup = review_keyboard.control_card_from_row(row)
    except Exception:
        logger.warning("渲染审核卡失败: review_id=%s", review_id, exc_info=True)
        return False
    try:
        await bot.edit_message_text(
            chat_id=REVIEW_CHAT_ID,
            message_id=int(control),
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
            **_review_timeout_kwargs(),
        )
        return True
    except Exception as exc:
        # "Message is not modified" is a no-op (the card already says this).
        if "not modified" not in str(exc).lower():
            logger.debug("更新审核卡失败: review_id=%s", review_id, exc_info=True)
        return False


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


def _row_value(row, key, default=None):
    """Read a column from either sqlite3.Row or plain dict."""
    try:
        if key in row.keys():
            return row[key]
    except (AttributeError, TypeError):
        pass
    if isinstance(row, dict):
        return row.get(key, default)
    return default


async def _apply_review_group_mask(bot, row, mask_on: bool):
    """Re-edit every already-sent maskable preview message to flip spoiler.

    Called from :func:`toggle_review_spoiler`: the DB flag and the card button
    are not enough — the review-group images already on screen only gain/lose
    their mask when each media message is re-edited with ``has_spoiler``.

    Uses the per-message specs persisted alongside ``review_message_ids``;
    message kinds that cannot hold a Telegram spoiler (document/audio) and the
    control card (not in the spec list) are skipped. Per-message failures are
    logged and never abort the remaining messages.
    """
    from telegram import InputMediaAnimation, InputMediaPhoto, InputMediaVideo

    _KIND_INPUT = {
        "photo": InputMediaPhoto,
        "video": InputMediaVideo,
        "animation": InputMediaAnimation,
    }
    try:
        ids = [
            int(v)
            for v in json.loads(_row_value(row, "review_message_ids") or "[]")
        ]
        specs = json.loads(_row_value(row, "review_message_specs") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning("解析审核群遮罩规格失败，跳过即时改掩: %s", error)
        return
    chat_id = _row_value(row, "review_chat_id") or REVIEW_CHAT_ID
    for message_id, spec in zip(ids, specs):
        if not spec or not isinstance(spec, dict):
            continue
        kind = spec.get("kind") or spec.get("type")
        file_id = spec.get("file_id")
        input_cls = _KIND_INPUT.get(kind)
        if input_cls is None or not file_id:
            continue
        try:
            await bot.edit_message_media(
                chat_id=chat_id,
                message_id=message_id,
                media=input_cls(media=file_id, has_spoiler=mask_on),
            )
        except Exception as error:
            logger.debug(
                "即时改掩失败 message=%s: %s", message_id, error, exc_info=True
            )


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
    # §review-group-mask (on-demand): flip the mask on the review-group media
    # that is already on screen. The DB flag + card button alone never re-masks
    # the already-sent preview images.
    bot = context.bot
    if bot is not None:
        await _apply_review_group_mask(bot, row, new_spoiler)
    try:
        await query.edit_message_reply_markup(
            reply_markup=_review_keyboard(
                review_id, row["link"], spoiler=new_spoiler,
                source=row["source"],
                pixiv_id=_pixiv_id_from_link(row["link"] or ""),
                submitter_user_id=_row_value(row, "submitter_user_id"),
                actor_kind=_row_value(row, "actor_kind") or "user",
                actor_subject=_row_value(row, "actor_subject") or "",
            )
        )
    except Exception:
        # §review-button-refresh: the mask is already in the DB and the
        # review-group media already re-masked above, so a keyboard refresh
        # failure must NOT be swallowed silently — otherwise the button label
        # stays showing the stale spoiler value. Log at warn (with traceback)
        # and surface a transient, visible alert to the admin via show_alert
        # (a popup, not a new chat message, so repeated toggles do not spam).
        logger.warning(
            "刷新审核键盘失败，审核卡遮罩按钮标签可能已陈旧（遮罩状态已入库生效）: "
            "review_id=%s",
            review_id,
            exc_info=True,
        )
        await _answer(
            query,
            f"已遮罩，但按钮无法刷新（状态已{'开启' if new_spoiler else '关闭'}）。",
            show_alert=True,
        )


def _submit_pixivflow_refetch(target_id: str, request_id: str,
                              correlation_id: str = "") -> dict:
    """Submit through the PixivFlow JOB PORT (no URL building here).

    Kept as a patchable module seam for tests/deployments. The port owns every
    URL, header and payload decode; this function only adapts the legacy return
    shape (``slot_id``) that existing callers/tests read.
    """
    client = _refetch_client(wake_producer=True)
    try:
        receipt = client.submit(
            "refetch", request_id, correlation_id=correlation_id,
            params={"target_id": target_id},
        )
    except Exception as exc:
        code = pixivflow_jobs_port.classify_error(exc)
        raise RuntimeError(f"PixivFlow 拒绝重抓（{code}）") from exc
    return {
        "status": "accepted",
        "replayed": bool(receipt.replayed),
        "slotId": receipt.slot_id(),
        "slot_id": receipt.slot_id(),
    }


def _read_pixivflow_refetch_status(target_id: str, request_id: str,
                                   detail: bool = False):
    """Read the durable remote job cell through the port.

    ``detail=False`` (default) returns the remote STATUS STRING — the historical
    contract kept for existing callers/tests. ``detail=True`` returns the
    protocol view the heartbeat loop needs: ``state`` plus the liveness
    timestamps and ``error_code``. Both come from the same port read.
    """
    client = _refetch_client()
    snapshot = client.get(request_id, params={"target_id": target_id})
    state = str(snapshot.status or "")
    if not detail:
        return state
    stamps = [value for value in (
        snapshot.heartbeat_at, snapshot.updated_at,
        snapshot.started_at, snapshot.created_at,
    ) if value is not None]
    return {
        "state": state,
        "heartbeat": max(stamps) if stamps else None,
        "job_id": str(snapshot.job_id or ""),
        "error_code": str(snapshot.error_code or ""),
    }


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
        # §refetch-card-state: the post that was pressed must show the new state.
        await refresh_refetch_card(context.bot, review_id)

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

    # Install the durable attempt row into the card right away: the remote call
    # can take a second or two, and an unmoved post is what reads as “卡死”.
    await refresh_refetch_card(context.bot, review_id)

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
                submitter_user_id=_row_value(row, "submitter_user_id"),
                actor_kind=_row_value(row, "actor_kind") or "user",
                actor_subject=_row_value(row, "actor_subject") or "",
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


async def block_review_user(update, context):
    """🚫 封禁投稿人 — insert a moderation block for a review's human submitter."""
    query = update.callback_query
    if update.effective_user.id not in ADMIN_IDS:
        await _answer(query, "你没有审核权限", show_alert=True)
        return
    try:
        review_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await _answer(query, "无效的审核记录", show_alert=True)
        return
    row = await _load_review_for_action(query, review_id)
    if row is None:
        await _answer(query, "审核记录不存在", show_alert=True)
        return
    from telepost.storage.sqlite.moderation import ModerationRepository, user_subject
    subject = user_subject(
        _row_value(row, "submitter_user_id") or _row_value(row, "user_id")
        or 0
    )
    if subject == "user:0":
        await _answer(query, "该投稿没有可封禁的投稿人", show_alert=True)
        return
    from telepost.observability import audit
    await ModerationRepository().add_block(
        subject, reason="admin block from review card", created_by=update.effective_user.id
    )
    await audit.record_event(
        "moderation.user_blocked", review_id=review_id,
        actor=update.effective_user.id,
        detail={"subject": subject, "reason": "review card"},
    )
    await _answer(query, "已封禁该投稿人：后续投稿将被自动拒绝")
    try:
        await query.edit_message_text(
            f"{query.message.text}\n\n🚫 投稿人封禁已由管理员 {update.effective_user.id} 记录"
        )
    except Exception as exc:
        logger.debug("封禁后更新审核卡失败: %s", exc)


async def block_review_api(update, context):
    """🔑 禁用API — insert a moderation block for the review's API token actor."""
    query = update.callback_query
    if update.effective_user.id not in ADMIN_IDS:
        await _answer(query, "你没有审核权限", show_alert=True)
        return
    try:
        review_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await _answer(query, "无效的审核记录", show_alert=True)
        return
    row = await _load_review_for_action(query, review_id)
    if row is None:
        await _answer(query, "审核记录不存在", show_alert=True)
        return
    actor_subject = str(_row_value(row, "actor_subject") or "")
    from telepost.storage.sqlite.moderation import ModerationRepository
    from telepost.storage.sqlite.moderation import canonical_actor_subject
    subject = canonical_actor_subject(actor_subject)
    if not subject or not subject.startswith("api:"):
        await _answer(query, "该投稿来自私聊/MiniApp，不是API来源", show_alert=True)
        return
    from telepost.observability import audit
    await ModerationRepository().add_block(
        subject, reason="admin block from review card", created_by=update.effective_user.id
    )
    await audit.record_event(
        "moderation.api_blocked", review_id=review_id,
        actor=update.effective_user.id,
        detail={"subject": subject, "reason": "review card"},
    )
    await _answer(query, "已禁用该API token：后续将停止接受其自动投稿")
    try:
        await query.edit_message_text(
            f"{query.message.text}\n\n🔑 API token 禁用已由管理员 {update.effective_user.id} 记录"
        )
    except Exception as exc:
        logger.debug("禁用API后更新审核卡失败: %s", exc)
