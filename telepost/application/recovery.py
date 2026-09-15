"""Manual recovery of a failed schedule target (§manual-recovery).

One business command, one presentation surface (the review-group inline buttons
on a failed schedule outcome). Admitting a recovery:

* is IDEMPOTENT per callback key (a webhook re-delivery of the same button
  click converges onto ONE attempt);
* enforces exactly ONE active recovery per target (data-layer partial UNIQUE);
* only ever re-runs the FAILED target(s) — success is decided by the caller's
  buttons, never by guessing;
* selects a SERVER-DEFINED policy preset (`normal` | `relaxed`); the operator
  never supplies acquisition parameters;
* goes through the SAME resource admission as every other Pixiv-consuming work
  item (PixivFlow queues the run when the account is busy; it never fails it);
* reports its outcome through the ordinary schedule-outcome channel, so the
  automatic run's history is never rewritten.

This module never touches PTB or aiohttp (mirrors ``telepost.application.refetch``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from database import db_manager

logger = logging.getLogger(__name__)

RECOVERY_TIMEOUT_SECONDS = 120

NORMAL = "normal"
RELAXED = "relaxed"

#: User-facing preview of what a preset does — never exposes raw parameters.
RELAXED_EXPLANATION = "系统会扩大搜索范围并适当放宽筛选条件，仅影响本次失败任务，不会改变之后的定时任务。"


class RecoveryError(Exception):
    """Base class for recovery admission failures (user-safe message)."""

    code = "recovery_error"

    def __init__(self, message: str, *, code: str = None):
        super().__init__(message)
        if code is not None:
            self.code = code


class RecoveryNotConfiguredError(RecoveryError):
    code = "recovery_not_configured"


class RecoveryAlreadyRunningError(RecoveryError):
    code = "recovery_already_running"


async def _record_recovery_event(event: str, *, target_id: str, request_id: str,
                                retry_mode: str, **fields) -> None:
    from telepost.observability import audit
    try:
        await audit.record_event(
            event,
            actor=None,
            execution_id=request_id or None,
            detail={"target_id": target_id, "request_id": request_id,
                    "retry_mode": retry_mode, **fields},
        )
    except Exception:
        logger.debug("记录手动恢复审计事件失败: %s", event, exc_info=True)


def _normalize_mode(retry_mode: str) -> str:
    mode = str(retry_mode or NORMAL).strip().lower()
    return mode if mode in (NORMAL, RELAXED) else NORMAL


async def request_target_recovery(
    target_id: str,
    *,
    retry_mode: str = NORMAL,
    callback_key: str = "",
    schedule_id: str = "",
) -> dict:
    """Admit one manual recovery attempt for a failed target (idempotent).

    Returns::

        {"state", "request_id", "replayed", "already_running",
         "retry_mode", "target_id", "schedule_id"}

    Raises RecoveryError subclasses mapped to user-safe answers by the handler.
    """
    target_id = str(target_id or "").strip()
    if not target_id:
        raise RecoveryError("缺少目标，无法恢复")
    mode = _normalize_mode(retry_mode)
    if not os.getenv("PIXIVFLOW_REFETCH_BASE_URL") or not os.getenv("PIXIVFLOW_REFETCH_TOKEN"):
        raise RecoveryNotConfiguredError("PixivFlow 重抓服务未配置")

    # Stable per-action identity: a transport retry with the SAME callback key
    # converges onto ONE attempt; a new intentional click starts a new one.
    key = callback_key or f"api-recovery:{target_id}:{uuid.uuid4().hex}"

    async with db_manager.get_db() as conn:
        from telepost.storage.sqlite.recovery import RecoveryRepository
        repo = RecoveryRepository(conn)
        existing = await repo.find_by_callback_key(key)
        if existing is not None:
            try:
                await _record_recovery_event(
                    "schedule.recovery_replayed", target_id=target_id,
                    request_id=existing["request_id"], retry_mode=mode,
                    schedule_id=existing["schedule_id"] or schedule_id,
                )
            except Exception:
                pass
            return {
                "state": existing["state"],
                "request_id": existing["request_id"],
                "replayed": True,
                "already_running": existing["state"] in ("requested", "accepted"),
                "retry_mode": existing["retry_mode"] or mode,
                "target_id": target_id,
                "schedule_id": existing["schedule_id"] or schedule_id,
            }

        attempt, refused = await repo.create_attempt(
            target_id=target_id,
            retry_mode=mode,
            callback_key=key,
            schedule_id=schedule_id,
        )
        if refused == "already_running":
            try:
                await _record_recovery_event(
                    "schedule.recovery_already_running", target_id=target_id,
                    request_id="", retry_mode=mode, schedule_id=schedule_id,
                )
            except Exception:
                pass
            raise RecoveryAlreadyRunningError("该目标正在恢复中，请稍候")

    request_id = attempt["request_id"]
    try:
        await _record_recovery_event(
            "schedule.recovery_requested", target_id=target_id,
            request_id=request_id, retry_mode=mode, schedule_id=schedule_id,
        )
    except Exception:
        pass

    # Remote submission is async: the endpoint answers once the attempt is
    # DURABLE; PixivFlow queues/runs under its own resource admission.
    try:
        result = await asyncio.to_thread(
            _submit_pixivflow_recover, target_id, request_id, mode
        )
        async with db_manager.get_db() as conn:
            from telepost.storage.sqlite.recovery import RecoveryRepository
            repo = RecoveryRepository(conn)
            await repo.mark_accepted(request_id, result.get("slotId", ""))
        try:
            await _record_recovery_event(
                "schedule.recovery_remote_accepted", target_id=target_id,
                request_id=request_id, retry_mode=mode, schedule_id=schedule_id,
                detail_slot=result.get("slotId"),
            )
        except Exception:
            pass
        return {
            "state": "accepted",
            "request_id": request_id,
            "replayed": False,
            "already_running": False,
            "retry_mode": mode,
            "target_id": target_id,
            "schedule_id": schedule_id,
        }
    except Exception as exc:
        code = _classify_recovery_error(exc)
        async with db_manager.get_db() as conn:
            from telepost.storage.sqlite.recovery import RecoveryRepository
            repo = RecoveryRepository(conn)
            await repo.mark_failed(request_id, code)
        try:
            await _record_recovery_event(
                "schedule.recovery_failed", target_id=target_id,
                request_id=request_id, retry_mode=mode, schedule_id=schedule_id,
                error_class=code,
            )
        except Exception:
            pass
        raise RecoveryError("恢复请求提交失败，请稍后重试", code=code) from exc


def _submit_pixivflow_recover(target_id: str, request_id: str,
                              retry_mode: str) -> dict:
    """POST the durable recovery request to PixivFlow (stdlib only)."""
    base = os.environ["PIXIVFLOW_REFETCH_BASE_URL"].rstrip("/")
    token = os.environ["PIXIVFLOW_REFETCH_TOKEN"]
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("无效的 PixivFlow 重抓地址")
    url = f"{base}/internal/targets/{quote(str(target_id), safe='')}/recover"
    body = {"requestId": request_id, "retryMode": retry_mode}
    request = Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=RECOVERY_TIMEOUT_SECONDS) as response:
            result = json.load(response)
            if response.status != 202 or result.get("status") != "accepted":
                raise RuntimeError(f"PixivFlow 拒绝恢复（HTTP {response.status}）")
            return result
    except HTTPError as error:
        raise RuntimeError(f"PixivFlow 拒绝恢复（HTTP {error.code}）") from error


def _classify_recovery_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "http 401" in lowered or "http 403" in lowered:
        return "unauthorized"
    if "http 404" in lowered:
        return "not_found"
    if "http 5" in lowered:
        return "remote_error"
    if "timeout" in lowered or "超时" in message:
        return "timeout"
    return "network_error"