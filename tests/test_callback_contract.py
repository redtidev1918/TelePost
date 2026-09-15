"""Callback-action contract test (AGENTS.md §preview-ux / task PART 23-24).

Invariant:

    every callback_data emitted by the chat-submission preview builder
    is routable by the submission ConversationHandler's active (PREVIEW)
    state.

A builder that emits a callback no consumer routes fails here in CI, so a
future producer/consumer contract drift surfaces as a red test instead of
the historical production "未知操作" fallthrough reply.
"""

import pytest
from telegram.ext import CallbackQueryHandler

from handlers.conversation import build_submission_conversation
from handlers.preview_handlers import _build_preview_keyboard
from models.state import STATE


def _emitted_callback_data() -> set[str]:
    """Every callback_data the preview builder can actually emit."""
    data: set[str] = set()
    for tags in (None, ["a", "b"]):  # no-tags and has-tags collapse to one keyboard
        kb = _build_preview_keyboard(
            {"anonymous": "false", "spoiler": "false", "tags": tags}
        )
        for row in kb.inline_keyboard:
            for button in row:
                assert button.callback_data, "preview button without callback_data"
                data.add(button.callback_data)
    return data


def _routable_preview_patterns() -> list:
    """The regex patterns the PREVIEW state routes to a consumer."""
    conv = build_submission_conversation()
    preview_handlers = conv.states[STATE["PREVIEW"]]
    patterns = []
    for handler in preview_handlers:
        if isinstance(handler, CallbackQueryHandler) and handler.pattern is not None:
            patterns.append(handler.pattern)
    return patterns


def test_every_emitted_preview_callback_is_routable():
    emitted = _emitted_callback_data()
    patterns = _routable_preview_patterns()
    assert patterns, "conversation PREVIEW state has no callback patterns to route"

    unmatchable = sorted(cb for cb in emitted if not any(p.fullmatch(cb) for p in patterns))
    assert not unmatchable, (
        "preview builder emits callback_data with no router consumer: "
        f"{unmatchable} (emitted={sorted(emitted)})"
    )


def test_emitted_callback_contract_is_the_expected_set():
    """Anchor the exact action vocabulary so neither side drifts silently."""
    emitted = _emitted_callback_data()
    assert emitted == {
        "publish",
        "edit_tag",
        "edit_title",
        "edit_note",
        "edit_link",
        "edit_media",
        "cancel",
        "toggle_anon",
        "toggle_spoiler",
    }