"""Editorial Revision domain (§editorial).

A reviewer edits a *Revision*, never the submitter's *Original* submission.
The original review row (pending_reviews) is immutable historical evidence.
This module owns:

* the snapshot shape (the editable content plus media order/removal);
* the structured ChangeSet computed by comparing base vs edited snapshots;
* the human-readable change summary;
* the review-generation guard used when finalizing/publishing.

The Review FSM answers "may this review be published?"; the Revision answers
"which content version is published?". They are orthogonal — a draft revision
never enters the Review FSM, and finalization never changes the review status.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

# --- supported editable fields (v1, §18) -----------------------------------
EDITABLE_FIELDS = ("title", "note", "tags", "link", "spoiler")

SNAPSHOT_FIELDS = (*EDITABLE_FIELDS, "media_order", "removed")

# Reviewer-selected severity (§45): minor vs substantive (requires UI warning).
MINOR = "minor"
SUBSTANTIVE = "substantive"


class RevisionStatus(str, Enum):
    DRAFT = "draft"
    FINALIZED = "finalized"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"


TERMINAL_REVISION_STATES = (RevisionStatus.PUBLISHED.value, RevisionStatus.SUPERSEDED.value)


@dataclass
class Snapshot:
    """One immutable content version. ``media_order`` indexes into the ORIGINAL
    media list (photo+document merged, in submission order); ``removed`` lists
    original indexes excluded from publication. Originals are never deleted —
    removal only excludes indexes from this revision's publication subset."""

    title: str = ""
    note: str = ""
    tags: str = ""
    link: str = ""
    spoiler: bool = False
    media_order: List[int] = field(default_factory=list)
    removed: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "title": self.title,
            "note": self.note,
            "tags": self.tags,
            "link": self.link,
            "spoiler": bool(self.spoiler),
            "media_order": list(self.media_order),
            "removed": list(self.removed),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Snapshot":
        data = data or {}
        try:
            order = [int(i) for i in (data.get("media_order") or [])]
        except (TypeError, ValueError):
            order = []
        try:
            removed = [int(i) for i in (data.get("removed") or [])]
        except (TypeError, ValueError):
            removed = []
        return cls(
            title=str(data.get("title") or ""),
            note=str(data.get("note") or ""),
            tags=str(data.get("tags") or ""),
            link=str(data.get("link") or ""),
            spoiler=bool(data.get("spoiler")),
            media_order=order,
            removed=removed,
        )

    @classmethod
    def from_review(cls, row) -> "Snapshot":
        """Base snapshot of an ORIGINAL submission (review row)."""
        media_count = _original_media_count(row)
        return cls(
            title=row["title"] or "",
            note=row["note"] or "",
            tags=row["tags"] or "",
            link=row["link"] or "",
            spoiler=bool(row["spoiler"]),
            media_order=list(range(media_count)),
        )

    def ordered_indexes(self, removed: Optional[List[int]] = None) -> List[int]:
        """Publication subset of original media: ordered, minus removals.

        A snapshot with no explicit order falls back to the original order
        (0..N-1, where N = order-length + removed-count + 1 is a bounded
        upper bound used only for imported/legacy snapshots)."""
        excluded = set(removed if removed is not None else self.removed)
        if not self.media_order:
            if not self.removed:
                return []
            return [i for i in range(self.removed[-1] + 1) if i not in excluded]
        return [i for i in self.media_order if i not in excluded]


def _original_media_count(row) -> int:
    import json as _json
    try:
        media = _json.loads(row["media_json"] or "[]")
    except (TypeError, ValueError):
        media = []
    try:
        docs = _json.loads(row["documents_json"] or "[]")
    except (TypeError, ValueError):
        docs = []
    return len(media) + len(docs)


def tags_list(tags: str) -> List[str]:
    """Split AND normalize a tags string the same way the caption formatter does
    (whitespace-separated, #-prefixed). Purely presentational for change diffing."""
    return [t for t in re.split(r"[\s,，]+", (tags or "").strip()) if t]


def change_set(base: Snapshot, edited: Snapshot) -> Dict[str, object]:
    """Structured field-level diff (generated server/domain-side, §21)."""
    changes: Dict[str, object] = {}
    if base.title != edited.title:
        changes["title"] = {"before": base.title, "after": edited.title}
    if base.note != edited.note:
        changes["note"] = {"changed": True}
    base_tags = set(tags_list(base.tags))
    edited_tags = set(tags_list(edited.tags))
    if base_tags != edited_tags:
        changes["tags"] = {
            "added": sorted(edited_tags - base_tags),
            "removed": sorted(base_tags - edited_tags),
        }
    if base.link != edited.link:
        changes["link"] = {"before": base.link, "after": edited.link}
    if base.spoiler != edited.spoiler:
        changes["spoiler"] = {"before": base.spoiler, "after": bool(edited.spoiler)}
    removed = sorted(set(base.removed) | (set(edited.removed) - set(base.removed)))
    if edited.media_order != base.media_order and edited.media_order:
        changes["media"] = {"reordered": edited.media_order != base.media_order,
                            "removed": removed}
    elif removed:
        changes["media"] = {"reordered": False, "removed": removed}
    return changes


def change_summary(changes: Dict[str, object]) -> List[str]:
    """Human-readable summary lines for the Telegram notification (§44)."""
    lines: List[str] = []
    if "title" in changes:
        lines.append("修正标题措辞")
    if "note" in changes:
        lines.append("修改简介")
    if "tags" in changes:
        tags = changes["tags"]
        added = tags.get("added") or []
        removed = tags.get("removed") or []
        if added:
            lines.append(f"新增 {len(added)} 个标签")
        if removed:
            lines.append(f"移除 {len(removed)} 个标签")
    if "link" in changes:
        lines.append("调整来源链接")
    if "spoiler" in changes:
        lines.append("调整剧透设置")
    if "media" in changes:
        media = changes["media"]
        removed = media.get("removed") or []
        if media.get("reordered"):
            lines.append("调整图片顺序")
        if removed:
            lines.append(f"移除 {len(removed)} 个附件")
    if not lines:
        lines.append("仅调整格式/排版")
    return lines


def is_editor_change_visible(change_set_data: Dict[str, object]) -> bool:
    """Whether a submittable notification should mention changes (any field)."""
    return bool(change_set_data)