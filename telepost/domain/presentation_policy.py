"""Publication presentation policy (SSOT, §online-reading).

This module decides *where* each navigation action is shown, given the Novel
Preview lifecycle state and the root message's capability. It is a pure
PTB-free value layer: it never imports python-telegram-bot and never decides
Telegram transport details (the telegram adapter does that).

It encodes the three independent state machines of the Online Reading feature:

* **FSM-A — Novel Preview Lifecycle** (see :class:`PreviewState`): NOT_APPLICABLE
  / DISABLED / GENERATING / SUCCEEDED / FAILED / TIMEOUT. Preview outcome never
  changes the Publication/Delivery outcome (§preview-lifecycle).
* **FSM-B — Publication / Delivery Lifecycle** (UNSENT → SENDING → DELIVERED /
  FAILED / UNCERTAIN): owned by :mod:`telepost.application.publication` and the
  delivery engine. Not touched here.
* **FSM-C — Publication Presentation State** (see :func:`build_publication_presentation`):
  maps (preview state, preview url, root capability, surface) →
  (root_navigation, footer_navigation). This is the only place that decides
  whether the ``📖 在线阅读`` action appears, and where.

Core invariant (§online-reading-invariant):

    Online Reading presentation MUST NOT change Publication topology.

It may only influence the root message's ``reply_markup``. It must never change
item count, batch count, media packing, caption ownership, reply/chain/discussion
routing, the ledger, idempotency, or the Publication outcome.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from .navigation import (
    BOT_SUBMIT_ACTION,
    BOT_SUBMIT_LABEL,
    MINI_APP_SUBMIT_ACTION,
    MINI_APP_SUBMIT_LABEL,
    READ_ONLINE_ACTION,
    READ_ONLINE_LABEL,
    NavigationItem,
    bot_submission_url,
    miniapp_submission_url,
)


class PreviewState(str, Enum):
    """FSM-A: Novel Preview lifecycle state (§preview-lifecycle)."""

    NOT_APPLICABLE = "not_applicable"   # not a novel, or no stable publication key
    DISABLED = "disabled"               # feature disabled in configuration
    GENERATING = "generating"           # enrichment in flight (transient)
    SUCCEEDED = "succeeded"             # Telegraph page ready → preview_url valid
    FAILED = "failed"                   # enrichment error → no preview_url
    TIMEOUT = "timeout"                 # enrichment timed out → no preview_url

    @property
    def has_url(self) -> bool:
        """Only SUCCEEDED yields a usable preview URL."""
        return self is PreviewState.SUCCEEDED


class PublicationSurface(str, Enum):
    """Where a publication is being rendered (drives CTA placement)."""

    CHANNEL_PUBLICATION = "channel_publication"
    REVIEW = "review"
    PREVIEW = "preview"
    NOTIFICATION = "notification"


@dataclass(frozen=True)
class PublicationPresentation:
    """FSM-C output: navigation placement for one publication.

    * ``root_navigation``  — actions rendered as the root message inline
      keyboard button(s) (channel publication only, single-root only);
    * ``footer_navigation`` — actions rendered as caption-footer hyperlinks
      (review/preview surfaces; channel publication keeps BOT_SUBMIT /
      MINI_APP_SUBMIT but never READ_ONLINE in the footer).
    """

    root_navigation: List[NavigationItem] = field(default_factory=list)
    footer_navigation: List[NavigationItem] = field(default_factory=list)


_SURFACE_FOOTER_CTA = frozenset({
    PublicationSurface.CHANNEL_PUBLICATION,
    PublicationSurface.REVIEW,
    PublicationSurface.PREVIEW,
})


def _submission_footer(link: Optional[str], *,
                       miniapp_cta: bool,
                       miniapp_short_name: Optional[str]) -> List[NavigationItem]:
    """BOT_SUBMIT (+ MINI_APP_SUBMIT when enabled) for the OWNING bot."""
    items: List[NavigationItem] = []
    if not link:
        return items
    bot_url = bot_submission_url(link)
    if bot_url:
        items.append(NavigationItem(BOT_SUBMIT_ACTION, BOT_SUBMIT_LABEL, bot_url))
    if miniapp_cta:
        mini_url = miniapp_submission_url(link, short_name=miniapp_short_name)
        if mini_url:
            items.append(NavigationItem(
                MINI_APP_SUBMIT_ACTION, MINI_APP_SUBMIT_LABEL, mini_url))
    return items


def _read_online(url: Optional[str]) -> Optional[NavigationItem]:
    if not url or not str(url).startswith(("http://", "https://")):
        return None
    return NavigationItem(READ_ONLINE_ACTION, READ_ONLINE_LABEL, str(url))


def build_publication_presentation(
    *,
    preview_state: PreviewState,
    preview_url: Optional[str] = None,
    surface: PublicationSurface,
    root_supports_button: bool = True,
    channel_footer_link: Optional[str] = None,
    miniapp_submit_cta: bool = False,
    miniapp_short_name: Optional[str] = None,
) -> PublicationPresentation:
    """FSM-C: decide navigation placement for one publication.

    Rules (§online-reading / §final-cta / §footer-rules):

    * The submission CTAs (BOT_SUBMIT / MINI_APP_SUBMIT) live in the caption
      footer for channel / review / preview surfaces, driven by owning-bot
      config. NOTIFICATION surfaces get no channel CTAs at all.
    * READ_ONLINE becomes a **root inline button** only on CHANNEL_PUBLICATION,
      and only when the preview SUCCEEDED, the URL is valid, and the root
      message is a single message capable of carrying a reply_markup (a novel
      root is exactly that). Otherwise — including every failure/disabled/
      not-applicable state — no button is produced.
    * On REVIEW / PREVIEW surfaces READ_ONLINE remains a caption-footer
      hyperlink (those surfaces do not use a root inline button), so reviewers
      can still open the preview.
    * CHANNEL_PUBLICATION never puts READ_ONLINE in the caption footer.

    The function is pure: identical inputs always yield identical placement, and
    it never alters items, batches, reply mode or routing.
    """
    footer: List[NavigationItem] = []
    if surface in _SURFACE_FOOTER_CTA:
        footer.extend(_submission_footer(
            channel_footer_link,
            miniapp_cta=miniapp_submit_cta,
            miniapp_short_name=miniapp_short_name,
        ))

    root: List[NavigationItem] = []
    if surface is PublicationSurface.CHANNEL_PUBLICATION:
        read_online = _read_online(preview_url) if preview_state.has_url else None
        if read_online is not None and root_supports_button:
            root.append(read_online)
    elif surface in (PublicationSurface.REVIEW, PublicationSurface.PREVIEW):
        read_online = _read_online(preview_url) if preview_state.has_url else None
        if read_online is not None:
            footer.append(read_online)

    return PublicationPresentation(root_navigation=root, footer_navigation=footer)


def is_novel_preview_success(*, preview_state: PreviewState,
                             preview_url: Optional[str]) -> bool:
    """True only when a Telegraph preview is ready to be linked."""
    return preview_state.has_url and bool(
        preview_url and str(preview_url).startswith(("http://", "https://"))
    )
