"""Serial, DB-driven Update processing for webhook mode (§durable-inbox).

The HTTP handler only validates and persists; this worker owns processing:

    claim oldest due row → process_update() → flush persistence → mark done

Ordering is ``update_id ASC`` (Telegram's delivery order), one row at a
time per bot process — the same serial semantics PTB's internal queue had.

Completion is deliberately conservative: a row is marked done only after
``Application.process_update`` returned AND the conversation state has been
flushed to disk (``update_persistence``; PicklePersistence writes the file
synchronously on changed state). PTB routes handler exceptions to the
registered error handler and returns normally — that is unchanged; the
durable business layers (submission rows, review queue, publication ledger)
carry their own recovery. What this worker adds is the crash boundary: a
process death between the 200 and the done mark replays the update exactly
once more, and the media-append journal (§submission epoch dedup) keeps
that replay from duplicating content.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from telegram import Update

from telepost.storage.sqlite.inbox import InboxRepository

logger = logging.getLogger(__name__)


class UpdateInboxWorker:
    def __init__(self, application, *, repo: Optional[InboxRepository] = None,
                 lease_seconds: float = 600.0, max_attempts: int = 5,
                 poll_interval: float = 5.0):
        self._application = application
        self._repo = repo or InboxRepository()
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._poll_interval = poll_interval
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    # ---- receipt -------------------------------------------------------
    async def receive(self, update_id: int, payload: dict) -> bool:
        """Durable receipt (called from the webhook HTTP handler).

        Returns True when this delivery created the row; duplicates of known
        updates (pending or done) return False and must not requeue work."""
        return await self._repo.receive(update_id, json.dumps(payload))

    def wake(self) -> None:
        self._wake.set()

    # ---- lifecycle -----------------------------------------------------
    async def recover(self, *, now: Optional[float] = None) -> int:
        """Reclaim rows a dead process left in-flight. Run once at startup,
        before the worker starts and before /ready."""
        now = time.time() if now is None else now
        recovered = await self._repo.recover_stale(now=now)
        if recovered:
            logger.info("启动恢复：%d 条未完成的 Telegram Update 已重新排队（按 update_id 顺序）",
                        recovered)
        return recovered

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="update-inbox-worker")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # ---- processing ----------------------------------------------------
    async def _run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self._poll_interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            try:
                await self.drain()
            except asyncio.CancelledError:
                raise
            except Exception:
                # The loop itself must never die: one bad drain round is
                # logged and retried on the next wake/interval.
                logger.exception("Update inbox 处理轮次失败，将在下轮重试")

    async def drain(self, *, now: Optional[float] = None) -> int:
        """Process every currently due row, oldest first. Returns the number
        of rows processed (tests and recovery drive this directly)."""
        now = time.time() if now is None else now
        processed = 0
        while True:
            row = await self._repo.claim_next_due(
                now=now, lease_seconds=self._lease_seconds)
            if row is None:
                return processed
            await self._process(row, now=now)
            processed += 1
            now = time.time()

    async def _process(self, row: dict, *, now: float) -> None:
        update_id = row["update_id"]
        try:
            update = Update.de_json(json.loads(row["payload"]),
                                    self._application.bot)
        except Exception as exc:
            await self._repo.mark_dead(
                update_id, now=now, error=type(exc).__name__)
            logger.warning("Inbox 载荷无法重建 Update，已标记 dead: update_id=%s error=%s",
                           update_id, type(exc).__name__)
            return
        if update is None or update.update_id is None:
            await self._repo.mark_dead(
                update_id, now=now, error="unparsable_payload")
            return
        # de_json attaches the bot to the Update's top level only; set_bot
        # propagates it so message.reply_text / query.answer work in handlers.
        update.set_bot(self._application.bot)
        try:
            await self._application.process_update(update)
            # Flush conversation state to disk BEFORE the done mark: a crash
            # after this point needs no replay; a crash before it replays an
            # update whose business writes are journalled and deduped.
            if getattr(self._application, "persistence", None):
                await self._application.update_persistence()
            await self._repo.mark_done(update_id, now=now)
        except Exception as exc:
            await self._repo.mark_failed(
                update_id, now=now, error=type(exc).__name__,
                max_attempts=self._max_attempts)
            logger.warning("Update 处理失败，按退避重试: update_id=%s error_class=%s "
                           "attempts=%s", update_id, type(exc).__name__,
                           row["attempts"] + 1)
