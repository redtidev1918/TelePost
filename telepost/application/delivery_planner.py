"""MediaAsset Delivery Planner (Step 11).

TelePost decides how to deliver a review's media instead of trusting an
upstream (PixivFlow) domain object wholesale. For each canonical media asset
reference we pick the cheapest *available* Telegram source:

* ``telegram_file_id`` – the review already holds a Telegram file_id (zero
  re-upload), either in the local media JSON or in the Step 12 TelegramMediaCache
  (``media_asset_refs.file_id``); this stays the default fast path.
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
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional

from ..domain.delivery import MediaItem, MediaKind, RemoteUrl, TelegramFileId
from ..domain.media import DeliveryVariant, MediaAsset



def _proxied_source_url(url: str) -> str:
    """Rewrite an allowlisted source URL through the public media proxy.

    Telegram cannot fetch Pixiv CDN directly because the request needs a
    Pixiv ``Referer``.  The fixed-upstream proxy supplies that header and never
    acts as an open proxy: if no proxy is configured or the host is not in the
    exact allowlist, the URL is returned unchanged.
    """
    from config.settings import MEDIA_PROXY_BASE_URL, MEDIA_PROXY_HOSTS
    if not MEDIA_PROXY_BASE_URL or not MEDIA_PROXY_HOSTS:
        return url
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in MEDIA_PROXY_HOSTS:
        return url
    path = parsed.path or "/"
    return f"{MEDIA_PROXY_BASE_URL}/media/{parsed.hostname}{path}" + (
        f"?{parsed.query}" if parsed.query else ""
    )

STRATEGY_FILE_ID = DeliveryVariant.TELEGRAM_FILE_ID
STRATEGY_REMOTE_URL = DeliveryVariant.REMOTE_URL
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


def _asset_belongs_to_other_work(asset_id: str, work_id: str) -> bool:
    """True only for a ``pixiv:<work_id>:...`` ref whose work is NOT this one.

    Unparseable / non-pixiv asset ids stay (conservative): filtering them would
    silently drop legitimate uploads. A chain's refs are replaced per
    submission, but a refetch replacement that arrives without its own asset
    manifest leaves the SUPERSEDED work's refs on the chain — publishing must
    never ship another work's pictures as this submission's content (review
    208: a 4-page illustration refetch-replaced by a ugoira).
    """
    parts = str(asset_id or "").split(":")
    if len(parts) < 3 or parts[0] != "pixiv":
        return False
    ref_work = parts[1].strip()
    return bool(ref_work) and bool(work_id) and ref_work != work_id


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
                      *, documents: Optional[List[Dict[str, Any]]] = None,
                      work_id: str = "") -> MediaDeliveryPlan:
    """Build a delivery plan from a review's local media + canonical asset refs.

    ``media`` / ``documents`` mirror the raw ``pending_reviews`` JSON arrays
    (``media_json`` / ``documents_json``): each item carries ``file_id`` and
    media type / filename. ``media_assets`` mirrors ``media_asset_refs`` rows
    (``asset_id``, ``kind``, ``source_url``, ``mime_type``).

    Positional pairing (same index) is used because PixivFlow manifests are
    ordered and match the Telegram file list order; a mismatch can ONLY fall
    back to ``remote_url`` and never produces a bogus file_id.

    ``work_id`` drops chain refs that provably belong to a DIFFERENT pixiv
    work (a refetch replacement may leave the superseded work's refs behind).
    """
    assets = [MediaAsset.coerce(a) for a in (media_assets or [])]
    if work_id:
        assets = [a for a in assets
                  if not _asset_belongs_to_other_work(a.asset_id, work_id)]
    media_items = list(media or [])
    document_items = list(documents or [])
    local_items = media_items + document_items

    if not assets:
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
    for index, asset in enumerate(assets):
        asset_id = asset.asset_id
        source_url = asset.source_url
        mime_type = asset.mime_type or None
        # Canonical assets pair media files from the front and documents from
        # the back when they outnumber local files. This handles asset-only
        # novel payloads, where a single TXT must never consume the first cover
        # asset's remote slot. Equal-length payloads keep the documented
        # positional order.
        if len(assets) > len(local_items):
            document_offset = len(assets) - len(document_items)
            if index < len(media_items):
                local = media_items[index]
            elif index >= document_offset:
                local = document_items[index - document_offset]
            else:
                local = None
        else:
            local = local_items[index] if index < len(local_items) else None
        # The indexed local item is authoritative for presentation: a Telegram
        # TXT document must never be sent as a photo, and a staged animation /
        # video / audio file_id must keep its real Telegram type even when the
        # canonical asset ref (or a stale refetch-chain ref) says kind=image
        # (review 208: sendPhoto on an animation file_id is a certain reject).
        local_kind = (
            str(local.get("type") or local.get("kind") or "")
            .strip().lower().replace("-", "_")
            if isinstance(local, dict) else ""
        )
        has_local = local is not None
        is_document = local_kind in ("document", "file") or (
            (has_local and index >= len(media_items)) or (
                not has_local and len(assets) > len(local_items)
                and index >= document_offset
            )
        )
        if is_document:
            kind = MediaKind.DOCUMENT.value
        elif local_kind:
            kind = _asset_kind_to_media_kind(local_kind)
        else:
            kind = MediaKind.PHOTO.value
        local_file_id = (str(local.get("file_id") or "").strip()
                         if isinstance(local, dict) else "")
        cached_file_id = asset.file_id
        chosen_file_id = local_file_id or cached_file_id
        if chosen_file_id:
            entries.append(MediaPlanEntry(
                index=index,
                kind=kind,
                strategy=STRATEGY_FILE_ID,
                asset_id=asset_id,
                file_id=chosen_file_id,
                mime_type=mime_type,
                filename=(str(local.get("filename") or "") or None)
                if isinstance(local, dict) else None,
            ))
        else:
            entries.append(MediaPlanEntry(
                index=index,
                kind=kind,
                strategy=STRATEGY_REMOTE_URL,
                asset_id=asset_id,
                source_url=_proxied_source_url(source_url),
                mime_type=mime_type,
            ))

    # Leftover local items (e.g. documents) keep their zero-reupload fast path.
    for index in range(len(assets), len(local_items)):
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
