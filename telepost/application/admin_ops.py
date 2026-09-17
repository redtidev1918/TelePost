"""Admin application service — ONE implementation for Bot and Mini App.

Both surfaces are presentation adapters:

    Telegram /botconfig + blacklist commands ─┐
                                              ├─→ telepost.application.admin_ops
    Mini App /api/v1/admin/* ─────────────────┘

Nothing here talks to Telegram, reads secrets, or serializes credentials. The
service owns validation, durable runtime policy writes, audit and the managed
reload signal so a Bot command and a Mini App tap cannot diverge.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Any, Dict, List, Optional

from config.settings import (
    CHANNEL_ID,
    CHAT_REVIEW_REQUIRED,
    DB_PATH,
    MINIAPP_REVIEW_REQUIRED,
    REVIEW_CHAT_ID,
    SHOW_SUBMITTER,
)
from database.db_manager import get_db
from telepost.observability import audit
from utils import blacklist as blacklist_store
from utils.runtime_policy import (
    clear_runtime_policy,
    load_runtime_policy,
    update_runtime_policy,
    validate_runtime_policy,
)

logger = logging.getLogger(__name__)

# Only product-exposed toggles are mutable from the Mini App. Chat targets stay
# a Bot-side operation because they need Telegram membership verification.
TOGGLE_KEYS = {
    "api_review": "API_REVIEW_REQUIRED",
    "miniapp_review": "MINIAPP_REVIEW_REQUIRED",
    "chat_review": "CHAT_REVIEW_REQUIRED",
    "show_submitter": "SHOW_SUBMITTER",
}


class AdminError(Exception):
    """Validation/state error with a stable code for the API layer."""

    def __init__(self, message: str, *, code: str = "invalid_state",
                 http_status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status


def _policy_path() -> str:
    return os.path.join(os.path.dirname(DB_PATH) or ".", "runtime-policy.json")


async def _pending_count() -> int:
    async with get_db() as conn:
        row = await (await conn.execute(
            "SELECT COUNT(*) FROM pending_reviews WHERE status='pending'"
        )).fetchone()
    return int(row[0]) if row else 0


async def _require_empty_review_queue(what: str) -> None:
    count = await _pending_count()
    if count:
        raise AdminError(
            f"仍有 {count} 条待审核投稿，请先批准或拒绝后再{what}",
            code="review_queue_busy", http_status=409,
        )


def current_policy() -> Dict[str, Any]:
    """Effective policy (deployment defaults + durable runtime overrides)."""
    overrides = load_runtime_policy(_policy_path())
    return {
        "api_review_required": True,
        "miniapp_review_required": MINIAPP_REVIEW_REQUIRED,
        "chat_review_required": CHAT_REVIEW_REQUIRED,
        "show_submitter": SHOW_SUBMITTER,
        "overrides": sorted(overrides.keys()),
        "review_chat_configured": bool(REVIEW_CHAT_ID),
        "channel_configured": bool(CHANNEL_ID),
    }


async def update_policy(changes: Dict[str, str], *, actor: str,
                        restart: bool = True) -> Dict[str, Any]:
    """Apply toggle changes to the durable runtime policy + audit them.

    ``changes`` maps product names (``api_review`` …) to ``"on"``/``"off"``.
    """
    if not isinstance(changes, dict) or not changes:
        raise AdminError("没有需要修改的配置", code="empty_changes")
    unknown = set(changes) - set(TOGGLE_KEYS)
    if unknown:
        raise AdminError(f"不支持的配置项: {sorted(unknown)}", code="unknown_policy_key")

    rendered: Dict[str, str] = {}
    for name, value in changes.items():
        text = str(value).strip().lower()
        if text not in {"on", "off", "true", "false", "1", "0"}:
            raise AdminError(f"{name} 只能是 on/off", code="invalid_policy_value")
        rendered[TOGGLE_KEYS[name]] = "true" if text in {"on", "true", "1"} else "false"

    # Enabling a review mode requires a configured review chat.
    effective_chat = REVIEW_CHAT_ID
    if not effective_chat and any(
        rendered.get(key) == "true"
        for key in ("API_REVIEW_REQUIRED", "MINIAPP_REVIEW_REQUIRED", "CHAT_REVIEW_REQUIRED")
    ):
        raise AdminError("请先在 Bot 侧设置审核群", code="review_chat_required",
                         http_status=409)

    validate_runtime_policy(
        {**{k: v for k, v in load_runtime_policy(_policy_path()).items()},
         **rendered}
    )
    update_runtime_policy(_policy_path(), rendered)
    try:
        await audit.record_event(
            "admin.policy_changed",
            actor=actor,
            detail={"changes": rendered},
        )
    except Exception:
        logger.debug("审计 admin.policy_changed 失败", exc_info=True)

    restarted = _schedule_managed_restart() if restart else False
    return {
        "applied": rendered,
        "policy": current_policy(),
        "reload_scheduled": restarted,
    }


async def clear_policy(*, actor: str, restart: bool = True) -> Dict[str, Any]:
    """Drop the durable overrides and fall back to the deployment config."""
    clear_runtime_policy(_policy_path())
    try:
        await audit.record_event("admin.policy_reset", actor=actor, detail={})
    except Exception:
        logger.debug("审计 admin.policy_reset 失败", exc_info=True)
    return {"policy": current_policy(),
            "reload_scheduled": _schedule_managed_restart() if restart else False}


def _schedule_managed_restart() -> bool:
    """Ask the supervisor for a single-child reload (same as /botconfig)."""
    if os.getenv("TELEPOST_MANAGED_RESTART", "").lower() not in {"1", "true", "yes"}:
        return False
    try:
        asyncio.get_running_loop().call_later(1.0, os.kill, os.getpid(), signal.SIGTERM)
        return True
    except RuntimeError:
        logger.debug("无事件循环，跳过重载调度", exc_info=True)
        return False


# ---- moderation (blacklist) ------------------------------------------------

async def blacklist_entries() -> List[Dict[str, Any]]:
    rows = await blacklist_store.get_blacklist()
    result = []
    for row in rows:
        if isinstance(row, dict):
            entry = dict(row)
        else:  # sqlite3.Row / tuple from older callers
            keys = row.keys() if hasattr(row, "keys") else ()
            entry = {k: row[k] for k in keys} if keys else {"user_id": row[0]}
        entry.pop("added_by", None)
        result.append(entry)
    return result


async def blacklist_add(user_id: int, reason: str, *, actor: str) -> Dict[str, Any]:
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        raise AdminError("user_id 必须是整数", code="invalid_user_id")
    if uid <= 0:
        raise AdminError("user_id 必须是正整数", code="invalid_user_id")
    text = (reason or "未指定原因").strip()[:120]
    added = await blacklist_store.add_to_blacklist(uid, text)
    try:
        await audit.record_event(
            "admin.blacklist_added", actor=actor,
            detail={"user_id": uid, "reason": text, "changed": bool(added)},
        )
    except Exception:
        logger.debug("审计 admin.blacklist_added 失败", exc_info=True)
    return {"user_id": uid, "reason": text, "added": bool(added)}


async def blacklist_remove(user_id: int, *, actor: str) -> Dict[str, Any]:
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        raise AdminError("user_id 必须是整数", code="invalid_user_id")
    removed = await blacklist_store.remove_from_blacklist(uid)
    try:
        await audit.record_event(
            "admin.blacklist_removed", actor=actor,
            detail={"user_id": uid, "removed": bool(removed)},
        )
    except Exception:
        logger.debug("审计 admin.blacklist_removed 失败", exc_info=True)
    return {"user_id": uid, "removed": bool(removed)}


# ---- status dashboard -----------------------------------------------------

async def status_snapshot() -> Dict[str, Any]:
    """Read-only operational snapshot. Never contains credentials."""
    async with get_db() as conn:
        counts = {}
        for status, key in (
            ("pending", "pending"),
            ("preparing", "staging"),
            ("failed", "failed"),
            ("superseded", "superseded"),
            ("published", "published"),
            ("rejected", "rejected"),
        ):
            row = await (await conn.execute(
                "SELECT COUNT(*) FROM pending_reviews WHERE status=?", (status,)
            )).fetchone()
            counts[key] = int(row[0]) if row else 0
        row = await (await conn.execute(
            "SELECT COUNT(*) FROM refetch_attempts "
            "WHERE state IN ('requested','admitted')"
        )).fetchone()
        active_refetch = int(row[0]) if row else 0
        cur = await conn.execute(
            "SELECT id, source_review_id, state, failure_code, finished_at "
            "FROM refetch_attempts WHERE state='failed' "
            "ORDER BY finished_at DESC LIMIT 5"
        )
        recent_failures = [dict(r) for r in await cur.fetchall()]
        cur = await conn.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE event LIKE 'submission.%' AND ts > strftime('%s','now') - 86400"
        )
        row = await cur.fetchone()
        submissions_24h = int(row[0]) if row else 0

    version = {}
    try:
        import _release_version as rel  # type: ignore
        version = {"version": getattr(rel, "RELEASE_VERSION", ""),
                   "commit": getattr(rel, "RELEASE_COMMIT", "")}
    except Exception:
        version = {}

    entries = await blacklist_entries()
    return {
        "service": "telepost",
        "bot_index": os.getenv("TELEPOST_BOT_INDEX", "1"),
        "version": version,
        "queue": counts,
        "refetch": {"active": active_refetch, "recent_failures": recent_failures},
        "submissions_24h": submissions_24h,
        "blacklist_size": len(entries),
        "policy": current_policy(),
        "restart_managed": os.getenv("TELEPOST_MANAGED_RESTART", "").lower()
        in {"1", "true", "yes"},
    }