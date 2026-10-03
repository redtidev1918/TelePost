"""The legacy delivery adapter must forward root navigation (§online-reading).

Regression (ROOT CAUSE H): ``PublicationService`` builds the READ_ONLINE button
onto ``DeliveryRequest.root_navigation``, but ``_LegacyDeliveryPort.deliver``
re-issued the call to ``deliver_items_to_chat`` without forwarding it — so every
application-layer publication (API direct post, review approval) silently lost
the Telegraph / 在线阅读 entrance, while the interactive chat path (which passes
``root_navigation`` explicitly) kept it.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from handlers import publish
from telepost.domain.delivery import (
    DeliveryRequest,
    MediaItem,
    MediaKind,
    ReplyMode,
)
from telepost.domain.navigation import read_online_navigation

PREVIEW_URL = "https://telegra.ph/Regression-Novel-10-04"


def _sent_message(message_id=101):
    """A raw PTB-shaped message with no media attributes set."""
    message = MagicMock()
    message.message_id = message_id
    message.chat = None
    message.photo = None
    message.video = None
    message.animation = None
    message.audio = None
    message.document = None
    return message


def _request(*, root_navigation=None):
    return DeliveryRequest(
        chat_id="@channel",
        items=[MediaItem.file_id("document", "TXT_FILE_ID", filename="novel.txt")],
        caption="#novel 测试小说",
        reply_mode=ReplyMode.CHAIN,
        root_navigation=list(root_navigation or []),
    )


@pytest.mark.asyncio
async def test_legacy_port_forwards_root_navigation(monkeypatch):
    sent = _sent_message()
    fake_deliver = AsyncMock(return_value=([sent], sent))
    monkeypatch.setattr(publish, "deliver_items_to_chat", fake_deliver)

    nav = read_online_navigation(PREVIEW_URL)
    result = await publish._LegacyDeliveryPort(object()).deliver(
        _request(root_navigation=[nav])
    )

    assert result.ok, result.reason
    assert fake_deliver.await_count == 1
    forwarded = fake_deliver.await_args.kwargs.get("root_navigation")
    assert [n.action for n in forwarded] == [nav.action], (
        "the legacy adapter must not drop root_navigation"
    )
    assert forwarded[0].url == PREVIEW_URL


@pytest.mark.asyncio
async def test_legacy_port_without_navigation_stays_clean(monkeypatch):
    sent = _sent_message()
    fake_deliver = AsyncMock(return_value=([sent], sent))
    monkeypatch.setattr(publish, "deliver_items_to_chat", fake_deliver)

    result = await publish._LegacyDeliveryPort(object()).deliver(_request())

    assert result.ok, result.reason
    assert fake_deliver.await_args.kwargs.get("root_navigation") == [], (
        "ordinary publications must not grow a navigation keyboard"
    )
