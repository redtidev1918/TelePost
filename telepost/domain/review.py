"""Review domain: canonical status + centralised transition rules.

No SQLite, no PTB. The repository is the only component allowed to persist a
status change, and it must do so with a conditional UPDATE; this module is the
single source of truth for *which* change is legal.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class ReviewStatus(str, Enum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    DELETED = "deleted"  # channel post soft-deleted after publication

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# Statuses from which a (possibly stale) claim to ``publishing`` is permitted.
CLAIMABLE = frozenset({ReviewStatus.PENDING, ReviewStatus.FAILED})

# A review that may still be edited by a reviewer.
EDITABLE = frozenset({ReviewStatus.PENDING, ReviewStatus.FAILED})

# Terminal statuses that must never move back to ``publishing``.
TERMINAL = frozenset(
    {ReviewStatus.PUBLISHED, ReviewStatus.REJECTED, ReviewStatus.EXPIRED, ReviewStatus.DELETED}
)


class ReviewTransitionError(ValueError):
    """A caller attempted an illegal review state transition."""

    def __init__(self, current: ReviewStatus, target: ReviewStatus):
        self.current = current
        self.target = target
        super().__init__(f"illegal review transition: {current.value} -> {target.value}")


def can_claim(current: ReviewStatus, *, stale: bool = False) -> bool:
    """Whether a reviewer action may move the row into ``publishing``.

    A ``publishing`` row is reclaimable only when flagged stale (crash recovery);
    an already published row short-circuits as an idempotent success rather than
    raising (handled by the service, not the state machine).
    """
    if current in CLAIMABLE:
        return True
    if current is ReviewStatus.PUBLISHING and stale:
        return True
    return False


def assert_can_claim(current: ReviewStatus, *, stale: bool = False) -> None:
    if not can_claim(current, stale=stale):
        raise ReviewTransitionError(current, ReviewStatus.PUBLISHING)


def can_reject(current: ReviewStatus) -> bool:
    return current in EDITABLE


def can_edit(current: ReviewStatus) -> bool:
    return current in EDITABLE


def next_after_outcome(success: bool) -> ReviewStatus:
    return ReviewStatus.PUBLISHED if success else ReviewStatus.FAILED
