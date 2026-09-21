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
import os
from typing import Optional

from ...domain.delivery import (
    MEDIA_GROUP_CAPACITY,
    DeliveredMessage,
    DeliveryRequest,
    DeliveryResult,
    ReplyMode,
)
from . import discussion as discussion_mod
from .executor import execute_plan, materialize_remote
from .planner import plan_delivery
from .preparation import cleanup_prepared, reclassify_oversized
from .sender import PTBSender, timeout_kwargs

logger = logging.getLogger(__name__)

DEFAULT_SEND_TIMEOUT_SECONDS = 120.0


class PTBTelegramDeliveryGateway:
    def __init__(self, bot, *, send_timeout: Optional[float] = None,
                 album_size: int = MEDIA_GROUP_CAPACITY, discussion=None,
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
        materialized_paths: list = []
        if any(item.is_remote for item in items):
            materialized_items = []
            for item in items:
                if not item.is_remote:
                    materialized_items.append(item)
                    continue
                local_item = await materialize_remote(item)
                if local_item is None:
                    for path in materialized_paths:
                        try:
                            os.unlink(path)
                        except OSError:
                            pass
                    return DeliveryResult.failed(
                        "remote media materialization failed",
                        retryable=True,
                    )
                materialized_paths.append(local_item.local_path)
                materialized_items.append(local_item)
            items = materialized_items
        try:
            plan = plan_delivery(
                items,
                album_size=request.album_size or self._album_size,
                reply_mode=request.reply_mode,
                anchor_message_id=request.reply_to_message_id,
            )
            sender = PTBSender(self._bot, request.chat_id, timeouts=self._timeouts())
            return await execute_plan(plan, sender, caption=request.caption)
        finally:
            cleanup_prepared(items)
            for path in materialized_paths:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    async def _deliver_discussion(self, request: DeliveryRequest) -> DeliveryResult:
        strategy = self._discussion or discussion_mod.DiscussionStrategy(
            self, self._bot
        )
        return await strategy.deliver(request)
