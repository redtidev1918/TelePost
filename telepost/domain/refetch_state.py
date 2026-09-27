"""Canonical refetch job state machine (architecture programme, P0).

Before this module the refetch attempt state was a free-form string written from
five places (two of them bypassing the repository), so no migration table, no
illegal-transition guard and no per-attempt timeline existed. This module is the
single authority for:

* the **canonical state names** of a refetch job;
* the **legal transitions** between them (``ALLOWED`` + :func:`assert_transition`);
* the **legacy mapping** in both directions, so databases written by older
  TelePost versions keep working (``admitted`` → :data:`SEARCHING`,
  ``no_alternative`` → :data:`NO_CANDIDATE`, ``obsolete`` → :data:`CANCELLED`)
  and API/Mini-App consumers that still speak the old vocabulary keep working
  (:func:`to_legacy`).

Lifecycle (user-visible progress is projected from it)::

    REQUESTED ─→ SEARCHING ─→ FILTERING ─→ CANDIDATE_FOUND ─→ REPLACED
         └──────────┴────────────┴──────────────┴──→ FAILED / TIMEOUT /
                                                     NO_CANDIDATE / CANCELLED

* ``REQUESTED``        — the attempt is durable in TelePost, not yet accepted by PixivFlow;
* ``SEARCHING``        — PixivFlow accepted the request (durable slot exists) and is searching;
* ``FILTERING``        — candidates are being filtered (already-seen / duplicate / invalid);
* ``CANDIDATE_FOUND``  — a candidate exists and the replacement review is being staged;
* ``REPLACED``         — terminal success: the replacement is installed as the chain head;
* ``FAILED``           — terminal failure with a reason (``failure_code``);
* ``TIMEOUT``          — terminal local-clock outcome (admission/stall) — never a silent wait;
* ``NO_CANDIDATE``     — terminal "PixivFlow found nothing usable";
* ``CANCELLED``        — the attempt became moot (the reviewer already decided, or the
                         operator cancelled). Nothing is superseded.

Terminal states are final: a terminal attempt never re-enters the normal flow
(see :data:`ALLOWED`). A retry after a terminal state is a NEW attempt (new
``generation``) on the same chain.
"""
from __future__ import annotations

import re

from typing import Dict, Iterable, Tuple

REQUESTED = "requested"
SEARCHING = "searching"
FILTERING = "filtering"
CANDIDATE_FOUND = "candidate_found"
REPLACED = "replaced"
FAILED = "failed"
TIMEOUT = "timeout"
NO_CANDIDATE = "no_candidate"
CANCELLED = "cancelled"

ACTIVE_STATES: Tuple[str, ...] = (REQUESTED, SEARCHING, FILTERING, CANDIDATE_FOUND)
TERMINAL_STATES: Tuple[str, ...] = (
    REPLACED, FAILED, TIMEOUT, NO_CANDIDATE, CANCELLED,
)
ALL_STATES: Tuple[str, ...] = ACTIVE_STATES + TERMINAL_STATES

#: Vocabulary written by TelePost <= 2.68.x (still present in older databases
#: and in the Mini App / OpenAPI schema).
LEGACY_NAMES: Tuple[str, ...] = (
    "requested", "admitted", "replaced", "no_alternative", "failed", "obsolete",
)

#: legacy name → canonical state
_FROM_LEGACY: Dict[str, str] = {
    "requested": REQUESTED,
    "admitted": SEARCHING,
    "replaced": REPLACED,
    "no_alternative": NO_CANDIDATE,
    "failed": FAILED,
    "obsolete": CANCELLED,
}

#: canonical state → the legacy name exported to old consumers
_TO_LEGACY: Dict[str, str] = {
    REQUESTED: "requested",
    SEARCHING: "admitted",
    FILTERING: "admitted",
    CANDIDATE_FOUND: "admitted",
    REPLACED: "replaced",
    NO_CANDIDATE: "no_alternative",
    FAILED: "failed",
    TIMEOUT: "failed",
    CANCELLED: "obsolete",
}

#: The single transition table. Every active state may reach every terminal
#: state (a job can fail, time out, find nothing or become moot at any point),
#: and may advance forward along the pipeline. Terminal states have no outgoing
#: edge — "no downgrade, no resurrection".
ALLOWED: Dict[str, Tuple[str, ...]] = {
    REQUESTED: (
        SEARCHING, FILTERING, CANDIDATE_FOUND,
        REPLACED, FAILED, TIMEOUT, NO_CANDIDATE, CANCELLED,
    ),
    SEARCHING: (
        FILTERING, CANDIDATE_FOUND,
        REPLACED, FAILED, TIMEOUT, NO_CANDIDATE, CANCELLED,
    ),
    FILTERING: (
        CANDIDATE_FOUND,
        REPLACED, FAILED, TIMEOUT, NO_CANDIDATE, CANCELLED,
    ),
    CANDIDATE_FOUND: (
        REPLACED, FAILED, TIMEOUT, NO_CANDIDATE, CANCELLED,
    ),
    REPLACED: (),
    FAILED: (),
    TIMEOUT: (),
    NO_CANDIDATE: (),
    CANCELLED: (),
}

#: Human labels for the review card / doctor output (system language: Chinese).
STATE_LABELS: Dict[str, str] = {
    REQUESTED: "已提交重抓请求",
    SEARCHING: "搜索 Pixiv 候选中",
    FILTERING: "筛选中（排除已看过/重复作品）",
    CANDIDATE_FOUND: "已找到候选，正在准备替换稿",
    REPLACED: "已替换为新候选",
    FAILED: "重抓失败",
    TIMEOUT: "重抓超时未完成",
    NO_CANDIDATE: "没有找到可替换的作品",
    CANCELLED: "重抓已取消",
}


#: PixivFlow's durable slot-cell state → the stage it proves.
#:
#: PixivFlow is the execution owner and reports the cell state through
#: ``GET /internal/targets/{target}/refetch/{request_id}`` (values:
#: ``pending | selected | artifact_ready | delivery_pending | submitted |
#: no_candidate | duplicate | failed``). TelePost must never invent progress it
#: cannot observe, so the projection is exactly this monotone mapping:
#:
#: * ``pending``                            — the slot exists, nothing chosen yet → SEARCHING;
#: * ``selected``                           — a work was picked and is being filtered
#:                                            (already-seen / duplicate / invalid) → FILTERING;
#: * ``artifact_ready`` / ``delivery_pending`` — a viable candidate exists and the
#:                                            replacement is being staged → CANDIDATE_FOUND.
#:
#: Terminal cell states (``no_candidate`` / ``duplicate`` / ``failed`` /
#: ``submitted``) are handled by the caller as outcomes, not as stages.
REMOTE_CELL_STAGES: Dict[str, str] = {
    "pending": SEARCHING,
    "selected": FILTERING,
    "artifact_ready": CANDIDATE_FOUND,
    "delivery_pending": CANDIDATE_FOUND,
}


def stage_for_remote_state(remote_state: str) -> str:
    """Map a PixivFlow cell state to a refetch stage ('' when not a stage)."""
    return REMOTE_CELL_STAGES.get(str(remote_state or "").strip().lower(), "")


#: The closed shape of ``refetch_attempts.failure_code``: a lowercase
#: code-shaped token. The column is a REASON CODE (doctor 24h failure
#: composition, Mini App state view), never free text — a real production row
#: held a 220-char multi-line nginx 502 HTML page in it.
FAILURE_CODE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
_FAILURE_CODE_RE = re.compile(FAILURE_CODE_PATTERN)

#: Code written when a remote reason is not code-shaped at all. Chosen so it can
#: never collide with a local watchdog code or with
#: ``doctor.LEGACY_REFETCH_FAILURE_CODES``.
REMOTE_FAILURE_CODE = "remote_failure"

#: ``terminal_reason`` is a bounded, single-line, human-readable message.
TERMINAL_REASON_LIMIT = 200

_HTML_TAG_RE = re.compile(r"<[^>]*>")
_WHITESPACE_RUN_RE = re.compile(r"\s+")


def sanitize_terminal_reason(raw, *, limit: int = TERMINAL_REASON_LIMIT) -> str:
    """Bound a free-form reason into a single-line human message (never raises).

    ``terminal_reason`` is read by the Mini App and by support; what reached it
    in production was the raw upstream HTTP body (HTML, CRLF, hundreds of
    chars). This strips HTML tags, drops CR/LF, collapses every run of
    whitespace to one space, strips the ends and truncates to ``limit``.

    Returns ``''`` for anything that is not a non-empty string after cleaning,
    so the caller can fall back to the machine state name.
    """
    if not isinstance(raw, str):
        return ""
    text = _WHITESPACE_RUN_RE.sub(" ", _HTML_TAG_RE.sub(" ", raw)).strip()
    if not text:
        return ""
    try:
        bounded = int(limit)
    except (TypeError, ValueError):
        return text
    if bounded >= 0 and len(text) > bounded:
        text = text[:bounded].strip()
    return text


def normalize_failure_code(raw) -> str:
    """Return a code-shaped failure code — never free text, never raises.

    ``failure_code`` is a closed-vocabulary CODE column, but a remote peer (or
    the legacy PixivFlow shim, which answers errors with a plain human string)
    can hand us anything. Only a token matching :data:`FAILURE_CODE_PATTERN`
    survives; everything else (including empty/None) becomes
    :data:`REMOTE_FAILURE_CODE`.
    """
    if isinstance(raw, str):
        text = raw.strip()
        if _FAILURE_CODE_RE.match(text):
            return text
    return REMOTE_FAILURE_CODE


class IllegalRefetchTransition(ValueError):
    """Raised when a caller tries to move a refetch attempt illegally."""

    def __init__(self, from_state: str, to_state: str):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"illegal refetch transition: {from_state!r} → {to_state!r}"
        )


def normalize(state: str) -> str:
    """Return the canonical state name for ``state``.

    Accepts canonical names, legacy names and ``None``/empty (→ :data:`REQUESTED`
    because that is what a freshly created attempt is). Unknown values are
    returned unchanged so callers can report them instead of silently guessing.
    """
    if not state:
        return REQUESTED
    text = str(state).strip().lower()
    if text in _FROM_LEGACY:
        return _FROM_LEGACY[text]
    return text


#: Explicit alias used where the *source* is known to be an old database row.
from_legacy = normalize


def to_legacy(state: str) -> str:
    """Map a canonical state back to the legacy vocabulary (API compatibility)."""
    canonical = normalize(state)
    return _TO_LEGACY.get(canonical, str(state or ""))


def is_active(state: str) -> bool:
    return normalize(state) in ACTIVE_STATES


def is_terminal(state: str) -> bool:
    return normalize(state) in TERMINAL_STATES


def label(state: str, *, fallback: str = "") -> str:
    return STATE_LABELS.get(normalize(state), fallback or str(state or ""))


def allowed_from(state: str) -> Tuple[str, ...]:
    return ALLOWED.get(normalize(state), ())


def can_transition(from_state: str, to_state: str) -> bool:
    """True when ``from_state → to_state`` is legal.

    An idempotent re-application of the same state is legal (webhook replays and
    retries must not explode); everything else is checked against ``ALLOWED``.
    """
    source = normalize(from_state)
    target = normalize(to_state)
    if target not in ALL_STATES:
        return False
    if source == target:
        return True
    return target in ALLOWED.get(source, ())


def assert_transition(from_state: str, to_state: str) -> str:
    """Validate a transition and return the canonical target state.

    Raises :class:`IllegalRefetchTransition` when the move is not allowed, with
    both states in the canonical vocabulary so the message is greppable.
    """
    if not can_transition(from_state, to_state):
        raise IllegalRefetchTransition(normalize(from_state), normalize(to_state))
    return normalize(to_state)


def sql_state_list(states: Iterable[str] = ACTIVE_STATES) -> str:
    """Render a tuple of canonical states as a SQL ``IN`` list literal.

    Used by the repository so the active/terminal sets exist in exactly one
    place (no more hand-written ``'requested','admitted'`` literals).
    """
    return ", ".join(f"'{normalize(state)}'" for state in states)


def legacy_aliases(states: Iterable[str] = ACTIVE_STATES) -> Tuple[str, ...]:
    """Legacy names that normalize into ``states``.

    The startup migration rewrites old rows in place, but a process that opens a
    database created by an older release (or a row written by hand) must still
    find its attempt: SQL queries therefore widen their ``IN`` list with these
    aliases instead of trusting the canonical column value alone.
    """
    wanted = {normalize(state) for state in states}
    return tuple(
        name for name, target in _FROM_LEGACY.items() if target in wanted
    )
