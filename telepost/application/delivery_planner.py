"""MediaAsset Delivery Planner (Step 11).

TelePost decides how to deliver a review's media instead of trusting an
upstream (PixivFlow) domain object wholesale. For each canonical media asset
reference we pick the cheapest *available* Telegram source:

* ``telegram_file_id`` – the review already holds a Telegram file_id (zero
  re-upload); this stays the default fast path.
* ``remote_url``       – only the canonical source URL exists (e.g. the
  on-demand preview path where PixivFlow stopped downloading cover images);
  the Telegram sender fetches the URL itself.
* ``local_upload``     – reserved for multipart-uploaded artifacts; current
  dispatch keeps using the existing ``LocalFile`` path and is not modeled here
  yet because this planner addresses the file_id / remote_url decision.

The planner is read-only: it returns a plan and never mutates state, so
production delivery only changes once an adapter explicitly commits to using
the plan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..domain.delivery import MediaItem, MediaKind, RemoteUrl, TelegramFileId

STRATEGY_FILE_ID = "telegram_file_id"
STRATEGY_REMOTE_URL = "remote_url"
VALID_STRATEGIES = frozenset({STRATEGY_FILE_ID, STRATEGY_REMOTE_URL})


@dataclass(frozen=True)
class MediaPlanEntry:
    index: int
    kind: str
    strategy: str
    asset_id: Optional[str] = None
    file_id: Optional[str] = None
    source_url: Optional[str] = None
    mime_type: Optional[str] = None
    filename: Optional[str] = None


@dataclass(frozen=True)
class MediaDeliveryPlan:
    """Outcome of :func:`plan_review_media`.

    ``strategy`` is the overall preferred source strategy:

    * ``telegram_file_id`` — every planned asset already has a file_id;
    * ``remote_url``       — every planned asset must be fetched by URL;
    * ``mixed``            — some file_id, some remote_url;
    * ``empty``            — nothing to deliver.
    """

    strategy: str
    entries: List[MediaPlanEntry] = field(default_factory=list)

    def to_media_items(self, *, spoiler: bool = False) -> List[MediaItem]:
        """Materialize domain :class:`MediaItem`s from this plan."""
        items: List[MediaItem] = []
        for entry in self.entries:
            if entry.strategy == STRATEGY_FILE_ID and entry.file_id:
                items.append(MediaItem.file_id(
                    entry.kind, entry.file_id, spoiler=spoiler,
                    filename=entry.filename,
                ))
            elif entry.strategy == STRATEGY_REMOTE_URL and entry.source_url:
                items.append(MediaItem(
                    MediaKind.coerce(entry.kind),
                    RemoteUrl(entry.source_url, filename=entry.filename),
                    spoiler=spoiler,
                ))
            # A missing source must not silently produce an empty slot; the
            # caller decides whether to fail closed.
        return items


def _asset_kind_to_media_kind(asset_kind: str) -> str:
    # media_asset_refs only contains kind='image' today; an image maps to a
    # Telegram photo in the visual gallery.
    if str(asset_kind).strip().lower() == "image":
        return MediaKind.PHOTO.value
    return str(asset_kind).strip().lower()


def _overall_strategy(entries: List[MediaPlanEntry]) -> str:
    if not entries:
        return "empty"
    strategies = {e.strategy for e in entries}
    if strategies == {STRATEGY_FILE_ID}:
        return STRATEGY_FILE_ID
    if strategies == {STRATEGY_REMOTE_URL}:
        return STRATEGY_REMOTE_URL
    if strategies <= VALID_STRATEGIES:
        return "mixed"
    return "unknown"


def plan_review_media(media: List[Dict[str, Any]],
                      media_assets: List[Dict[str, Any]],
                      *, documents: Optional[List[Dict[str, Any]]] = None) -> MediaDeliveryPlan:
    """Build a delivery plan from a review's local media + canonical asset refs.

    ``media`` / ``documents`` mirror the raw ``pending_reviews`` JSON arrays
    (``media_json`` / ``documents_json``): each item carries ``file_id`` and
    media type / filename. ``media_assets`` mirrors ``media_asset_refs`` rows
    (``asset_id``, ``kind``, ``source_url``, ``mime_type``).

    Positional pairing (same index) is used because PixivFlow manifests are
    ordered and match the Telegram file list order; a mismatch can ONLY fall
    back to ``remote_url`` and never produces a bogus file_id.
    """
    media_items = list(media or [])
    document_items = list(documents or [])
    local_items = media_items + document_items

    if not media_assets:
        entries: List[MediaPlanEntry] = []
        for index, item in enumerate(local_items):
            file_id = str(item.get("file_id") or "").strip()
            if not file_id:
                continue
            kind = _asset_kind_to_media_kind(
                item.get("type") or ("document" if index >= len(media_items) else "photo")
            )
            entries.append(MediaPlanEntry(
                index=index,
                kind=kind,
                strategy=STRATEGY_FILE_ID,
                file_id=file_id,
                filename=(str(item.get("filename") or "") or None),
            ))
        return MediaDeliveryPlan(STRATEGY_FILE_ID if entries else "empty", entries)

    entries = []
    for index, asset in enumerate(media_assets):
        asset_id = str(asset.get("asset_id") or "").strip()
        source_url = (str(asset.get("source_url") or "").strip()
                      or str(asset.get("sourceUrl") or "").strip())
        mime_type = str(asset.get("mime_type") or "").strip() or None
        local = local_items[index] if index < len(local_items) else None
        local_file_id = (str(local.get("file_id") or "").strip()
                         if isinstance(local, dict) else "")
        if local_file_id:
            entries.append(MediaPlanEntry(
                index=index,
                kind=MediaKind.PHOTO.value,
                strategy=STRATEGY_FILE_ID,
                asset_id=asset_id,
                file_id=local_file_id,
                mime_type=mime_type,
                filename=(str(local.get("filename") or "") or None),
            ))
        else:
            entries.append(MediaPlanEntry(
                index=index,
                kind=MediaKind.PHOTO.value,
                strategy=STRATEGY_REMOTE_URL,
                asset_id=asset_id,
                source_url=source_url,
                mime_type=mime_type,
            ))

    # Leftover local items (e.g. documents) keep their zero-reupload fast path.
    for index in range(len(media_assets), len(local_items)):
        item = local_items[index]
        file_id = str(item.get("file_id") or "").strip()
        if not file_id:
            continue
        is_doc = index >= len(media_items)
        kind = _asset_kind_to_media_kind(
            item.get("type") or ("document" if is_doc else "photo")
        )
        entries.append(MediaPlanEntry(
            index=index,
            kind=kind,
            strategy=STRATEGY_FILE_ID,
            file_id=file_id,
            filename=(str(item.get("filename") or "") or None),
        ))
    return MediaDeliveryPlan(_overall_strategy(entries), entries)
