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
import subprocess
import time
import uuid
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
REFETCH_TIMEOUT_SECONDS = 3900

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
) -> dict:
    """Stage a file_id submission and create a durable pending review."""
    key = normalize_idempotency_key(user_id, idempotency_key, source)
    command = QueueCommand(
        user_id=user_id, username=username, tags=tags, title=title, note=note,
        link=link, anonymous=anonymous, spoiler=spoiler, source=source,
        idempotency_key=key, target_id=target_id, work_type=work_type,
        pixiv_id=pixiv_id, source_label=source_label, source_ref=source_ref,
        scheduled_at=scheduled_at, review_chat_id=str(REVIEW_CHAT_ID),
    )
    return await queue_service.enqueue(
        command, _stager(bot), media=media, documents=documents
    )


async def queue_review_from_files(
    bot, files, *, tags="", title="", note="", link="",
    anonymous=False, spoiler=False, user_id, username="",
    idempotency_key="", source="api", target_id="", source_label="",
    source_ref="", scheduled_at="", work_type="", pixiv_id="",
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


def _run_pixivflow_refetch(target_id: str = "") -> subprocess.CompletedProcess:
    config_path = os.getenv("PIXIVFLOW_CONFIG", "")
    command = ["pixivflow", "run-once"]
    if config_path:
        command += ["--config", config_path]
    if target_id:
        command += ["--target", target_id]
    logger.info("审核群触发 PixivFlow 重抓: %s", " ".join(command))
    return subprocess.run(
        command, cwd="/app", timeout=REFETCH_TIMEOUT_SECONDS,
        capture_output=True, text=True,
    )


async def refetch_review(update, context):
    query = update.callback_query
    if update.effective_user.id not in ADMIN_IDS:
        await _answer(query, "你没有审核权限", show_alert=True)
        return
    try:
        review_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await _answer(query, "无效的审核记录", show_alert=True)
        return

    if os.getenv("PIXIVFLOW_ENABLED", "false").strip().lower() not in {
        "true", "1", "yes"
    }:
        await _answer(query, "PixivFlow 未启用，无法重抓", show_alert=True)
        return

    row = await _load_review_for_action(query, review_id)
    if row is None:
        await _answer(query, "审核记录不存在", show_alert=True)
        return
    target_id = (row["target_id"] or "").strip() if "target_id" in row.keys() else ""

    await _answer(
        query,
        "已触发重抓，新作品下载投递后会作为新审核稿进入本群（已发布的旧稿不受影响）。",
        show_alert=True,
    )
    try:
        await context.bot.send_message(
            chat_id=REVIEW_CHAT_ID,
            text=f"🔄 审核 #{review_id} 由管理员触发重抓，PixivFlow 正在后台重新下载，请稍候……",
        )
    except Exception:
        logger.debug("发送重抓提示失败", exc_info=True)

    async def _do_refetch():
        try:
            proc = await asyncio.to_thread(_run_pixivflow_refetch, target_id)
            if proc.returncode == 0:
                tail = (proc.stdout or "")[-300:]
                logger.info("PixivFlow 重抓完成: %s", tail)
                await context.bot.send_message(
                    chat_id=REVIEW_CHAT_ID,
                    text="✅ 重抓完成，新投稿已进入审核队列（无新作品时会提示空结果）。",
                )
            else:
                tail = ((proc.stderr or "") + (proc.stdout or ""))[-300:]
                logger.warning("PixivFlow 重抓失败 code=%s: %s",
                               proc.returncode, tail)
                await context.bot.send_message(
                    chat_id=REVIEW_CHAT_ID,
                    text=f"⚠️ 重抓异常退出（{proc.returncode}）：{tail[:200]}",
                )
        except Exception as exc:
            logger.warning("PixivFlow 重抓任务异常: %s", exc, exc_info=True)
            try:
                await context.bot.send_message(
                    chat_id=REVIEW_CHAT_ID,
                    text=f"⚠️ 重抓任务出错：{str(exc)[:200]}",
                )
            except Exception:
                pass

    asyncio.create_task(_do_refetch())


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
