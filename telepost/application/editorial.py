"""Editorial Revision application service (§editorial).

Adapters (Bot chat, Mini App, HTTP API) call this single service. It owns the
revision lifecycle (create/edit/finalize), the review-current guard, and the
immutability rules — never the publication itself (that stays in
:mod:`services.review_service` so approve-original and approve-edited share one
review FSM + one idempotency ledger).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from telepost.storage.sqlite.editorial import (
    EditorialConflictError,
    EditorialNotFoundError,
    EditorialStateError,
    EditorialRepository,
)
from telepost.storage.sqlite.reviews import ReviewRepository

from ..domain import editorial as domain


class EditorialError(Exception):
    """User-safe editorial error mapped to an HTTP/callback alert."""


class EditorialObsoleteError(EditorialError):
    """The review's generation is no longer the current chain head (409)."""


def _editor_identity(actor: Optional[Dict[str, Any]]) -> tuple:
    if not isinstance(actor, dict):
        return None, "", ""
    return (
        actor.get("telegram_user_id"),
        str(actor.get("username") or ""),
        str(actor.get("display_name") or ""),
    )


class EditorialService:
    def __init__(self, repository: Optional[EditorialRepository] = None,
                 reviews: Optional[ReviewRepository] = None):
        self._repo = repository or EditorialRepository()
        self._reviews = reviews or ReviewRepository()

    async def _require_review(self, review_id: int) -> Dict[str, Any]:
        row = await self._reviews.get(int(review_id))
        if row is None:
            raise EditorialObsoleteError("审核记录不存在")
        return row

    async def _require_current(self, review_id: int) -> Dict[str, Any]:
        """The review must still be the pending CURRENT head of its chain — a
        refetch that produced a new generation makes every older revision
        unpublishable (§35)."""
        row = await self._require_review(review_id)
        if row["status"] != "pending":
            raise EditorialStateError("该审核已结束，无法编辑")
        # Normal submissions carry an EMPTY review_chain_id (only refetch
        # replacements get a chain id). An empty chain id means this review is
        # its own single-row chain: no newer generation can exist, so the DB
        # chain lookup would only find nothing and falsely declare the pending
        # review obsolete. Treat the row itself as the head instead of
        # querying with a synthetic id that matches no row.
        if not row["review_chain_id"]:
            return row
        chain_id = row["review_chain_id"]
        head = await self._reviews.head_of_chain(chain_id)
        if head is None or int(head["id"]) != int(review_id):
            raise EditorialObsoleteError("该审核已不是最新版本（可能已被重抓替换），请刷新后操作")
        return row

    async def create(self, review_id: int, actor: Optional[Dict[str, Any]] = None,
                     ) -> Dict[str, Any]:
        row = await self._require_current(review_id)
        base = domain.Snapshot.from_review(row)
        editor_id, editor_username, editor_display = _editor_identity(actor)
        revision = await self._repo.create(
            int(review_id), row["review_chain_id"] or f"review-{review_id}",
            base, editor_id, editor_username, editor_display,
        )
        return revision

    async def get(self, review_id: int, revision_id: int) -> Dict[str, Any]:
        revision = await self._repo.get_for_review(int(review_id), int(revision_id))
        if revision is None:
            raise EditorialNotFoundError("revision 不存在")
        return revision

    async def list_for_review(self, review_id: int) -> List[Dict[str, Any]]:
        return await self._repo.list_for_review(int(review_id))

    async def update(self, review_id: int, revision_id: int, *,
                     payload: Dict[str, Any], expected_version: int,
                     actor: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        revision = await self.get(review_id, revision_id)
        base = domain.Snapshot.from_dict(json.loads(revision["base_snapshot"] or "{}"))
        try:
            edited = _snapshot_from_payload(base, payload)
        except (TypeError, ValueError) as exc:
            raise EditorialStateError(str(exc)) from exc
        editor_id, editor_username, editor_display = _editor_identity(actor)
        updated = await self._repo.update_draft(
            int(revision_id), edited, int(expected_version),
            editor_user_id=editor_id, editor_username=editor_username,
            editor_display_name=editor_display,
            severity=str(payload.get("severity") or domain.MINOR),
        )
        return updated

    async def preview(self, review_id: int, revision_id: int,
                      payload: Optional[Dict[str, Any]] = None) -> str:
        """Server-side caption preview of an edited version (side-effect free)."""
        if payload:
            revision = await self.get(review_id, revision_id)
            base = domain.Snapshot.from_dict(json.loads(revision["base_snapshot"] or "{}"))
            edited = _snapshot_from_payload(base, payload)
        else:
            revision = await self.get(review_id, revision_id)
            edited = domain.Snapshot.from_dict(
                json.loads(revision["edited_snapshot"] or "{}"))
        from utils.helper_functions import build_caption

        row = await self._require_review(review_id)
        caption_data = {
            "title": edited.title,
            "tags": edited.tags, "note": edited.note, "link": edited.link,
            "anonymous": str(bool(row["anonymous"])).lower(),
            "spoiler": str(bool(edited.spoiler)).lower(),
            "user_id": row["user_id"], "username": row["username"] or "",
        }
        return build_caption(caption_data, surface="review")

    async def finalize(self, review_id: int, revision_id: int, *,
                       expected_version: int) -> Dict[str, Any]:
        await self._require_current(review_id)  # stale generation guard
        return await self._repo.finalize(int(revision_id), int(expected_version))

    async def supersede_for_review(self, review_id: int) -> int:
        return await self._repo.supersede_for_review(int(review_id))


def _snapshot_from_payload(base: domain.Snapshot, payload: Dict[str, Any]) -> domain.Snapshot:
    """Build an edited snapshot from a PATCH payload; unspecified fields fall
    back to the base snapshot (partial edits, §26)."""
    media_order = payload.get("media_order")
    removed = payload.get("removed")
    order = [int(i) for i in media_order] if media_order is not None else list(base.media_order)
    removed_list = [int(i) for i in removed] if removed is not None else []
    # Removed indexes are drawn from base order (original positions); an editor
    # cannot remove media that was never part of the submission.
    allowed = set(base.media_order) or set(range(0, (max(base.media_order, default=-1) + 1)))
    out_of_range = [i for i in removed_list if i not in allowed]
    if out_of_range:
        raise EditorialStateError(f"附件索引超出范围：{out_of_range[:5]}")
    return domain.Snapshot(
        title=str(payload.get("title", base.title)),
        note=str(payload.get("note", base.note)),
        tags=str(payload.get("tags", base.tags)),
        link=str(payload.get("link", base.link)),
        spoiler=bool(payload.get("spoiler", base.spoiler)),
        media_order=order,
        removed=sorted(set(removed_list)),
    )