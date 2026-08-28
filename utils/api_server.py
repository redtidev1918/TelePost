"""
HTTP API（/api/v1）—— 供外部项目自动化投稿

认证：Authorization: Bearer tp_xxx（token 由 TG 内 /gen_token 生成，绑定 Telegram 用户）
错误格式：{"ok": false, "error": {"code": "...", "message": "..."}}
"""
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime

from aiohttp import web

from config.settings import SUBMIT_LIMIT_PER_HOUR
from utils.api_tokens import authenticate
from utils.cache import TTLCache

logger = logging.getLogger(__name__)

API_VERSION = "1.0"
MAX_FILE_BYTES = 50 * 1024 * 1024      # Telegram Bot API 单文件上限
MAX_FILES = 10
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


def _fields_title(payload) -> str:
    return str(payload.get("title", ""))[:100]


def _fields_note(payload) -> str:
    return str(payload.get("note", ""))[:600]


def _fields_link(payload) -> str:
    return str(payload.get("link", "")).strip()


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
                    "bot_version": hf.CONFIG.get("VERSION", "")})

    async def me(request):
        row = await authenticate(_bearer(request) or "")
        if row is None:
            return _error(401, "invalid_token", "token 无效或已吊销")
        used = _rate_cache.get(f"api:{row['id']}") or 0
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

            from handlers.publish import publish_from_file_ids
            try:
                result = await publish_from_file_ids(
                    bot, media, documents,
                    tags=_fields_tags(payload), title=_fields_title(payload),
                    note=_fields_note(payload), link=link,
                    anonymous=_fields_bool(payload, "anonymous"),
                    spoiler=_fields_bool(payload, "spoiler"),
                    user_id=user_id, username=username,
                )
            except Exception as e:
                logger.error(f"API file_id 投稿失败: {e}", exc_info=True)
                return _error(502, "publish_failed", f"发布到频道失败: {str(e)[:200]}")
            logger.info(f"API file_id 投稿成功: user={user_id} message_id={result['message_id']}")
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

        try:
            reader = await request.multipart()
            while part := await reader.next():
                if part.name == "files":
                    filename = os.path.basename(part.filename or f"file-{len(files)+1}")
                    kind_hint = part.headers.get("Content-Type", "")
                    tmp_path = os.path.join(session_dir, f"{len(files)+1:02d}_{filename}")
                    size = 0
                    with open(tmp_path, "wb") as fh:
                        while chunk := await part.read_chunk(65536):
                            size += len(chunk)
                            if size > MAX_FILE_BYTES:
                                return _error(413, "file_too_large",
                                              f"{filename} 超过 50MB 上限")
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
            result = await publish_from_files(
                bot, files,
                tags=tags, title=title, note=note, link=link,
                anonymous=anonymous, spoiler=spoiler,
                user_id=user_id, username=username,
            )
        except Exception as e:
            logger.error(f"API 投稿发布失败: {e}", exc_info=True)
            return _error(502, "publish_failed", f"发布到频道失败: {str(e)[:200]}")

        logger.info(f"API 投稿成功: user={user_id} message_id={result['message_id']}")
        return _ok(result, status=201)

    web_app.router.add_get("/api/v1/health", health)
    web_app.router.add_get("/api/v1/me", me)
    web_app.router.add_post("/api/v1/submissions", create_submission)
    logger.info("API 路由已注册: /api/v1/*")
