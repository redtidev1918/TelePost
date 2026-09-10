"""
HTTP API（/api/v1）—— 供外部项目自动化投稿

认证：Authorization: Bearer tp_xxx（token 由 TG 内 /gen_token 生成，绑定 Telegram 用户）
错误格式：{"ok": false, "error": {"code": "...", "message": "..."}}
"""
import asyncio
import hmac
import json
import logging
import os
import shutil
import time
import uuid

from aiohttp import web

from config.settings import (
    API_REVIEW_REQUIRED,
    CHAT_REVIEW_REQUIRED,
    OWNER_ID,
    REVIEW_CHAT_ID,
    SUBMIT_LIMIT_PER_HOUR,
)
from utils.api_tokens import authenticate
from utils.cache import TTLCache
from database.db_manager import (
    claim_api_notification,
    mark_api_notification_sent,
    release_api_notification,
)
from services.review_service import ReviewError, load_review_policy

logger = logging.getLogger(__name__)

# 上传会话目录保留时长：超过该时长的孤儿目录由后台清扫器删除
UPLOAD_SESSION_MAX_AGE_SECONDS = int(
    os.getenv("UPLOAD_SESSION_MAX_AGE_SECONDS", "3600")
)
_upload_sweeper_started = False

API_VERSION = "1.0"
MAX_FILE_BYTES = 50 * 1024 * 1024      # Telegram Bot API 单文件上限
from config.settings import MAX_SUBMISSION_FILES

# 入站文件数上限（发布侧会按每组 ≤10 自动拆成多个 Telegram media group）。
MAX_FILES = MAX_SUBMISSION_FILES
# 文件数与体积分别受限。不能按 50 × 50 MiB 放宽到 2.5 GiB，否则低配
# 实例的持久卷可能被单个请求占满。默认累计 500 MiB，并与父路由一致。
MAX_TOTAL_FILE_BYTES = 500 * 1024 * 1024
API_CLIENT_MAX_BYTES = MAX_TOTAL_FILE_BYTES + 10 * 1024 * 1024
_rate_cache = TTLCache(default_ttl=3600, max_size=4096)


def _error(status: int, code: str, message: str) -> web.Response:
    return web.json_response(
        {"ok": False, "error": {"code": code, "message": message}},
        status=status,
    )


def _ok(data, status: int = 200) -> web.Response:
    return web.json_response({"ok": True, "data": data}, status=status)


# ---- Formal business ACK statuses --------------------------------------
# accepted            : published this request
# idempotent_replay   : same idempotency_key was already published
# duplicate_existing  : a different key already published the same work
# retryable_failure   : network/timeout; Telegram state unknown, DO NOT blind-retry
# permanent_failure   : deterministic rejection/validation
_BUSINESS_STATUS = {
    "published": "accepted",
    "pending_review": "accepted",
}


def _business_ack(result: dict) -> web.Response:
    """Normalize a facade result dict to the formal business ACK envelope."""
    reason = result.get("reuse_reason") or ""
    if reason == "idempotent_replay":
        business = "idempotent_replay"
        http_status = 200
    elif reason == "duplicate_existing":
        business = "duplicate_existing"
        http_status = 200
    elif result.get("status") in ("failed",):
        business = "permanent_failure"
        http_status = 400
    else:
        business = _BUSINESS_STATUS.get(result.get("status"), "accepted")
        http_status = 201
    data = dict(result)
    data["business_status"] = business
    return web.json_response(
        {"ok": business in ("accepted", "idempotent_replay", "duplicate_existing"),
         "data": data},
        status=http_status,
    )


_RETRYABLE_MARKERS = (
    "timed out", "timeout", "network", "connection", "server closed",
    "flood", "retry after", "unavailable",
)


def _failure_ack(exc: Exception) -> web.Response:
    """Map a delivery exception to retryable_failure / permanent_failure.

    Timeouts/network errors are retryable *in the sense the client may query*
    the delivery-lookup endpoint; the server itself never auto-resends because
    Telegram may already have accepted the album.
    """
    message = str(exc)[:200]
    from telegram import error as _tg_error
    if isinstance(exc, (_tg_error.BadRequest, _tg_error.Forbidden, ValueError)):
        retryable = False
    elif isinstance(exc, (_tg_error.TimedOut, _tg_error.NetworkError)):
        retryable = True
    else:
        lowered = message.lower()
        retryable = any(marker in lowered for marker in _RETRYABLE_MARKERS)
    business = "retryable_failure" if retryable else "permanent_failure"
    return web.json_response(
        {"ok": False,
         "data": {"business_status": business,
                  "reason": message}},
        status=503 if retryable else 400,
    )


# ---- 字段解析（multipart fields 与 JSON body 共用）----

def _fields_tags(payload) -> str:
    from utils.helper_functions import process_tags
    ok, tags = process_tags(str(payload.get("tags", "")))
    return tags if ok else ""


def _fields_text(payload, key: str, limit: int) -> str:
    """Read text fields and accept escaped newlines from config-driven clients."""
    return (str(payload.get(key, ""))
            .replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\r", "\n"))[:limit]


def _fields_title(payload) -> str:
    return _fields_text(payload, "title", 100)


def _fields_note(payload) -> str:
    return _fields_text(payload, "note", 600)


def _fields_link(payload) -> str:
    return str(payload.get("link", "")).strip()


def _fields_idempotency_key(payload) -> str:
    return str(payload.get("idempotency_key", "")).strip()[:240]


def _fields_target_id(payload) -> str:
    return str(payload.get("target_id", "")).strip()[:120]


def _fields_work_type(payload) -> str:
    value = str(payload.get("work_type", "")).strip().lower()
    return value if value in ("illustration", "novel") else ""


def _fields_pixiv_id(payload) -> str:
    return str(payload.get("pixiv_id", "")).strip()[:32]


def _clean_provenance_text(value, limit: int) -> str:
    """Bounded single-line provenance text.

    TelePost only stores and displays these strings; it never interprets their
    meaning. They are rendered in the plain-text review control card (no HTML),
    so control characters are stripped and the value is length-capped.
    """
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = "".join(ch for ch in text if ch == " " or ch.isprintable()).strip()
    return text[:limit]


def _fields_source_label(payload) -> str:
    """Human-readable source label for the review card, e.g. 'PixivFlow · 每日推荐'.

    Generic: any API client may send this; TelePost does not parse it or know
    what a "slot" or "shift" is. Empty => hidden. Bounded to 80 chars.
    """
    return _clean_provenance_text(payload.get("source_label", ""), 80)


def _fields_source_ref(payload) -> str:
    """Stable, opaque machine-readable source reference (a job/execution id).

    Stored for traceability only; TelePost never interprets its structure.
    Bounded to 160 chars.
    """
    return _clean_provenance_text(payload.get("source_ref", ""), 160)


def _fields_scheduled_at(payload) -> str:
    """Optional ISO-8601 scheduled time for the work (provenance/trace only).

    TelePost does not act on it. Bounded to 40 chars.
    """
    return _clean_provenance_text(payload.get("scheduled_at", ""), 40)


def _fields_bool(payload, key: str) -> bool:
    return str(payload.get(key, "false")).lower() in ("true", "1", "yes")


def detect_kind(filename: str, content_type: str) -> str:
    """根据扩展名与 MIME 判定媒体类型"""
    ct = (content_type or "").lower()
    ext = os.path.splitext(filename or "")[1].lower()
    if ct == "image/gif" or ext == ".gif":
        return "animation"
    if ct.startswith("image/") or ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        return "photo"
    if ct.startswith("video/") or ext in (".mp4", ".mkv", ".mov", ".avi", ".webm"):
        return "video"
    if ct.startswith("audio/") or ext in (".mp3", ".ogg", ".m4a", ".flac", ".wav"):
        return "audio"
    return "document"


def _review_mode() -> str:
    return os.getenv("TELEPOST_REVIEW_API_MODE", "readwrite").strip().lower()


def _matches_secret(value: str, secret: str) -> bool:
    return bool(value and secret) and hmac.compare_digest(value, secret)


async def _review_auth(request, *, write: bool):
    token = request.headers.get("Authorization", "")
    if token.startswith("Bearer "):
        token = token[7:].strip()
    else:
        token = ""

    review_token = os.getenv("TELEPOST_REVIEW_TOKEN", "")
    mcp_token = os.getenv("TELEPOST_MCP_REVIEW_TOKEN", "")
    if _matches_secret(token, review_token) or _matches_secret(token, mcp_token):
        actor = "mcp" if request.headers.get("X-TelePost-Source", "").lower() == "mcp" else "review-token"
        if write and _review_mode() == "readonly":
            return None, _error(403, "permission_denied", "Review API is read-only")
        return {"telegram_user_id": None, "name": actor, "scope": "review"}, None

    row = await authenticate(token)
    if row is None:
        return None, _error(401, "invalid_token", "token 无效或已吊销")
    if write and OWNER_ID is not None and int(row["telegram_user_id"] or 0) != int(OWNER_ID):
        return None, _error(403, "permission_denied", "Only owner token may modify reviews")
    if write and OWNER_ID is None:
        return None, _error(403, "permission_denied", "OWNER_ID is required for review writes")
    if write and _review_mode() == "readonly":
        return None, _error(403, "permission_denied", "Review API is read-only")
    return row, None


def _review_error(exc: ReviewError) -> web.Response:
    return _error(exc.http_status, exc.code, str(exc)[:200])


async def _run_review_action(handler):
    try:
        return await handler()
    except ReviewError as exc:
        return _review_error(exc)
    except Exception:
        logger.error("Review API action failed", exc_info=True)
        return _error(500, "internal_error", "Review action failed")


def add_api_routes(web_app, application) -> None:
    """把 /api/v1 路由挂到既有 aiohttp 应用上（每个 bot 子进程独立一套）"""
    bot = application.bot
    from handlers.publish import publish_from_files

    def _bearer(request):
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:].strip()
        return None

    async def health(request):
        import utils.helper_functions as hf
        return _ok({"service": "telepost-api", "api_version": API_VERSION,
                    "bot_version": hf.CONFIG.get("VERSION", ""),
                    "review_required": API_REVIEW_REQUIRED,
                    "api_review_required": API_REVIEW_REQUIRED,
                    "chat_review_required": CHAT_REVIEW_REQUIRED})

    async def me(request):
        row = await authenticate(_bearer(request) or "")
        if row is None:
            return _error(401, "invalid_token", "token 无效或已吊销")
        used = _rate_cache.get(f"api:{row['telegram_user_id']}") or 0
        return _ok({
            "telegram_user_id": row["telegram_user_id"],
            "name": row["name"],
            "submissions_last_hour": used,
            "rate_limit_per_hour": SUBMIT_LIMIT_PER_HOUR,
        })

    async def create_submission(request):
        token_row = await authenticate(_bearer(request) or "")
        if token_row is None:
            return _error(401, "invalid_token", "token 无效或已吊销")
        user_id = token_row["telegram_user_id"]
        username = token_row["name"] or f"user{user_id}"

        # 限频
        used = _rate_cache.get(f"api:{user_id}") or 0
        if SUBMIT_LIMIT_PER_HOUR > 0 and used >= SUBMIT_LIMIT_PER_HOUR:
            return _error(429, "rate_limited",
                          f"每小时最多 {SUBMIT_LIMIT_PER_HOUR} 次投稿，请稍后再试")
        _rate_cache.set(f"api:{user_id}", used + 1, ttl=3600)

        if (request.content_type or "").startswith("application/json"):
            # file_id 直投：素材已在 Telegram 服务器（file_id 归属本 bot），零媒体传输
            try:
                payload = await request.json()
            except Exception:
                return _error(400, "invalid_json", "JSON 解析失败")
            if not isinstance(payload, dict):
                return _error(400, "invalid_json", "JSON body 必须是对象")

            media = payload.get("media") or []
            documents = payload.get("documents") or []
            if not isinstance(media, list) or not isinstance(documents, list):
                return _error(400, "invalid_media", "media/documents 必须是数组")
            if not media and not documents:
                return _error(400, "missing_media", "media 与 documents 至少提供一项")
            if len(media) + len(documents) > MAX_FILES:
                return _error(400, "too_many_files", f"单次最多 {MAX_FILES} 个文件")

            allowed_types = ("photo", "video", "animation", "audio")
            for item in media:
                if not isinstance(item, dict) or not item.get("file_id"):
                    return _error(400, "invalid_media", "media 项必须包含 file_id")
                if item.get("type") not in allowed_types:
                    return _error(400, "invalid_media",
                                  f"media type 必须是 {'/'.join(allowed_types)}")
            for item in documents:
                if not isinstance(item, dict) or not item.get("file_id"):
                    return _error(400, "invalid_media", "documents 项必须包含 file_id")

            link = _fields_link(payload)
            if link and not link.startswith(("http://", "https://")):
                return _error(400, "invalid_link", "链接必须以 http:// 或 https:// 开头")

            try:
                common = {
                    "tags": _fields_tags(payload),
                    "title": _fields_title(payload),
                    "note": _fields_note(payload),
                    "link": link,
                    "anonymous": _fields_bool(payload, "anonymous"),
                    "spoiler": _fields_bool(payload, "spoiler"),
                    "user_id": user_id,
                    "username": username,
                }
                # JSON file_id clients always receive the four provenance
                # kwargs (empty string means "absent"); multipart omits them.
                provenance = {
                    "idempotency_key": _fields_idempotency_key(payload),
                    "target_id": _fields_target_id(payload),
                    "work_type": _fields_work_type(payload),
                    "pixiv_id": _fields_pixiv_id(payload),
                }
                if API_REVIEW_REQUIRED:
                    from handlers.review import queue_review_from_file_ids
                    queue_kwargs = dict(
                        source_label=_fields_source_label(payload),
                        source_ref=_fields_source_ref(payload),
                        scheduled_at=_fields_scheduled_at(payload),
                        **provenance,
                    )
                    result = await queue_review_from_file_ids(
                        bot, media, documents, **queue_kwargs, **common,
                    )
                else:
                    from handlers.publish import publish_from_file_ids
                    result = await publish_from_file_ids(
                        bot, media, documents, **provenance, **common,
                    )
            except Exception as e:
                action = "进入审核队列" if API_REVIEW_REQUIRED else "发布到频道"
                logger.error(f"API file_id 投稿失败: {e}", exc_info=True)
                code = "review_queue_failed" if API_REVIEW_REQUIRED else "publish_failed"
                return _error(502, code, f"{action}失败: {str(e)[:200]}")
            logger.info(
                "API file_id 投稿已处理: user=%s status=%s",
                user_id, result.get("status"),
            )
            return _business_ack(result)

        if not (request.content_type or "").startswith("multipart/"):
            return _error(400, "invalid_content_type",
                          "请使用 multipart/form-data 提交（字段 files 为媒体文件），"
                          "或以 application/json 提交 file_id 直投")

        fields = {}
        files = []
        previews = []
        upload_dir = os.path.join("data", "api_uploads")
        os.makedirs(upload_dir, exist_ok=True)
        session_dir = os.path.join(upload_dir, uuid.uuid4().hex)
        os.makedirs(session_dir, exist_ok=True)
        # One aiohttp task owns one upload directory. Cleanup runs for every
        # return/exception path, including validation errors and Telegram
        # failures, so stale uploads cannot slowly fill the persistent volume.
        request_task = asyncio.current_task()
        if request_task is not None:
            request_task.add_done_callback(
                lambda _task: shutil.rmtree(session_dir, ignore_errors=True)
            )

        try:
            reader = await request.multipart()
            total_file_bytes = 0
            while part := await reader.next():
                if part.name in {"files", "previews"}:
                    target = files if part.name == "files" else previews
                    if len(target) >= MAX_FILES:
                        return _error(400, "too_many_files", f"单次最多 {MAX_FILES} 个文件")
                    filename = os.path.basename(
                        part.filename or f"{part.name}-{len(target)+1}"
                    )
                    kind_hint = part.headers.get("Content-Type", "")
                    prefix = "file" if part.name == "files" else "preview"
                    tmp_path = os.path.join(
                        session_dir, f"{prefix}_{len(target)+1:02d}_{filename}"
                    )
                    size = 0
                    with open(tmp_path, "wb") as fh:
                        while chunk := await part.read_chunk(65536):
                            size += len(chunk)
                            total_file_bytes += len(chunk)
                            if size > MAX_FILE_BYTES:
                                return _error(413, "file_too_large",
                                              f"{filename} 超过 50MB 上限")
                            if total_file_bytes > MAX_TOTAL_FILE_BYTES:
                                return _error(
                                    413,
                                    "request_too_large",
                                    "单次投稿文件累计超过 500MB 上限",
                                )
                            fh.write(chunk)
                    target.append({"path": tmp_path, "filename": filename,
                                   "kind": detect_kind(filename, kind_hint)})
                else:
                    fields[part.name] = (await part.text()).strip()
        except Exception as e:
            return _error(400, "invalid_multipart", f"multipart 解析失败: {e}")

        if not files:
            return _error(400, "missing_files", "至少需要一个 files 字段")
        if len(files) > MAX_FILES:
            return _error(400, "too_many_files", f"单次最多 {MAX_FILES} 个文件")
        if previews and len(previews) != len(files):
            return _error(
                400, "invalid_previews",
                "previews 必须与 files 一一对应，或完全省略",
            )
        for index, preview in enumerate(previews):
            files[index]["preview_path"] = preview["path"]

        tags = _fields_tags(fields)
        if not tags:
            return _error(400, "invalid_tags", '标签格式错误（必填，最多30个，逗号分隔）')

        title = _fields_title(fields)
        note = _fields_note(fields)
        link = _fields_link(fields)
        if link and not link.startswith(("http://", "https://")):
            return _error(400, "invalid_link", "链接必须以 http:// 或 https:// 开头")
        anonymous = _fields_bool(fields, "anonymous")
        spoiler = _fields_bool(fields, "spoiler")

        try:
            common = {
                "tags": tags,
                "title": title,
                "note": note,
                "link": link,
                "anonymous": anonymous,
                "spoiler": spoiler,
                "user_id": user_id,
                "username": username,
            }
            provenance = {}
            for _name, _value in (
                ("idempotency_key", _fields_idempotency_key(fields)),
                ("target_id", _fields_target_id(fields)),
                ("work_type", _fields_work_type(fields)),
                ("pixiv_id", _fields_pixiv_id(fields)),
            ):
                if _value:
                    provenance[_name] = _value
            if API_REVIEW_REQUIRED:
                from handlers.review import queue_review_from_files
                queue_kwargs = dict(
                    source_label=_fields_source_label(fields),
                    source_ref=_fields_source_ref(fields),
                    scheduled_at=_fields_scheduled_at(fields),
                    **provenance,
                )
                result = await queue_review_from_files(
                    bot, files, **queue_kwargs, **common,
                )
            else:
                result = await publish_from_files(
                    bot, files, **provenance, **common,
                )
        except Exception as e:
            logger.error(f"API 投稿处理失败: {e}", exc_info=True)
            return _failure_ack(e)

        logger.info(
            "API 投稿已处理: user=%s status=%s",
            user_id, result.get("status"),
        )
        return _business_ack(result)

    async def create_notification(request):
        token_row = await authenticate(_bearer(request) or "")
        if token_row is None:
            return _error(401, "invalid_token", "token 无效或已吊销")
        if not REVIEW_CHAT_ID:
            return _error(409, "review_chat_not_configured", "未配置审核群")
        if not (request.content_type or "").startswith("application/json"):
            return _error(400, "invalid_content_type", "请使用 application/json")
        try:
            payload = await request.json()
        except Exception:
            return _error(400, "invalid_json", "JSON 解析失败")
        if not isinstance(payload, dict):
            return _error(400, "invalid_json", "JSON body 必须是对象")
        text = str(payload.get("text", "")).strip()[:2000]
        if not text:
            return _error(400, "missing_text", "text 不能为空")
        key = _fields_idempotency_key(payload)
        user_id = token_row["telegram_user_id"]
        if key:
            try:
                claimed = await claim_api_notification(user_id, key)
            except Exception:
                logger.error("API 通知幂等状态持久化失败", exc_info=True)
                return _error(503, "notification_state_failed", "通知状态暂时不可用，请稍后重试")
            if not claimed:
                return _ok({"status": "duplicate"}, status=201)
        try:
            message = await bot.send_message(
                chat_id=REVIEW_CHAT_ID,
                text=text,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            if key:
                try:
                    await release_api_notification(user_id, key)
                except Exception:
                    logger.error("API 通知失败后释放幂等键失败", exc_info=True)
            logger.error("API 审核群通知失败: %s", exc, exc_info=True)
            return _error(502, "notification_failed", f"审核群通知失败: {str(exc)[:200]}")
        if key:
            try:
                await mark_api_notification_sent(user_id, key, message.message_id)
            except Exception:
                # Telegram 已经接收成功；不向调用方谎报失败并触发立即重发。
                logger.error("API 通知已发送，但持久化完成状态失败", exc_info=True)
        logger.info(
            "API 审核群通知已发送: user=%s message_id=%s",
            token_row["telegram_user_id"], message.message_id,
        )
        return _ok({"status": "notified", "message_id": message.message_id}, status=201)

    from services.review_service import ReviewService
    review_service = ReviewService()

    async def list_reviews(request):
        async def action():
            _, error = await _review_auth(request, write=False)
            if error:
                return error
            try:
                limit = int(request.query.get("limit", "20"))
            except (TypeError, ValueError):
                return _error(400, "invalid_limit", "limit 必须是整数")
            cursor = request.query.get("cursor") or None
            data = await review_service.list_pending(limit=limit, cursor=cursor)
            return _ok(data)
        return await _run_review_action(action)

    async def get_review(request):
        async def action():
            _, error = await _review_auth(request, write=False)
            if error:
                return error
            try:
                review_id = int(request.match_info["review_id"])
            except (TypeError, ValueError):
                return _error(400, "invalid_review_id", "review_id 必须是整数")
            item = await review_service.get_review(review_id)
            return _ok(item.to_dict())
        return await _run_review_action(action)

    async def get_review_media(request):
        async def action():
            _, error = await _review_auth(request, write=False)
            if error:
                return error
            try:
                review_id = int(request.match_info["review_id"])
                index = int(request.match_info["index"])
            except (TypeError, ValueError):
                return _error(400, "invalid_media_index", "review_id/index 必须是整数")
            result = await review_service.get_media(
                bot, review_id, index, request.query.get("variant", "preview")
            )
            response = web.Response(body=result.data, content_type=result.mime_type)
            response.headers["Cache-Control"] = "private, max-age=60"
            if result.filename:
                response.headers["Content-Disposition"] = f'inline; filename="{result.filename}"'
            response.headers["X-Review-Media"] = json.dumps(result.to_metadata(), ensure_ascii=False)
            return response
        return await _run_review_action(action)

    async def review_policy(request):
        _, error = await _review_auth(request, write=False)
        if error:
            return error
        return _ok({"policy": load_review_policy(), "media_type": "text/markdown"})

    async def _json_body(request):
        try:
            payload = await request.json()
        except Exception:
            return None, _error(400, "invalid_json", "JSON 解析失败")
        if not isinstance(payload, dict):
            return None, _error(400, "invalid_json", "JSON body 必须是对象")
        return payload, None

    async def approve_review(request):
        async def action():
            actor_row, auth_error = await _review_auth(request, write=True)
            if auth_error:
                return auth_error
            payload, body_error = await _json_body(request)
            if body_error:
                return body_error
            spoiler = payload.get("spoiler")
            if spoiler is not None and not isinstance(spoiler, bool):
                return _error(400, "invalid_spoiler", "spoiler 必须是布尔值")
            try:
                review_id = int(request.match_info["review_id"])
            except (TypeError, ValueError):
                return _error(400, "invalid_review_id", "review_id 必须是整数")
            source = "mcp" if request.headers.get("X-TelePost-Source", "").lower() == "mcp" else "http"
            result = await review_service.approve(
                bot,
                review_id,
                spoiler=spoiler,
                actor=actor_row["name"] if actor_row["telegram_user_id"] is None else actor_row["telegram_user_id"],
                source=source,
                notify_chat_submitter=True,
            )
            return _ok(result.to_dict())
        return await _run_review_action(action)

    async def reject_review(request):
        async def action():
            actor_row, auth_error = await _review_auth(request, write=True)
            if auth_error:
                return auth_error
            payload, body_error = await _json_body(request)
            if body_error:
                return body_error
            reason = payload.get("reason")
            if reason is not None and not isinstance(reason, str):
                return _error(400, "invalid_reason", "reason 必须是字符串")
            try:
                review_id = int(request.match_info["review_id"])
            except (TypeError, ValueError):
                return _error(400, "invalid_review_id", "review_id 必须是整数")
            source = "mcp" if request.headers.get("X-TelePost-Source", "").lower() == "mcp" else "http"
            result = await review_service.reject(
                bot,
                review_id,
                reason=reason,
                actor=actor_row["name"] if actor_row["telegram_user_id"] is None else actor_row["telegram_user_id"],
                source=source,
                notify_chat_submitter=True,
            )
            return _ok(result.to_dict())
        return await _run_review_action(action)

    async def set_review_spoiler(request):
        async def action():
            actor_row, auth_error = await _review_auth(request, write=True)
            if auth_error:
                return auth_error
            payload, body_error = await _json_body(request)
            if body_error:
                return body_error
            if not isinstance(payload.get("spoiler"), bool):
                return _error(400, "invalid_spoiler", "spoiler 必须是布尔值")
            try:
                review_id = int(request.match_info["review_id"])
            except (TypeError, ValueError):
                return _error(400, "invalid_review_id", "review_id 必须是整数")
            source = "mcp" if request.headers.get("X-TelePost-Source", "").lower() == "mcp" else "http"
            result = await review_service.set_spoiler(
                review_id,
                payload["spoiler"],
                actor=actor_row["name"] if actor_row["telegram_user_id"] is None else actor_row["telegram_user_id"],
                source=source,
            )
            return _ok(result.to_dict())
        return await _run_review_action(action)

    async def delivery_lookup(request):
        """Authenticated reconciliation lookup for a downstream work.
        Lets the caller (e.g. PixivFlow doctor) ask 'did target X already
        publish work Y?' without trusting a 2xx alone."""
        token_row = await authenticate(_bearer(request) or "")
        if token_row is None:
            return _error(401, "invalid_token", "token 无效或已吊销")
        q = request.query
        target = (q.get("target") or "").strip()[:120]
        work_type = (q.get("work_type") or "").strip().lower()
        pixiv_id = (q.get("pixiv_id") or "").strip()[:32]
        if work_type not in ("illustration", "novel") or not pixiv_id:
            return _error(400, "invalid_query", "work_type (illustration|novel) 与 pixiv_id 必填")
        from database.db_manager import get_db
        cutoff = time.time() - (7 * 24 * 3600)
        params = [pixiv_id, work_type] + ([target] if target else []) + [cutoff]
        async with get_db() as conn:
            cursor = await conn.execute(
                "SELECT id, status, idempotency_key, target_id, published_message_id AS message_id, "
                "pixiv_id, work_type, decided_at FROM pending_reviews "
                "WHERE pixiv_id=? AND work_type=? "
                + ("AND target_id=? " if target else "")
                + "AND status='published' AND decided_at >= ? ORDER BY decided_at DESC LIMIT 1",
                params,
            )
            review_row = await cursor.fetchone()
            cursor = await conn.execute(
                "SELECT id, status, idempotency_key, target_id, message_id, pixiv_id, work_type, created_at "
                "FROM delivery_ledger WHERE pixiv_id=? AND work_type=? "
                + ("AND target_id=? " if target else "")
                + "AND status='published' AND created_at >= ? ORDER BY created_at DESC LIMIT 1",
                ([pixiv_id, work_type, target, cutoff] if target else [pixiv_id, work_type, cutoff]),
            )
            ledger_row = await cursor.fetchone()

        match = review_row or ledger_row
        if match is None:
            return _ok({"found": False, "target": target, "work_type": work_type, "pixiv_id": pixiv_id})
        return _ok({
            "found": True,
            "target": target,
            "work_type": work_type,
            "pixiv_id": pixiv_id,
            "delivery_status": match["status"],
            "message_id": match["message_id"],
            "matched_idempotency_key": match["idempotency_key"],
            "source": "review" if review_row is not None else "direct",
        })

    web_app.router.add_get("/api/v1/reviews/policy", review_policy)
    web_app.router.add_get("/api/v1/reviews", list_reviews)
    web_app.router.add_get("/api/v1/reviews/{review_id}", get_review)
    web_app.router.add_get(
        "/api/v1/reviews/{review_id}/media/{index}", get_review_media
    )
    web_app.router.add_post("/api/v1/reviews/{review_id}/approve", approve_review)
    web_app.router.add_post("/api/v1/reviews/{review_id}/reject", reject_review)
    web_app.router.add_patch("/api/v1/reviews/{review_id}/spoiler", set_review_spoiler)
    web_app.router.add_get("/api/v1/health", health)
    web_app.router.add_get("/api/v1/me", me)
    web_app.router.add_post("/api/v1/submissions", create_submission)
    web_app.router.add_get("/api/v1/deliveries/lookup", delivery_lookup)
    web_app.router.add_post("/api/v1/notifications", create_notification)
    logger.info("API 路由已注册: /api/v1/*")
    _ensure_upload_sweeper()


async def _sweep_old_upload_dirs() -> None:
    """Periodically remove orphaned upload session dirs.

    The per-request cleanup relies on an asyncio done-callback which is not
    guaranteed to fire in every aiohttp execution path (observed leaks on
    large multi-page uploads). This sweeper deletes any session dir older
    than UPLOAD_SESSION_MAX_AGE_SECONDS, so failed/aborted requests can
    never fill the persistent volume.
    """
    upload_dir = os.path.join("data", "api_uploads")
    while True:
        try:
            if os.path.isdir(upload_dir):
                cutoff = time.time() - UPLOAD_SESSION_MAX_AGE_SECONDS
                removed = 0
                for name in os.listdir(upload_dir):
                    path = os.path.join(upload_dir, name)
                    try:
                        if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                            shutil.rmtree(path, ignore_errors=True)
                            removed += 1
                    except OSError:
                        logger.debug("扫描上传会话目录失败: %s", path, exc_info=True)
                if removed:
                    logger.info("清理 %d 个过期上传会话目录", removed)
        except Exception:
            logger.warning("上传会话目录清扫失败", exc_info=True)
        await asyncio.sleep(3600)


def _ensure_upload_sweeper() -> None:
    """Start the background sweeper once per process."""
    global _upload_sweeper_started
    if _upload_sweeper_started:
        return
    _upload_sweeper_started = True
    try:
        asyncio.get_running_loop().create_task(_sweep_old_upload_dirs())
    except RuntimeError:
        # No running loop yet (startup phase) — safe to ignore; the next
        # add_api_routes call will start it once the loop is active.
        _upload_sweeper_started = False
