"""Review-chat value contract (§review-chat-contract).

The review group is optional at the configuration layer (``REVIEW_CHAT_ID`` is
``None`` when unset, see ``config/settings.py``), but every durable store of the
value — the ``pending_reviews.review_chat_id`` column, the queue command and
the manager-notification payload — has always been a string. ``str(None)`` at a
call site therefore used to persist the literal ``"None"``, which downstream
readers treat as a *truthy chat id* (broken ``t.me/c/None/…`` links, edits
aimed at a chat called ``None``).

This module is the single source of that contract: an unconfigured review chat
is the EMPTY STRING, everywhere. Nothing here talks to Telegram or the config
layer, so application, storage and handler code can all depend on it.
"""

from __future__ import annotations

# Values that must never be treated as a chat id. ``"none"``/``"null"`` show up
# when an unset value round-trips through string interpolation or JSON.
_EMPTY_MARKERS = frozenset({"", "none", "null"})


def normalize_review_chat_id(value: object) -> str:
    """Return the canonical chat-id string for a configured/unset value.

    ``None``/``"None"``/``"null"``/whitespace/empty all collapse to ``""``; a
    real Telegram chat id keeps its decimal string form.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in _EMPTY_MARKERS:
        return ""
    return text


def is_configured(value: object) -> bool:
    """True when ``value`` names a usable review chat."""
    return bool(normalize_review_chat_id(value))
