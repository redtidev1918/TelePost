"""Discussion-group publication strategy.

Workflow (previously embedded in ``handlers/publish._deliver_discussion``):

1. send only the cover (first item) to the channel;
2. wait for Telegram's automatic forward of that post into the linked
   discussion group to obtain the anchor message;
3. send the remaining items into the discussion group, all attached to the
   anchor;
4. on failure roll back conservatively – delete only messages confirmed to
   exist; never blindly retry a request whose response was lost (that could
   duplicate an album Telegram actually accepted).

Determinate failures are rolled back and retried exactly once. An
*uncertain* failure (response lost after the comment album was sent) rolls
back whatever is known and stops: a human must verify the channel/discussion.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional, Tuple

from ...domain.delivery import (
    DeliveryRequest,
    DeliveryResult,
    DeliveryState,
    ReplyMode,
)
from .executor import execute_plan
from .planner import PlanningOrder, plan_delivery
from .preparation import reclassify_oversized
from .registry import ForwardRegistry, default_registry
from .sender import PTBSender

logger = logging.getLogger(__name__)

FORWARD_TIMEOUT_SECONDS = 10.0


class DiscussionDeliveryError(RuntimeError):
    """Discussion delivery failed.

    ``uncertain=True`` means partial landing cannot be ruled out; the caller
    must not auto-retry. ``sent`` groups confirmed messages as
    ``{"cover": [(chat_id, msg_id)], "anchor": [...], "rest": [...]}``.
    """

    def __init__(self, message: str, *, uncertain: bool = False,
                 sent: Optional[dict] = None):
        super().__init__(message)
        self.uncertain = uncertain
        self.sent = sent if sent is not None else {"cover": [], "anchor": [], "rest": []}


Waiter = Callable[[int, int], Awaitable[Tuple[int, int]]]
Scanner = Callable[[int], Awaitable[Optional[Tuple[int, int, int]]]]
Rollback = Callable[[dict], Awaitable[bool]]


class DiscussionStrategy:
    """Channel cover + discussion-thread strategy.

    A chain gateway is used for the cover post; the remaining items are
    planned/sent directly into the discussion chat so an ``on_sent`` callback
    can record every message that lands (needed for a complete rollback).

    Forward wait/scan and rollback are injectable so the legacy handler module
    and tests can patch them; production wiring uses the shared
    :class:`ForwardRegistry` fed by the webhook update hook.
    """

    def __init__(self, gateway, bot, *,
                 registry: ForwardRegistry = default_registry,
                 forward_timeout: float = FORWARD_TIMEOUT_SECONDS,
                 waiter: Optional[Waiter] = None,
                 scanner: Optional[Scanner] = None,
                 rollback: Optional[Rollback] = None,
                 retry_sleep: float = 2.0):
        self._gateway = gateway
        self._bot = bot
        self._registry = registry
        self._forward_timeout = forward_timeout
        self._waiter = waiter
        self._scanner = scanner
        self._rollback = rollback
        self._retry_sleep = retry_sleep

    async def _wait_forward(self, channel_id: int,
                            message_id: int) -> Tuple[int, int]:
        if self._waiter is not None:
            return await self._waiter(channel_id, message_id)
        return await self._registry.wait_for_forward(
            channel_id, message_id, self._forward_timeout
        )

    async def _scan(self, channel_id: int):
        if self._scanner is not None:
            return await self._scanner(channel_id)
        return await self._registry.scan_recent(channel_id)

    async def _delete(self, chat_id: int, message_id: int) -> bool:
        try:
            await self._bot.delete_message(chat_id=chat_id, message_id=message_id)
            return True
        except Exception as exc:
            msg = str(exc).lower()
            if "not found" in msg or "message can't be deleted" in msg:
                return True
            logger.exception("回滚删除消息失败 chat=%s msg=%s", chat_id, message_id)
            return False

    async def _do_rollback(self, sent: dict) -> bool:
        if self._rollback is not None:
            return await self._rollback(sent)
        clean = True
        for chat_id, msg_id in (
            sent.get("rest", []) + sent.get("anchor", []) + sent.get("cover", [])
        ):
            clean = await self._delete(chat_id, msg_id) and clean
        return clean

    async def _attempt(self, request: DeliveryRequest, linked_chat_id: Optional[int]):
        items = request.items
        sent = {"cover": [], "anchor": [], "rest": []}

        # Stage 1: channel cover.
        cover_result = await self._gateway.deliver(
            DeliveryRequest(
                chat_id=request.chat_id,
                items=items[:1],
                caption=request.caption,
                spoiler=request.spoiler,
                reply_mode=ReplyMode.POST,
                album_size=request.album_size,
            )
        )
        if cover_result.is_uncertain:
            found = await self._scan(int(request.chat_id))
            if found is not None:
                cover_id, dchat, dmsg = found
                sent["cover"] = [(int(request.chat_id), cover_id)]
                sent["anchor"] = [(dchat, dmsg)]
                raise DiscussionDeliveryError(
                    "频道首贴发送响应丢失，已反查到帖子并回滚", sent=sent
                )
            raise DiscussionDeliveryError(
                "频道首贴发送响应丢失，未在讨论区发现转发，重发一次", sent=sent
            )
        if not cover_result.ok:
            raise DiscussionDeliveryError(
                f"频道首贴发送失败：{cover_result.reason}", sent=sent
            )
        main = cover_result.main_message
        sent["cover"] = [(m.chat_id, m.message_id) for m in cover_result.messages]

        # Stage 2: wait for the auto-forward anchor.
        try:
            dchat, dmsg = await self._wait_forward(
                int(request.chat_id), main.message_id
            )
        except Exception:
            raise DiscussionDeliveryError(
                "等待频道帖转发到讨论组超时", sent=sent
            )
        if linked_chat_id is not None and dchat != linked_chat_id:
            raise DiscussionDeliveryError(
                "频道自动转发落到了非预期讨论组", sent=sent
            )
        sent["anchor"] = [(dchat, dmsg)]

        # Stage 3: remaining items into the discussion thread.
        rest_items = items[1:]
        rest_known: list = []
        if rest_items:
            def _collect(messages):
                rest_known.extend((m.chat_id, m.message_id) for m in messages)

            rest_result = await self._send_rest(
                rest_items, dchat, dmsg, request, _collect
            )
            if rest_result.is_uncertain:
                sent["rest"] = rest_known
                raise DiscussionDeliveryError(
                    "评论区相册发送响应丢失，可能已部分送达，不自动重试",
                    uncertain=True, sent=sent,
                )
            if not rest_result.ok:
                sent["rest"] = rest_known
                raise DiscussionDeliveryError(
                    f"评论区相册发送失败：{rest_result.reason}", sent=sent
                )
            sent["rest"] = rest_known
            messages = list(cover_result.messages) + list(rest_result.messages)
        else:
            messages = list(cover_result.messages)
        return messages, main

    async def _send_rest(self, rest_items, dchat, dmsg, request, on_sent):
        items = reclassify_oversized(list(rest_items))
        plan = plan_delivery(
            items,
            album_size=request.album_size,
            reply_mode=ReplyMode.POST,
            anchor_message_id=dmsg,
            ordering=PlanningOrder.FAMILY,
        )
        # Caption is owned by the channel cover; the discussion rest has none.
        sender = PTBSender(
            self._bot, dchat,
            timeouts=self._gateway._timeouts() if hasattr(self._gateway, "_timeouts") else None,
        )
        return await execute_plan(plan, sender, caption=None, on_sent=on_sent)

    async def deliver(self, request: DeliveryRequest,
                      linked_chat_id: Optional[int] = None) -> DeliveryResult:
        last: Optional[DiscussionDeliveryError] = None
        for attempt_no in (1, 2):
            try:
                messages, main = await self._attempt(request, linked_chat_id)
                return DeliveryResult.delivered(messages, main)
            except DiscussionDeliveryError as exc:
                last = exc
                if exc.uncertain:
                    await self._do_rollback(exc.sent)
                    raise
                if not await self._do_rollback(exc.sent):
                    raise DiscussionDeliveryError(
                        f"{exc}；且回滚未能删净，请人工检查",
                        uncertain=True, sent=exc.sent,
                    )
                if attempt_no == 2:
                    raise
                await asyncio.sleep(self._retry_sleep)
        raise last or DiscussionDeliveryError("评论区发布失败", uncertain=True)
