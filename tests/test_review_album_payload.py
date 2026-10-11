"""Oversized review uploads retain ordered albums and cleanup evidence."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import NetworkError, TimedOut

from telepost.telegram.review_stager import TelegramReviewStager


def message(item):
    index = item["index"]
    return SimpleNamespace(
        message_id=index + 100,
        photo=[SimpleNamespace(file_id=f"photo-{index}")],
        video=None, animation=None, audio=None, document=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [True, False])
async def test_oversized_album_splits_without_reordering_or_singles(local):
    stager = TelegramReviewStager(AsyncMock(), -100, sleep=AsyncMock())
    items = [{"kind": "photo", "type": "photo", "index": i} for i in range(14)]
    calls = []

    async def album(chunk, caption, spoiler, reply_to):
        calls.append(([i["index"] for i in chunk], caption, reply_to))
        if len(chunk) > 5:
            raise NetworkError("Request Entity Too Large")
        return [message(i) for i in chunk]

    stager._local_album = stager._file_id_album = AsyncMock(side_effect=album)
    stager._local_single = stager._file_id_single = AsyncMock()
    ids, specs = [], []
    media, documents = await stager._stage(
        items, "caption", False, ids, local=local, specs=specs,
    )

    assert [len(c[0]) for c in calls] == [10, 5, 5, 4]
    assert [c[0] for c in calls[1:]] == [list(range(5)), list(range(5, 10)), list(range(10, 14))]
    assert [c[1] for c in calls] == ["caption", "caption", None, None]
    assert [c[2] for c in calls] == [None, None, 104, 109]
    assert ids == list(range(100, 114))
    assert [m["file_id"] for m in media] == [f"photo-{i}" for i in range(14)]
    assert len(specs) == 14
    assert documents == []
    stager._local_single.assert_not_awaited()


@pytest.mark.asyncio
async def test_split_keeps_sent_ids_when_later_album_response_is_uncertain():
    stager = TelegramReviewStager(AsyncMock(), -100, sleep=AsyncMock())
    items = [{"kind": "photo", "index": i} for i in range(10)]

    async def album(chunk, *_):
        if len(chunk) == 10:
            raise NetworkError("Request Entity Too Large")
        if chunk[0]["index"] == 5:
            raise TimedOut("response lost")
        return [message(i) for i in chunk]

    stager._local_album = AsyncMock(side_effect=album)
    stager._local_single = AsyncMock()
    ids, specs = [], []
    with pytest.raises(TimedOut):
        await stager._stage(items, "caption", False, ids, local=True, specs=specs)
    assert ids == list(range(100, 105))
    assert len(specs) == 5
    stager._local_single.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [NetworkError("connection reset"), TimedOut("response lost")])
async def test_uncertain_album_is_never_resent_as_singles(error):
    stager = TelegramReviewStager(AsyncMock(), -100, sleep=AsyncMock())
    stager._local_album = AsyncMock(side_effect=error)
    stager._local_single = AsyncMock()
    with pytest.raises(type(error)):
        await stager._stage(
            [{"kind": "photo"}] * 4, None, False, [], local=True,
        )
    assert stager._local_album.await_count == 1
    stager._local_single.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_413_splitting_is_bounded_and_tracks_singletons():
    stager = TelegramReviewStager(AsyncMock(), -100, sleep=AsyncMock())
    stager._local_album = AsyncMock(side_effect=NetworkError("Payload Too Large"))
    stager._local_single = AsyncMock(side_effect=lambda item, *_: message(item))
    ids = []
    media, _ = await stager._stage(
        [{"kind": "photo", "index": i} for i in range(10)],
        "caption", False, ids, local=True,
    )
    assert stager._local_album.await_count == 9
    assert stager._local_single.await_count == 10
    assert ids == list(range(100, 110))
    assert len(media) == 10
