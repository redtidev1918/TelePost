"""
HTTP API（/api/v1）—— 供外部项目自动化投稿 / Telegram Mini App

认证（两种 Principal，统一进应用层，§81-§82）：
* API token：``Authorization: Bearer tp_xxx``（TG 内 /gen_token 生成，绑定 Telegram 用户）。
* Mini App session：``Authorization: Bearer ma_v1.xxx``（经 /api/v1/miniapp/session 签发，
  服务器验证过 Telegram initData 后的短期身份；不暴露任何 Bot/API 秘密）。
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
from typing import Optional

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


# ---- Principal bridge (§82): API token OR Mini App session -------------
# Every authenticated handler resolves a *principal* dict with one shape, so
# business code never parses Authorization headers itself.

def _bearer_token(request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return ""


async def _resolve_principal(request) -> Optional[dict]:
    """Resolve the caller to a unified principal (None when unauthenticated).

    Order: Mini App session (ma_v1.*) first, then the API token (tp_*).

    ``kind`` separates the ACTING principal class (§identity):
    * ``user``    — a verified Telegram human (Mini App session);
    * ``service`` — an API token (automatic/third-party delivery). A service
      principal MUST NOT become a submission owner; ownership is only ever set
      through an explicit verified human submitter.
    """
    token = _bearer_token(request)
    from telepost.miniapp import session as miniapp_session
    if token.startswith("ma_v1."):
        principal = miniapp_session.verify_session(token)
        if principal is None:
            return None
        return {
            "kind": "user",
            "telegram_user_id": principal.telegram_user_id,
            "name": principal.username or principal.display_name
                     or f"user{principal.telegram_user_id}",
            "username": principal.username,
            "display_name": principal.display_name,
            "roles": principal.roles,
            "surface": "mini_app",
            "actor_subject": f"telegram:{principal.telegram_user_id}",
        }
    row = await authenticate(token)
    if row is None:
        return None
    return {
        "kind": "service",
        "telegram_user_id": int(row["telegram_user_id"] or 0),
        "name": row["name"] or f"user{row['telegram_user_id']}",
        "roles": None,  # API tokens reuse the Bot's OWNER_ID/ADMIN_IDS rule
        "surface": "api",
        "token_id": int(row["id"] or 0),
        "token_name": row["name"] or "",
        "actor_subject": f"api_token:{row['id'] or 0}",
    }


def _principal_roles(principal: dict):
    roles = principal.get("roles")
    if roles:
        return list(roles)
    from telepost.miniapp import rbac as miniapp_rbac
    uid = principal.get("telegram_user_id")
    return miniapp_rbac.roles_for(uid if uid else None)


def _principal_is_reviewer(principal: dict) -> bool:
    """Reviewer decision for any principal (Mini App roles or Bot identity)."""
    from telepost.miniapp import rbac as miniapp_rbac
    return miniapp_rbac.can_review(_principal_roles(principal))


def _principal_is_owner(principal: dict) -> bool:
    uid = principal.get("telegram_user_id")
    return uid is not None and OWNER_ID is not None and int(uid) == int(OWNER_ID)


async def _audit_submission(event: str, *, user_id, idempotency_key="",
                            target_id="", work_type="", pixiv_id="",
                            source_ref="", review_id=None, detail=None,
                            actor_kind="user", actor_subject="") -> None:
    from telepost.observability import audit as audit_mod
    if actor_kind == "service":
        actor = f"service:{actor_subject or 'api'}"
    elif user_id:
        actor = f"telegram_user:{user_id}"
    else:
        actor = "api"
    await audit_mod.record_event(
        event,
        actor=actor,
        idempotency_key=idempotency_key or None,
        target_id=target_id or None,
        work_type=work_type or None,
        pixiv_id=pixiv_id or None,
        review_id=review_id,
        execution_id=audit_mod.execution_id_from_ref(source_ref),
        detail=detail,
    )


USER_STATUS = {
    "preparing": "preparing",
    "pending": "in_review",
    "pending_review": "in_review",
    "publishing": "publishing",
    "published": "published",
    "rejected": "rejected",
    "failed": "failed",
    "expired": "expired",
}


def _logical_summary(row) -> dict:
    """User-facing LOGICAL submission DTO (one per review chain, §mine).

    Never exposes internal lineage/audit fields (chain internals, refetch
    request ids, slot ids, actor subjects) and never returns a superseded
    generation as its own item.
    """
    import json as _json
    generations = int(row["generation_count"] or 1)
    return {
        "submission_id": row["review_chain_id"] or f"review-{row['id']}",
        "review_chain_id": row["review_chain_id"] or "",
        "current_review_id": row["id"],
        "status": USER_STATUS.get(row["status"], row["status"]),
        "title": row["title"] or "",
        "tags": [t for t in (row["tags"] or "").split() if t],
        "media_count": len(_json.loads(row["media_json"] or "[]")),
        "document_count": len(_json.loads(row["documents_json"] or "[]")),
        "spoiler": bool(row["spoiler"]),
        "created_at": row["chain_created_at"] or row["created_at"],
        "updated_at": row["updated_at"],
        "generation": int(row["generation"] or 0),
        "refetch_count": max(0, generations - 1),
    }


def _parse_own_cursor(cursor: Optional[str]):
    if not cursor:
        return None, None
    import re as _re
    match = _re.fullmatch(r"(\d+(?:\.\d+)?):(\d+)", cursor)
    if not match:
        raise ReviewError("invalid cursor", details={"cursor": cursor})
    return float(match.group(1)), int(match.group(2))


async def _own_submissions(user_id, *, limit: int, cursor: Optional[str]):
    """Own-submission history: human-owned LOGICAL submissions, keyset paged.

    One item per review chain (refetch generations collapse), keyed on the
    verified submitter (§identity): service/automatic submissions never appear
    even when an API token bound to this user created them.
    """
    from telepost.storage.sqlite.reviews import ReviewRepository

    updated_cursor, id_cursor = _parse_own_cursor(cursor)
    rows = await ReviewRepository().list_logical_submissions(
        user_id, limit=limit + 1,
        updated_cursor=updated_cursor, id_cursor=id_cursor,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [_logical_summary(row) for row in rows]
    next_cursor = None
    if has_more and rows:
        next_cursor = f"{rows[-1]['updated_at']}:{rows[-1]['id']}"
    return items, next_cursor


async def _own_submission_detail(user_id: int, review_id: int) -> dict:
    """Owner-scoped detail for one logical submission (user-safe fields only)."""
    from telepost.storage.sqlite.reviews import ReviewRepository

    from services.review_service import ReviewNotFoundError, _media

    repo = ReviewRepository()
    row = await repo.get(review_id)
    if row is None:
        raise ReviewNotFoundError("review not found", details={"review_id": review_id})
    if int(row["submitter_user_id"] or 0) != int(user_id):
        # Never reveal that someone else's submission exists.
        raise ReviewNotFoundError("review not found", details={"review_id": review_id})
    chain_id = row["review_chain_id"] or f"review-{row['id']}"
    head = await repo.head_of_chain(chain_id)
    source = head if head is not None else row
    if int(source["submitter_user_id"] or 0) != int(user_id):
        raise ReviewNotFoundError("review not found")
    base = _logical_summary(source)
    base.update({
        "media": [{"index": item.index, "kind": item.kind,
                   "filename": item.filename}
                  for item in _media(source)],
        "note": source["note"] or "",
        "link": source["link"] or "",
    })
    return base


_TARGET_TYPE_LABEL = {"illustration": "插画", "novel": "小说"}

_TARGET_STATUS_TEXT = {
    "submitted": "✅ 已提交",
    "no_candidate": "❌ 未找到合适作品",
    "duplicate": "❌ 候选均为历史重复",
    "failed": "❌ 执行失败",
    "delivery_failed": "❌ 投递失败",
    "skipped": "➖ 跳过",
}

_OVERALL = {
    "success": ("✅", "执行完成"),
    "partial": ("⚠️", "部分完成"),
    "failed": ("❌", "执行失败"),
}


def build_schedule_outcome_text(schedule_id: str, status: str,
                                targets: list, duration_ms=None) -> str:
    """Terminal schedule summary for the review/admin group (§schedule-notify).

    All terminal outcomes (success/partial/failed) are reported. The text uses
    user-facing labels only — no slot/cell/outbox jargon — and never contains
    mention-capable entities (no @-anchors, no tg://user links, §ghost-mention).
    """
    emoji, verb = _OVERALL.get(status, ("ℹ️", "结束"))
    lines = [f"{emoji} {schedule_id} {verb}"]
    if targets:
        for item in targets:
            if not isinstance(item, dict):
                continue
            label = _TARGET_TYPE_LABEL.get(str(item.get("work_type") or ""),
                                           str(item.get("target_id") or "任务"))
            value = _TARGET_STATUS_TEXT.get(str(item.get("status") or ""),
                                            str(item.get("status") or "未知"))
            lines.append(f"{label}：{value}")
    if status == "partial":
        lines.append("已完成全部恢复尝试，本次不再重试。")
    return "\n".join(lines)


def _result_reused_id(result: dict):
    return result.get("review_id") or result.get("message_id")


def _audit_key(raw_key: str, result: dict, *, user_id: int) -> str:
    """Mirror the review queue's key normalization so events link to the row."""
    if not (raw_key and API_REVIEW_REQUIRED and result.get("review_id")):
        return raw_key
    from telepost.application.review_queue import normalize_idempotency_key
    return normalize_idempotency_key(user_id, raw_key, "api")


async def _audit_submission_outcome(result: dict, **kwargs) -> None:
    if result.get("reused"):
        reused_id = _result_reused_id(result)
        await _audit_submission(
            "submission.duplicate",
            review_id=result.get("review_id") if result.get("status") in (
                "pending_review", "pending"
            ) else None,
            detail={"reused_id": reused_id,
                    "reuse_reason": result.get("reuse_reason", "")},
            **kwargs,
        )
    else:
        await _audit_submission(
            "submission.accepted",
            review_id=result.get("review_id")
            if result.get("status") == "pending_review" else None,
            detail={"message_id": result.get("message_id")},
            **kwargs,
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


# Terminal remote review states. They must never be reported as an accepted
# delivery, and retrying the same idempotency key can only return the same row.
_FAILED_DELIVERY_STATUSES = ("failed", "rejected", "invalid", "expired")


def _is_failed_status(status) -> bool:
    return str(status or "").strip().lower() in _FAILED_DELIVERY_STATUSES


def _business_ack(result: dict) -> web.Response:
    """Normalize a facade result dict to the formal business ACK envelope."""
    reason = result.get("reuse_reason") or ""
    if reason == "idempotent_replay" and _is_failed_status(result.get("status")):
        # A reuse only ACKs successfully when the REUSED RECORD is itself on the
        # success path. Reporting a reused `failed` record as 200 /
        # idempotent_replay told callers "already accepted" for a submission
        # that never reached the review queue, so a downstream failure was
        # recorded as an end-to-end success by the client. The row is terminal
        # for this idempotency key (a retry returns the same failed record).
        business = "permanent_failure"
        http_status = 400
    elif reason == "idempotent_replay":
        business = "idempotent_replay"
        http_status = 200
    elif reason == "duplicate_existing":
        business = "duplicate_existing"
        http_status = 200
    elif _is_failed_status(result.get("status")):
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


def _fields_refetch_request_id(payload) -> str:
    """Request UUID of the remote refetch attempt that produced this submission.

    When non-empty, this submission IS a replacement: the review chain advances
    and the source review is superseded only after this row is durable
    (commit-after-success). Empty for scheduled/original submissions.
    """
    return _clean_provenance_text(payload.get("refetch_request_id", ""), 40)


def _invalid_refetch_request_id(payload) -> bool:
    value = payload.get("refetch_request_id", "")
    if value is None or value == "":
        return False
    if not isinstance(value, str):
        return True
    try:
        return str(uuid.UUID(value)) != value
    except ValueError:
        return True


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


async def _maybe_notify_direct_submitter(result: dict, submitter_user_id,
                                         anonymous: bool, source: str) -> None:
    """UNIFIED publication-success hook for DIRECT_PUBLISH API submissions
    (§notify-submitter). Only after a CONFIRMED channel publish; service rows
    (submitter NULL) skip; replays never re-notify."""
    from telepost.application.submitter_notify import (
        PublicationContext,
        SubmitterNotifyService,
    )

    if not submitter_user_id:
        return
    if result.get("reused") or str(result.get("status") or "") != "published":
        return
    message_id = result.get("message_id") or result.get("published_message_id")
    if not message_id:
        return
    try:
        link = ""
        try:
            from telepost.application.publication import _legacy_link
            link = _legacy_link(int(message_id))
        except Exception:
            pass
        context = PublicationContext(
            source=source,
            publication_id=int(message_id),
            submitter_user_id=int(submitter_user_id),
            anonymous=bool(anonymous),
            link=link,
        )
        await SubmitterNotifyService().notify_published(context)
    except Exception as exc:
        logger.warning("API 直发投稿者通知失败: message=%s error=%s", message_id, exc)


def _api_review() -> bool:
    """API (Mini App / service) submissions route to the review queue when the
    operator policy says so (API_REVIEW_REQUIRED=true). Domain-owned disposition
    (§submission-disposition): the HTTP entry point shares the ReviewQueueService
    but its default may differ from the native chat default.

    The domain model is the SSOT; the module-level import is ALSO honoured so
    established tests that monkeypatch ``api_server.API_REVIEW_REQUIRED`` keep
    working (both derive from the same environment at import time)."""
    from telepost.domain.submission import SubmissionDisposition, api_disposition

    if api_disposition() == SubmissionDisposition.REVIEW_REQUIRED:
        return True
    return bool(API_REVIEW_REQUIRED)


def _display_name_of(user: dict) -> str:
    """Presentation-only display name (never an ownership key; §identity)."""
    parts = [str(user.get(k, "") or "").strip() for k in ("first_name", "last_name")]
    return " ".join(p for p in parts if p)[:128]


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

    principal = await _resolve_principal(request)
    if principal is None:
        return None, _error(401, "invalid_token", "token 无效或已吊销")
    if principal["surface"] == "api":
        # Legacy API-token path: owner-only writes (unchanged contract).
        if write and OWNER_ID is not None and int(principal["telegram_user_id"] or 0) != int(OWNER_ID):
            return None, _error(403, "permission_denied", "Only owner token may modify reviews")
        if write and OWNER_ID is None:
            return None, _error(403, "permission_denied", "OWNER_ID is required for review writes")
        if write and _review_mode() == "readonly":
            return None, _error(403, "permission_denied", "Review API is read-only")
        return {"telegram_user_id": principal["telegram_user_id"],
                "name": principal["name"], "scope": "api"}, None

    # Mini App principal: server-side RBAC (§13-§14, §83).
    # TELEPOST_REVIEW_API_MODE=readonly constrains API-token/MCP write (an
    # unattended-automation safety switch); a human reviewer acting in the
    # Mini App is an explicit manual action and stays writable regardless.
    if not _principal_is_reviewer(principal):
        return None, _error(403, "permission_denied", "需要审核权限")
    return {
        "telegram_user_id": principal["telegram_user_id"],
        "name": principal["name"],
        "roles": principal["roles"],
        "scope": "mini_app",
    }, None


async def _review_owner_principal(request) -> Optional[int]:
    """Verified HUMAN principal id for owner-scope reads (§identity).

    Returns the current user's telegram_user_id ONLY for ``kind=user``
    principals (Mini App sessions). Service/api-token principals never qualify,
    so an automatic submission can never be read back through an owner-scope
    path as if a human owned it.
    """
    principal = await _resolve_principal(request)
    if principal is None or principal.get("kind") != "user":
        return None
    uid = principal.get("telegram_user_id")
    return int(uid) if uid else None


def _review_error(exc: ReviewError) -> web.Response:
    return _error(exc.http_status, exc.code, str(exc)[:200])


def _editorial_error(exc: Exception) -> web.Response:
    from telepost.application.editorial import EditorialObsoleteError
    from telepost.storage.sqlite.editorial import (
        EditorialConflictError,
        EditorialNotFoundError,
        EditorialStateError,
    )

    if isinstance(exc, EditorialNotFoundError):
        return _error(404, "editorial_not_found", str(exc)[:200])
    if isinstance(exc, EditorialObsoleteError):
        return _error(409, "editorial_stale", str(exc)[:200])
    if isinstance(exc, EditorialConflictError):
        return _error(409, "editorial_conflict", str(exc)[:200])
    if isinstance(exc, EditorialStateError):
        return _error(409, "editorial_state", str(exc)[:200])
    return _error(409, "editorial_error", str(exc)[:200])


def _editor_actor(actor_row) -> dict:
    """Editor identity for revision bookkeeping (never exposed to submitters)."""
    name = str(actor_row.get("name") or "") if isinstance(actor_row, dict) else ""
    uid = actor_row.get("telegram_user_id") if isinstance(actor_row, dict) else None
    username = str(actor_row.get("username") or "") if isinstance(actor_row, dict) else ""
    if not username:
        username = name
    return {
        "telegram_user_id": uid,
        "username": username,
        "display_name": name,
    }


def editorial_dto(revision: dict) -> dict:
    import json as _json

    return {
        "id": revision["id"],
        "review_id": revision["review_id"],
        "revision_number": revision["revision_number"],
        "status": revision["status"],
        "severity": revision["severity"],
        "summary": revision["summary"],
        "change_set": _json.loads(revision["change_set"] or "{}"),
        "base_snapshot": _json.loads(revision["base_snapshot"] or "{}"),
        "edited_snapshot": _json.loads(revision["edited_snapshot"] or "{}"),
        "version": revision["version"],
        "editor_display": revision["editor_display_name"] or revision["editor_username"] or "",
        "created_at": revision["created_at"],
        "updated_at": revision["updated_at"],
        "finalized_at": revision["finalized_at"],
        "published_at": revision["published_at"],
        "published_message_id": revision["published_message_id"],
        "published_snapshot": _json.loads(revision["published_snapshot"] or "{}"),
    }


def editorial_submitter_dto(revision: dict) -> dict:
    """Submitter-safe DTO: no editor_user_id, no internal fields (§49-§54)."""
    import json as _json

    changes = _json.loads(revision["change_set"] or "{}")
    published = _json.loads(revision["published_snapshot"] or "{}")
    edited = _json.loads(revision["edited_snapshot"] or "{}")
    return {
        "revision_number": revision["revision_number"],
        "status": revision["status"],
        "summary": revision["summary"] or "",
        "change_set": changes,
        "edited_snapshot": edited,
        "published_snapshot": published,
        "finalized_at": revision["finalized_at"],
        "published_at": revision["published_at"],
        "published_message_id": revision["published_message_id"],
        "editor_display": "频道管理员",
    }


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
        principal = await _resolve_principal(request)
        if principal is None:
            return _error(401, "invalid_token", "token 无效或已吊销")
        uid = principal["telegram_user_id"]
        used = _rate_cache.get(f"api:{uid}") or 0
        payload = {
            "telegram_user_id": uid,
            "name": principal["name"],
            "username": principal.get("username", ""),
            "display_name": principal.get("display_name", ""),
            "surface": principal["surface"],
            "submissions_last_hour": used,
            "rate_limit_per_hour": SUBMIT_LIMIT_PER_HOUR,
        }
        if principal["surface"] == "mini_app":
            payload["roles"] = _principal_roles(principal)
        return _ok(payload)

    async def miniapp_session(request):
        """POST /api/v1/miniapp/session — validate Telegram initData, mint a session.

        A Mini App never touches a bot token or a long-lived API token: it only
        sends ``initData``; the server verifies it, derives roles, and returns
        a short-lived session (Bearer ma_v1.*). The raw initData is never
        logged or persisted (§9-§11, §56, §58).
        """
        from telepost.miniapp import auth as miniapp_auth
        from telepost.miniapp import rbac as miniapp_rbac
        from telepost.miniapp import session as miniapp_session

        if not miniapp_auth.init_data_enabled():
            return _error(403, "miniapp_disabled", "Mini App 未启用")
        try:
            payload = await request.json()
        except Exception:
            return _error(400, "invalid_json", "JSON 解析失败")
        if not isinstance(payload, dict):
            return _error(400, "invalid_json", "JSON body 必须是对象")
        init_data = str(payload.get("initData") or payload.get("init_data") or "").strip()
        try:
            user = await asyncio.to_thread(miniapp_auth.validate_init_data, init_data)
        except miniapp_auth.InitDataError as exc:
            return _error(401, exc.code, str(exc)[:200])
        uid = int(user["telegram_user_id"])
        roles = miniapp_rbac.roles_for(uid)
        display_name = _display_name_of(user)
        token = miniapp_session.issue_session(
            uid, roles, username=user.get("username", ""),
            display_name=display_name,
        )
        await _audit_submission(
            "miniapp.session_created", user_id=uid,
            detail={"surface": "mini_app", "roles": roles},
        )
        return _ok({
            "token": token,
            "expires_in": miniapp_session.session_ttl_seconds(),
            "user": {
                "telegram_user_id": uid,
                "username": user.get("username", ""),
                "display_name": display_name,
                "roles": roles,
            },
        })

    async def my_submissions(request):
        """GET /api/v1/me/submissions — the caller's own LOGICAL submissions.

        One item per review chain (refetch generations collapse), strictly the
        verified human submitter's rows (§identity). Service/automatic
        submissions never appear here.
        """
        principal = await _resolve_principal(request)
        if principal is None:
            return _error(401, "invalid_token", "token 无效或已吊销")
        uid = principal["telegram_user_id"]
        if not uid or principal.get("kind") != "user":
            return _error(403, "permission_denied", "需要用户会话")
        try:
            limit = int(request.query.get("limit", "20"))
        except (TypeError, ValueError):
            return _error(400, "invalid_limit", "limit 必须是整数")
        limit = max(1, min(limit, 100))
        cursor = request.query.get("cursor") or None
        try:
            items, next_cursor = await _own_submissions(
                uid, limit=limit, cursor=cursor
            )
        except ReviewError as exc:
            return _review_error(exc)
        return _ok({"items": items, "next_cursor": next_cursor})

    async def my_submission_detail(request):
        """GET /api/v1/me/submissions/{review_id} — user-safe own detail.

        The submitter may read their own logical submission; other users get the
        same 404 as a missing row. Reviewer RBAC does not widen this endpoint —
        reviewers use the review API.
        """
        principal = await _resolve_principal(request)
        if principal is None:
            return _error(401, "invalid_token", "token 无效或已吊销")
        uid = principal["telegram_user_id"]
        if not uid or principal.get("kind") != "user":
            return _error(403, "permission_denied", "需要用户会话")
        try:
            review_id = int(request.match_info["review_id"])
        except (TypeError, ValueError):
            return _error(400, "invalid_review_id", "review_id 必须是整数")
        try:
            detail = await _own_submission_detail(uid, review_id)
        except ReviewError as exc:
            return _review_error(exc)
        return _ok(detail)

    async def my_submission_media(request):
        principal = await _resolve_principal(request)
        if principal is None:
            return _error(401, "invalid_token", "token 无效或已吊销")
        if principal.get("kind") != "user":
            return _error(403, "permission_denied", "需要用户会话")
        try:
            review_id = int(request.match_info["review_id"])
            index = int(request.match_info["index"])
        except (TypeError, ValueError):
            return _error(400, "invalid_media_index", "review_id/index 必须是整数")
        try:
            detail = await _own_submission_detail(principal["telegram_user_id"], review_id)
            result = await review_service.get_media(
                bot, detail["current_review_id"], index,
                request.query.get("variant", "preview"),
            )
        except ReviewError as exc:
            return _review_error(exc)
        response = web.Response(body=result.data, content_type=result.mime_type)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if result.kind in {"document", "audio"}:
            response.content_type = "application/octet-stream"
            response.headers["Content-Disposition"] = "attachment"
            response.headers["Content-Security-Policy"] = "sandbox"
        return response

    async def submission_preview(request):
        principal = await _resolve_principal(request)
        if principal is None:
            return _error(401, "invalid_token", "token 无效或已吊销")
        if principal.get("kind") != "user":
            return _error(403, "permission_denied", "需要用户会话")
        payload, error = await _json_body(request)
        if error:
            return error
        for field in ("title", "tags", "note", "link"):
            if field in payload and not isinstance(payload[field], str):
                return _error(400, "invalid_field", f"{field} 必须是文本")
        for field in ("anonymous", "spoiler"):
            if field in payload and not isinstance(payload[field], bool):
                return _error(400, "invalid_field", f"{field} 必须是布尔值")
        from utils.helper_functions import build_caption
        # Public-facing preview (Mini App). The client renders the submitter
        # line from the verified session identity, so the caption itself carries
        # no submitter fallback; media kinds decide the media action
        # (§publication-presentation: document-only never shows 点击查看).
        media_types = payload.get("media_types")
        if media_types is not None and not isinstance(media_types, list):
            return _error(400, "invalid_field", "media_types 必须是数组")
        caption = build_caption({
            **{k: payload.get(k, "") for k in ("title", "tags", "note", "link")},
            "anonymous": str(payload.get("anonymous", False)).lower(),
            "spoiler": str(payload.get("spoiler", False)).lower(),
            "user_id": principal["telegram_user_id"], "username": principal["name"],
            "media_types": media_types or [],
        }, surface="miniapp")
        return _ok({"caption": caption, "parse_mode": "HTML"})

    async def _notify_refetch_replacement(refetch_request_id: str,
                                          new_review_id) -> None:
        """审核群回执：重抓替换成功（新稿已落库，attempt 已置 replaced）。

        只对**新创建**的审核卡发一条成功消息；幂等重放（reused）不再重复通知。
        obsolete（审核人已决）或仍在处理中不发成功回执。
        """
        if not refetch_request_id or not new_review_id or not REVIEW_CHAT_ID:
            return
        try:
            from telepost.storage.sqlite.refetch import RefetchRepository
            attempt = await RefetchRepository().find_by_request_id(
                refetch_request_id
            )
            if attempt is None or attempt["state"] != "replaced":
                return
            source_id = attempt["source_review_id"]
            await application.bot.send_message(
                chat_id=REVIEW_CHAT_ID,
                text=(
                    f"✅ 审核 #{source_id} 重抓成功：新候选 #{new_review_id} "
                    "已进入审核队列，原稿件已替换。请在新卡片上审核。"
                ),
            )
        except Exception:
            logger.warning("发送重抓成功回执失败: request_id=%s",
                           refetch_request_id, exc_info=True)

    async def create_submission(request):
        principal = await _resolve_principal(request)
        if principal is None:
            return _error(401, "invalid_token", "token 无效或已吊销")
        user_id = principal["telegram_user_id"]
        username = principal["name"] or f"user{user_id}"

        # Identity/provenance split (§identity): a service principal (API
        # token) NEVER becomes a submission owner. Only a verified human
        # principal (Mini App session) sets submitter_user_id; the API-token
        # owner is the request actor, not the submitter.
        principal_kind = principal.get(
            "kind", "service" if principal.get("surface") == "api" else "user"
        )
        if principal_kind == "user":
            submitter_user_id = int(user_id) if user_id else None
            submitter_username = username or ""
            actor_subject = principal.get("actor_subject") or f"telegram:{user_id}"
        else:
            submitter_user_id = None
            submitter_username = ""
            actor_subject = principal.get("actor_subject") or (
                f"api_token:{principal.get('token_id') or 0}"
            )
        actor_kind = principal_kind

        # 限频
        used = _rate_cache.get(f"api:{user_id}") or 0
        if SUBMIT_LIMIT_PER_HOUR > 0 and used >= SUBMIT_LIMIT_PER_HOUR:
            return _error(429, "rate_limited",
                          f"每小时最多 {SUBMIT_LIMIT_PER_HOUR} 次投稿，请稍后再试")
        _rate_cache.set(f"api:{user_id}", used + 1, ttl=3600)
        await _audit_submission("submission.received", user_id=user_id,
                                actor_kind=actor_kind, actor_subject=actor_subject)

        if (request.content_type or "").startswith("application/json"):
            # file_id 直投：素材已在 Telegram 服务器（file_id 归属本 bot），零媒体传输
            try:
                payload = await request.json()
            except Exception:
                return _error(400, "invalid_json", "JSON 解析失败")
            if not isinstance(payload, dict):
                return _error(400, "invalid_json", "JSON body 必须是对象")
            if _invalid_refetch_request_id(payload):
                await _audit_submission("submission.invalid_refetch_provenance", user_id=user_id,
                                        actor_kind=actor_kind, actor_subject=actor_subject)
                return _error(400, "invalid_refetch_provenance", "refetch_request_id 必须是 UUID")

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
                    "submitter_user_id": submitter_user_id,
                    "submitter_username": submitter_username,
                    "actor_kind": actor_kind,
                    "actor_subject": actor_subject,
                }
                # JSON file_id clients always receive the four provenance
                # kwargs (empty string means "absent"); multipart omits them.
                provenance = {
                    "idempotency_key": _fields_idempotency_key(payload),
                    "target_id": _fields_target_id(payload),
                    "work_type": _fields_work_type(payload),
                    "pixiv_id": _fields_pixiv_id(payload),
                }
                if _api_review():
                    from handlers.review import queue_review_from_file_ids
                    queue_kwargs = dict(
                        source_label=_fields_source_label(payload),
                        source_ref=_fields_source_ref(payload),
                        scheduled_at=_fields_scheduled_at(payload),
                        refetch_request_id=_fields_refetch_request_id(payload),
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
            except ValueError as e:
                return _failure_ack(e)
            except Exception as e:
                action = "进入审核队列" if _api_review() else "发布到频道"
                logger.error(f"API file_id 投稿失败: {e}", exc_info=True)
                code = "review_queue_failed" if _api_review() else "publish_failed"
                return _error(502, code, f"{action}失败: {str(e)[:200]}")
            logger.info(
                "API file_id 投稿已处理: user=%s status=%s",
                user_id, result.get("status"),
            )
            await _audit_submission_outcome(
                result, user_id=user_id,
                idempotency_key=_audit_key(
                    provenance.get("idempotency_key", ""), result, user_id=user_id),
                target_id=provenance.get("target_id", ""),
                work_type=provenance.get("work_type", ""),
                pixiv_id=provenance.get("pixiv_id", ""),
                source_ref=_fields_source_ref(payload),
                actor_kind=actor_kind, actor_subject=actor_subject,
            )
            if not result.get("reused"):
                await _notify_refetch_replacement(
                    _fields_refetch_request_id(payload), result.get("review_id")
                )
            if not _api_review():
                await _maybe_notify_direct_submitter(
                    result, common.get("submitter_user_id"), bool(common.get("anonymous")),
                    "api_direct",
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

        if _invalid_refetch_request_id(fields):
            await _audit_submission("submission.invalid_refetch_provenance", user_id=user_id)
            return _error(400, "invalid_refetch_provenance", "refetch_request_id 必须是 UUID")
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
                "submitter_user_id": submitter_user_id,
                "submitter_username": submitter_username,
                "actor_kind": actor_kind,
                "actor_subject": actor_subject,
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
            if _api_review():
                from handlers.review import queue_review_from_files
                queue_kwargs = dict(
                    source_label=_fields_source_label(fields),
                    source_ref=_fields_source_ref(fields),
                    scheduled_at=_fields_scheduled_at(fields),
                    refetch_request_id=_fields_refetch_request_id(fields),
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
        await _audit_submission_outcome(
            result, user_id=user_id,
            idempotency_key=_audit_key(
                provenance.get("idempotency_key", ""), result, user_id=user_id),
            target_id=provenance.get("target_id", ""),
            work_type=provenance.get("work_type", ""),
            pixiv_id=provenance.get("pixiv_id", ""),
            source_ref=_fields_source_ref(fields),
            actor_kind=actor_kind, actor_subject=actor_subject,
        )
        if not result.get("reused"):
            await _notify_refetch_replacement(
                _fields_refetch_request_id(fields), result.get("review_id")
            )
        if not _api_review():
            await _maybe_notify_direct_submitter(
                result, common.get("submitter_user_id"), bool(common.get("anonymous")),
                "api_direct",
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
                # Owner-scope read: a verified human submitter may view (but
                # never mutate) their OWN review rows (§identity).
                owner_uid = _review_owner_principal(request)
                if owner_uid is None:
                    return error
                try:
                    review_id = int(request.match_info["review_id"])
                except (TypeError, ValueError):
                    return _error(400, "invalid_review_id", "review_id 必须是整数")
                row = await review_service.get_review(review_id)
                if int(getattr(row, "submitter_user_id", None) or 0) != owner_uid:
                    return error
                return _ok(row.to_dict())
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
                # Owner-scope media read for the review's verified submitter.
                owner_uid = _review_owner_principal(request)
                if owner_uid is None:
                    return error
                try:
                    review_id = int(request.match_info["review_id"])
                except (TypeError, ValueError):
                    return _error(400, "invalid_review_id", "review_id 必须是整数")
                row = await review_service.get_review(review_id)
                if int(getattr(row, "submitter_user_id", None) or 0) != owner_uid:
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

    # ---- admin surface (Mini App + shared application service) ------------
    async def _admin_check(request):
        """Admin-only principal gate: kind=user session with admin role."""
        principal = await _resolve_principal(request)
        if principal is None:
            return None, _error(401, "invalid_token", "token 无效或已吊销")
        if principal.get("kind") != "user":
            return None, _error(403, "permission_denied", "需要管理员会话")
        from telepost.miniapp import rbac as _rbac
        roles = _principal_roles(principal)
        if not _rbac.can_administer(roles):
            return None, _error(403, "permission_denied", "需要管理员权限")
        return principal, None

    def _admin_actor(principal) -> str:
        return f"telegram_user:{principal.get('telegram_user_id')}"

    async def admin_status(request):
        principal, error = await _admin_check(request)
        if error:
            return error
        from telepost.application import admin_ops
        try:
            snapshot = await admin_ops.status_snapshot()
        except Exception:
            logger.error("Admin status snapshot failed", exc_info=True)
            return _error(500, "internal_error", "状态读取失败")
        return _ok(snapshot)

    async def admin_policy_get(request):
        principal, error = await _admin_check(request)
        if error:
            return error
        from telepost.application import admin_ops
        return _ok(admin_ops.current_policy())

    async def admin_policy_patch(request):
        principal, error = await _admin_check(request)
        if error:
            return error
        payload, err = await _json_body(request)
        if err:
            return err
        from telepost.application.admin_ops import AdminError, update_policy
        try:
            changes = {k: v for k, v in payload.items()
                       if k in {"api_review", "chat_review", "show_submitter"}}
            result = await update_policy(
                changes, actor=_admin_actor(principal),
            )
        except AdminError as exc:
            return _error(exc.http_status, exc.code, exc.message)
        return _ok(result)

    async def admin_blacklist_list(request):
        principal, error = await _admin_check(request)
        if error:
            return error
        from telepost.application import admin_ops
        entries = await admin_ops.blacklist_entries()
        return _ok({"items": entries, "size": len(entries)})

    async def admin_blacklist_add(request):
        principal, error = await _admin_check(request)
        if error:
            return error
        payload, err = await _json_body(request)
        if err:
            return err
        from telepost.application.admin_ops import AdminError, blacklist_add
        try:
            result = await blacklist_add(
                payload.get("user_id"), payload.get("reason", ""),
                actor=_admin_actor(principal),
            )
        except AdminError as exc:
            return _error(exc.http_status, exc.code, exc.message)
        return _ok(result, status=201)

    async def admin_blacklist_remove(request):
        principal, error = await _admin_check(request)
        if error:
            return error
        from telepost.application.admin_ops import AdminError, blacklist_remove
        try:
            user_id = int(request.match_info["user_id"])
        except (TypeError, ValueError):
            return _error(400, "invalid_user_id", "user_id 必须是整数")
        try:
            result = await blacklist_remove(
                user_id, actor=_admin_actor(principal),
            )
        except AdminError as exc:
            return _error(exc.http_status, exc.code, exc.message)
        return _ok(result)

    async def _json_body(request):
        try:
            payload = await request.json()
        except Exception:
            return None, _error(400, "invalid_json", "JSON 解析失败")
        if not isinstance(payload, dict):
            return None, _error(400, "invalid_json", "JSON body 必须是对象")
        return payload, None

    def _action_source(request, actor_row) -> str:
        if actor_row.get("scope") == "mini_app":
            return "mini_app"
        if request.headers.get("X-TelePost-Source", "").lower() == "mcp":
            return "mcp"
        return "http"

    def _action_actor(actor_row):
        """Actor identity: telegram_user_id when known (Mini App / user token)."""
        uid = actor_row.get("telegram_user_id")
        if uid is not None:
            return uid
        return actor_row.get("name") or None

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
            source = _action_source(request, actor_row)
            result = await review_service.approve(
                bot,
                review_id,
                spoiler=spoiler,
                actor=_action_actor(actor_row),
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
            source = _action_source(request, actor_row)
            result = await review_service.reject(
                bot,
                review_id,
                reason=reason,
                actor=_action_actor(actor_row),
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
            source = _action_source(request, actor_row)
            result = await review_service.set_spoiler(
                review_id,
                payload["spoiler"],
                actor=_action_actor(actor_row),
                source=source,
            )
            return _ok(result.to_dict())
        return await _run_review_action(action)

    async def refetch_review_api(request):
        """POST /api/v1/reviews/{id}/refetch — Mini App refetch command.

        Reuses the SAME application command as the Bot button
        (:func:`telepost.application.refetch.request_refetch`), so the two
        surfaces share one state machine / one idempotency / one audit trail
        (§39, §41). Reviewer RBAC is enforced server-side (§14).
        """
        async def action():
            actor_row, auth_error = await _review_auth(request, write=True)
            if auth_error:
                return auth_error
            try:
                review_id = int(request.match_info["review_id"])
            except (TypeError, ValueError):
                return _error(400, "invalid_review_id", "review_id 必须是整数")
            from telepost.application.refetch import (
                RefetchAlreadyRunningError,
                RefetchError,
                RefetchNotConfiguredError,
                RefetchNotFoundError,
                RefetchStateError,
                request_refetch,
            )
            try:
                result = await request_refetch(
                    review_id,
                    actor=_action_actor(actor_row),
                    surface=actor_row.get("scope") or "api",
                )
            except RefetchNotFoundError as exc:
                return _error(404, exc.code, str(exc))
            except RefetchStateError as exc:
                return _error(409, exc.code, str(exc))
            except RefetchAlreadyRunningError as exc:
                return _ok({"state": "running", "request_id": None,
                            "message": str(exc)}, status=202)
            except RefetchNotConfiguredError as exc:
                return _error(503, exc.code, str(exc))
            except RefetchError as exc:
                return _error(409, exc.code, str(exc))
            return _ok(result, status=202)
        return await _run_review_action(action)

    async def refetch_review_state(request):
        """GET /api/v1/reviews/{id}/refetch — attempt + lineage (read-only)."""
        async def action():
            actor_row, auth_error = await _review_auth(request, write=False)
            if auth_error:
                return auth_error
            try:
                review_id = int(request.match_info["review_id"])
            except (TypeError, ValueError):
                return _error(400, "invalid_review_id", "review_id 必须是整数")
            from telepost.application.refetch import (
                RefetchNotFoundError,
                get_refetch_state,
            )
            try:
                return _ok(await get_refetch_state(review_id))
            except RefetchNotFoundError as exc:
                return _error(404, exc.code, str(exc))
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

    async def refetch_outcome(request):
        """PixivFlow reports a TERMINAL refetch verdict back to the review.

        Body (machine-readable, JSON):
          request_id  — the attempt's request UUID (PixivFlow slot identity);
          disposition — 'no_alternative' | 'failed';
          reason / scanned / skipped{duplicate,invalid,unavailable} — optional
          diagnostics.

        Authentication is the SAME service token as submissions (the per-bot
        SUBMIT_TOKEN PixivFlow already holds); no new credential is introduced.
        Idempotent: an already-terminal attempt answers 200 without re-notifying.
        Stale results (source review no longer pending) become 'obsolete' and
        never overwrite a reviewer decision.
        """
        token_row = await authenticate(_bearer(request) or "")
        if token_row is None:
            return _error(401, "invalid_token", "token 无效或已吊销")
        try:
            payload = await request.json()
        except Exception:
            return _error(400, "invalid_json", "JSON body 必须是对象")
        if not isinstance(payload, dict):
            return _error(400, "invalid_json", "JSON body 必须是对象")
        request_id = str(payload.get("request_id") or "").strip()[:240]
        disposition = str(payload.get("disposition") or "").strip()
        if not request_id:
            return _error(400, "missing_request_id", "request_id 必填")
        if disposition not in ("no_alternative", "failed"):
            return _error(400, "invalid_disposition",
                          "disposition 必须是 no_alternative 或 failed")
        skipped = payload.get("skipped") if isinstance(payload.get("skipped"), dict) else {}
        try:
            scanned = int(payload.get("scanned") or 0)
        except (TypeError, ValueError):
            scanned = 0
        try:
            dup = int(skipped.get("duplicate") or 0)
            inv = int(skipped.get("invalid") or 0)
            una = int(skipped.get("unavailable") or 0)
        except (TypeError, ValueError):
            dup = inv = una = 0

        from telepost.storage.sqlite.refetch import RefetchRepository
        repo = RefetchRepository()
        attempt = await repo.find_by_request_id(request_id)
        _, applied, changed = await repo.apply_outcome(
            request_id, disposition,
            reason=str(payload.get("reason") or "")[:400],
            scanned=scanned, skipped_duplicate=dup,
            skipped_invalid=inv, skipped_unavailable=una,
        )
        if applied == "not_found":
            return _error(404, "unknown_attempt", "attempt 不存在")
        if applied == "obsolete":
            # Source review was decided while the refetch was running: the
            # verdict is recorded as obsolete, and we never disturb the review.
            # The attempt IS terminal — the review group must see that instead
            # of an endless "仍在处理中" (§refetch-terminal-notify).
            if changed:
                review_id = attempt["source_review_id"] if attempt else None
                try:
                    await application.bot.send_message(
                        chat_id=REVIEW_CHAT_ID,
                        text=(
                            f"🔄 审核 #{review_id} 的重抓已取消：该审核在重抓期间已被"
                            "处理（驳回/通过），不会产生替换稿，当前稿件保持不变。"
                        ),
                    )
                except Exception as exc:
                    logger.warning("发送重抓取消通知失败: review_id=%s error=%s",
                                   review_id, exc)
            return _ok({"ok": True, "attempt_state": applied, "notified": bool(changed)})
        if not changed:
            # Already-terminal replay (same verdict redelivered): converge with
            # the same state, never re-notify the review group.
            return _ok({"ok": True, "attempt_state": applied, "replayed": True})

        review_id = attempt["source_review_id"] if attempt else None
        try:
            if applied == "no_alternative":
                text = (
                    f"📭 审核 #{review_id} 没有找到新的可替换作品，当前稿件保持不变。\n"
                    "稍后有新候选时可以再次重抓。"
                )
            else:  # failed
                text = f"⚠️ 审核 #{review_id} 重抓失败，当前稿件未变，请稍后重试。"
            await application.bot.send_message(
                chat_id=REVIEW_CHAT_ID, text=text
            )
        except Exception:
            logger.warning("发送重抓终态通知失败: request_id=%s",
                           request_id, exc_info=True)
        try:
            from telepost.observability import audit
            await audit.record_event(
                "review.refetch_" + applied,
                review_id=review_id,
                execution_id=request_id,
                actor="pixivflow_service",
                error_class=None if applied == "no_alternative" else "remote_outcome",
                detail={
                    "request_id": request_id,
                    "disposition": applied,
                    "reason": str(payload.get("reason") or "")[:400],
                    "scanned": scanned,
                    "skipped": {"duplicate": dup, "invalid": inv, "unavailable": una},
                },
            )
        except Exception:
            logger.debug("记录重抓终态审计失败: request_id=%s", request_id, exc_info=True)
        return _ok({"ok": True, "attempt_state": applied})

    web_app.router.add_post("/api/v1/miniapp/session", miniapp_session)
    web_app.router.add_get("/api/v1/me/submissions", my_submissions)
    web_app.router.add_get(
        "/api/v1/me/submissions/{review_id}", my_submission_detail
    )

    web_app.router.add_get("/api/v1/me/submissions/{review_id}/media/{index}", my_submission_media)
    web_app.router.add_post("/api/v1/submissions/preview", submission_preview)
    web_app.router.add_get("/api/v1/reviews/policy", review_policy)
    web_app.router.add_get("/api/v1/reviews", list_reviews)
    web_app.router.add_get("/api/v1/reviews/{review_id}", get_review)
    web_app.router.add_get(
        "/api/v1/reviews/{review_id}/media/{index}", get_review_media
    )
    web_app.router.add_post("/api/v1/reviews/{review_id}/approve", approve_review)
    web_app.router.add_post("/api/v1/reviews/{review_id}/reject", reject_review)
    web_app.router.add_patch("/api/v1/reviews/{review_id}/spoiler", set_review_spoiler)
    web_app.router.add_post("/api/v1/reviews/{review_id}/refetch", refetch_review_api)
    web_app.router.add_get("/api/v1/reviews/{review_id}/refetch", refetch_review_state)
    web_app.router.add_get("/api/v1/health", health)
    web_app.router.add_get("/api/v1/admin/status", admin_status)
    web_app.router.add_get("/api/v1/admin/policy", admin_policy_get)
    web_app.router.add_patch("/api/v1/admin/policy", admin_policy_patch)
    web_app.router.add_get("/api/v1/admin/blacklist", admin_blacklist_list)
    web_app.router.add_post("/api/v1/admin/blacklist", admin_blacklist_add)
    web_app.router.add_delete(
        "/api/v1/admin/blacklist/{user_id}", admin_blacklist_remove
    )
    web_app.router.add_get("/api/v1/me", me)
    web_app.router.add_post("/api/v1/submissions", create_submission)
    web_app.router.add_get("/api/v1/deliveries/lookup", delivery_lookup)
    web_app.router.add_post("/api/v1/notifications", create_notification)
    async def schedule_outcome(request):
        """PixivFlow reports a TERMINAL schedule occurrence verdict.

        Every occurrence (success / partial / failed) must reach the review or
        admin group: an operator should never have to notice a missing post to
        learn that a run degraded. Durable + idempotent per slot_id: a replayed
        delivery or a duplicate external trigger never sends a second summary.
        """
        token_row = await authenticate(_bearer(request) or "")
        if token_row is None:
            return _error(401, "invalid_token", "token 无效或已吊销")
        try:
            payload = await request.json()
        except Exception:
            return _error(400, "invalid_json", "JSON 解析失败")
        if not isinstance(payload, dict):
            return _error(400, "invalid_json", "JSON body 必须是对象")

        schedule_id = str(payload.get("schedule_id") or "").strip()[:80]
        slot_id = str(payload.get("slot_id") or "").strip()[:200]
        if not schedule_id or not slot_id:
            return _error(400, "missing_slot", "schedule_id/slot_id 必填")
        status = str(payload.get("status") or "").strip()
        if status not in ("success", "partial", "failed"):
            return _error(400, "invalid_status",
                          "status 必须是 success/partial/failed")
        targets = payload.get("targets")
        if targets is None:
            targets = []
        if not isinstance(targets, list):
            return _error(400, "invalid_targets", "targets 必须是数组")
        targets = [t for t in targets if isinstance(t, dict)][:20]

        from database import db_manager as _db_manager
        key = f"schedule-outcome:{slot_id}"
        # Keep legacy receipts, but new delivery uses the shared durable claim.
        async with _db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT 1 FROM schedule_outcome_notifications WHERE slot_id=?",
                (slot_id,),
            )
            if await cur.fetchone() is not None:
                return _ok({"ok": True, "replayed": True, "delivered": True,
                            "status": status})
        if not REVIEW_CHAT_ID:
            return _error(503, "review_chat_not_configured", "未配置审核群，请重试")
        # Reserved service namespace: token rotation must not change slot identity.
        if not await claim_api_notification(0, key):
            async with _db_manager.get_db() as conn:
                cur = await conn.execute(
                    "SELECT status FROM api_notifications "
                    "WHERE telegram_user_id=0 AND idempotency_key=?", (key,),
                )
                receipt = await cur.fetchone()
            if receipt is not None and receipt["status"] == "sent":
                return _ok({"ok": True, "replayed": True, "delivered": True,
                            "status": status})
            return _error(503, "notification_pending", "通知处理中，请重试")
        text = build_schedule_outcome_text(schedule_id, status, targets)
        try:
            message = await application.bot.send_message(
                chat_id=REVIEW_CHAT_ID, text=text, parse_mode=None,
            )
        except Exception:
            await release_api_notification(0, key)
            logger.warning("发送 schedule 终态通知失败: slot=%s", slot_id)
            return _error(502, "notification_failed", "审核群通知失败，请重试")
        await mark_api_notification_sent(0, key, message.message_id)
        delivered = True
        try:
            from telepost.observability import audit as audit_mod
            await audit_mod.record_event(
                "schedule.outcome_notified",
                actor="service:schedule",
                execution_id=slot_id,
                detail={"schedule_id": schedule_id, "status": status,
                        "delivered": delivered,
                        "targets": [{"target_id": str(t.get("target_id") or "")[:64],
                                     "work_type": str(t.get("work_type") or "")[:32],
                                     "status": str(t.get("status") or "")[:32]}
                                    for t in targets]},
            )
        except Exception:
            logger.debug("审计 schedule.outcome_notified 失败", exc_info=True)
        return _ok({"ok": True, "delivered": delivered, "status": status})


    # ---- Editorial Revision API (§editorial) --------------------------------
    async def editorial_list(request):
        async def action():
            _actor_row, auth_error = await _review_auth(request, write=False)
            if auth_error:
                return auth_error
            try:
                review_id = int(request.match_info["review_id"])
            except (TypeError, ValueError):
                return _error(400, "invalid_review_id", "review_id 必须是整数")
            from telepost.application.editorial import EditorialService
            revisions = await EditorialService().list_for_review(review_id)
            return _ok({"revisions": [editorial_dto(r) for r in revisions]})
        return await _run_review_action(action)

    async def editorial_create(request):
        async def action():
            actor_row, auth_error = await _review_auth(request, write=True)
            if auth_error:
                return auth_error
            try:
                review_id = int(request.match_info["review_id"])
            except (TypeError, ValueError):
                return _error(400, "invalid_review_id", "review_id 必须是整数")
            from telepost.application.editorial import EditorialService
            revision = await EditorialService().create(
                review_id, actor=_editor_actor(actor_row)
            )
            from telepost.observability import audit
            await audit.record_event(
                "editorial_revision.created",
                review_id=review_id, actor=_action_actor(actor_row),
                detail={"revision_id": revision["id"],
                        "revision_number": revision["revision_number"]},
            )
            return _ok(editorial_dto(revision), status=201)
        return await _run_review_action(action)

    async def editorial_get(request):
        async def action():
            _actor_row, auth_error = await _review_auth(request, write=False)
            if auth_error:
                return auth_error
            try:
                review_id = int(request.match_info["review_id"])
                revision_id = int(request.match_info["revision_id"])
            except (TypeError, ValueError):
                return _error(400, "invalid_revision_id", "revision_id 必须是整数")
            from telepost.application.editorial import EditorialService
            revision = await EditorialService().get(review_id, revision_id)
            return _ok(editorial_dto(revision))
        return await _run_review_action(action)

    async def editorial_update(request):
        async def action():
            actor_row, auth_error = await _review_auth(request, write=True)
            if auth_error:
                return auth_error
            try:
                review_id = int(request.match_info["review_id"])
                revision_id = int(request.match_info["revision_id"])
            except (TypeError, ValueError):
                return _error(400, "invalid_revision_id", "revision_id 必须是整数")
            payload, body_error = await _json_body(request)
            if body_error:
                return body_error
            expected_version = payload.get("expected_version")
            if not isinstance(expected_version, int) or expected_version < 1:
                return _error(400, "invalid_version", "expected_version 必填")
            from telepost.application.editorial import EditorialService
            try:
                updated = await EditorialService().update(
                    review_id, revision_id, payload=payload,
                    expected_version=expected_version,
                    actor=_editor_actor(actor_row),
                )
            except Exception as exc:
                return _editorial_error(exc)
            from telepost.observability import audit
            await audit.record_event(
                "editorial_revision.updated",
                review_id=review_id, actor=_action_actor(actor_row),
                detail={"revision_id": revision_id,
                        "version": updated["version"]},
            )
            return _ok(editorial_dto(updated))
        return await _run_review_action(action)

    async def editorial_finalize(request):
        async def action():
            actor_row, auth_error = await _review_auth(request, write=True)
            if auth_error:
                return auth_error
            try:
                review_id = int(request.match_info["review_id"])
                revision_id = int(request.match_info["revision_id"])
            except (TypeError, ValueError):
                return _error(400, "invalid_revision_id", "revision_id 必须是整数")
            payload, body_error = await _json_body(request)
            if body_error:
                return body_error
            expected_version = payload.get("expected_version")
            if not isinstance(expected_version, int) or expected_version < 1:
                return _error(400, "invalid_version", "expected_version 必填")
            from telepost.application.editorial import EditorialService
            try:
                finalized = await EditorialService().finalize(
                    review_id, revision_id, expected_version=expected_version)
            except Exception as exc:
                return _editorial_error(exc)
            from telepost.observability import audit
            await audit.record_event(
                "editorial_revision.finalized",
                review_id=review_id, actor=_action_actor(actor_row),
                detail={"revision_id": revision_id,
                        "revision_number": finalized["revision_number"]},
            )
            return _ok(editorial_dto(finalized))
        return await _run_review_action(action)

    async def editorial_preview(request):
        async def action():
            _actor_row, auth_error = await _review_auth(request, write=False)
            if auth_error:
                return auth_error
            try:
                review_id = int(request.match_info["review_id"])
                revision_id = int(request.match_info["revision_id"])
            except (TypeError, ValueError):
                return _error(400, "invalid_revision_id", "revision_id 必须是整数")
            payload, body_error = await _json_body(request)
            if body_error:
                return body_error
            from telepost.application.editorial import EditorialService
            try:
                caption = await EditorialService().preview(
                    review_id, revision_id,
                    payload=payload if payload else None,
                )
            except Exception as exc:
                return _editorial_error(exc)
            return _ok({"caption": caption, "parse_mode": "HTML"})
        return await _run_review_action(action)

    async def publish_with_source(request):
        """POST /api/v1/reviews/{id}/publish — body {revision_id?}.

        None  → approve ORIGINAL;
        id    → publish the FINALIZED editorial revision (edited snapshot).
        Same review FSM + one idempotency ledger; the source revision is
        recorded on the review row (NULL = original, §33).
        """
        async def action():
            actor_row, auth_error = await _review_auth(request, write=True)
            if auth_error:
                return auth_error
            payload, body_error = await _json_body(request)
            if body_error:
                return body_error
            try:
                review_id = int(request.match_info["review_id"])
            except (TypeError, ValueError):
                return _error(400, "invalid_review_id", "review_id 必须是整数")
            revision_id = payload.get("revision_id")
            if revision_id is not None and not isinstance(revision_id, int):
                return _error(400, "invalid_revision_id", "revision_id 必须是整数")
            spoiler = payload.get("spoiler")
            if spoiler is not None and not isinstance(spoiler, bool):
                return _error(400, "invalid_spoiler", "spoiler 必须是布尔值")
            source = _action_source(request, actor_row)
            try:
                if revision_id is None:
                    result = await review_service.approve(
                        bot, review_id, spoiler=spoiler,
                        actor=_action_actor(actor_row), source=source,
                        notify_chat_submitter=True,
                    )
                else:
                    result = await review_service.publish_edited(
                        bot, review_id, int(revision_id),
                        actor=_action_actor(actor_row), source=source,
                        notify_chat_submitter=True,
                    )
            except ReviewError as exc:
                return _review_error(exc)
            except Exception as exc:
                return _editorial_error(exc)
            return _ok(result.to_dict())
        return await _run_review_action(action)

    async def editorial_history_me(request):
        """GET /api/v1/me/submissions/{id}/editorial-history — OWNER only.

        Returns published revisions with submitter-safe DTO: no editor ids,
        no internal moderation fields (§49-§50). Editor identity is rendered
        as a label ('频道管理员' unless SHOW_EDITOR_USERNAME is configured).
        """
        principal = await _resolve_principal(request)
        if principal is None:
            return _error(401, "invalid_token", "token 无效或已吊销")
        if principal.get("kind") != "user":
            return _error(403, "permission_denied", "需要用户会话")
        uid = int(principal["telegram_user_id"])
        try:
            review_id = int(request.match_info["review_id"])
        except (TypeError, ValueError):
            return _error(400, "invalid_review_id", "review_id 必须是整数")
        from telepost.storage.sqlite.reviews import ReviewRepository
        row = await ReviewRepository().get(review_id)
        if row is None:
            return _error(404, "review_not_found", "review not found")
        row = dict(row) if not isinstance(row, dict) else row
        if int(row.get("submitter_user_id") or 0) != uid:
            # Never reveal that someone else's submission exists.
            return _error(404, "review_not_found", "review not found")
        chain_id = row["review_chain_id"] or f"review-{review_id}"
        from telepost.storage.sqlite.editorial import EditorialRepository
        revisions = await EditorialRepository().list_published_for_chain(chain_id)
        return _ok({
            "review_id": review_id,
            "status": row["status"],
            "edited_before_publication": bool(
                row.get("published_source_revision_id")),
            "published_message_id": row.get("published_message_id"),
            "revisions": [editorial_submitter_dto(r) for r in revisions],
        })

    web_app.router.add_get(
        "/api/v1/reviews/{review_id}/editorial-revisions", editorial_list)
    web_app.router.add_post(
        "/api/v1/reviews/{review_id}/editorial-revisions", editorial_create)
    web_app.router.add_get(
        "/api/v1/reviews/{review_id}/editorial-revisions/{revision_id}",
        editorial_get)
    web_app.router.add_patch(
        "/api/v1/reviews/{review_id}/editorial-revisions/{revision_id}",
        editorial_update)
    web_app.router.add_post(
        "/api/v1/reviews/{review_id}/editorial-revisions/{revision_id}/finalize",
        editorial_finalize)
    web_app.router.add_post(
        "/api/v1/reviews/{review_id}/editorial-revisions/{revision_id}/preview",
        editorial_preview)
    web_app.router.add_post(
        "/api/v1/reviews/{review_id}/publish", publish_with_source)
    web_app.router.add_get(
        "/api/v1/me/submissions/{review_id}/editorial-history",
        editorial_history_me)

    web_app.router.add_post("/api/v1/schedule/outcomes", schedule_outcome)
    web_app.router.add_post("/api/v1/refetch/outcomes", refetch_outcome)
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
