"""Submission entry point (channel publication CTA) — thin navigation helper.

The footer CTA on a channel publication must open the OWNING bot's Mini App
submission surface, not a generic or hardcoded bot. Everything here is a pure
function over bot/runtime context:

* the base bot link (``https://t.me/<username>`` — the existing per-bot
  ``CHANNEL_FOOTER_LINK``);
* whether the bot exposes a Mini App submission surface (``mini_app_enabled``);
* an optional Direct Mini App ``short_name`` (main Mini App when absent).

The ``startapp=submit`` parameter is NAVIGATION INTENT ONLY: it never carries
identity, tokens or authorization, and the Mini App still authenticates via
server-validated Telegram initData.

Missing/minimal configuration never produces a malformed URL: when the base
link is not a valid ``t.me/<username>`` URL or the Mini App surface is
disabled, the helper returns ``None`` and the caller falls back to the
pre-existing entry point (the plain bot deep link), exactly today's semantics.
"""
from __future__ import annotations

import re
from typing import Optional

#: Telegram Main Mini App deep link form: https://t.me/<bot>?startapp=<nav-intent>
_TME_BOT_RE = re.compile(r"^https?://t\.me/([A-Za-z0-9_]{3,64})$")
_SUBMIT_INTENT = "submit"

DEFAULT_CTA_LABEL = "✉️ 我要投稿"


def bot_username_from_link(base_link: str) -> Optional[str]:
    """The bot username embedded in a t.me deep link, or None when invalid."""
    if not isinstance(base_link, str):
        return None
    match = _TME_BOT_RE.match(base_link.strip())
    return match.group(1) if match else None


def submission_entrypoint_url(
    base_link: str,
    *,
    mini_app_enabled: bool,
    short_name: Optional[str] = None,
) -> Optional[str]:
    """Mini App submission deep link for the OWNING bot, or None on fallback.

    ``short_name`` is the Direct Mini App short name when configured; the pure
    Main Mini App form is used when absent (the mode this deployment uses).

    The returned URL carries only the navigation intent ``startapp=submit`` —
    never user ids, tokens or any other secret.
    """
    if not mini_app_enabled:
        return None
    username = bot_username_from_link(base_link)
    if username is None:
        return None
    if short_name and re.fullmatch(r"[A-Za-z0-9_]{1,64}", short_name):
        # Direct Mini App: https://t.me/<bot>/<short_name>?startapp=submit
        return f"https://t.me/{username}/{short_name}?startapp={_SUBMIT_INTENT}"
    return f"https://t.me/{username}?startapp={_SUBMIT_INTENT}"