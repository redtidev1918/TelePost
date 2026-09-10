"""PTB-backed Telegram delivery gateway.

Implements the application's :class:`TelegramDeliveryPort`: one
``deliver(DeliveryRequest) -> DeliveryResult`` call. It composes the pure
planner, the PTB sender and (for ``ReplyMode.DISCUSSION``) the discussion
strategy. No business/submission/DB knowledge lives here.

The delivered PTB ``Message`` objects are attached to each
:class:`DeliveredMessage` as ``raw`` for legacy adapter code that still needs
them (the new application layer never touches ``raw``).
"""
from __future__ import annotations

import logging
from typing import Optional

from ...domain.delivery import (
    DeliveredMessage,
    DeliveryRequest,
    DeliveryResult,
    ReplyMode,
)
from . import discussion as discussion_mod
from .executor import execute_plan
from .planner import PlanningOrder, plan_delivery
from .preparation import reclassify_oversized
from .sender import PTBSender, timeout_kwargs

logger = logging.getLogger(__name__)

DEFAULT_SEND_TIMEOUT_SECONDS = 120.0


class PTBTelegramDeliveryGateway:
    def __init__(self, bot, *, send_timeout: Optional[float] = None,
                 album_size: int = 10, discussion=None,
                 reclassify_photos: bool = False):
        self._bot = bot
        self._timeout = send_timeout
        self._album_size = album_size
        self._discussion = discussion  # injected strategy (legacy seam)
        self._reclassify_photos = reclassify_photos

    def _timeouts(self) -> dict:
        return timeout_kwargs(self._timeout or DEFAULT_SEND_TIMEOUT_SECONDS)

    async def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        if request.reply_mode is ReplyMode.DISCUSSION and len(request.items) > 1:
            return await self._deliver_discussion(request)
        return await self._deliver_chain(request)

    async def _deliver_chain(self, request: DeliveryRequest) -> DeliveryResult:
        items = request.items
        if self._reclassify_photos:
            items = reclassify_oversized(items)
        plan = plan_delivery(
            items,
            album_size=request.album_size or self._album_size,
            reply_mode=request.reply_mode,
            anchor_message_id=request.reply_to_message_id,
            ordering=PlanningOrder.FAMILY,
        )
        sender = PTBSender(self._bot, request.chat_id, timeouts=self._timeouts())
        return await execute_plan(plan, sender, caption=request.caption)

    async def _deliver_discussion(self, request: DeliveryRequest) -> DeliveryResult:
        strategy = self._discussion or discussion_mod.DiscussionStrategy(
            self, self._bot
        )
        return await strategy.deliver(request)
