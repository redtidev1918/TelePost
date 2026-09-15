"""Editorial Revision durable storage (§editorial).

Revisions are a separate ledger from the Review FSM: a revision row never
changes ``pending_reviews.status``, and the review flow never reads revision
rows to decide whether publication is allowed. The only coupling is:

* the review row carries ``published_source_revision_id`` (NULL = original
  publish), recorded by the publication service AFTER a confirmed delivery;
* a finalized revision is required before it may be published;
* when a review is superseded/decided, its open drafts are terminalized
  (``superseded``) so a stale editor can never publish a stale generation.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from database.db_manager import get_db

from ...domain import editorial as domain

RevisionRow = Dict[str, Any]


class EditorialConflictError(Exception):
    """CAS/version conflict: another editor moved the revision first (409)."""


class EditorialNotFoundError(Exception):
    """Revision does not exist (404)."""


class EditorialStateError(Exception):
    """Operation illegal in the current revision state (409)."""


def _stamp(now: Optional[float] = None) -> float:
    return time.time() if now is None else float(now)


class EditorialRepository:
    """SQLite repository keyed on review rows (pending_reviews)."""

    async def create(self, review_id: int, review_chain_id: str, base: domain.Snapshot,
                     editor_user_id: Optional[int], editor_username: str = "",
                     editor_display_name: str = "",
                     connection: Any = None) -> Dict[str, Any]:
        """Create the next draft revision for a review.

        Revision numbering is data-layer monotonic and race-safe: the number is
        allocated inside a transaction and the UNIQUE(review_id, revision_number)
        constraint rejects a concurrent duplicate; a loser of that race retries
        once with the fresh maximum.
        """
        now = _stamp()
        async with (connection or get_db()) as conn:
            for _attempt in range(2):
                cur = await conn.execute(
                    "SELECT COALESCE(MAX(revision_number), 0) + 1 AS next "
                    "FROM editorial_revisions WHERE review_id = ?",
                    (int(review_id),),
                )
                row = await cur.fetchone()
                number = int(row["next"]) if row else 1
                try:
                    cur = await conn.execute(
                        """
                        INSERT INTO editorial_revisions (
                          review_id, review_chain_id, revision_number, status,
                          base_snapshot, edited_snapshot, change_set, summary,
                          editor_user_id, editor_username, editor_display_name,
                          version, created_at, updated_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            int(review_id), review_chain_id, number,
                            domain.RevisionStatus.DRAFT.value,
                            json.dumps(base.to_dict(), ensure_ascii=False),
                            json.dumps(base.to_dict(), ensure_ascii=False),
                            "{}", "",
                            editor_user_id, editor_username, editor_display_name,
                            1, now, now,
                        ),
                    )
                    return await self._select_in_conn(conn, cur.lastrowid)
                except Exception as exc:
                    if "UNIQUE" in str(exc).upper() or getattr(exc, "sqlite_errorcode", None) == 2067:
                        continue  # concurrent number allocation; retry once
                    raise
        raise EditorialConflictError("无法分配 revision 编号，请重试")

    async def get(self, revision_id: int) -> Optional[Dict[str, Any]]:
        async with get_db() as conn:
            return await self._select_in_conn(conn, int(revision_id))

    async def get_for_review(self, review_id: int, revision_id: int) -> Optional[Dict[str, Any]]:
        async with get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM editorial_revisions WHERE id = ? AND review_id = ?",
                (int(revision_id), int(review_id)),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_for_review(self, review_id: int) -> List[Dict[str, Any]]:
        async with get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM editorial_revisions WHERE review_id = ? "
                "ORDER BY revision_number ASC", (int(review_id),),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def list_published_for_chain(self, review_chain_id: str) -> List[Dict[str, Any]]:
        """Submitter-safe published history (one logical submission, §36)."""
        async with get_db() as conn:
            cur = await conn.execute(
                "SELECT * FROM editorial_revisions WHERE review_chain_id = ? "
                "AND status = 'published' ORDER BY revision_number ASC",
                (review_chain_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def update_draft(self, revision_id: int, edited: domain.Snapshot,
                           expected_version: int, *,
                           editor_user_id: Optional[int] = None,
                           editor_username: str = "",
                           editor_display_name: str = "",
                           severity: str = domain.MINOR) -> Optional[Dict[str, Any]]:
        """CAS update of a DRAFT: rejects when ``version`` moved (409)."""
        now = _stamp()
        current = await self.get(revision_id)
        if current is None:
            raise EditorialNotFoundError("revision 不存在")
        if current["status"] != domain.RevisionStatus.DRAFT.value:
            raise EditorialStateError("只有草稿可以编辑")
        if int(current["version"]) != int(expected_version):
            raise EditorialConflictError("此投稿已被其他审核员更新，请刷新后继续编辑")
        base = domain.Snapshot.from_dict(json.loads(current["base_snapshot"] or "{}"))
        changes = domain.change_set(base, edited)
        summary = "\n".join(domain.change_summary(changes))
        severity = severity if severity in (domain.MINOR, domain.SUBSTANTIVE) else domain.MINOR
        async with get_db() as conn:
            cur = await conn.execute(
                """
                UPDATE editorial_revisions SET
                  edited_snapshot = ?, change_set = ?, summary = ?,
                  editor_user_id = ?, editor_username = ?, editor_display_name = ?,
                  severity = ?, version = version + 1, updated_at = ?
                WHERE id = ? AND version = ?
                """,
                (
                    json.dumps(edited.to_dict(), ensure_ascii=False),
                    json.dumps(changes, ensure_ascii=False),
                    summary,
                    editor_user_id, editor_username, editor_display_name,
                    severity, now, int(revision_id), int(expected_version),
                ),
            )
            if cur.rowcount != 1:
                raise EditorialConflictError("此投稿已被其他审核员更新，请刷新后继续编辑")
            return await self._select_in_conn(conn, int(revision_id))

    async def finalize(self, revision_id: int, expected_version: int) -> Optional[Dict[str, Any]]:
        """draft → finalized (only a finalized revision may be published, §30)."""
        current = await self.get(revision_id)
        if current is None:
            raise EditorialNotFoundError("revision 不存在")
        if int(current["version"]) != int(expected_version):
            raise EditorialConflictError("此投稿已被其他审核员更新，请刷新后继续编辑")
        if current["status"] not in (
            domain.RevisionStatus.DRAFT.value, domain.RevisionStatus.FINALIZED.value
        ):
            raise EditorialStateError("该版本已发布或已作废，无法再次编辑发布")
        now = _stamp()
        async with get_db() as conn:
            cur = await conn.execute(
                "UPDATE editorial_revisions SET status = 'finalized', "
                "finalized_at = ?, updated_at = ?, version = version + 1 "
                "WHERE id = ? AND version = ?",
                (now, now, int(revision_id), int(expected_version)),
            )
            if cur.rowcount != 1:
                raise EditorialConflictError("此投稿已被其他审核员更新，请刷新后继续编辑")
            return await self._select_in_conn(conn, int(revision_id))

    async def mark_published(self, revision_id: int, message_id: int,
                             published_snapshot: Dict[str, object]) -> Optional[Dict[str, Any]]:
        """finalized → published, with the immutable published snapshot (§32)."""
        now = _stamp()
        async with get_db() as conn:
            cur = await conn.execute(
                "UPDATE editorial_revisions SET status = 'published', "
                "published_message_id = ?, published_snapshot = ?, "
                "published_at = ?, updated_at = ? "
                "WHERE id = ? AND status = 'finalized'",
                (
                    int(message_id),
                    json.dumps(published_snapshot, ensure_ascii=False),
                    now, now, int(revision_id),
                ),
            )
            if cur.rowcount != 1:
                raise EditorialStateError("该版本状态已变化，无法标记发布")
            return await self._select_in_conn(conn, int(revision_id))

    async def supersede_for_review(self, review_id: int) -> int:
        """Decide/expire/supersede a review: terminalize its open revisions so a
        stale editor can never publish them (§35)."""
        now = _stamp()
        async with get_db() as conn:
            cur = await conn.execute(
                "UPDATE editorial_revisions SET status = 'superseded', "
                "updated_at = ? WHERE review_id = ? AND status IN ('draft','finalized')",
                (now, int(review_id)),
            )
            return cur.rowcount

    async def _select_in_conn(self, conn: Any, revision_id: int) -> Dict[str, Any]:
        cur = await conn.execute(
            "SELECT * FROM editorial_revisions WHERE id = ?", (int(revision_id),)
        )
        row = await cur.fetchone()
        if row is None:
            raise EditorialNotFoundError("revision 不存在")
        return dict(row)