"""MediaAsset domain model (Batch 5, RFC telepost-media-asset-model).

Canonical delivery-asset facts, shared by the wire validator, the SQLite
repository and the DeliveryPlanner. The class carries NO transport concern:
``file_id`` / ``file_unique_id`` are optional Telegram cache facts observed
after a confirmed send (Step 12), never transport inputs.

Wire contract (unchanged): ``{asset_id, kind, source_url, mime_type?}``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping

VALID_KINDS = frozenset({"image"})

#: PixivFlow novel covers ride the same ``kind: image`` wire contract, but their
#: canonical ``asset_id`` ends with a dedicated pixivKind suffix
#: (``pixiv:<workId>:novelcover``). Inline novel illustrations keep their
#: ``uploadedimage`` / ``pixivimage`` ids, so the two roles never collide and
#: legacy payloads (no cover asset at all) stay valid.
NOVEL_COVER_ASSET_SUFFIX = ":novelcover"


def is_novel_cover_asset(asset: Any) -> bool:
    """True when this canonical asset is the novel's real cover (not body art)."""
    if isinstance(asset, MediaAsset):
        return asset.asset_id.endswith(NOVEL_COVER_ASSET_SUFFIX)
    if isinstance(asset, Mapping):
        return str(asset.get("asset_id") or "").endswith(NOVEL_COVER_ASSET_SUFFIX)
    return False


class DeliveryVariant(str, Enum):
    """How a canonical asset may reach Telegram."""

    TELEGRAM_FILE_ID = "telegram_file_id"
    REMOTE_URL = "remote_url"
    LOCAL_UPLOAD = "local_upload"


@dataclass(frozen=True)
class MediaAsset:
    asset_id: str
    kind: str
    source_url: str
    mime_type: str = ""
    file_id: str = ""
    file_unique_id: str = ""

    # ---- constructors ----
    @classmethod
    def from_wire(cls, item: Mapping[str, Any]) -> "MediaAsset":
        """Validate + clean one wire payload (same rules the API enforced).

        Raises ValueError with the historical messages so callers keep their
        exact HTTP 400 bodies.
        """
        asset_id = str(item.get("asset_id") or "").strip()
        kind = str(item.get("kind") or "image").strip()
        source_url = str(item.get("source_url") or "").strip()
        if not asset_id:
            raise ValueError("media_assets 每项必须包含 asset_id")
        if kind not in VALID_KINDS:
            raise ValueError(f"media_assets kind 只支持 {'/'.join(sorted(VALID_KINDS))}")
        if not source_url.startswith(("http://", "https://")):
            raise ValueError("media_assets source_url 必须是 http(s) URL")
        return cls(
            asset_id=asset_id[:200],
            kind=kind,
            source_url=source_url[:2048],
            mime_type=str(item.get("mime_type") or "")[:100],
            file_id=str(item.get("file_id") or "").strip()[:512],
            file_unique_id=str(item.get("file_unique_id") or "").strip()[:512],
        )

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "MediaAsset":
        """Build from a media_asset_refs row (already cleaned at write time)."""
        return cls(
            asset_id=str(row["asset_id"]),
            kind=str(row["kind"]),
            source_url=str(row["source_url"]),
            mime_type=str(row["mime_type"] or ""),
            file_id=str(row["file_id"] or ""),
            file_unique_id=str(row["file_unique_id"] or ""),
        )

    @classmethod
    def coerce(cls, value: Any) -> "MediaAsset":
        """Accept a MediaAsset or a plain dict (planner boundary tolerance)."""
        if isinstance(value, MediaAsset):
            return value
        if isinstance(value, Mapping):
            return cls(
                asset_id=str(value.get("asset_id") or "").strip(),
                kind=str(value.get("kind") or "image").strip(),
                source_url=str(
                    value.get("source_url") or value.get("sourceUrl") or ""
                ).strip(),
                mime_type=str(value.get("mime_type") or value.get("mimeType") or ""),
                file_id=str(value.get("file_id") or value.get("fileId") or ""),
                file_unique_id=str(
                    value.get("file_unique_id") or value.get("fileUniqueId") or ""
                ),
            )
        raise TypeError(f"cannot coerce {type(value).__name__} to MediaAsset")

    # ---- serialization ----
    def to_dict(self) -> Dict[str, Any]:
        """Wire/storage shape (the shape every JSON boundary has emitted)."""
        return {
            "asset_id": self.asset_id,
            "kind": self.kind,
            "source_url": self.source_url,
            "mime_type": self.mime_type,
            "file_id": self.file_id,
            "file_unique_id": self.file_unique_id,
        }
