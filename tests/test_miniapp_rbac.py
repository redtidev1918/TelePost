"""Mini App RBAC tests (§59): deterministic role matrix against real endpoints.

submitter  : submit yes, own submission yes, other submission no, queue no, approve no
reviewer   : queue yes, approve/reject/refetch yes
admin      : reviewer permissions plus admin-flagged capabilities
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.security


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    # config.settings reads env at process import (conftest already set
    # TOKEN/CHANNEL_ID/OWNER_ID). Patch the rbac module's imported identity
    # directly so each test sees the exact reviewer allowlist it asserts.
    from telepost.miniapp import rbac
    monkeypatch.setattr(rbac, "OWNER_ID", 100)
    monkeypatch.setattr(rbac, "ADMIN_IDS", [100, 200])
    monkeypatch.setenv("MINIAPP_SESSION_SECRET", "s" * 40)


def _roles(uid: int):
    from telepost.miniapp import rbac
    return rbac.roles_for(uid)


def test_submitter_matrix():
    from telepost.miniapp import rbac
    roles = _roles(1)  # not in ADMIN_IDS/OWNER
    assert rbac.can_submit(roles) is True
    assert rbac.can_view_own_submissions(roles) is True
    assert rbac.can_view_review_queue(roles) is False
    assert rbac.can_view_review_detail(roles) is False
    assert rbac.can_approve(roles) is False
    assert rbac.can_reject(roles) is False
    assert rbac.can_set_spoiler(roles) is False
    assert rbac.can_refetch(roles) is False


def test_reviewer_matrix():
    from telepost.miniapp import rbac
    roles = _roles(200)  # ADMIN_IDS member
    assert rbac.can_submit(roles) is True
    assert rbac.can_view_review_queue(roles) is True
    assert rbac.can_view_review_detail(roles) is True
    assert rbac.can_approve(roles) is True
    assert rbac.can_reject(roles) is True
    assert rbac.can_set_spoiler(roles) is True
    assert rbac.can_refetch(roles) is True


def test_admin_matrix():
    from telepost.miniapp import rbac
    roles = _roles(100)  # OWNER_ID
    for fn in (
        rbac.can_submit,
        rbac.can_view_own_submissions,
        rbac.can_view_review_queue,
        rbac.can_view_review_detail,
        rbac.can_approve,
        rbac.can_reject,
        rbac.can_set_spoiler,
        rbac.can_refetch,
    ):
        assert fn(roles) is True


def test_reviewer_source_is_shared():
    """Mini App must consume the SAME reviewer identity as the Bot (§15)."""
    from telepost.miniapp import rbac
    assert rbac.reviewer_ids() == {100, 200}


def test_no_identity_has_no_roles():
    from telepost.miniapp import rbac
    assert rbac.roles_for(None) == []
    assert rbac.roles_for(0) == []
    assert rbac.roles_for(-1) == []

def test_role_binding_read_failure_degrades_to_env(monkeypatch):
    """A broken role_bindings DB must not fail role derivation (503/500)."""
    from telepost.miniapp import rbac, role_bindings
    import sqlite3

    def _boom():
        raise sqlite3.OperationalError("readonly database")

    monkeypatch.setattr(role_bindings, "_connect", _boom)
    assert rbac.roles_for(1) == ["submitter"]
    assert rbac.roles_for(200) == ["submitter", "reviewer"]  # env baseline kept
    assert rbac.roles_for(100) == ["submitter", "reviewer", "admin"]
