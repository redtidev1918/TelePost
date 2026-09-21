"""Remote_url → local materialization fallback (webpage_curl_failed)."""
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from telepost.domain.delivery import LocalFile, MediaItem, MediaKind, RemoteUrl
from telepost.telegram.delivery.executor import (
    NetworkFailure,
    execute_plan,
    is_remote_fetch_error,
)
from telepost.telegram.delivery.planner import plan_delivery


def _fake_message(index):
    return SimpleNamespace(
        message_id=index + 1,
        chat=SimpleNamespace(id=-100123),
        photo=[SimpleNamespace(file_id=f"F{index}")],
        video=None,
        animation=None,
        audio=None,
        document=None,
    )


def _remote_item():
    return MediaItem(
        MediaKind.PHOTO,
        RemoteUrl("https://proxy.example/pixiv/7.png"),
        spoiler=True,
    )


def test_recognizes_telegram_url_fetch_failure():
    assert is_remote_fetch_error(RuntimeError("webpage_curl_failed"))
    assert is_remote_fetch_error(RuntimeError("Failed to fetch media URL"))
    assert not is_remote_fetch_error(RuntimeError("photo_too_big"))


@pytest.mark.asyncio
async def test_remote_fetch_failure_materializes_and_retries(tmp_path):
    item = _remote_item()
    plan = plan_delivery([item])
    local_path = str(tmp_path / "materialized.png")
    with open(local_path, "wb") as fh:
        fh.write(b"png")

    local_item = MediaItem(
        MediaKind.PHOTO,
        LocalFile(local_path, "materialized.png", temporary=True),
        spoiler=True,
    )
    calls = []
    sender = AsyncMock()

    async def send_single(send_item, *, reply_to=None, caption=None):
        calls.append(send_item)
        if isinstance(send_item.source, RemoteUrl):
            raise RuntimeError('Failed to send message #7 with the error message "webpage_curl_failed"')
        return _fake_message(0)

    sender.send_single.side_effect = send_single

    with patch(
        "telepost.telegram.delivery.executor.materialize_remote",
        new=AsyncMock(return_value=local_item),
    ):
        result = await execute_plan(plan, sender, caption="cap")

    assert result.ok
    assert len(calls) == 2
    assert isinstance(calls[1].source, LocalFile)
    assert calls[1].local_path == local_path
    assert os.path.exists(local_path) is False  # temp fallback cleaned up


@pytest.mark.asyncio
async def test_remote_failure_without_materialization_fails_cleanly():
    plan = plan_delivery([_remote_item()])
    sender = AsyncMock()
    sender.send_single.side_effect = RuntimeError(
        'Failed to send message #7 with the error message "webpage_curl_failed"'
    )

    with patch(
        "telepost.telegram.delivery.executor.materialize_remote",
        new=AsyncMock(return_value=None),
    ):
        result = await execute_plan(plan, sender, caption=None)

    assert result.state.value == "failed"
    assert "remote media unavailable" in result.reason


@pytest.mark.asyncio
async def test_network_failure_with_url_fetch_marker_falls_back(tmp_path):
    plan = plan_delivery([_remote_item()])
    local_path = str(tmp_path / "materialized.png")
    with open(local_path, "wb") as fh:
        fh.write(b"png")
    local_item = MediaItem(
        MediaKind.PHOTO,
        LocalFile(local_path, "materialized.png", temporary=True),
        spoiler=True,
    )
    sender = AsyncMock()

    async def send_single(send_item, *, reply_to=None, caption=None):
        if isinstance(send_item.source, RemoteUrl):
            raise NetworkFailure(
                'Failed to send message #7 with the error message '
                '"webpage_curl_failed"',
                original=RuntimeError("webpage_curl_failed"),
            )
        return _fake_message(0)

    sender.send_single.side_effect = send_single
    with patch(
        "telepost.telegram.delivery.executor.materialize_remote",
        new=AsyncMock(return_value=local_item),
    ):
        result = await execute_plan(plan, sender, caption=None)

    assert result.ok
    assert os.path.exists(local_path) is False
