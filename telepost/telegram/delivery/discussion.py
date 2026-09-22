"""Discussion-group publication strategy.

Workflow (previously embedded in ``handlers/publish._deliver_discussion``):

1. fill the channel root publication to media-group capacity;
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
from .planner import family_of, plan_delivery
from .preparation import cleanup_prepared, reclassify_oversized
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
    """Capacity-filled channel root + discussion-thread overflow strategy.

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
        # §discussion-failure-isolation: a confirmed channel root (cover) is the
        # successful Publication and is NEVER deleted because a linked-discussion
        # follow-up (anchor/rest) failed. Only discussion-side artifacts that this
        # strategy posted are cleaned up, best-effort.
        for chat_id, msg_id in (
            sent.get("rest", []) + sent.get("anchor", [])
        ):
            clean = await self._delete(chat_id, msg_id) and clean
        return clean

    async def _attempt(self, request: DeliveryRequest, linked_chat_id: Optional[int]):
        items = request.items
        sent = {"cover": [], "anchor": [], "rest": []}

        plan = plan_delivery(
            items,
            album_size=request.album_size,
            reply_mode=ReplyMode.POST,
        )
        visual_batches = [b for b in plan.batches if b.family == "visual"]
        document_batches = [b for b in plan.batches if b.family == "document"]
        other_batches = [b for b in plan.batches
                         if b.family not in ("visual", "document")]

        root_items = []
        if visual_batches:
            root_items.extend(visual_batches[0].items)
        if document_batches:
            root_items.extend(document_batches[0].items)
        if not root_items and other_batches:
            root_items.extend(other_batches[0].items)

        # Stage 1: the first compatible batch is the whole root publication.
        cover_result = await self._gateway.deliver(
            DeliveryRequest(
                chat_id=request.chat_id,
                items=root_items,
                caption=request.caption,
                spoiler=request.spoiler,
                reply_mode=ReplyMode.CHAIN,
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

        if len(root_items) >= len(request.items):
            return list(cover_result.messages), main

        # Channel root is now CONFIRMED: the channel Publication has happened.
        # The linked-discussion overflow is a follow-up whose failure must NOT
        # roll back (delete) the successful channel root, and must NOT re-run
        # the whole operation (which would re-post a duplicate root)
        # (§discussion-failure-isolation). We keep the cover and report the
        # overflow as dropped / needs verification.
        # Stage 2/3: one anchor per media family. Overflow images reply to the
        # forwarded image root; overflow files reply to the forwarded file root,
        # so each family keeps its own discussion thread.
        root_messages = list(cover_result.messages)
        visual_root_len = len(visual_batches[0].items) if visual_batches else 0
        visual_main = None
        doc_main = None
        # Telegram anchors a linked-discussion thread to the FIRST message of
        # each channel media group. Waiting on a later album member misses the
        # automatic-forward update and silently drops the overflow.
        if visual_batches and root_messages:
            visual_main = root_messages[0]
        if document_batches:
            doc_index = visual_root_len
            if doc_index < len(root_messages):
                doc_main = root_messages[doc_index]

        async def _anchor_for(message):
            if message is None:
                return None
            try:
                dchat, dmsg = await self._wait_forward(
                    int(request.chat_id), message.message_id
                )
            except Exception:
                return None
            if linked_chat_id is not None and dchat != linked_chat_id:
                return None
            return dchat, dmsg

        messages = list(root_messages)

        async def _overflow(group, anchor, label):
            if not group:
                return
            if anchor is None:
                logger.warning(
                    "讨论组转发超时：保留频道主贴，%s溢出未投递 (cover msg=%s)",
                    label, main.message_id,
                )
                return
            dchat, dmsg = anchor
            sent["anchor"].append((dchat, dmsg))
            known: list = []

            def _collect(sent_messages):
                known.extend(
                    (m.chat_id, m.message_id) for m in sent_messages
                )

            try:
                rest_result = await self._send_rest(
                    group, dchat, dmsg, request, _collect
                )
            except Exception as exc:
                for chat_id, msg_id in known:
                    await self._delete(chat_id, msg_id)
                logger.warning(
                    "评论区%s发送异常：保留频道主贴，溢出已回滚 (cover msg=%s): %s",
                    label, main.message_id, type(exc).__name__,
                )
                return
            if rest_result.is_uncertain:
                logger.warning(
                    "评论区%s发送响应不确定：保留频道主贴，请人工核验评论区 (cover msg=%s)",
                    label, main.message_id,
                )
                return
            if not rest_result.ok:
                for chat_id, msg_id in known:
                    await self._delete(chat_id, msg_id)
                logger.warning(
                    "评论区%s发送失败：保留频道主贴，溢出已回滚 (cover msg=%s): %s",
                    label, main.message_id, rest_result.reason,
                )
                return
            sent["rest"].extend(known)
            messages.extend(rest_result.messages)

        rest_visual = [
            item for batch in visual_batches[1:] for item in batch.items
        ]
        rest_doc = [
            item for batch in document_batches[1:] for item in batch.items
        ]
        rest_other = [
            item for batch in other_batches for item in batch.items
        ]
        if visual_batches:
            await _overflow(rest_visual, await _anchor_for(visual_main), "图片")
        if document_batches:
            await _overflow(
                rest_doc + rest_other, await _anchor_for(doc_main), "文件"
            )
        elif rest_other:
            anchor = await _anchor_for(main)
            await _overflow(rest_other, anchor, "其他")

        return messages, main

    async def _send_rest(self, rest_items, dchat, dmsg, request, on_sent):
        items = reclassify_oversized(list(rest_items))
        try:
            plan = plan_delivery(
                items,
                album_size=request.album_size,
                reply_mode=ReplyMode.POST,
                anchor_message_id=dmsg,
            )
            # Caption is owned by the channel cover; the discussion rest has none.
            sender = PTBSender(
                self._bot, dchat,
                timeouts=self._gateway._timeouts() if hasattr(self._gateway, "_timeouts") else None,
            )
            return await execute_plan(plan, sender, caption=None, on_sent=on_sent)
        finally:
            cleanup_prepared(items)

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
