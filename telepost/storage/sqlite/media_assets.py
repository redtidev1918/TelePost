"""Delivery Asset Contract (Step 10) repository.

Stores canonical media references (asset_id / source_url / kind / mime_type)
on a review chain. These are the minimal facts TelePost needs to decide how to
deliver later (Step 11 DeliveryPlanner); local file_ids remain in
pending_reviews.media_json/documents_json untouched.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from database import db_manager

VALID_KINDS = frozenset({"image"})


def _clean(refs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        asset_id = str(ref.get("asset_id") or "").strip()
        kind = str(ref.get("kind") or "image").strip()
        source_url = str(ref.get("source_url") or "").strip()
        if not asset_id or kind not in VALID_KINDS or not source_url:
            continue
        if not source_url.startswith(("http://", "https://")):
            continue
        cleaned.append({
            "asset_id": asset_id[:200],
            "kind": kind,
            "source_url": source_url[:2048],
            "mime_type": str(ref.get("mime_type") or "")[:100],
        })
    return cleaned


async def replace_for_chain(review_chain_id: str,
                            refs: List[Dict[str, Any]]) -> int:
    """Replace the media asset refs on a review chain (idempotent upsert)."""
    if not review_chain_id:
        return 0
    cleaned = _clean(refs)
    async with db_manager.get_db() as conn:
        await conn.execute(
            "DELETE FROM media_asset_refs WHERE review_chain_id=?",
            (review_chain_id,),
        )
        now = time.time()
        for ref in cleaned:
            await conn.execute(
                "INSERT OR IGNORE INTO media_asset_refs "
                "(review_chain_id, asset_id, kind, source_url, mime_type, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (review_chain_id, ref["asset_id"], ref["kind"],
                 ref["source_url"], ref["mime_type"], now),
            )
        await conn.commit()
    return len(cleaned)


async def list_for_chain(review_chain_id: str) -> List[Dict[str, Any]]:
    if not review_chain_id:
        return []
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT asset_id, kind, source_url, mime_type "
            "FROM media_asset_refs WHERE review_chain_id=? "
            "ORDER BY id",
            (review_chain_id,),
        )
        rows = await cur.fetchall()
    return [
        {
            "asset_id": r["asset_id"],
            "kind": r["kind"],
            "source_url": r["source_url"],
            "mime_type": r["mime_type"],
        }
        for r in rows
    ]


async def chain_id_for_review(review_id: int, row=None) -> str:
    """Resolve the review_chain_id for a review row (row optional short-circuit)."""
    if row is not None:
        return str(row["review_chain_id"] or "")
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT review_chain_id FROM pending_reviews WHERE id=?",
            (int(review_id),),
        )
        found = await cur.fetchone()
    return str(found["review_chain_id"]) if found else ""
