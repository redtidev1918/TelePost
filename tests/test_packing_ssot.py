"""Capacity-first media packing tests (§media-packing, §tests).

Boundary contract with the SSOT capacity: 10 -> root 10, 11 -> root 10 + 1,
21 -> root 10 + 10 + 1. Caption belongs to the root once. The capacity is read
from ONE SSOT (MEDIA_GROUP_CAPACITY) and is never hardcoded in handlers.
"""
import os
from types import SimpleNamespace

import pytest

from telepost.domain.delivery import MediaItem, MediaKind, ReplyMode
from telepost.domain.packing import (
    MEDIA_GROUP_CAPACITY,
    pack_media,
)
from telepost.telegram.delivery.planner import plan_delivery


def _photos(count: int):
    return [
        MediaItem.local("photo", f"/tmp/p{i}.jpg", f"p{i}.jpg") for i in range(count)
    ]


@pytest.mark.parametrize("total", [0, 1, 2, 9, 10, 11, 19, 20, 21])
def test_pack_media_boundaries(total):
    root, overflow = pack_media(_photos(total), capacity=10)
    expected_root = min(total, 10)
    expected_overflow = (total - expected_root + 9) // 10 if total > expected_root else 0
    assert len(root) == expected_root
    assert len(overflow) == expected_overflow
    # 21 -> root 10 + reply 10 + reply 1
    if total == 21:
        assert [len(b) for b in overflow] == [10, 1]
    if total == 11:
        assert [len(b) for b in overflow] == [1]
    # Ordering preserved exactly: root first, then overflow, no reordering.
    flat = list(root) + [item for batch in overflow for item in batch]
    assert [item.filename for item in flat] == [f"p{i}.jpg" for i in range(total)]


def test_plan_delivery_uses_the_same_ssot_capacity():
    plan = plan_delivery(_photos(11))
    kinds = [(batch.kind.value, len(batch.items)) for batch in plan.batches]
    assert kinds == [("album", MEDIA_GROUP_CAPACITY), ("single", 1)]


def test_capacity_is_clamped_to_telegram_limit(monkeypatch):
    monkeypatch.setenv("MEDIA_GROUP_CAPACITY", "999")
    import importlib
    from telepost.domain import packing
    importlib.reload(packing)
    try:
        assert packing.MEDIA_GROUP_CAPACITY == 10
    finally:
        monkeypatch.delenv("MEDIA_GROUP_CAPACITY")
        importlib.reload(packing)


def test_capacity_rejects_zero():
    with pytest.raises(ValueError):
        pack_media(_photos(1), capacity=0)


def test_overflow_has_no_caption_and_root_link_is_the_root_batch():
    """Telegram models a media group as several messages; the business ROOT is
    the whole first batch. Caption lands on the root batch's first item only —
    never repeated on overflow batches."""
    plan = plan_delivery(_photos(21))
    assert plan.batches[0].is_album
    assert len(plan.batches) == 3
    # The first overflow batch is a reply of capacity 1 in this case.
    overflow = plan.batches[1:]
    assert len(overflow[0].items) <= MEDIA_GROUP_CAPACITY
    assert plan.reply_mode is ReplyMode.CHAIN


@pytest.mark.asyncio
async def test_preview_batching_uses_the_same_ssot_capacity(monkeypatch):
    """Regression (§media-packing): handlers/preview_handlers.py hardcoded 10, so
    lowering MEDIA_GROUP_CAPACITY grouped the review preview differently from the
    actual publication (review shows 10, publish sends 5). The preview batches
    must be cut with the same SSOT constant the delivery planner uses.
    """
    from handlers import preview_handlers

    sent = []

    class _Bot:
        async def send_media_group(self, chat_id=None, media=None):
            sent.append(len(media))

    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=42),
        effective_message=SimpleNamespace(chat_id=42),
    )
    context = SimpleNamespace(user_data={}, bot=_Bot())
    monkeypatch.setattr(preview_handlers, "MEDIA_GROUP_CAPACITY", 3)

    media_list = [f"photo:pid{i}:p{i}.jpg" for i in range(7)]
    await preview_handlers._send_preview_media(update, context, media_list, [])

    assert sent == [3, 3, 1]


@pytest.mark.asyncio
async def test_preview_batching_default_capacity_matches_the_planner():
    from handlers import preview_handlers

    sent = []

    class _Bot:
        async def send_media_group(self, chat_id=None, media=None):
            sent.append(len(media))

    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=42),
        effective_message=SimpleNamespace(chat_id=42),
    )
    context = SimpleNamespace(user_data={}, bot=_Bot())

    media_list = [f"photo:pid{i}:p{i}.jpg" for i in range(2 * MEDIA_GROUP_CAPACITY + 1)]
    await preview_handlers._send_preview_media(update, context, media_list, [])

    assert sent == [MEDIA_GROUP_CAPACITY, MEDIA_GROUP_CAPACITY, 1]
