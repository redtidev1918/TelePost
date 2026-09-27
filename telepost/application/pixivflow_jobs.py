"""PixivFlow Job port — the ONE boundary between TelePost and PixivFlow.

Rationale (Workflow Protocol v1)
--------------------------------
PixivFlow is the content acquisition/processing engine; TelePost is the
workflow orchestrator. Across that boundary only four object kinds are allowed
— Task/Job/Event/Result/Asset — plus capability discovery, and correlation is
carried by the opaque keys ``idempotency_key`` / ``correlation_id`` / ``job_id``
/ ``labels``. Nothing in TelePost may reason about PixivFlow internals.

This module is that boundary. All HTTP paths, request bodies and response field
decoding live here, and **nothing else** in TelePost may build a PixivFlow URL
or read a PixivFlow payload:

* :meth:`PixivFlowJobClient.submit` — start (or idempotently resume) one job;
* :meth:`PixivFlowJobClient.get` — read one job snapshot.

The refetch heartbeat loop and the refetch state machine depend only on
:class:`JobSnapshot` (``status`` / heartbeat / timestamps / ``error_code``).
Today the concrete :class:`HttpPixivFlowJobClient` still speaks the legacy
routes ``POST /internal/targets/{target}/refetch`` and ``GET
/internal/targets/{target}/refetch/{id}``; switching to ``POST /jobs`` +
``GET /jobs/{id}`` later is a change to this file only, with zero change to the
state machine or the heartbeat loop.

Deliberate non-goals: the snapshot never exposes a PixivFlow business concept
(``slotId``/``disposition``/…) as a decision input. ``labels`` is an opaque
diagnostic bag (it is where a legacy ``slotId`` is parked so existing rows keep
their slot for the Mini App), and liveness is judged ONLY from the protocol
fields — status, heartbeat/timestamps, ``error.code``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, Tuple
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

# Default per-request network budget (seconds). A poll must never outlive a
# tick, and 10s was the pre-existing status-read timeout in handlers.review.
# Both are env-overridable: PixivFlow defaults to stopped and cold-starts on
# demand, so the submit budget is an operational knob, not a hardcoded 120s that
# a caller cannot shorten.
DEFAULT_TIMEOUT_SECONDS = max(1.0, float(os.environ.get("PIXIVFLOW_JOB_TIMEOUT_SECONDS", "10")))
SUBMIT_TIMEOUT_SECONDS = max(
    DEFAULT_TIMEOUT_SECONDS,
    float(os.environ.get("PIXIVFLOW_JOB_SUBMIT_TIMEOUT_SECONDS", "120")),
)
# Protocol timestamps are epoch seconds; guard against millisecond payloads.
_EPOCH_MS_FLOOR = 1e11
# Trace strings are diagnostics only — they are never compared, only logged.
_TRACE_LIMIT = 200


class PixivFlowJobError(RuntimeError):
    """A boundary failure, classified by ``error.code`` vocabulary.

    ``code`` is one of ``unauthorized`` / ``not_found`` / ``remote_error`` /
    ``timeout`` / ``network_error`` / ``remote_rejected`` — the same opaque
    vocabulary the pre-existing ``_classify_refetch_error`` produced, so callers
    keep their behaviour while no longer parsing HTTP strings themselves.
    """

    def __init__(self, code: str, detail: Any = "", *, status: int = 0):
        self.code = str(code or "network_error")
        self.detail = str(detail or "")[:400]
        self.status = int(status or 0)
        super().__init__(f"{self.code}: {self.detail}" if self.detail else self.code)


@dataclass(frozen=True)
class JobSnapshot:
    """One normalized view of a job — protocol fields only.

    ``status`` is the remote's own status token, passed through UNTRANSLATED.
    The caller (refetch state machine) maps it to a stage via
    ``refetch_state.stage_for_remote_state``; this port never decides business
    meaning. ``labels`` stays opaque and diagnostic.
    """

    job_id: str = ""
    status: str = ""
    heartbeat_at: Optional[float] = None
    updated_at: Optional[float] = None
    started_at: Optional[float] = None
    created_at: Optional[float] = None
    error_code: str = ""
    labels: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        """True only when the remote ADAPTER marks the snapshot finished."""
        return bool(self.labels.get("terminal"))


@dataclass(frozen=True)
class SubmitReceipt:
    """Acknowledged job submission (``replayed`` = idempotent re-submit)."""

    job_id: str = ""
    accepted: bool = False
    replayed: bool = False
    labels: Dict[str, Any] = field(default_factory=dict)

    def slot_id(self) -> str:
        """Opaque diagnostic label (legacy slot id), never a decision input."""
        return str(self.labels.get("slot_id") or "")


class PixivFlowJobClient(Protocol):
    """The replaceable port the refetch job lifecycle depends on."""

    def submit(
        self,
        job_type: str,
        idempotency_key: str,
        *,
        correlation_id: str = "",
        params: Optional[Dict[str, Any]] = None,
    ) -> SubmitReceipt:
        """Start (or idempotently resume) one job; idempotency is the KEY's."""
        ...

    def get(self, job_id_or_key: str, *, job_type: str = "",
            params: Optional[Dict[str, Any]] = None) -> JobSnapshot:
        """Read one job snapshot; raises :class:`PixivFlowJobError` on failure."""
        ...


def _to_epoch(value: Any) -> Optional[float]:
    """Coerce a protocol timestamp to epoch seconds (``None`` when unusable)."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            try:
                from datetime import datetime, timezone

                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return float(parsed.timestamp())
            except (ValueError, TypeError):
                return None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        return None
    if number <= 0:
        return None
    # A 13-digit value is milliseconds, not seconds.
    if number > _EPOCH_MS_FLOOR:
        number = number / 1000.0
    return number


def _first_timestamp(payload: Dict[str, Any]) -> Optional[float]:
    """The newest protocol liveness timestamp the payload offers.

    Fallback chain ``heartbeatAt`` → ``updatedAt`` → ``startedAt`` →
    ``createdAt`` keeps liveness working on a PixivFlow that does not yet expose
    explicit heartbeats (deployed 3.2.0 answers with only
    ``{requestId, slotId, state, slotStatus}``, i.e. no timestamp at all).
    """
    for key in ("heartbeatAt", "updatedAt", "startedAt", "createdAt"):
        stamp = _to_epoch(payload.get(key))
        if stamp is not None:
            return stamp
    return None


def _error_code(payload: Dict[str, Any], status: int) -> str:
    """Extract the protocol ``error.code`` (never a business outcome name)."""
    raw = payload.get("error")
    if isinstance(raw, dict):
        code = str(raw.get("code") or raw.get("failureCode") or "")
        if code:
            return code[:120]
    elif isinstance(raw, str) and raw.strip():
        return raw.strip()[:120]
    for key in ("errorCode", "failureCode", "failure_code"):
        code = str(payload.get(key) or "").strip()
        if code:
            return code[:120]
    return str(status) if status else ""


def decode_job_snapshot(payload: Any) -> JobSnapshot:
    """Normalize one raw PixivFlow job payload into protocol fields.

    Unknown/opaque extras are parked in ``labels`` (diagnostics) instead of
    being interpreted. A payload missing timestamps and status still yields a
    valid snapshot — degraded, never fatal.
    """
    if not isinstance(payload, dict):
        return JobSnapshot()
    labels: Dict[str, Any] = {}
    for label_key in ("slotId", "slot_id"):
        value = payload.get(label_key)
        if value not in (None, ""):
            labels["slot_id"] = str(value)[:120]
            break
    raw_labels = payload.get("labels")
    if isinstance(raw_labels, dict):
        for key, value in raw_labels.items():
            labels[str(key)[:60]] = str(value)[:120]
    raw_state = payload.get("state")
    if raw_state in (None, ""):
        raw_state = payload.get("status")
    return JobSnapshot(
        job_id=str(payload.get("requestId") or payload.get("jobId")
                   or payload.get("job_id") or ""),
        status=str(raw_state or ""),
        heartbeat_at=_to_epoch(payload.get("heartbeatAt")),
        updated_at=_to_epoch(payload.get("updatedAt")),
        started_at=_to_epoch(payload.get("startedAt")),
        created_at=_to_epoch(payload.get("createdAt")),
        error_code=_error_code(payload, 0),
        labels=labels,
    )


def decode_submit_receipt(payload: Any, status: int) -> SubmitReceipt:
    """Normalize a submit acknowledgement; accepted ⇔ HTTP 202 + status."""
    if not isinstance(payload, dict):
        payload = {}
    snapshot = decode_job_snapshot(payload)
    return SubmitReceipt(
        job_id=snapshot.job_id,
        accepted=(status == 202 and str(payload.get("status") or "") == "accepted"),
        replayed=bool(payload.get("replayed") or payload.get("reuse")),
        labels=dict(snapshot.labels),
    )


def classify_error(exc: BaseException) -> str:
    """Map a boundary exception to the opaque ``error.code`` vocabulary."""
    if isinstance(exc, PixivFlowJobError):
        return exc.code
    lowered = str(exc).lower()
    if "http 401" in lowered or "http 403" in lowered:
        return "unauthorized"
    if "http 404" in lowered:
        return "not_found"
    if "http 5" in lowered:
        return "remote_error"
    if "timed out" in lowered or "timeout" in lowered or "超时" in str(exc):
        return "timeout"
    return "network_error"


class HttpPixivFlowJobClient:
    """The HTTP adapter of the port (legacy refetch routes, unchanged).

    Owns every URL segment, header and payload decode. The rest of TelePost
    never sees them. ``transport`` is injectable for tests.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport=None,
    ):
        self._base = (base_url if base_url is not None
                      else os.environ.get("PIXIVFLOW_REFETCH_BASE_URL", ""))
        self._token = (token if token is not None
                       else os.environ.get("PIXIVFLOW_REFETCH_TOKEN", ""))
        self._timeout = float(timeout)
        self._transport = transport or urlopen

    # ---- plumbing -----------------------------------------------------
    def _validated_base(self) -> str:
        base = str(self._base or "").rstrip("/")
        parsed = urlparse(base)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc
                or parsed.username or parsed.password):
            raise PixivFlowJobError(
                "network_error", "无效的 PixivFlow 重抓地址",
            )
        return base

    def _target_of(self, params: Optional[Dict[str, Any]],
                   default: str = "") -> str:
        if not params:
            return default
        for key in ("target_id", "target", "scope"):
            value = params.get(key)
            if value not in (None, ""):
                return str(value)
        return default

    @staticmethod
    def _http_error_code(status: int) -> str:
        if status in (401, 403):
            return "unauthorized"
        if status == 404:
            return "not_found"
        if status >= 500:
            return "remote_error"
        return "remote_rejected"

    # ---- port ---------------------------------------------------------
    def submit(
        self,
        job_type: str,
        idempotency_key: str,
        *,
        correlation_id: str = "",
        params: Optional[Dict[str, Any]] = None,
    ) -> SubmitReceipt:
        """Start/resume one job. ``job_type`` is 'refetch' for this port today."""
        base = self._validated_base()
        target = self._target_of(params)
        if not target:
            raise PixivFlowJobError("remote_rejected", "缺少 target_id")
        url = f"{base}/internal/targets/{quote(target, safe='')}/{job_type}"
        body: Dict[str, Any] = {"requestId": idempotency_key}
        if correlation_id:
            body["correlationId"] = correlation_id
        request = Request(
            url,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._transport(request, timeout=SUBMIT_TIMEOUT_SECONDS) as response:
                payload = json.load(response)
                status = int(getattr(response, "status", 0) or 0)
        except HTTPError as error:
            raise PixivFlowJobError(
                self._http_error_code(int(getattr(error, "code", 0) or 0)),
                f"PixivFlow 拒绝重抓（HTTP {getattr(error, 'code', '?')}）",
                status=int(getattr(error, "code", 0) or 0),
            ) from error
        except PixivFlowJobError:
            raise
        except Exception as error:  # transport/protocol — never a business verdict
            raise PixivFlowJobError(classify_error(error), error) from error
        receipt = decode_submit_receipt(payload, status)
        if not receipt.accepted and not receipt.replayed:
            raise PixivFlowJobError(
                self._http_error_code(status),
                f"PixivFlow 拒绝重抓（HTTP {status}）",
                status=status,
            )
        return receipt

    def get(self, job_id_or_key: str, *, job_type: str = "",
            params: Optional[Dict[str, Any]] = None) -> JobSnapshot:
        """Read one job snapshot (no business interpretation).

        ``params`` carries the opaque scope (``target_id``) the legacy route
        needs; when PixivFlow exposes ``GET /jobs/{id}`` the scope argument
        simply stops being needed and only this method changes.
        """
        base = self._validated_base()
        target = self._target_of(params)
        if not target:
            raise PixivFlowJobError("remote_rejected", "缺少 target_id")
        path = f"/internal/targets/{quote(target, safe='')}/{job_type or 'refetch'}"
        url = f"{base}{path}/{quote(str(job_id_or_key), safe='')}"
        request = Request(
            url, headers={"Authorization": f"Bearer {self._token}"}, method="GET",
        )
        try:
            with self._transport(request, timeout=self._timeout) as response:
                payload = json.load(response)
                status = int(getattr(response, "status", 0) or 0)
        except HTTPError as error:
            raise PixivFlowJobError(
                self._http_error_code(int(getattr(error, "code", 0) or 0)),
                f"PixivFlow 读取重抓状态失败（HTTP {getattr(error, 'code', '?')}）",
                status=int(getattr(error, "code", 0) or 0),
            ) from error
        except PixivFlowJobError:
            raise
        except Exception as error:
            raise PixivFlowJobError(classify_error(error), error) from error
        if status not in (200, 0):
            raise PixivFlowJobError(
                self._http_error_code(status),
                f"PixivFlow 读取重抓状态失败（HTTP {status}）",
                status=status,
            )
        snapshot = decode_job_snapshot(payload)
        if snapshot.job_id and str(snapshot.job_id) != str(job_id_or_key):
            raise PixivFlowJobError(
                "remote_rejected",
                f"PixivFlow 重抓状态不匹配（{snapshot.job_id}）",
            )
        return snapshot

def default_job_client() -> HttpPixivFlowJobClient:
    """Build the production client from the environment (fresh env each call)."""
    return HttpPixivFlowJobClient()


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "HttpPixivFlowJobClient",
    "JobSnapshot",
    "PixivFlowJobClient",
    "PixivFlowJobError",
    "SubmitReceipt",
    "classify_error",
    "decode_job_snapshot",
    "decode_submit_receipt",
    "default_job_client",
]
