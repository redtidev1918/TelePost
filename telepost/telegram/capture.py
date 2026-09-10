"""Feed channel→discussion automatic forwards into the delivery registry.

``capture_discussion_forward`` accepts a raw PTB ``Update`` (or anything
duck-typed like it) and is called for every incoming update *before* it is
queued for PTB handler dispatch, because the discussion publisher waits for the
anchor inside the approve callback. Safe to call from both webhook and polling
ingestion paths.
"""
from __future__ import annotations

from .delivery.registry import ForwardRegistry, default_registry


def capture_discussion_forward(update, *,
                               registry: ForwardRegistry = default_registry) -> None:
    message = getattr(update, "message", None)
    if not message or not getattr(message, "is_automatic_forward", False):
        return

    origin = getattr(message, "forward_origin", None)
    source_chat = getattr(origin, "chat", None)
    source_id = getattr(origin, "message_id", None)
    if source_chat is None or source_id is None:
        # PTB v20 fallback fields.
        source_chat = getattr(message, "forward_from_chat", None)
        source_id = getattr(message, "forward_from_message_id", None)
    if source_chat is None or source_id is None:
        return

    registry.capture(
        source_chat_id=source_chat.id,
        source_message_id=source_id,
        discussion_chat_id=message.chat.id,
        discussion_message_id=message.message_id,
    )
