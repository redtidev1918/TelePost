"""Mini App RBAC (§13-§15): single server-side source of truth.

Roles are derived from the SAME reviewer identity the Telegram Bot already
uses — ``ADMIN_IDS`` and ``OWNER_ID`` in ``config.settings``. There is exactly
one reviewer list for the whole service; the Mini App never carries its own
allowlist (no Bot handler list vs Mini App API list split, §15).

* ``submitter``  — any server-verified Telegram user: submit + own submissions.
* ``reviewer``   — ADMIN_IDS (or the owner): review queue + mutations.
* ``admin``      — OWNER_ID: everything a reviewer can do (and only operations
                   the product actually exposes today; no invented powers).

The permission check entries are centralized here so endpoints never scatter
role logic (§83). The RBAC decision is about IDENTITY + ROLE only. Extra
ownership/state checks (own-submission filtering, review access) live in the
API layer / services.
"""
from __future__ import annotations

from typing import List, Optional, Set

from config.settings import ADMIN_IDS, OWNER_ID
from telepost.miniapp import role_bindings

ROLE_SUBMITTER = "submitter"
ROLE_REVIEWER = "reviewer"
ROLE_ADMIN = "admin"


def _owner_ids() -> Set[int]:
    if OWNER_ID is None:
        return set()
    return {int(OWNER_ID)}


def reviewer_ids() -> Set[int]:
    """The single authoritative reviewer identity (Bot + Mini App both use it)."""
    ids = _owner_ids()
    ids.update(int(value) for value in ADMIN_IDS if isinstance(value, int))
    return ids


def roles_for(telegram_user_id: Optional[int]) -> List[str]:
    """Roles for a server-verified Telegram user id (always ≥ submitter)."""
    if not telegram_user_id or int(telegram_user_id) <= 0:
        return []
    uid = int(telegram_user_id)
    roles = [ROLE_SUBMITTER]
    if uid in reviewer_ids():
        roles.append(ROLE_REVIEWER)
    if uid in _owner_ids():
        roles.append(ROLE_ADMIN)
    # Durable Role Bindings (Admin Control Plane) add roles on top of the env
    # baseline. This lets an operator grant reviewer/admin without editing Fly
    # secrets; OWNER_ID break-glass stays always-admin regardless.
    for bound_role in role_bindings.bound_roles(uid):
        if bound_role not in roles:
            roles.append(bound_role)
    return roles


# ---- centralized permission decisions (endpoints call these, §83) --------

def can_submit(roles: List[str]) -> bool:
    return ROLE_SUBMITTER in roles


def can_view_own_submissions(roles: List[str]) -> bool:
    return ROLE_SUBMITTER in roles


def can_review(roles: List[str]) -> bool:
    return ROLE_REVIEWER in roles or ROLE_ADMIN in roles


def can_approve(roles: List[str]) -> bool:
    return can_review(roles)


def can_reject(roles: List[str]) -> bool:
    return can_review(roles)


def can_set_spoiler(roles: List[str]) -> bool:
    return can_review(roles)


def can_refetch(roles: List[str]) -> bool:
    return can_review(roles)


def can_view_review_detail(roles: List[str]) -> bool:
    return can_review(roles)


def can_view_review_queue(roles: List[str]) -> bool:
    return can_review(roles)


def can_administer(roles: List[str]) -> bool:
    """Admin-only operational surface (Bot status/policy/moderation, §admin)."""
    return ROLE_ADMIN in roles