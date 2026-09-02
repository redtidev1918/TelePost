"""
HTTP API（/api/v1）—— 供外部项目自动化投稿

认证：Authorization: Bearer tp_xxx（token 由 TG 内 /gen_token 生成，绑定 Telegram 用户）
错误格式：{"ok": false, "error": {"code": "...", "message": "..."}}
"""
import asyncio
import logging
import os
import shutil
import time
import uuid

from aiohttp import web

from config.settings import (
    API_REVIEW_REQUIRED,
    CHAT_REVIEW_REQUIRED,
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

logger = logging.getLogger(__name__)

# 上传会话目录保留时长：超过该时长的孤儿目录由后台清扫器删除
UPLOAD_SESSION_MAX_AGE_SECONDS = int(
    os.getenv("UPLOAD_SESSION_MAX_AGE_SECONDS", "3600")
)
_upload_sweeper_started = False

API_VERSION = "1.0"
MAX_FILE_BYTES = 50 * 1024 * 1024      # Telegram Bot API 单文件上限
# 入站文件数上限（发布侧会按每组 ≤10 自动拆成多个 Telegram media group），
# 放宽以支持多页插画/图集整本投稿（如 Pixiv 24 页作品）。
MAX_FILES = 50
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
                if API_REVIEW_REQUIRED:
                    from handlers.review import queue_review_from_file_ids
                    result = await queue_review_from_file_ids(
                        bot, media, documents,
                        idempotency_key=_fields_idempotency_key(payload),
                        **common,
                    )
                else:
                    from handlers.publish import publish_from_file_ids
                    result = await publish_from_file_ids(
                        bot, media, documents, **common,
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
            return _ok(result, status=201)

        if not (request.content_type or "").startswith("multipart/"):
            return _error(400, "invalid_content_type",
                          "请使用 multipart/form-data 提交（字段 files 为媒体文件），"
                          "或以 application/json 提交 file_id 直投")

        fields = {}
        files = []
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
                if part.name == "files":
                    if len(files) >= MAX_FILES:
                        return _error(400, "too_many_files", f"单次最多 {MAX_FILES} 个文件")
                    filename = os.path.basename(part.filename or f"file-{len(files)+1}")
                    kind_hint = part.headers.get("Content-Type", "")
                    tmp_path = os.path.join(session_dir, f"{len(files)+1:02d}_{filename}")
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
                    files.append({"path": tmp_path, "filename": filename,
                                  "kind": detect_kind(filename, kind_hint)})
                else:
                    fields[part.name] = (await part.text()).strip()
        except Exception as e:
            return _error(400, "invalid_multipart", f"multipart 解析失败: {e}")

        if not files:
            return _error(400, "missing_files", "至少需要一个 files 字段")
        if len(files) > MAX_FILES:
            return _error(400, "too_many_files", f"单次最多 {MAX_FILES} 个文件")

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
            if API_REVIEW_REQUIRED:
                from handlers.review import queue_review_from_files
                result = await queue_review_from_files(
                    bot, files,
                    idempotency_key=_fields_idempotency_key(fields),
                    **common,
                )
            else:
                result = await publish_from_files(bot, files, **common)
        except Exception as e:
            action = "进入审核队列" if API_REVIEW_REQUIRED else "发布到频道"
            logger.error(f"API 投稿处理失败: {e}", exc_info=True)
            code = "review_queue_failed" if API_REVIEW_REQUIRED else "publish_failed"
            return _error(502, code, f"{action}失败: {str(e)[:200]}")

        logger.info(
            "API 投稿已处理: user=%s status=%s",
            user_id, result.get("status"),
        )
        return _ok(result, status=201)

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

    web_app.router.add_get("/api/v1/health", health)
    web_app.router.add_get("/api/v1/me", me)
    web_app.router.add_post("/api/v1/submissions", create_submission)
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
