"""Publication media packing (capacity-first) — the SSOT for album capacity.

One Telegram media group holds at most ``MEDIA_GROUP_CAPACITY`` items
(Telegram's hard cap is 10). Every publication path (chat direct, API direct,
review approval, editorial, refetch replacement) must plan the same way: the
ROOT publication takes the first ``capacity`` ordered items, and the overflow
is chunked into replies of the SAME capacity. The number ``10`` is defined
exactly once here (operator-overridable via ``MEDIA_GROUP_CAPACITY``, clamped
to Telegram's real limit); no handler hardcodes a batch size.

A media group is ONE business root publication even though Telegram models it
as several Messages — the caption, the canonical link and the message id all
refer to the root batch.
"""
from __future__ import annotations

import os
from typing import List, Sequence, Tuple, TypeVar

T = TypeVar("T")

#: Telegram's hard media-group limit.
TELEGRAM_MAX_MEDIA_GROUP_SIZE = 10

#: Operator-overridable publication capacity (telegram's real limit is the cap).
MEDIA_GROUP_CAPACITY = max(
    1,
    min(
        TELEGRAM_MAX_MEDIA_GROUP_SIZE,
        int(os.getenv("MEDIA_GROUP_CAPACITY", str(TELEGRAM_MAX_MEDIA_GROUP_SIZE))),
    ),
)


def _chunk(items: Sequence[T], capacity: int) -> List[List[T]]:
    return [list(items[i:i + capacity]) for i in range(0, len(items), capacity)]


def pack_media(ordered: Sequence[T], capacity: int = MEDIA_GROUP_CAPACITY) -> Tuple[List[T], List[List[T]]]:
    """Capacity-first packing.

    Returns ``(root_batch, overflow_batches)`` where ``root_batch`` holds the
    first ``capacity`` ordered items and each overflow batch holds at most
    ``capacity`` items. With capacity 10: 10 -> (10, []), 11 -> (10, [1]),
    21 -> (10, [10, 1]). Ordering is preserved exactly.
    """
    if capacity < 1:
        raise ValueError("media group capacity must be >= 1")
    if not ordered:
        return [], []
    return list(ordered[:capacity]), _chunk(ordered[capacity:], capacity)