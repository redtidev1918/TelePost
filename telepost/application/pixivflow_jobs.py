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

Transport
---------
:class:`HttpPixivFlowJobClient` speaks the generic Workflow Protocol v1 Job API
(``GET /capabilities``, ``POST /jobs``, ``GET /jobs/{job_id}``,
``GET /jobs?idempotency_key=…``) and negotiates capabilities before it is used.
TelePost's logical name for this work is ``refetch``; the protocol job type is
:data:`PROTOCOL_JOB_TYPE` (``candidate_search``) — that mapping lives HERE so no
caller outside this module ever names a protocol job type.

``PIXIVFLOW_JOB_TRANSPORT=legacy`` (read in exactly one place,
:func:`job_transport`) keeps the pre-migration internal route
``POST /internal/targets/{target}/refetch`` +
``GET /internal/targets/{target}/refetch/{id}`` working exactly as before, as
the documented rollback switch. The default is the protocol path, and a
producer that does not declare the protocol version and job type fails LOUDLY
during capability negotiation — it is never a silent fallback to the legacy
route (an operator who wants the old route sets the flag explicitly).

Deliberate non-goals: the snapshot never exposes a PixivFlow business concept
(``slotId``/``disposition``/…) as a decision input. ``labels`` is an opaque
diagnostic bag (it is where a legacy ``slotId`` is parked so existing rows keep
their slot for the Mini App), and liveness is judged ONLY from the protocol
fields — status, heartbeat/timestamps, ``error.code``.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, Tuple
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from telepost.domain import refetch_state

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

#: The Workflow Protocol major version this consumer speaks.
PROTOCOL_VERSION = "1"
#: The protocol job type for TelePost's logical ``refetch`` work: the protocol
#: names the WORK (a candidate search), never TelePost's business flow.
PROTOCOL_JOB_TYPE = "candidate_search"
#: TelePost's internal/business name for the same work (legacy route + callers).
LEGACY_JOB_TYPE = "refetch"
#: Transport selector values (see :func:`job_transport`). ``protocol`` is the
#: documented default; ``legacy`` is the rollback switch.
DEFAULT_JOB_TRANSPORT = "protocol"
LEGACY_JOB_TRANSPORT = "legacy"

#: Protocol ``Job.status`` -> the remote-state token TelePost's refetch state
#: machine consumes (``handlers.review._REFETCH_TERMINAL_OUTCOMES`` and
#: ``refetch_state.stage_for_remote_state``). ``progress.stage`` is display-only
#: for consumers, so no sub-stage is invented: a claimed job reports ``running``
#: (no stage claim) instead of guessing searching vs filtering, and only a job
#: the producer has not claimed (``queued``) leaves the admission budget armed.
PROTOCOL_STATUS_REMOTE_STATES: Dict[str, str] = {
    "queued": "pending",
    "running": "running",
    "succeeded": "submitted",
    "failed": "failed",
    "cancelled": "failed",
    "expired": "failed",
}
#: Projected remote-state tokens that are a reported OUTCOME, not a stage.
TERMINAL_REMOTE_STATES = frozenset({"no_candidate", "duplicate", "failed", "submitted"})

#: Protocol Event ``type`` values that carry a terminal verdict. These are the
#: only events the consumer's §events wiring feeds into the refetch outcome
#: path (the "不再永久静默" requirement): a terminal Event and the poll agree on
#: the same terminal outcome through the shared outcome/notify seam.
TERMINAL_EVENT_TYPES = frozenset({
    "job.succeeded", "job.failed", "job.expired", "job.cancelled",
})
#: Event ``type`` -> the protocol ``status`` token from which the remote-state
#: projection is derived (``cancelled``/``expired`` fold onto ``failed``).
_EVENT_TYPE_STATUS = {
    "job.accepted": "queued",
    "job.started": "running",
    "job.progress": "running",
    "job.succeeded": "succeeded",
    "job.failed": "failed",
    "job.expired": "expired",
    "job.cancelled": "cancelled",
}
#: Consumer-side public ingress root for the events callback. A submitted Task's
#: ``callback_url`` is ``{base}/api/bot{N}/v1/jobs/events`` where ``base`` is
#: ``TELEPOST_API_BASE_URL`` (must be *configured*; production compose sets it to
#: ``http://telepost:8080``) and ``N`` is the per-bot process index (child
#: ``TELEPOST_BOT_INDEX``, default ``1``). Read at call time by
#: :func:`consumer_callback_url`, never cached at import.
TELEPOST_API_BASE_URL = "http://telepost:8080"
#: Vendored protocol SSOT (``protocol/v1/error-mapping.json``): the retryable
#: default per closed error code.
_ERROR_MAPPING_PATH = (
    Path(__file__).resolve().parents[2] / "protocol" / "v1" / "error-mapping.json"
)


def job_transport() -> str:
    """The configured transport — the ONE reader of ``PIXIVFLOW_JOB_TRANSPORT``.

    ``protocol`` (the documented default) speaks the Workflow Protocol v1 Job
    API; ``legacy`` keeps the pre-migration internal refetch route. Any other
    value resolves to the default: there is no third mode and no silent
    downgrade when the producer does not declare the protocol.
    """
    configured = str(os.environ.get("PIXIVFLOW_JOB_TRANSPORT", "") or "").strip().lower()
    if configured == LEGACY_JOB_TRANSPORT:
        return LEGACY_JOB_TRANSPORT
    return DEFAULT_JOB_TRANSPORT


def protocol_job_type(job_type: str) -> str:
    """Map TelePost's logical job name onto the protocol job type."""
    name = str(job_type or "").strip()
    if name in ("", LEGACY_JOB_TYPE):
        return PROTOCOL_JOB_TYPE
    return name


def project_remote_state(status: str, error_code: str = "") -> str:
    """Project a protocol ``status`` (+ ``error.code``) onto TelePost's vocabulary.

    The caller (``handlers.review``) drives both its state machine and its
    watchdog budgets from this token, so the projection IS the contract:

    * ``queued`` → ``pending`` (admitted, not claimed yet);
    * ``running`` → ``running`` (claimed and executing; no sub-stage observed);
    * ``succeeded`` → ``submitted`` (the producer finished the work);
    * ``failed`` with ``error.code == 'no_candidate'`` → ``no_candidate``;
    * ``failed`` / ``cancelled`` / ``expired`` → ``failed``.

    An unknown status is returned unchanged: the caller records it as
    unavailable instead of guessing progress it cannot observe.
    """
    token = str(status or "").strip().lower()
    projected = PROTOCOL_STATUS_REMOTE_STATES.get(token)
    if projected is None:
        return token
    if projected == "failed" and str(error_code or "").strip().lower() == "no_candidate":
        return "no_candidate"
    return projected


def is_terminal_remote_state(state: str) -> bool:
    """True when a projected remote-state token is a reported outcome."""
    return str(state or "").strip().lower() in TERMINAL_REMOTE_STATES


@lru_cache(maxsize=1)
def protocol_retryable_defaults() -> Dict[str, bool]:
    """The protocol's own retryable default per error code (vendored SSOT).

    ``protocol/v1/error-mapping.json`` is machine-checked against the schema
    enum: a payload's own ``Error.retryable`` wins when present, and this table
    is the default the consumer must fall back to. A missing/unreadable file
    degrades to ``{}`` (the caller then keeps its status-derived default).
    """
    try:
        document = json.loads(_ERROR_MAPPING_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    codes = document.get("protocol_codes") if isinstance(document, dict) else None
    if not isinstance(codes, dict):
        return {}
    return {
        str(code): bool(spec["retryable"])
        for code, spec in codes.items()
        if isinstance(spec, dict) and isinstance(spec.get("retryable"), bool)
    }


def _is_protocol_v1(version: str) -> bool:
    """True when a declared protocol version is major version 1."""
    text = str(version or "").strip()
    return text == PROTOCOL_VERSION or text.split(".")[0] == PROTOCOL_VERSION


def _unwrap_job(payload: Any) -> Dict[str, Any]:
    """``POST /jobs`` wraps the Job as ``{"job": …}``; every other route is bare."""
    if isinstance(payload, dict):
        inner = payload.get("job")
        if isinstance(inner, dict):
            return inner
        return payload
    return {}


def _error_envelope(error: BaseException) -> Dict[str, Any]:
    """The ``{"error": …}`` body an HTTP failure carries (best effort, never fatal)."""
    reader = getattr(error, "read", None)
    if not callable(reader):
        return {}
    try:
        raw = reader()
    except Exception:
        return {}
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    inner = payload.get("error") if isinstance(payload, dict) else None
    return inner if isinstance(inner, dict) else {}


def _job_from_page(page: Any, *, idempotency_key: str) -> Optional[Dict[str, Any]]:
    """The ONE job a key query identifies (the protocol allows at most one)."""
    if not isinstance(page, dict):
        return None
    jobs = [item for item in (page.get("jobs") or []) if isinstance(item, dict)]
    matches = [
        item for item in jobs
        if str(item.get("idempotency_key") or "") == str(idempotency_key)
    ]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    # Defensive only: a producer must never return two Jobs for one key. If it
    # ever does, the newest projection wins — never a merge of the two.
    return max(
        matches,
        key=lambda item: (
            _to_epoch(item.get("updated_at")) or 0.0,
            _to_epoch(item.get("created_at")) or 0.0,
        ),
    )


class PixivFlowJobError(RuntimeError):
    """A boundary failure, classified by ``error.code`` vocabulary.

    ``code`` is one of ``unauthorized`` / ``not_found`` / ``remote_error`` /
    ``timeout`` / ``network_error`` / ``remote_rejected`` — the same opaque
    vocabulary the pre-existing ``_classify_refetch_error`` produced, so callers
    keep their behaviour while no longer parsing HTTP strings themselves.

    ``retryable`` carries the protocol ``Error.retryable`` verdict when the
    producer stated it (or the vendored default for its code); ``None`` means
    "unknown" (e.g. a transport failure that never reached the producer). A 404
    for an unknown job is always ``retryable=False``: it is a definitive answer,
    not a transient failure.
    """

    def __init__(self, code: str, detail: Any = "", *, status: int = 0,
                 retryable: Optional[bool] = None):
        self.code = str(code or "network_error")
        self.detail = str(detail or "")[:400]
        self.status = int(status or 0)
        self.retryable = None if retryable is None else bool(retryable)
        super().__init__(f"{self.code}: {self.detail}" if self.detail else self.code)


@dataclass(frozen=True)
class JobSnapshot:
    """One normalized view of a job — protocol fields only.

    ``status`` is the remote's status token PROJECTED onto TelePost's
    remote-state vocabulary (see :func:`project_remote_state`); the caller
    (refetch state machine) maps it to a stage via
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
        """True when the protocol ``status`` projects onto a reported outcome.

        Derived from :data:`TERMINAL_REMOTE_STATES` — never from ``labels``:
        protocol v1 persists no labels, so a label-driven terminality would
        silently never fire (the attempt would hang until a local budget).
        """
        return is_terminal_remote_state(self.status)


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


def _pick(payload: Dict[str, Any], *keys: str) -> Any:
    """First non-empty value among equivalent field spellings.

    The same field is spelled ``camelCase`` by the legacy adapter payload and
    ``snake_case`` by Workflow Protocol v1 (``heartbeatAt`` vs ``heartbeat_at``);
    a decoder must accept both, and neither spelling may silently win by default.
    """
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _first_timestamp(payload: Dict[str, Any]) -> Optional[float]:
    """The newest protocol liveness timestamp the payload offers.

    Fallback chain ``heartbeatAt``/``heartbeat_at`` → ``updatedAt``/``updated_at``
    → ``startedAt``/``started_at`` → ``createdAt``/``created_at`` keeps liveness
    working on a PixivFlow that does not yet expose explicit heartbeats
    (deployed 3.2.0 answers with only ``{requestId, slotId, state, slotStatus}``,
    i.e. no timestamp at all).
    """
    for key in ("heartbeatAt", "heartbeat_at", "updatedAt", "updated_at",
                "startedAt", "started_at", "createdAt", "created_at"):
        stamp = _to_epoch(payload.get(key))
        if stamp is not None:
            return stamp
    return None


#: ``error.code`` is code-shaped by contract; the pattern is the SAME one the
#: refetch store enforces at its single write choke point (one vocabulary).
_CODE_SHAPED_RE = re.compile(refetch_state.FAILURE_CODE_PATTERN)


def _error_code(payload: Dict[str, Any], status: int) -> str:
    """Extract the protocol ``error.code`` (never a business outcome name)."""
    raw = payload.get("error")
    if isinstance(raw, dict):
        code = str(raw.get("code") or raw.get("failureCode") or "")
        if code:
            return code[:120]
    elif isinstance(raw, str) and raw.strip():
        # The legacy shim answers errors with a PLAIN HUMAN STRING, e.g.
        # ``{"status":"error","error":"requestId must be a UUID"}``. That is not
        # a code: keep it only when it is already code-shaped and otherwise
        # collapse it to the opaque ``remote_error``. Defence in depth — the
        # refetch store normalizes ``failure_code`` at its single write choke
        # point, so a future caller cannot leak free text into the column.
        text = raw.strip()
        return text if _CODE_SHAPED_RE.match(text) else "remote_error"
    for key in ("errorCode", "failureCode", "failure_code"):
        code = str(payload.get(key) or "").strip()
        if code:
            return code[:120]
    return str(status) if status else ""


def decode_job_snapshot(payload: Any) -> JobSnapshot:
    """Normalize one raw PixivFlow job payload into protocol fields.

    Unknown/opaque extras are parked in ``labels`` (diagnostics) instead of
    being interpreted. A payload missing timestamps and status still yields a
    valid snapshot — degraded, never fatal. ``status`` is passed through as the
    payload spells it (``state`` first, for legacy payloads); use
    :func:`decode_protocol_job` for a protocol Job, which also projects it.
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
    return JobSnapshot(
        job_id=str(_pick(payload, "requestId", "jobId", "job_id") or ""),
        status=str(_pick(payload, "state", "status") or ""),
        heartbeat_at=_to_epoch(_pick(payload, "heartbeatAt", "heartbeat_at")),
        updated_at=_to_epoch(_pick(payload, "updatedAt", "updated_at")),
        started_at=_to_epoch(_pick(payload, "startedAt", "started_at")),
        created_at=_to_epoch(_pick(payload, "createdAt", "created_at")),
        error_code=_error_code(payload, 0),
        labels=labels,
    )


def decode_protocol_job(payload: Any) -> JobSnapshot:
    """Normalize one protocol ``$defs/Job`` (bare, or wrapped as ``{"job": …}``).

    Terminality and outcome come from the protocol ``status`` and
    ``error.code``, which this function projects onto the remote-state token
    the caller's state machine already understands.
    """
    snapshot = decode_job_snapshot(_unwrap_job(payload))
    return replace(
        snapshot,
        status=project_remote_state(snapshot.status, snapshot.error_code),
    )


def decode_submit_receipt(payload: Any, status: int) -> SubmitReceipt:
    """Normalize a legacy submit acknowledgement; accepted ⇔ HTTP 202 + status."""
    if not isinstance(payload, dict):
        payload = {}
    snapshot = decode_job_snapshot(payload)
    return SubmitReceipt(
        job_id=snapshot.job_id,
        accepted=(status == 202 and str(payload.get("status") or "") == "accepted"),
        replayed=bool(payload.get("replayed") or payload.get("reuse")),
        labels=dict(snapshot.labels),
    )


def decode_protocol_submit_receipt(payload: Any, status: int) -> SubmitReceipt:
    """Normalize a ``POST /jobs`` acknowledgement.

    The protocol answers **202** for the first acceptance and **200** for an
    idempotent replay of the same key; both carry the Job (wrapped as
    ``{"job": …}``).
    """
    snapshot = decode_job_snapshot(_unwrap_job(payload))
    http_status = int(status or 0)
    return SubmitReceipt(
        job_id=snapshot.job_id,
        accepted=(http_status == 202),
        replayed=(http_status == 200),
        labels=dict(snapshot.labels),
    )


def consumer_callback_url(bot_index: Optional[str] = None) -> str:
    """The consumer's own events-ingress URL to declare on a submitted Task.

    Only meaningful under the ``protocol`` transport: the producer's outbox
    POSTs each Event to this callback (at-least-once). The public base comes
    from ``TELEPOST_API_BASE_URL`` (production compose sets this to
    ``http://telepost:8080``); the per-bot process supplies its own index via
    ``TELEPOST_BOT_INDEX`` (default ``1``). ``TELEPOST_API_BASE_URL`` must
    actually be configured for the field to be emitted — this keeps Task bodies
    byte-stable for consumers/fixtures that do not run the ingress. Returns
    ``""`` when no base is configured or none resolves to a usable origin, so
    the caller can omit the field entirely.
    """
    base = os.environ.get("TELEPOST_API_BASE_URL", "").strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    bot = (str(bot_index if bot_index is not None
               else os.environ.get("TELEPOST_BOT_INDEX", "1")).strip() or "1")
    return f"{base}/api/bot{bot}/v1/jobs/events"


@dataclass(frozen=True)
class ProtocolEvent:
    """One normalized consumer-side Event document.

    Normalized from a bare ``$defs/Event`` (the exact body a producer callback
    POSTs to the consumer ingress). Only a few projection fields are surfaced;
    ``payload`` stays the raw, opaque bag so business handling can read
    ``payload.job``/``payload.error`` without this port guessing meaning.
    ``at`` is coerced to epoch seconds (the protocol often ships milliseconds).
    """

    event_id: str = ""
    job_id: str = ""
    event_type: str = ""
    at: Optional[float] = None
    correlation_id: str = ""
    labels: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        """True when this Event carries a terminal verdict."""
        return str(self.event_type or "").strip() in TERMINAL_EVENT_TYPES

    @property
    def error_code(self) -> str:
        """The protocol ``error.code`` carried on a terminal event, if any."""
        error = self.payload.get("error")
        if isinstance(error, dict):
            return str(error.get("code") or "")
        return ""

    @property
    def status_token(self) -> str:
        """The protocol ``status`` token this Event reports (best effort).

        Prefers ``payload.job.status`` (the authoritative snapshot) and falls
        back to the type-to-status table so an event without a job payload still
        projects onto a remote-state token.
        """
        job = self.payload.get("job") if isinstance(self.payload, dict) else None
        if isinstance(job, dict) and str(job.get("status") or "").strip():
            return str(job["status"]).strip()
        return _EVENT_TYPE_STATUS.get(str(self.event_type or "").strip(), "")

    @property
    def remote_state(self) -> str:
        """The remote-state token this Event projects onto.

        Reuses :func:`project_remote_state`, so a terminal Event and the poll
        agree on the exact same outcome vocabulary the refetch state machine
        already consumes (``submitted`` / ``failed`` / ``no_candidate``).
        """
        token = project_remote_state(self.status_token, self.error_code)
        token = str(token or "").strip().lower()
        if token in TERMINAL_REMOTE_STATES:
            return token
        return token


@dataclass(frozen=True)
class EventPage:
    """``GET /jobs/{job_id}/events`` — one ascending replay page."""

    job_id: str = ""
    events: tuple = ()
    next_after: str = ""
    unacked: int = 0

    @property
    def has_more(self) -> bool:
        """True when ``next_after`` says there are further events to pull."""
        return bool(str(self.next_after or "").strip())


@dataclass(frozen=True)
class AckResult:
    """``POST /jobs/{job_id}/events/ack`` — the consumer's cursor acknowledgement."""

    job_id: str = ""
    acked: int = 0
    unacked: int = 0


def decode_protocol_event(payload: Any) -> ProtocolEvent:
    """Normalize one bare ``$defs/Event`` into :class:`ProtocolEvent`.

    Tolerant: a payload missing fields still yields a valid (degraded) event,
    never a fatal error — mirroring the snapshot decoders.
    """
    if not isinstance(payload, dict):
        return ProtocolEvent()
    labels = payload.get("labels") if isinstance(payload.get("labels"), dict) else {}
    inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    return ProtocolEvent(
        event_id=str(payload.get("event_id") or ""),
        job_id=str(payload.get("job_id") or ""),
        event_type=str(payload.get("type") or ""),
        at=_to_epoch(payload.get("at")),
        correlation_id=str(payload.get("correlation_id") or ""),
        labels=dict(labels) if isinstance(labels, dict) else {},
        payload=dict(inner),
    )


def decode_event_page(payload: Any) -> EventPage:
    """Normalize a protocol ``EventPage`` into :class:`EventPage`."""
    if not isinstance(payload, dict):
        return EventPage()
    raw_events = [item for item in (payload.get("events") or []) if isinstance(item, dict)]
    events = tuple(decode_protocol_event(item) for item in raw_events)
    return EventPage(
        job_id=str(payload.get("job_id") or ""),
        events=events,
        next_after=str(payload.get("next_after") or ""),
        unacked=int(payload.get("unacked") or 0),
    )


def decode_ack_result(payload: Any) -> AckResult:
    """Normalize a protocol ``AckResult`` into :class:`AckResult`."""
    if not isinstance(payload, dict):
        return AckResult()
    return AckResult(
        job_id=str(payload.get("job_id") or ""),
        acked=int(payload.get("acked") or 0),
        unacked=int(payload.get("unacked") or 0),
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
    """The HTTP adapter of the port.

    Owns every URL segment, header and payload decode. The rest of TelePost
    never sees them. ``transport`` is injectable for tests; the transport itself
    is selected once per instance by :func:`job_transport`.
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
        self._job_transport = job_transport()
        # ``GET /capabilities`` is read at most once per instance (negotiation).
        self._capabilities_cache: Optional[Dict[str, Any]] = None

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

    @staticmethod
    def _status_retryable(status: int) -> bool:
        """Transport-level retryability, used when the producer states nothing."""
        return int(status or 0) in (408, 429) or int(status or 0) >= 500

    def _protocol_http_error(self, error: HTTPError, *, action: str) -> PixivFlowJobError:
        """Classify an HTTP failure, honouring the protocol ``{error}`` envelope."""
        status = int(getattr(error, "code", 0) or 0)
        code = self._http_error_code(status)
        retryable = self._status_retryable(status)
        envelope = _error_envelope(error)
        protocol_code = str(envelope.get("code") or "")[:120]
        if protocol_code:
            flag = envelope.get("retryable")
            if isinstance(flag, bool):
                # A payload's own retryable flag wins (error-mapping.json).
                retryable = flag
            else:
                retryable = protocol_retryable_defaults().get(protocol_code, retryable)
        if retryable and code == "remote_rejected":
            # A retryable failure is transient by definition, never a rejection.
            code = "remote_error"
        if status == 404:
            # An unknown job is a definitive answer, never a retryable failure.
            retryable = False
        detail = f"PixivFlow {action}（HTTP {status}）"
        if protocol_code:
            detail = f"{detail[:-1]}，{protocol_code}）"
        return PixivFlowJobError(code, detail, status=status, retryable=retryable)

    def _request_json(
        self,
        base: str,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        action: str = "请求失败",
    ) -> Tuple[Any, int]:
        """One authenticated protocol request; failures become PixivFlowJobError."""
        headers = {"Authorization": f"Bearer {self._token}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{base}{path}", data=data, headers=headers, method=method,
        )
        try:
            with self._transport(
                request, timeout=self._timeout if timeout is None else timeout
            ) as response:
                payload = json.load(response)
                status = int(getattr(response, "status", 0) or 0)
        except HTTPError as error:
            raise self._protocol_http_error(error, action=action) from error
        except PixivFlowJobError:
            raise
        except Exception as error:  # transport/protocol — never a business verdict
            raise PixivFlowJobError(classify_error(error), error) from error
        if status not in (200, 202, 0):
            raise PixivFlowJobError(
                self._http_error_code(status),
                f"PixivFlow {action}（HTTP {status}）",
                status=status,
                retryable=self._status_retryable(status),
            )
        return payload, status

    # ---- capability negotiation ---------------------------------------
    def _capabilities(self) -> Dict[str, Any]:
        """``GET /capabilities``, read at most ONCE per client instance."""
        if self._capabilities_cache is None:
            base = self._validated_base()
            payload, _ = self._request_json(
                base, "GET", "/capabilities", action="能力发现失败",
            )
            if not isinstance(payload, dict):
                raise PixivFlowJobError(
                    "remote_rejected", "PixivFlow 能力声明不是对象", retryable=False,
                )
            self._capabilities_cache = payload
        return self._capabilities_cache

    def _require_protocol_v1(self) -> None:
        """Fail LOUDLY unless the producer serves protocol v1 + this job type.

        There is deliberately NO fallback to the legacy route here: an operator
        who needs it sets ``PIXIVFLOW_JOB_TRANSPORT=legacy`` explicitly, so a
        half-migrated producer can never be talked to by accident.
        """
        capabilities = self._capabilities()
        versions = [str(item) for item in (capabilities.get("protocol_versions") or [])]
        if not any(_is_protocol_v1(item) for item in versions):
            raise PixivFlowJobError(
                "remote_rejected",
                f"PixivFlow 未声明协议版本 {PROTOCOL_VERSION}"
                f"（已声明：{', '.join(versions) or '无'}）",
                retryable=False,
            )
        declared = [
            str(item.get("name") or "")
            for item in (capabilities.get("job_types") or [])
            if isinstance(item, dict)
        ]
        if PROTOCOL_JOB_TYPE not in declared:
            raise PixivFlowJobError(
                "remote_rejected",
                f"PixivFlow 未声明作业类型 {PROTOCOL_JOB_TYPE}"
                f"（已声明：{', '.join(declared) or '无'}）",
                retryable=False,
            )

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
        if self._job_transport == LEGACY_JOB_TRANSPORT:
            return self._legacy_submit(
                job_type, idempotency_key,
                correlation_id=correlation_id, params=params,
            )
        return self._protocol_submit(
            job_type, idempotency_key,
            correlation_id=correlation_id, params=params,
        )

    def get(self, job_id_or_key: str, *, job_type: str = "",
            params: Optional[Dict[str, Any]] = None) -> JobSnapshot:
        """Read one job snapshot (no business interpretation).

        Accepts either identity the protocol exposes: the ``idempotency_key``
        this port submits with, or a real ``job_id``. ``params`` carries the
        opaque scope (``target_id``) the legacy route needs.
        """
        if self._job_transport == LEGACY_JOB_TRANSPORT:
            return self._legacy_get(job_id_or_key, job_type=job_type, params=params)
        return self._protocol_get(job_id_or_key, job_type=job_type, params=params)

    # ---- protocol transport (Workflow Protocol v1) --------------------
    def _protocol_submit(
        self,
        job_type: str,
        idempotency_key: str,
        *,
        correlation_id: str = "",
        params: Optional[Dict[str, Any]] = None,
    ) -> SubmitReceipt:
        base = self._validated_base()
        self._require_protocol_v1()
        target = self._target_of(params)
        if not target:
            raise PixivFlowJobError("remote_rejected", "缺少 target_id", retryable=False)
        body: Dict[str, Any] = {
            "protocol_version": PROTOCOL_VERSION,
            "job_type": protocol_job_type(job_type),
            "idempotency_key": str(idempotency_key),
            "params": {"target_id": target},
        }
        if correlation_id:
            body["correlation_id"] = str(correlation_id)
        # §events: under the protocol transport TelePost declares its OWN events
        # ingress as the Task's callback_url, so the producer's outbox can push
        # each Event to us (at-least-once). This is ACCELERATION, not the only
        # channel — the reconcile loop still pulls — so a missing/unusable base
        # simply omits the field (a producer that does not see it falls back to
        # pull-only, which §events already supports).
        callback = consumer_callback_url()
        if callback:
            body["callback_url"] = callback
        payload, status = self._request_json(
            base, "POST", "/jobs",
            body=body, timeout=SUBMIT_TIMEOUT_SECONDS, action="拒绝重抓",
        )
        receipt = decode_protocol_submit_receipt(payload, status)
        if not receipt.accepted and not receipt.replayed:
            raise PixivFlowJobError(
                self._http_error_code(status),
                f"PixivFlow 拒绝重抓（HTTP {status}）",
                status=status,
                retryable=self._status_retryable(status),
            )
        return receipt

    def _protocol_get(self, job_id_or_key: str, *, job_type: str = "",
                      params: Optional[Dict[str, Any]] = None) -> JobSnapshot:
        base = self._validated_base()
        self._require_protocol_v1()
        token = str(job_id_or_key or "")
        if not token:
            raise PixivFlowJobError("remote_rejected", "缺少 job_id", retryable=False)
        # Primary read: the durable identity this port submits with IS the
        # idempotency key, so a key lookup is the poll's normal path.
        page, _ = self._request_json(
            base, "GET", f"/jobs?idempotency_key={quote(token, safe='')}",
            action="读取重抓状态失败",
        )
        job = _job_from_page(page, idempotency_key=token)
        if job is None:
            # Fallback: the caller passed a real job_id (protocol identity).
            job, _ = self._request_json(
                base, "GET", f"/jobs/{quote(token, safe='')}",
                action="读取重抓状态失败",
            )
        snapshot = decode_protocol_job(job)
        if not snapshot.job_id:
            raise PixivFlowJobError(
                "remote_rejected", "PixivFlow 作业快照缺少 job_id", retryable=False,
            )
        return snapshot

    def _require_protocol(self, feature: str) -> str:
        """Refuse a protocol-only call on the legacy transport.

        ``events``/``ackEvents`` are §events consumer operations that have no
        legacy route; calling them under ``PIXIVFLOW_JOB_TRANSPORT=legacy`` is a
        configuration error, surfaced loudly rather than silently no-op'd.
        """
        if self._job_transport == LEGACY_JOB_TRANSPORT:
            raise PixivFlowJobError(
                "remote_rejected",
                f"{feature} 仅在协议通道可用（请启用 PIXIVFLOW_JOB_TRANSPORT=protocol）",
                retryable=False,
            )
        return self._validated_base()

    def events(self, job_id: str, *, after: str = "",
               unacked: bool = False) -> EventPage:
        """``GET /jobs/{job_id}/events`` — one ascending replay page (protocol only).

        ``after`` is the monotonic cursor (the newest event_id this consumer has
        durably persisted); ``unacked=True`` asks the producer to include events
        we have not yet acknowledged. Failures surface as
        :class:`PixivFlowJobError`; a page with no new events returns an empty
        page, never an error.
        """
        base = self._require_protocol("事件流")
        job_id = str(job_id or "").strip()
        if not job_id:
            raise PixivFlowJobError("remote_rejected", "缺少 job_id", retryable=False)
        query = f"/jobs/{quote(job_id, safe='')}/events"
        params = []
        if str(after or "").strip():
            params.append(f"after={quote(str(after).strip(), safe='')}")
        if unacked:
            params.append("unacked=1")
        if params:
            query = f"{query}?{'&'.join(params)}"
        payload, _ = self._request_json(base, "GET", query, action="读取事件流失败")
        return decode_event_page(payload)

    def ackEvents(self, job_id: str, *, ack_through: str = "") -> AckResult:
        """``POST /jobs/{job_id}/events/ack`` — ack the newest persisted event (protocol only).

        ``ack_through`` is the event_id of the newest Event we durably persisted
        (monotonic). The producer MUST treat an older/unknown cursor as a no-op
        (HTTP 200) and never change job state, so a stale ack here is never an
        error. Returns :class:`AckResult`.
        """
        base = self._require_protocol("事件确认")
        job_id = str(job_id or "").strip()
        if not job_id:
            raise PixivFlowJobError("remote_rejected", "缺少 job_id", retryable=False)
        body: Dict[str, Any] = {"ack_through": str(ack_through or "")}
        payload, _ = self._request_json(
            base, "POST", f"/jobs/{quote(job_id, safe='')}/events/ack",
            body=body, action="确认事件失败",
        )
        return decode_ack_result(payload)

    # ---- legacy transport (rollback switch, unchanged behaviour) ------
    def _legacy_submit(
        self,
        job_type: str,
        idempotency_key: str,
        *,
        correlation_id: str = "",
        params: Optional[Dict[str, Any]] = None,
    ) -> SubmitReceipt:
        """The pre-migration internal route, byte for byte."""
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

    def _legacy_get(self, job_id_or_key: str, *, job_type: str = "",
                    params: Optional[Dict[str, Any]] = None) -> JobSnapshot:
        """The pre-migration internal route, byte for byte."""
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
    "DEFAULT_JOB_TRANSPORT",
    "DEFAULT_TIMEOUT_SECONDS",
    "HttpPixivFlowJobClient",
    "JobSnapshot",
    "LEGACY_JOB_TRANSPORT",
    "PROTOCOL_JOB_TYPE",
    "PROTOCOL_STATUS_REMOTE_STATES",
    "PROTOCOL_VERSION",
    "PixivFlowJobClient",
    "PixivFlowJobError",
    "SubmitReceipt",
    "TERMINAL_REMOTE_STATES",
    "classify_error",
    "decode_job_snapshot",
    "decode_protocol_job",
    "decode_protocol_submit_receipt",
    "decode_submit_receipt",
    "default_job_client",
    "is_terminal_remote_state",
    "job_transport",
    "project_remote_state",
    "protocol_job_type",
    "protocol_retryable_defaults",
    "AckResult",
    "EventPage",
    "ProtocolEvent",
    "TERMINAL_EVENT_TYPES",
    "consumer_callback_url",
    "decode_ack_result",
    "decode_event_page",
    "decode_protocol_event",
]
