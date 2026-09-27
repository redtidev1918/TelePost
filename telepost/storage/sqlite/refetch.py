"""SQLite repository for the review ``refetch`` lineage.

Owns ``refetch_attempts``, ``refetch_events`` and ``refetch_seen_candidates``.
The `pending_reviews` row stays the source of truth for review state; these
tables record the attempt (job) lifecycle, its timeline and which candidates a
review chain has already shown.

The job state machine lives in :mod:`telepost.domain.refetch_state` and is the
single authority for the canonical state names and the legal transitions. This
module is the ONLY writer of ``refetch_attempts.state``: every change goes
through :meth:`RefetchRepository._apply_transition`, which validates the move
and appends a row to ``refetch_events`` on the same connection/transaction.

Invariants (enforced here, data-level where possible):

* one active attempt per review chain — partial UNIQUE index
  ``idx_refetch_one_active`` rejects the second row at the database level;
* request idempotency — the same ``callback_key`` (Telegram callback id) always
  resolves to the same attempt and the same ``request_id``, and the same
  ``request_id`` sent to PixivFlow maps to the same durable slot;
* a new generation is only created by a NEW user click (new callback id) after
  the previous attempt is terminal;
* terminal is terminal — a terminal attempt never re-enters the normal flow
  (``ALLOWED`` has no outgoing edge from a terminal state);
* replacement is commit-after-success: the new review row is inserted first and
  the old review is superseded only in the same transaction that marks the
  attempt ``replaced``;
* stale asynchronous results never overwrite a terminal/superseded review
  (``apply_outcome`` and ``apply_replacement`` re-check that the source review
  is still ``pending`` before writing anything).
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, Optional, Tuple

from database import db_manager
from telepost.domain import refetch_state as fsm

logger = logging.getLogger(__name__)

# Canonical job states (see telepost/domain/refetch_state.py).
ACTIVE_STATES = fsm.ACTIVE_STATES
TERMINAL_STATES = fsm.TERMINAL_STATES
from_legacy = fsm.from_legacy  # noqa: F401  (re-exported for callers/tests)
IllegalRefetchTransition = fsm.IllegalRefetchTransition  # noqa: F401

# The outcome vocabularies returned to callers stay LEGACY-COMPATIBLE: the Mini
# App schema (webapp/src/api/*), utils/api_server.py and handlers.review all
# compare against these strings. Only the stored state is canonical.
OUTCOME_REPLACED = "replaced"
OUTCOME_NO_ALTERNATIVE = "no_alternative"
OUTCOME_FAILED = "failed"
OUTCOME_OBSOLETE = "obsolete"

# Canonical outcome → the string old consumers expect.
_LEGACY_OUTCOME = {
    fsm.REPLACED: OUTCOME_REPLACED,
    fsm.NO_CANDIDATE: OUTCOME_NO_ALTERNATIVE,
    fsm.FAILED: OUTCOME_FAILED,
    fsm.TIMEOUT: OUTCOME_FAILED,
    fsm.CANCELLED: OUTCOME_OBSOLETE,
}

_ACTIVE_IN = fsm.sql_state_list(fsm.ACTIVE_STATES + fsm.legacy_aliases())
from_legacy_state = fsm.from_legacy


def legacy_outcome(state: str) -> str:
    """Map a canonical terminal state to the legacy outcome string."""
    return _LEGACY_OUTCOME.get(fsm.normalize(state), str(state or ""))


class RefetchRepository:
    # ---- reads ---------------------------------------------------------
    async def find_by_callback_key(self, callback_key: str):
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM refetch_attempts WHERE callback_key = ?",
                (callback_key,),
            )
            return await cur.fetchone()

    async def find_by_request_id(self, request_id: str):
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM refetch_attempts WHERE request_id = ?",
                (request_id,),
            )
            return await cur.fetchone()

    async def find_active_by_chain(self, review_chain_id: str):
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM refetch_attempts "
                f"WHERE review_chain_id = ? AND state IN ({_ACTIVE_IN}) "
                "ORDER BY created_at ASC LIMIT 1",
                (review_chain_id,),
            )
            return await cur.fetchone()

    async def find_latest_by_chain(self, review_chain_id: str):
        """The newest attempt of a chain, active OR terminal (progress view)."""
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM refetch_attempts WHERE review_chain_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (review_chain_id,),
            )
            return await cur.fetchone()

    async def list_events(self, request_id: str, *, limit: int = 50) -> list:
        """The durable timeline of one attempt, oldest first."""
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM refetch_events WHERE request_id = ? "
                "ORDER BY created_at ASC, id ASC LIMIT ?",
                (request_id, int(limit)),
            )
            return await cur.fetchall()

    # ---- attempt creation (idempotent per callback key) -----------------
    async def create_attempt(
        self,
        *,
        callback_key: str,
        review_chain_id: str,
        generation: int,
        source_review_id: int,
        source_candidate_id: str,
        request_id: Optional[str] = None,
        actor: str = "",
    ) -> Tuple[Optional[Any], Optional[str]]:
        """Insert a new attempt or return the existing one.

        Returns ``(row, None)`` on success/replay and ``(None, reason)`` when
        the data layer refused a second ACTIVE attempt for this chain (the
        caller should answer "正在重抓，请稍候").
        """
        now = time.time()
        rid = request_id or str(uuid.uuid4())
        async with db_manager.get_db() as conn:
            try:
                cur = await conn.execute(
                    """
                    INSERT INTO refetch_attempts (
                        callback_key, request_id, review_chain_id, generation,
                        source_review_id, source_candidate_id, state,
                        created_at, started_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (callback_key, rid, review_chain_id, generation,
                     source_review_id, source_candidate_id, fsm.REQUESTED,
                     now, now, now),
                )
                attempt_id = cur.lastrowid
            except Exception as exc:
                import aiosqlite
                if not isinstance(exc, aiosqlite.IntegrityError):
                    raise
                # Either the same callback (idempotent replay) or a different
                # user click while an attempt is already active.
                existing = await self.find_by_callback_key(callback_key)
                if existing is not None:
                    return existing, None
                active = await self.find_active_by_chain(review_chain_id)
                return None, "already_running"
            # Attempt creation is the BEGIN event of the timeline.
            await self._record_event(
                conn, request_id=rid, review_id=int(source_review_id),
                review_chain_id=review_chain_id, from_state="",
                to_state=fsm.REQUESTED, reason="requested", actor=actor,
            )
            # Read on the SAME (uncommitted) connection: the outer transaction
            # has not committed yet, so a second connection cannot see the row.
            cur = await conn.execute(
                "SELECT * FROM refetch_attempts WHERE id = ?", (attempt_id,)
            )
            return await cur.fetchone(), None

    # ---- state transitions ----------------------------------------------
    async def _record_event(
        self, conn, *, request_id: str, review_id: Optional[int],
        review_chain_id: str, from_state: str, to_state: str,
        reason: str = "", actor: str = "", remote_state: str = "",
        created_at: Optional[float] = None,
    ) -> None:
        await conn.execute(
            "INSERT INTO refetch_events (request_id, review_id, review_chain_id,"
            " from_state, to_state, reason, actor, remote_state, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request_id, review_id, review_chain_id or "",
             fsm.normalize(from_state), fsm.normalize(to_state),
             (reason or "")[:400], str(actor or "")[:200],
             (remote_state or "")[:200], created_at or time.time()),
        )

    async def apply_transition_on(
        self, conn, request_id: str, to_state: str, *,
        reason: str = "", actor: str = "", remote_state: str = "",
        extra: Optional[Dict[str, Any]] = None, event: bool = True,
    ) -> Tuple[bool, str]:
        """Apply ONE legal state change on the caller's connection.

        Returns ``(changed, canonical_state)``: ``changed`` is True only when the
        state actually moved (a repeated observation of the same state is a
        no-op, so polling never rewrites ``updated_at`` and never masks a stuck
        attempt). Illegal moves are REFUSED here — nothing is written, no event
        is recorded, and the attempt keeps its state (a terminal attempt can
        never be resurrected). Use :func:`telepost.domain.refetch_state
        .assert_transition` when a caller needs to know why.
        """
        cur = await conn.execute(
            "SELECT * FROM refetch_attempts WHERE request_id = ?", (request_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return False, ""
        current = fsm.normalize(row["state"])
        try:
            target = fsm.assert_transition(current, to_state)
        except IllegalRefetchTransition as exc:
            logger.warning(
                "拒绝非法的重抓状态迁移: request_id=%s %s", request_id, exc,
            )
            return False, current

        now = time.time()
        moved = target != current
        # A repeated observation of the same remote state is not progress: writing
        # it would refresh updated_at and hide a stalled stage from the watchdog.
        remote_state = remote_state or ""
        if remote_state == (row["last_remote_state"] or ""):
            remote_state = ""
        extra = {
            column: value for column, value in (extra or {}).items()
            if value != (row[column] if column in row.keys() else None)
        }
        if not moved and not remote_state and not extra:
            return False, current
        sets = ["updated_at=?"]
        params: list = [now]
        # ``failure_code`` answers "why did it fail", so it is only meaningful on a
        # failure terminal; the transition reason always lives in the event log
        # (and in terminal_reason for every other terminal).
        if moved and target in (fsm.FAILED, fsm.TIMEOUT):
            sets.append("failure_code=?")
            params.append((reason or target)[:400])
        if remote_state:
            sets.append("last_remote_state=?")
            params.append(remote_state[:200])
        if moved and target in fsm.TERMINAL_STATES:
            sets.append("finished_at=?")
            params.append(now)
            sets.append("terminal_reason=?")
            params.append((reason or target)[:400])
        for column, value in extra.items():
            sets.append(f"{column}=?")
            params.append(value)
        if moved:
            sets.append("state=?")
            params.append(target)

        params.append(request_id)
        await conn.execute(
            f"UPDATE refetch_attempts SET {', '.join(sets)} "
            "WHERE request_id=?",
            params,
        )
        if moved and event:
            await self._record_event(
                conn, request_id=request_id, review_id=row["source_review_id"],
                review_chain_id=row["review_chain_id"], from_state=current,
                to_state=target, reason=reason, actor=actor,
                remote_state=remote_state, created_at=now,
            )
        return moved, target

    async def _transition(
        self, request_id: str, to_state: str, *, reason: str = "",
        actor: str = "", remote_state: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        async with db_manager.get_db() as conn:
            return await self.apply_transition_on(
                conn, request_id, to_state, reason=reason, actor=actor,
                remote_state=remote_state, extra=extra,
            )

    async def mark_admitted(self, request_id: str, slot_id: str,
                            *, actor: str = "") -> bool:
        """PixivFlow accepted the request (202): durable slot exists there."""
        moved, _ = await self._transition(
            request_id, fsm.SEARCHING, reason="remote_accepted", actor=actor,
            extra={"slot_id": slot_id or "", "started_at": time.time()},
        )
        return moved

    async def mark_failed(self, request_id: str, failure_code: str, *,
                          actor: str = "") -> bool:
        moved, _ = await self._transition(
            request_id, fsm.FAILED, reason=failure_code, actor=actor,
        )
        return moved

    async def mark_timeout(self, request_id: str, failure_code: str, *,
                           actor: str = "", remote_state: str = "") -> bool:
        """Terminal local-clock outcome (admission stall / hard timeout)."""
        moved, _ = await self._transition(
            request_id, fsm.TIMEOUT, reason=failure_code, actor=actor,
            remote_state=remote_state,
        )
        return moved

    async def mark_cancelled(self, request_id: str, reason: str, *,
                             actor: str = "", remote_state: str = "") -> bool:
        moved, _ = await self._transition(
            request_id, fsm.CANCELLED, reason=reason, actor=actor,
            remote_state=remote_state,
        )
        return moved

    async def advance_stage(self, request_id: str, to_state: str, *,
                            remote_state: str = "", reason: str = "") -> bool:
        """Advance the pipeline stage (searching/filtering/candidate_found).

        Forward-only: the transition table refuses a downgrade, so a stale
        remote read can never move the projection backwards.
        """
        moved, _ = await self._transition(
            request_id, to_state, reason=reason, remote_state=remote_state,
        )
        return moved

    async def mark_cancelled_on(self, conn, request_id: str, reason: str, *,
                                actor: str = "service:review_queue") -> bool:
        """Cancel an attempt inside the caller's transaction (no bypass writes)."""
        moved, _ = await self.apply_transition_on(
            conn, request_id, fsm.CANCELLED, reason=reason, actor=actor,
        )
        return moved

    async def mark_replaced(self, request_id: str,
                            result_candidate_id: str, *,
                            result_review_id: Optional[int] = None) -> bool:
        """Terminal 'replaced' (replacement delivered and linked).

        The normal path runs inside the submission transaction
        (``finalize_replacement``); this is the explicit transition for
        callers/tests that already know the replacement landed.
        """
        moved, _ = await self._transition(
            request_id, fsm.REPLACED, reason="replaced",
            extra={
                "result_candidate_id": result_candidate_id,
                "result_review_id": result_review_id,
            },
        )
        return moved

    async def active_since(self, cutoff_remind: float, cutoff_fail: float) -> list:
        """Active attempts that have been waiting too long.

        Used by the progress watchdog: rows older than ``cutoff_remind`` deserve
        a "still running" reminder; rows older than ``cutoff_fail`` must be
        failed (no terminal outcome ever arrived). Bounded batch, oldest first.
        """
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM refetch_attempts "
                f"WHERE state IN ({_ACTIVE_IN}) AND created_at <= ? "
                "ORDER BY created_at ASC LIMIT 50",
                (cutoff_remind,),
            )
            out = []
            for row in await cur.fetchall():
                out.append((row, "stale" if row["created_at"] <= cutoff_fail else "remind"))
            return out

    async def bump_progress_notified(self, request_id: str, at: float) -> bool:
        """Record a progress reminder and bump its durable counter.

        ``updated_at`` is deliberately NOT touched: it is the state-transition
        clock the stage timeout is measured against, and a reminder is not
        progress (otherwise a stuck attempt could keep itself alive forever).
        """
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "UPDATE refetch_attempts SET last_progress_notified_at=?, "
                "notify_count=COALESCE(notify_count, 0) + 1 "
                "WHERE request_id=?",
                (at, request_id),
            )
            return cur.rowcount == 1

    async def resolve_replacement(self, conn, request_id: str):
        """Resolve a submission's refetch correlation inside an open connection.

        Returns the attempt row plus its source review when the attempt is
        still ACTIVE; ``(None, None)`` when it is unknown or already terminal.
        The caller must still re-check the source review's status within the
        same transaction before applying anything.
        """
        cur = await conn.execute(
            "SELECT * FROM refetch_attempts WHERE request_id = ?", (request_id,)
        )
        attempt = await cur.fetchone()
        if attempt is None or not fsm.is_active(attempt["state"]):
            return None, None
        cur = await conn.execute(
            "SELECT * FROM pending_reviews WHERE id = ?",
            (attempt["source_review_id"],),
        )
        source = await cur.fetchone()
        return attempt, source

    async def _record_candidate_outcome(
        self, conn, *, review_chain_id: str, candidate_id: str,
        outcome: str, reason: str = "", replaced_by: str = "",
    ) -> None:
        """Record what happened to ONE candidate of a chain (lifecycle).

        Additive bookkeeping: the candidate row may not exist (legacy chains),
        in which case nothing happens.
        """
        if not candidate_id:
            return
        try:
            await conn.execute(
                "UPDATE refetch_seen_candidates SET outcome=?, reason=?, "
                "decided_at=?, replaced_by=? WHERE review_chain_id=? AND "
                "candidate_id=?",
                (outcome[:40], (reason or "")[:200], time.time(),
                 replaced_by or "", review_chain_id, candidate_id),
            )
        except Exception:
            logger.debug("记录候选结局失败: chain=%s candidate=%s",
                         review_chain_id, candidate_id, exc_info=True)

    async def insert_seen(
        self, conn, *, review_chain_id: str, candidate_id: str,
        source: str, generation: int, created_at: Optional[float] = None,
        request_id: str = "", outcome: str = "", reason: str = "",
    ) -> None:
        await conn.execute(
            "INSERT OR IGNORE INTO refetch_seen_candidates "
            "(review_chain_id, candidate_id, source, generation, created_at, "
            " request_id, outcome, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (review_chain_id, candidate_id, source, generation,
             created_at or time.time(), request_id or "", outcome or "",
             (reason or "")[:200]),
        )

    async def finalize_replacement(
        self,
        conn,
        *,
        attempt,
        new_review_id: int,
        new_candidate_id: str,
        source_review_id: int,
        review_chain_id: str,
        generation: int,
    ) -> str:
        """COMMIT the replacement inside the caller's transaction.

        Returns the resulting LEGACY attempt outcome: ``'replaced'`` when the
        source was still pending and the chain advanced, ``'obsolete'`` when the
        source left pending (approve/reject/expire raced the result) — in which
        case nothing is linked and nothing is superseded.

        The stored state is canonical (``replaced`` / ``cancelled``) and the
        timeline + candidate lifecycle rows are written in the same transaction.
        """
        request_id = attempt["request_id"]
        cur = await conn.execute(
            "UPDATE pending_reviews SET status='superseded', updated_at=? "
            "WHERE id=? AND status='pending'",
            (time.time(), source_review_id),
        )
        if cur.rowcount != 1:
            # The reviewer already decided; a late replacement must not
            # overwrite that decision or create a phantom superseded row.
            await self.apply_transition_on(
                conn, request_id, fsm.CANCELLED,
                reason="source_review_resolved", actor="service:refetch",
            )
            return OUTCOME_OBSOLETE

        # Link the NEW review into the chain and the attempt's result.
        await conn.execute(
            "UPDATE pending_reviews SET review_chain_id=?, generation=?, "
            "supersedes_review_id=?, updated_at=? WHERE id=?",
            (review_chain_id, generation, source_review_id, time.time(),
             new_review_id),
        )
        await self.apply_transition_on(
            conn, request_id, fsm.REPLACED, reason="replaced",
            actor="service:refetch",
            extra={
                "result_candidate_id": new_candidate_id,
                "result_review_id": int(new_review_id),
            },
        )
        # Candidate lifecycle: the source candidate was rejected by this refetch
        # and replaced by the new one (who / why / when / by whom).
        await self._record_candidate_outcome(
            conn, review_chain_id=review_chain_id,
            candidate_id=attempt["source_candidate_id"] or "",
            outcome="rejected_by_refetch", reason="refetch_replacement",
            replaced_by=new_candidate_id,
        )
        await self.insert_seen(
            conn, review_chain_id=review_chain_id, candidate_id=new_candidate_id,
            source="replacement", generation=generation,
            request_id=request_id, outcome="pending_review",
        )
        return OUTCOME_REPLACED

    async def apply_outcome(
        self,
        request_id: str,
        disposition: str,
        *,
        reason: str = "",
        scanned: int = 0,
        skipped_duplicate: int = 0,
        skipped_invalid: int = 0,
        skipped_unavailable: int = 0,
    ) -> Tuple[Optional[Any], str, bool]:
        """Apply a PixivFlow terminal verdict (no_alternative | failed).

        Returns ``(attempt_row, applied_state, changed)`` where ``applied_state``
        is the LEGACY outcome string after the call and ``changed`` is True only
        when THIS call moved the attempt (an already-terminal replay returns
        ``changed=False`` so the caller does not re-notify). Never touches a
        review the reviewer already decided.
        """
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM refetch_attempts WHERE request_id = ?",
                (request_id,),
            )
            attempt = await cur.fetchone()
            if attempt is None:
                return None, "not_found", False
            if not fsm.is_active(attempt["state"]):
                return attempt, legacy_outcome(attempt["state"]), False
            cur = await conn.execute(
                "SELECT * FROM pending_reviews WHERE id = ?",
                (attempt["source_review_id"],),
            )
            source = await cur.fetchone()
            if source is None or source["status"] != "pending":
                await self.apply_transition_on(
                    conn, request_id, fsm.CANCELLED,
                    reason="source_review_resolved", actor="service:refetch",
                )
                return None, OUTCOME_OBSOLETE, True

            target = (fsm.NO_CANDIDATE if disposition == "no_alternative"
                      else fsm.FAILED)
            await self.apply_transition_on(
                conn, request_id, target,
                reason=(reason or disposition), actor="service:pixivflow",
                extra={
                    "scanned": int(scanned),
                    "skipped_duplicate": int(skipped_duplicate),
                    "skipped_invalid": int(skipped_invalid),
                    "skipped_unavailable": int(skipped_unavailable),
                },
            )
            # The source candidate stays the current candidate: record WHY it was
            # kept so the history explains the outcome (no silent failures).
            await self._record_candidate_outcome(
                conn, review_chain_id=attempt["review_chain_id"],
                candidate_id=attempt["source_candidate_id"] or "",
                outcome="kept", reason=(reason or disposition),
            )
            cur = await conn.execute(
                "SELECT * FROM refetch_attempts WHERE request_id = ?",
                (request_id,),
            )
            return await cur.fetchone(), legacy_outcome(target), True

    async def chain_of_review(self, conn, review_row) -> Tuple[str, int]:
        """Stable (chain_id, generation) for a review; bootstraps if missing.

        Runs on the caller's connection so the bootstrap commit is atomic with
        whatever the caller is doing (e.g. attempt creation).
        """
        chain_id = review_row["review_chain_id"] or ""
        if not chain_id:
            chain_id = f"chain-{review_row['id']}"
            await conn.execute(
                "UPDATE pending_reviews SET review_chain_id=?, updated_at=? "
                "WHERE id=?",
                (chain_id, time.time(), review_row["id"]),
            )
        return chain_id, int(review_row["generation"] or 0)

    async def next_generation(self, review_chain_id: str) -> int:
        """Generation for the NEXT attempt on a chain.

        Every intentional click increments the chain's generation (spec §12), so
        a no_alternative attempt followed by a fresh click on the SAME review is
        a NEW generation, and A→B→C replacements number 1,2,3.
        """
        async with db_manager.get_db() as conn:
            cur = await conn.execute(
                "SELECT COALESCE(MAX(generation), 0) AS max_gen "
                "FROM refetch_attempts WHERE review_chain_id = ?",
                (review_chain_id,),
            )
            row = await cur.fetchone()
            return int(row["max_gen"] or 0) + 1

    async def seed_original_seen(self, conn, review_row) -> None:
        """Ensure the CURRENT candidate of every review is in seen-history.

        Keeps the spec invariant "the current candidate is always in the seen
        set" true for reviews created after the migration bootstrap.
        """
        if not review_row["pixiv_id"]:
            return
        chain_id, generation = await self.chain_of_review(conn, review_row)
        await self.insert_seen(
            conn, review_chain_id=chain_id,
            candidate_id=review_row["pixiv_id"],
            source="original", generation=int(review_row["generation"] or 0),
            outcome="current",
        )
