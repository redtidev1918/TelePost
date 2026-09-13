"""Pure media-delivery planning (no Telegram I/O, no PTB imports).

Turns an ordered list of :class:`MediaItem` into concrete send batches:

* photo/video share visual albums (Telegram ``sendMediaGroup``);
* animations (GIF) can never be in an album → always standalone;
* audio is always standalone;
* documents form their own homogeneous albums;
* a run of one compatible item is also a single message (keeps its caption);
* each album holds at most ``album_size`` items (Telegram cap = 10).

Two orderings are supported because channel delivery and review-chat preview
historically differ:

* ``INPUT``  – **default**: keep the original artwork order and split it into
  maximal same-family runs. Telegram only forces a split when a family genuinely
  cannot share a media group (animation/audio are never album members, and
  documents are only homogeneous with documents), so a gallery whose items are
  all photos stays one ordered album instead of being regrouped into "photos
  first, documents last";
* ``FAMILY`` – stable family order visual → animation → audio → document, used
  by channel publishing (preserves ``handlers.publish._item_batches``).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from ...domain.delivery import MediaItem, MediaKind, ReplyMode


class BatchKind(str, Enum):
    ALBUM = "album"
    SINGLE = "single"


#: Kinds that Telegram accepts inside sendMediaGroup.
ALBUM_FAMILIES = {
    MediaKind.PHOTO: "visual",
    MediaKind.VIDEO: "visual",
    MediaKind.DOCUMENT: "document",
}
#: Family keys are the plain strings returned by :func:`family_of` (a
#: ``MediaKind`` member is a ``str`` subclass, so mixing both shapes only works
#: by accident of str-mixin hashing — keep this dict string-only).
FAMILY_ORDER = {"visual": 0, "animation": 1, "audio": 2, "document": 3}


def family_of(kind: MediaKind) -> str:
    """Album family for a kind; animation/audio are standalone singleton families."""
    return ALBUM_FAMILIES.get(kind, kind.value)


@dataclass(frozen=True)
class Batch:
    kind: BatchKind
    family: str
    items: List[MediaItem]

    @property
    def is_album(self) -> bool:
        return self.kind is BatchKind.ALBUM


class PlanningOrder(str, Enum):
    FAMILY = "family"
    INPUT = "input"


@dataclass(frozen=True)
class DeliveryPlan:
    batches: List[Batch]
    reply_mode: ReplyMode
    anchor_message_id: Optional[int] = None

    def __iter__(self):
        return iter(self.batches)

    def __len__(self) -> int:
        return len(self.batches)


def _chunk_runs(ordering: PlanningOrder, items: List[MediaItem],
                album_size: int) -> List[tuple]:
    """Return ``(family, items)`` runs of at most ``album_size``."""
    if ordering is PlanningOrder.FAMILY:
        ordered = sorted(
            items,
            key=lambda it: FAMILY_ORDER.get(family_of(it.kind), 9),
        )
    else:
        ordered = list(items)

    runs: List[tuple] = []
    for item in ordered:
        fam = family_of(item.kind)
        albumable = fam in ("visual", "document")
        if (
            albumable
            and runs
            and runs[-1][0] == fam
            and len(runs[-1][1]) < album_size
        ):
            runs[-1][1].append(item)
        else:
            runs.append((fam, [item]))
    return runs


def plan_delivery(items: List[MediaItem], *, album_size: int = 10,
                  reply_mode: ReplyMode = ReplyMode.CHAIN,
                  anchor_message_id: Optional[int] = None,
                  ordering: PlanningOrder = PlanningOrder.INPUT) -> DeliveryPlan:
    """Partition items into batches; album-capable runs longer than one become albums.

    The default ``INPUT`` ordering keeps the artwork order the submitter sent:
    items only leave their run when Telegram forbids sharing a media group.
    """
    if album_size < 1:
        raise ValueError("album_size must be >= 1")
    batches: List[Batch] = []
    for fam, batch_items in _chunk_runs(ordering, items, album_size):
        if fam in ("visual", "document") and len(batch_items) > 1:
            batches.append(Batch(BatchKind.ALBUM, fam, batch_items))
        else:
            batches.append(Batch(BatchKind.SINGLE, fam, batch_items))
    return DeliveryPlan(
        batches=batches,
        reply_mode=reply_mode,
        anchor_message_id=anchor_message_id,
    )
