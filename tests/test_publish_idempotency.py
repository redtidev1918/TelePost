"""Direct-publish idempotency (API_REVIEW_REQUIRED=false path).

An ACK loss / client retry must not produce a second channel post:
- same idempotency key  -> idempotent_replay, no second Telegram send
- different key, same work already published -> duplicate_existing, no second send
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import database.db_manager as dbm
from database.db_manager import init_db
from handlers import publish


def _photo_message(message_id: int):
    return SimpleNamespace(
        message_id=message_id,
        chat=None,
        photo=(SimpleNamespace(file_id=f"fid-{message_id}"),),
    )


@pytest.mark.asyncio
async def test_direct_publish_idempotent_replay_then_historical_duplicate(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(dbm, "DB_PATH", str(db_path))
    await init_db()

    deliver = AsyncMock(side_effect=[
        ([_photo_message(100)], _photo_message(100)),
    ])
    monkeypatch.setattr(publish, "deliver_items_to_chat", deliver)

    media = [{"type": "photo", "file_id": "AAA"}]
    common = dict(tags="#t", user_id=1, link="https://www.pixiv.net/artworks/55")

    first = await publish.publish_from_file_ids(
        None, media, [],
        idempotency_key="pixivflow:bot1:illustration:55:slot-a:t1",
        target_id="bot1", work_type="illustration", pixiv_id="55",
        **common,
    )
    assert first["status"] == "published"
    assert deliver.await_count == 1

    # ACK lost: identical intent retried -> replay, no second Telegram send.
    replay = await publish.publish_from_file_ids(
        None, media, [],
        idempotency_key="pixivflow:bot1:illustration:55:slot-a:t1",
        target_id="bot1", work_type="illustration", pixiv_id="55",
        **common,
    )
    assert replay["reused"] is True
    assert replay["reuse_reason"] == "idempotent_replay"
    assert replay["matched_idempotency_key"].endswith(":slot-a:t1")
    assert replay["message_id"] == 100
    assert deliver.await_count == 1

    # New occurrence, different key, same work -> historical duplicate.
    historical = await publish.publish_from_file_ids(
        None, media, [],
        idempotency_key="pixivflow:bot1:illustration:55:slot-b:t1",
        target_id="bot1", work_type="illustration", pixiv_id="55",
        **common,
    )
    assert historical["reused"] is True
    assert historical["reuse_reason"] == "duplicate_existing"
    assert deliver.await_count == 1
