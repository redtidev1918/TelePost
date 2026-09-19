"""Durable Role Bindings for the Admin Control Plane.

One server-side store (SQLite) that grants ``reviewer`` / ``admin`` to a
Telegram user independently of ``OWNER_ID`` / ``ADMIN_IDS`` env. Env roles stay
the bootstrap baseline (production compat); bindings ADD roles on top so an
operator can grant access without editing fly secrets. The break-glass
``OWNER_ID`` remains always-admin regardless of bindings.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from typing import Dict, List


VALID_ROLES = frozenset({"reviewer", "admin"})
PRINCIPAL_TELEGRAM = "telegram"

_lock = threading.RLock()


def _connect() -> sqlite3.Connection:
    # Read through db_manager so test fixtures can monkeypatch DB_PATH like the
    # rest of the app; this is the single source of the SQLite path.
    from database import db_manager
    return sqlite3.connect(db_manager.DB_PATH, timeout=10)


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS role_bindings (
            principal_kind TEXT NOT NULL,
            principal_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            PRIMARY KEY (principal_kind, principal_id, role)
        )
        """
    )


def bound_roles(telegram_user_id: int) -> List[str]:
    """Roles granted through bindings for a Telegram user (sync, tiny query)."""
    with _lock:
        conn = _connect()
        try:
            _ensure_table(conn)
            rows = conn.execute(
                "SELECT role FROM role_bindings "
                "WHERE principal_kind=? AND principal_id=?",
                (PRINCIPAL_TELEGRAM, int(telegram_user_id)),
            ).fetchall()
            return sorted(r[0] for r in rows if r[0] in VALID_ROLES)
        finally:
            conn.close()


def list_bindings() -> List[Dict]:
    with _lock:
        conn = _connect()
        try:
            _ensure_table(conn)
            rows = conn.execute(
                "SELECT principal_id, role, created_by, created_at "
                "FROM role_bindings WHERE principal_kind=? "
                "ORDER BY principal_id, role",
                (PRINCIPAL_TELEGRAM,),
            ).fetchall()
            return [
                {
                    "telegram_user_id": int(r[0]),
                    "role": r[1],
                    "created_by": r[2],
                    "created_at": r[3],
                }
                for r in rows if r[1] in VALID_ROLES
            ]
        finally:
            conn.close()


def add_binding(telegram_user_id: int, role: str, *, created_by: str = "") -> bool:
    """Insert a role binding; returns False when it already existed."""
    if role not in VALID_ROLES:
        raise ValueError(f"unsupported role: {role}")
    if int(telegram_user_id) <= 0:
        raise ValueError("telegram_user_id must be positive")
    with _lock:
        conn = _connect()
        try:
            _ensure_table(conn)
            cur = conn.execute(
                "INSERT OR IGNORE INTO role_bindings "
                "(principal_kind, principal_id, role, created_by, created_at) "
                "VALUES (?,?,?,?,?)",
                (PRINCIPAL_TELEGRAM, int(telegram_user_id), role,
                 (created_by or "")[:200], time.time()),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def remove_binding(telegram_user_id: int, role: str) -> bool:
    if role not in VALID_ROLES:
        raise ValueError(f"unsupported role: {role}")
    with _lock:
        conn = _connect()
        try:
            _ensure_table(conn)
            cur = conn.execute(
                "DELETE FROM role_bindings "
                "WHERE principal_kind=? AND principal_id=? AND role=?",
                (PRINCIPAL_TELEGRAM, int(telegram_user_id), role),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
