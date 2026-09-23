"""Automation application service: sync DB rules to JobQueue, execute tasks.

DB is the persistent SSOT. JobQueue is the runtime projection. Every
add/enable/disable/delete re-syncs; restarts re-sync on startup. Idempotency
comes from the ``automation_runs`` table (task_id, occurrence_at UNIQUE).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone as dt_timezone
from typing import Any, Dict, List, Optional

from telegram.ext import CallbackContext, ContextTypes

from telepost.domain.automation import ACTION_HOT, AutomationTask
from telepost.domain.hot import HotQuery, week_start_utc, week_range_label
from telepost.storage.sqlite import automation as automation_store

logger = logging.getLogger(__name__)

# Track registered jobs so we can cancel them before re-registering.
_registered_job_names: set = set()


def _job_name(task_id: int) -> str:
    return f"automation_{task_id}"


async def sync_jobqueue(application) -> int:
    """Load all enabled tasks from DB and (re)register them in JobQueue.

    Cancels all previously registered automation jobs first, then registers
    only currently enabled tasks. Returns the number of registered jobs.
    """
    job_queue = application.job_queue
    for name in list(_registered_job_names):
        for job in job_queue.jobs():
            if job.name == name:
                job.schedule_removal()
        _registered_job_names.discard(name)

    await automation_store.ensure_tables()
    tasks = await automation_store.list_tasks()
    registered = 0
    now = datetime.now()
    for task in tasks:
        if not task.enabled:
            continue
        next_run = task.next_run_at(now)
        if next_run is None:
            continue
        delay = max(0.0, next_run - time.time())
        name = _job_name(task.id)
        job_queue.run_once(
            _run_scheduled_task,
            when=delay,
            name=name,
            data={"task_id": task.id, "occurrence_at": next_run},
        )
        _registered_job_names.add(name)
        registered += 1
    logger.info("automation: %d/%d tasks registered in JobQueue", registered, len(tasks))
    return registered


async def _run_scheduled_task(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data or {}
    task_id = data.get("task_id")
    occurrence_at = data.get("occurrence_at", 0.0)
    if not task_id:
        return
    task = await automation_store.get_task(task_id)
    if not task or not task.enabled:
        return
    await execute_task(task, context.bot, occurrence_at, trigger="scheduled")


async def execute_task(
    task: AutomationTask,
    bot,
    occurrence_at: float,
    *,
    trigger: str = "scheduled",
) -> tuple:
    """Execute one task occurrence. Returns (status, error_message).

    Idempotent: if (task_id, occurrence_at) already recorded, skip entirely.
    """
    # For manual runs, use a unique occurrence key so re-runs work.
    if trigger == "manual":
        occurrence_at = time.time()
    claimed = await automation_store.record_run_start(task.id, occurrence_at, trigger)
    if not claimed:
        logger.info("automation: task %s occurrence %s already claimed, skipping", task.id, occurrence_at)
        return ("skipped", None)
    try:
        if task.action_type == ACTION_HOT:
            await _execute_hot(task, bot)
        else:
            raise ValueError(f"不支持的任务类型：{task.action_type}")
        await automation_store.record_run_finish(task.id, occurrence_at, "success")
        await automation_store.update_run_result(task.id, occurrence_at, "success")
        return ("success", None)
    except Exception as exc:
        error_msg = str(exc)[:200] if str(exc) else type(exc).__name__
        logger.error("automation task %s failed: %s", task.id, error_msg)
        await automation_store.record_run_finish(task.id, occurrence_at, "failed", error_msg)
        await automation_store.update_run_result(task.id, occurrence_at, "failed", error_msg)
        return ("failed", error_msg)


async def _execute_hot(task: AutomationTask, bot) -> None:
    """Build the hot list message and send it to the task's target chat."""
    payload = task.action_payload or {}
    scope = payload.get("scope", "all")
    limit = payload.get("limit", 10)
    query = HotQuery(scope=scope, limit=min(max(int(limit), 1), 50))
    from handlers.stats_handlers import build_hot_message
    text = await build_hot_message(query)
    target = task.target_chat_id
    if not target:
        raise ValueError("目标 chat 未配置")
    await bot.send_message(
        chat_id=target, text=text, parse_mode="HTML", disable_web_page_preview=True
    )


async def manual_run(task: AutomationTask, bot) -> tuple:
    """Run a task immediately (manual trigger)."""
    return await execute_task(task, bot, trigger="manual")
