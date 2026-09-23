"""Automation task storage: SQLite CRUD and durable run ledger.

Two tables:
- ``automation_tasks``: persistent rule SSOT. JobQueue is a runtime projection.
- ``automation_runs``: (task_id, occurrence_at) UNIQUE — one send per occurrence.
"""
from __future__ import annotations

import json
import logging
import time
from typing import List, Optional

from database.db_manager import get_db
from telepost.domain.automation import AutomationTask, parse_automation_payload

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS automation_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    schedule_type TEXT NOT NULL DEFAULT 'weekly',
    schedule_day INTEGER NOT NULL DEFAULT 6,
    schedule_hour INTEGER NOT NULL DEFAULT 20,
    schedule_minute INTEGER NOT NULL DEFAULT 0,
    action_type TEXT NOT NULL DEFAULT 'hot',
    action_payload TEXT NOT NULL DEFAULT '{}',
    target_chat_id TEXT NOT NULL DEFAULT '',
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    created_by INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0,
    last_run_at REAL,
    last_run_status TEXT,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS automation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES automation_tasks(id),
    occurrence_at REAL NOT NULL,
    trigger TEXT NOT NULL DEFAULT 'scheduled',
    status TEXT NOT NULL DEFAULT 'running',
    error TEXT,
    started_at REAL NOT NULL DEFAULT 0,
    finished_at REAL,
    UNIQUE(task_id, occurrence_at)
);
"""


async def ensure_tables() -> None:
    async with get_db() as conn:
        await conn.executescript(_SCHEMA)
        await conn.commit()


def _row_to_task(row) -> AutomationTask:
    return AutomationTask(
        id=row["id"],
        name=row["name"],
        enabled=bool(row["enabled"]),
        schedule_type=row["schedule_type"],
        schedule_day=row["schedule_day"],
        schedule_hour=row["schedule_hour"],
        schedule_minute=row["schedule_minute"],
        action_type=row["action_type"],
        action_payload=parse_automation_payload(row["action_payload"]),
        target_chat_id=row["target_chat_id"],
        timezone=row["timezone"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_run_at=row["last_run_at"],
        last_run_status=row["last_run_status"],
        last_error=row["last_error"],
    )


async def create_task(task: AutomationTask) -> AutomationTask:
    now = time.time()
    async with get_db() as conn:
        cursor = await conn.execute(
            """INSERT INTO automation_tasks
               (name, enabled, schedule_type, schedule_day, schedule_hour,
                schedule_minute, action_type, action_payload, target_chat_id,
                timezone, created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task.name, int(task.enabled), task.schedule_type,
                task.schedule_day, task.schedule_hour, task.schedule_minute,
                task.action_type, json.dumps(task.action_payload),
                task.target_chat_id, task.timezone, task.created_by,
                now, now,
            ),
        )
        task_id = cursor.lastrowid
        await conn.commit()
    task.id = task_id
    task.created_at = now
    task.updated_at = now
    return task


async def list_tasks() -> List[AutomationTask]:
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT * FROM automation_tasks ORDER BY id"
        )
        rows = await cursor.fetchall()
    return [_row_to_task(r) for r in rows]


async def get_task(task_id: int) -> Optional[AutomationTask]:
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT * FROM automation_tasks WHERE id = ?", (task_id,)
        )
        row = await cursor.fetchone()
    return _row_to_task(row) if row else None


async def set_enabled(task_id: int, enabled: bool) -> bool:
    async with get_db() as conn:
        cursor = await conn.execute(
            "UPDATE automation_tasks SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(enabled), time.time(), task_id),
        )
        await conn.commit()
        return cursor.rowcount > 0


async def delete_task(task_id: int) -> bool:
    async with get_db() as conn:
        cursor = await conn.execute(
            "DELETE FROM automation_tasks WHERE id = ?", (task_id,)
        )
        await conn.commit()
        return cursor.rowcount > 0


async def update_run_result(
    task_id: int, occurrence_at: float, status: str, error: Optional[str] = None
) -> None:
    now = time.time()
    async with get_db() as conn:
        await conn.execute(
            """UPDATE automation_tasks
               SET last_run_at = ?, last_run_status = ?, last_error = ?, updated_at = ?
               WHERE id = ?""",
            (occurrence_at, status, error, now, task_id),
        )
        await conn.commit()


async def record_run_start(
    task_id: int, occurrence_at: float, trigger: str
) -> bool:
    """Insert a run record; return False if (task_id, occurrence_at) already exists."""
    async with get_db() as conn:
        try:
            await conn.execute(
                """INSERT INTO automation_runs
                   (task_id, occurrence_at, trigger, status, started_at)
                   VALUES (?,?,?,?,?)""",
                (task_id, occurrence_at, trigger, "running", time.time()),
            )
            await conn.commit()
            return True
        except Exception:
            return False


async def record_run_finish(
    task_id: int, occurrence_at: float, status: str, error: Optional[str] = None
) -> None:
    async with get_db() as conn:
        await conn.execute(
            """UPDATE automation_runs
               SET status = ?, error = ?, finished_at = ?
               WHERE task_id = ? AND occurrence_at = ?""",
            (status, error, time.time(), task_id, occurrence_at),
        )
        await conn.commit()
