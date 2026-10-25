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
import time
import uuid
from typing import Optional
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
    stage = "recovery_request"
    retryable = True
    hint = ""

    def __init__(self, message: str, *, code: str = None, stage: str = None,
                 retryable: bool = None, hint: str = None):
        super().__init__(message)
        if code is not None:
            self.code = code
        if stage is not None:
            self.stage = stage
        if retryable is not None:
            self.retryable = bool(retryable)
        if hint is not None:
            self.hint = hint


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


async def admit_recovery(target_id: str, *, retry_mode: str = NORMAL,
                         callback_key: str = "", schedule_id: str = "") -> dict:
    """DURABLY admit one recovery attempt — local DB only, no remote I/O.

    Split from the remote submission (2026-10-25): the Telegram callback
    handler runs inside the strictly-serial Durable Inbox worker, where a
    remote wait (PixivFlow cold start ~8s+, worst case the submit timeout)
    both blows Telegram's ~10s callback-answer window and head-of-line blocks
    every later update of that bot. Callers answer the user from THIS result
    and submit out-of-band via :func:`submit_recovery` (immediate background
    task + the periodic resubmit sweep).

    Returns the admission dict (``state`` is ``requested`` for a fresh admit,
    or the replayed row's state) with the same shape as before::

        {"state", "request_id", "replayed", "already_running",
         "retry_mode", "target_id", "schedule_id"}

    Raises RecoveryError subclasses for configuration/admission failures
    exactly as before.
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
    return {
        "state": "requested",
        "request_id": request_id,
        "replayed": False,
        "already_running": False,
        "retry_mode": mode,
        "target_id": target_id,
        "schedule_id": schedule_id,
    }


async def submit_recovery(target_id: str, request_id: str,
                          retry_mode: str, *,
                          terminal_on_failure: bool = True) -> dict:
    """POST the durable recovery to PixivFlow and settle the attempt row.

    Transient failures retain the same requestId. Non-retryable admission
    errors require reconciliation: a previous response may already have been
    lost, so rejection of this HTTP request cannot terminate that execution.
    ``terminal_on_failure`` preserves synchronous error reporting only.
    """
    mode = _normalize_mode(retry_mode)
    # Remote submission is async: the endpoint answers once the attempt is
    # DURABLE; PixivFlow queues/runs under its own resource admission.
    try:
        result = await asyncio.to_thread(
            _submit_pixivflow_recover, target_id, request_id, mode
        )
        logger.info(
            "恢复提交成功: target=%s request=%s slot=%s",
            target_id, request_id, result.get("slotId", ""),
        )
        async with db_manager.get_db() as conn:
            from telepost.storage.sqlite.recovery import RecoveryRepository
            repo = RecoveryRepository(conn)
            await repo.mark_accepted(request_id, result.get("slotId", ""))
        try:
            await _record_recovery_event(
                "schedule.recovery_remote_accepted", target_id=target_id,
                request_id=request_id, retry_mode=mode, schedule_id="",
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
            "schedule_id": "",
        }
    except Exception as exc:
        failure = _classify_recovery_error(exc)
        code = failure["code"]
        # A lost HTTP response is not proof that admission failed. Even the
        # synchronous caller must retain the same durable identity and lock.
        terminal = not failure.get("retryable", False)
        if terminal:
            async with db_manager.get_db() as conn:
                from telepost.storage.sqlite.recovery import RecoveryRepository
                repo = RecoveryRepository(conn)
                await repo.require_reconciliation(request_id)
            try:
                await _record_recovery_event(
                    "schedule.recovery_reconciliation_required", target_id=target_id,
                    request_id=request_id, retry_mode=mode, schedule_id="",
                    error_class=code,
                )
            except Exception:
                pass
            cause = str(exc).strip().rstrip("。") or ""
            reason = f"：{cause[:200]}" if cause else ""
            raise RecoveryError(
                f"恢复请求提交失败{reason}，请稍后重试",
                code=code or failure["code"],
                stage="recovery_request",
                retryable=failure["retryable"],
                hint=failure["hint"],
            ) from exc
        logger.warning(
            "恢复提交暂缓（可重试，等待兜底重扫）: target=%s request=%s "
            "error_class=%s detail=%s",
            target_id, request_id, code, str(exc)[:200],
        )
        try:
            await _record_recovery_event(
                "schedule.recovery_submit_deferred", target_id=target_id,
                request_id=request_id, retry_mode=mode, schedule_id="",
                error_class=code,
            )
        except Exception:
            pass
        if terminal_on_failure:
            raise RecoveryError(
                '恢复请求结果尚未确认，系统将按原请求继续对账', code=code,
                retryable=True, hint=failure['hint']) from exc
        return {
            "state": "requested",
            "request_id": request_id,
            "replayed": False,
            "already_running": True,
            "retry_mode": mode,
            "target_id": target_id,
            "schedule_id": "",
            "submit_error": code,
        }


async def request_target_recovery(
    target_id: str,
    *,
    retry_mode: str = NORMAL,
    callback_key: str = "",
    schedule_id: str = "",
) -> dict:
    """Admit + synchronously submit (historical contract, non-inbox callers)."""
    admitted = await admit_recovery(
        target_id, retry_mode=retry_mode, callback_key=callback_key,
        schedule_id=schedule_id,
    )
    if admitted.get("replayed") or admitted.get("state") != "requested":
        return admitted
    return await submit_recovery(
        target_id, admitted["request_id"], admitted["retry_mode"],
    )


#: A 'requested' row younger than this is owned by its background submit task;
#: older ones are fair game for the periodic sweep.
RECOVERY_SUBMIT_SWEEP_MIN_AGE = 60.0
#: Stop automatic submission after this age; unresolved requests retain their
#: identity and exclusion lock while querying the execution owner.
RECOVERY_SUBMIT_MAX_AGE = 15 * 60.0


async def resubmit_pending_recoveries(*, now: Optional[float] = None) -> int:
    """Reconcile the existing remote ledger; local age never stops remote work."""
    now = time.time() if now is None else now
    if not os.getenv("PIXIVFLOW_REFETCH_BASE_URL") or not os.getenv("PIXIVFLOW_REFETCH_TOKEN"):
        return 0
    from telepost.storage.sqlite.recovery import RecoveryRepository
    rows = []
    async with db_manager.get_db() as conn:
        repo = RecoveryRepository(conn)
        rows = await repo.list_active_before(now - RECOVERY_SUBMIT_SWEEP_MIN_AGE)
    for row in rows:
        try:
            result = await asyncio.to_thread(
                _get_pixivflow_recovery, row["target_id"], row["request_id"])
            if result is not None:
                await reconcile_recovery_result(row["target_id"], row["request_id"], result)
            elif (row["state"] == 'requested' and not row['reconciliation_required']
                  and float(row['created_at']) >= now - RECOVERY_SUBMIT_MAX_AGE):
                await submit_recovery(row['target_id'], row['request_id'],
                                      row['retry_mode'] or NORMAL, terminal_on_failure=False)
            else:
                raise RecoveryError('远端记录未确认', code='reconciliation_required')
        except Exception:
            async with db_manager.get_db() as conn:
                await RecoveryRepository(conn).require_reconciliation(row['request_id'])
            logger.warning('恢复需要对账: request=%s', row['request_id'])
            # 无进展不重复记账：审计只在首次转入待对账时落一条，
            # 之后每轮仅刷新 last_checked_at（对账进度信号）。
            if not row['reconciliation_required']:
                await _record_recovery_event(
                    'schedule.recovery_reconciliation_required', target_id=row['target_id'],
                    request_id=row['request_id'], retry_mode=row['retry_mode'] or NORMAL)
    return len(rows)


async def reconcile_recovery_result(target_id: str, request_id: str, result: dict) -> None:
    if result.get('requestId') != request_id:
        raise ValueError('recovery identity mismatch')
    if result.get('targetId', target_id) != target_id:
        raise ValueError('recovery target mismatch')
    remote_state = result.get('state')
    states = {'submitted': 'succeeded', 'failed': 'failed',
              'no_candidate': 'no_candidate', 'duplicate': 'duplicate',
              'cancelled': 'cancelled', 'pending': 'accepted', 'running': 'accepted',
              'selected': 'accepted', 'artifact_ready': 'accepted', 'delivery_pending': 'accepted'}
    if remote_state not in states:
        raise ValueError('unknown remote recovery state')
    from telepost.storage.sqlite.recovery import RecoveryRepository
    async with db_manager.get_db() as conn:
        await RecoveryRepository(conn).apply_remote(
            request_id, target_id, state=states[remote_state], remote_state=remote_state,
            slot_id=str(result.get('slotId') or ''),
            reason=str(result.get('errorCode') or 'remote_execution_failed'))


def _get_pixivflow_recovery(target_id: str, request_id: str) -> Optional[dict]:
    base = os.environ['PIXIVFLOW_REFETCH_BASE_URL'].rstrip('/')
    parsed = urlparse(base)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('invalid recovery endpoint')
    request = Request(
        f'{base}/internal/targets/{quote(target_id, safe="")}/recover/{quote(request_id, safe="")}',
        headers={'Authorization': f'Bearer {os.environ["PIXIVFLOW_REFETCH_TOKEN"]}'})
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise RecoveryError('恢复状态查询暂不可用', code='remote_error') from error


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
        code = {400: 'invalid_request', 401: 'unauthorized', 403: 'unauthorized',
                404: 'not_found', 409: 'invalid_request'}.get(error.code, 'remote_error')
        raise RecoveryError(f'PixivFlow 拒绝恢复（HTTP {error.code}）', code=code,
                            retryable=code == 'remote_error') from error


_FAILURE_META = {
    "unauthorized": {"retryable": False, "hint": "服务凭据无效，需管理员检查配置后重试"},
    "invalid_request": {"retryable": False, "hint": "请求格式或参数被 PixivFlow 拒绝，需管理员检查后重试"},
    "not_found": {"retryable": False, "hint": "目标不存在或已被处理，无需重试"},
    "remote_error": {"retryable": True, "hint": "PixivFlow 服务暂时不可用，稍后重试"},
    "timeout": {"retryable": True, "hint": "请求超时，稍后重试"},
    "network_error": {"retryable": True, "hint": "网络连接失败，稍后重试"},
}


def _classify_recovery_error(exc: Exception) -> dict:
    if isinstance(exc, RecoveryError):
        return {'code': exc.code, 'retryable': exc.retryable,
                'hint': _FAILURE_META.get(exc.code, {}).get('hint', '')}
    message = str(exc)
    lowered = message.lower()
    if "http 401" in lowered or "http 403" in lowered:
        code = "unauthorized"
    elif "http 404" in lowered:
        code = "not_found"
    elif "http 400" in lowered or ("http 4" in lowered and "http 41" not in lowered):
        code = "invalid_request"
    elif "http 5" in lowered:
        code = "remote_error"
    elif "timeout" in lowered or "超时" in message:
        code = "timeout"
    else:
        code = "network_error"
    meta = _FAILURE_META.get(code, {})
    return {"code": code, "retryable": bool(meta.get("retryable", True)),
            "hint": meta.get("hint", "网络或服务异常，稍后重试")}
