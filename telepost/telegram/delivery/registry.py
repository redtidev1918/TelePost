"""In-memory registry of channel→discussion automatic forwards.

``capture_discussion_forward`` is fed every Telegram update by the channel
listener. When the channel cover post is sent but the HTTP response is lost,
the delivery strategy scans this bounded registry to discover whether Telegram
actually accepted the post (and auto-forwarded it to the linked discussion),
which makes a safe rollback + retry possible.

Deliberately a bounded process-local structure: at most ~200 entries / 60 s of
history, matching the uncertainty window. No external cache is introduced.
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Optional, Tuple

TARGET_TTL_SECONDS = 60.0
MAX_ENTRIES = 200


class ForwardRegistry:
    def __init__(self, *, ttl: float = TARGET_TTL_SECONDS, max_entries: int = MAX_ENTRIES):
        self._ttl = ttl
        self._max = max_entries
        self._forwards: Dict[Tuple[int, int], Tuple[Tuple[int, int], float]] = {}
        self._waiters: Dict[Tuple[int, int], "asyncio.Future"] = {}
        # (source_chat, source_msg, discussion_chat, discussion_msg, at)
        self._recent: List[Tuple[int, int, int, int, float]] = []

    def _now(self) -> float:
        return time.monotonic()

    def _prune(self, now: Optional[float] = None) -> None:
        now = now or self._now()
        for key, (_, seen_at) in list(self._forwards.items()):
            if now - seen_at > self._ttl:
                self._forwards.pop(key, None)
        while self._recent and now - self._recent[0][4] > self._ttl:
            self._recent.pop(0)

    def capture(self, *, source_chat_id: int, source_message_id: int,
                discussion_chat_id: int, discussion_message_id: int) -> None:
        now = self._now()
        self._prune(now)
        key = (source_chat_id, source_message_id)
        target = (discussion_chat_id, discussion_message_id)
        self._recent.append(
            (source_chat_id, source_message_id, discussion_chat_id,
             discussion_message_id, now)
        )
        del self._recent[: -self._max]
        waiter = self._waiters.pop(key, None)
        if waiter is not None and not waiter.done():
            waiter.set_result(target)
        else:
            self._forwards[key] = (target, now)

    def pop_known(self, source_chat_id: int,
                  source_message_id: int) -> Optional[Tuple[int, int]]:
        """Return a captured forward immediately if already seen (consumes it)."""
        self._prune()
        cached = self._forwards.pop((source_chat_id, source_message_id), None)
        return cached[0] if cached is not None else None

    def pop_recent(self, channel_id: int,
                   channel_message_id: int) -> Optional[Tuple[int, int]]:
        self._prune()
        for i, (cid, mid, dchat, dmsg, _t) in enumerate(self._recent):
            if cid == channel_id and mid == channel_message_id:
                self._recent.pop(i)
                return dchat, dmsg
        return None

    async def wait_for_forward(
        self, channel_id: int, message_id: int, timeout: float
    ) -> Tuple[int, int]:
        """Wait for the linked-discussion auto-forward; raises on timeout."""
        key = (channel_id, message_id)
        cached = self.pop_known(channel_id, message_id)
        if cached is not None:
            return cached
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        self._waiters[key] = waiter
        try:
            return await asyncio.wait_for(waiter, timeout)
        finally:
            if self._waiters.get(key) is waiter:
                self._waiters.pop(key, None)

    async def scan_recent(self, channel_id: int, *,
                          deadline_seconds: float = 6.0,
                          interval: float = 1.0) -> Optional[Tuple[int, int, int]]:
        """Poll recent forwards after a cover-post response loss.

        Returns ``(source_message_id, discussion_chat_id, discussion_message_id)``
        for the newest forward from this channel, or ``None`` on timeout.
        """
        deadline = self._now() + deadline_seconds
        while self._now() < deadline:
            for i in range(len(self._recent) - 1, -1, -1):
                cid, mid, dchat, dmsg, _t = self._recent[i]
                if cid == channel_id:
                    self._recent.pop(i)
                    return mid, dchat, dmsg
            await asyncio.sleep(interval)
        return None


# Process-wide default registry, fed by the channel listener.
default_registry = ForwardRegistry()
