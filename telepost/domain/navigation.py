"""Submission entry point (channel publication footer) — thin navigation helper.

The footer on a channel publication must open the OWNING bot's submission
surfaces, not a generic or hardcoded bot. Everything here is a pure function
over bot/link context:

* ``bot_submission_url``        → ``https://t.me/<bot>?start=submit``
* ``miniapp_submission_url``    → ``https://t.me/<bot>?startapp=submit``
  (or the Direct Mini App form ``https://t.me/<bot>/<short_name>?startapp=submit``)

The ``start=`` / ``startapp=`` parameters are NAVIGATION INTENT ONLY: they
never carry identity, tokens or authorization, and the Mini App still
authenticates via server-validated Telegram initData.

Missing/minimal configuration never produces a malformed URL: when the base
link is not a valid ``t.me/<username>`` URL, the resolvers return ``None``
and the caller omits that navigation item entirely.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

#: Telegram Main Mini App deep link form: https://t.me/<bot>?startapp=<nav-intent>
_TME_BOT_RE = re.compile(r"^https?://t\.me/([A-Za-z0-9_]{3,64})$")
_SHORT_NAME_RE = re.compile(r"[A-Za-z0-9_]{1,64}")
_SUBMIT_INTENT = "submit"

# Typed navigation actions and their fixed presentation labels.
READ_ONLINE_ACTION = "READ_ONLINE"
BOT_SUBMIT_ACTION = "BOT_SUBMIT"
MINI_APP_SUBMIT_ACTION = "MINI_APP_SUBMIT"

READ_ONLINE_LABEL = "📖 在线阅读"
BOT_SUBMIT_LABEL = "✉️ TG 投稿"
MINI_APP_SUBMIT_LABEL = "📱 Mini App"


@dataclass(frozen=True)
class NavigationItem:
    """One typed navigation action in the channel publication footer."""

    action: str
    label: str
    url: str


def bot_username_from_link(base_link: str) -> Optional[str]:
    """The bot username embedded in a t.me deep link, or None when invalid."""
    if not isinstance(base_link, str):
        return None
    match = _TME_BOT_RE.match(base_link.strip())
    return match.group(1) if match else None


def bot_submission_url(base_link: str) -> Optional[str]:
    """Bot submission deep link ``?start=submit`` for the OWNING bot, or None.

    ``start=submit`` is NAVIGATION INTENT ONLY — never identity material.
    """
    username = bot_username_from_link(base_link)
    if username is None:
        return None
    return f"https://t.me/{username}?start={_SUBMIT_INTENT}"


def miniapp_submission_url(
    base_link: str,
    short_name: Optional[str] = None,
) -> Optional[str]:
    """Mini App submission deep link for the OWNING bot, or None.

    ``short_name`` is the Direct Mini App short name when configured; the pure
    Main Mini App form is used when absent. The returned URL carries only the
    navigation intent ``startapp=submit`` — never user ids, tokens or secrets.
    """
    username = bot_username_from_link(base_link)
    if username is None:
        return None
    if short_name and _SHORT_NAME_RE.fullmatch(short_name):
        # Direct Mini App: https://t.me/<bot>/<short_name>?startapp=submit
        return f"https://t.me/{username}/{short_name}?startapp={_SUBMIT_INTENT}"
    return f"https://t.me/{username}?startapp={_SUBMIT_INTENT}"
