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


# ---- the on_sent branch is a second adapter over execute_plan -------------

_MARKUP = object()


class _FakeSender:
    """Stands in for PTBSender; records whatever markup it is asked to build."""

    def __init__(self, *args, **kwargs):
        pass

    def _navigation_markup(self, navigation):
        return _MARKUP if navigation else None


def _patch_on_sent_path(monkeypatch):
    from telepost.domain.delivery import DeliveredMessage, DeliveryResult
    from telepost.telegram.delivery import executor

    async def fake_execute_plan(plan, sender, *, caption=None,
                                root_reply_markup=None, on_sent=None):
        fake_execute_plan.seen = root_reply_markup
        return DeliveryResult.delivered([
            DeliveredMessage(chat_id="@channel", message_id=1,
                             kind=MediaKind.DOCUMENT)
        ])

    fake_execute_plan.seen = "not-called"
    monkeypatch.setattr(publish, "PTBSender", _FakeSender)
    monkeypatch.setattr(executor, "execute_plan", fake_execute_plan)
    gateway = MagicMock()
    gateway._bot = MagicMock()
    gateway._timeouts.return_value = {}
    return gateway, fake_execute_plan


@pytest.mark.asyncio
async def test_on_sent_branch_forwards_root_markup(monkeypatch):
    gateway, fake = _patch_on_sent_path(monkeypatch)
    nav = read_online_navigation(PREVIEW_URL)

    result = await publish._execute_with_on_sent(
        gateway, _request(root_navigation=[nav]), lambda messages: None
    )

    assert result.ok, result.reason
    assert fake.seen is _MARKUP, (
        "the on_sent branch must not drop the root reply markup"
    )


@pytest.mark.asyncio
async def test_on_sent_branch_without_navigation_sends_no_markup(monkeypatch):
    gateway, fake = _patch_on_sent_path(monkeypatch)

    result = await publish._execute_with_on_sent(
        gateway, _request(), lambda messages: None
    )

    assert result.ok, result.reason
    assert fake.seen is None, "empty navigation must produce no keyboard"
