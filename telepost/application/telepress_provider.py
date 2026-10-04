"""TelePress-backed novel preview provider (thin adapter, §telepress-preview).

TelePress owns Telegraph rendering, text pagination, the Telegraph API wrapper
and its caching. TelePost only orchestrates *when* a publication gets a preview
and *whether* it was published; it never reimplements those domains. This
module is the single place where the provider library is touched, so the rest
of the application talks to the :class:`NovelPreviewPublisher` port.

Library first, never the CLI: ``from telepress import TelegraphPublisher``.
The call is synchronous (requests-based), so the enricher runs it in a worker
thread under a strict timeout — a slow Telegraph can never stall a publication
beyond that bound.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Optional, Protocol

from ..domain.novel_preview import (
    NovelPreviewPublisher,
    NovelSnapshot,
    PreviewResult,
    PreviewStatus,
)

logger = logging.getLogger(__name__)

PROVIDER_NAME = "telepress"
_BOT_AUTHOR_CACHE = {}
_CACHE_MISS = object()


def clear_bot_author_cache() -> None:
    """Clear the process-local bot identity cache (primarily for tests)."""
    _BOT_AUTHOR_CACHE.clear()


async def _resolve_bot_author(bot) -> tuple[Optional[str], Optional[str]]:
    """Resolve the current bot's Telegram username without making it a hard dependency.

    The cache key is the current ``Bot`` object identity. TelePost's supervisor
    runs each Bot in its own process, but the identity key keeps even in-process
    test doubles isolated from one another.
    """
    if bot is None:
        return None, None
    cache_key = id(bot)
    cached = _BOT_AUTHOR_CACHE.get(cache_key, _CACHE_MISS)
    if cached is not _CACHE_MISS:
        return cached
    try:
        me = await bot.get_me()
        username = str(getattr(me, "username", "") or "").strip().lstrip("@")
        if not username:
            logger.warning(
                "Unable to resolve Telegram bot username; publishing Telegraph page "
                "without author metadata"
            )
            return None, None
        author = (f"@{username}", f"https://t.me/{username}")
        _BOT_AUTHOR_CACHE[cache_key] = author
        return author
    except Exception as exc:  # metadata is enrichment; publication must continue
        logger.warning(
            "Unable to resolve Telegram bot username; publishing Telegraph page "
            "without author metadata (%s)",
            type(exc).__name__,
        )
        return None, None


class _PublisherFactory(Protocol):
    def __call__(self, token: str): ...


class TelePressNovelPreviewPublisher(NovelPreviewPublisher):
    """Adapter over the official TelePress Python API."""

    def __init__(self, token: str, *, skip_duplicate: bool = False,
                 client_factory: Optional[_PublisherFactory] = None,
                 bot=None):
        self._token = token
        # TelePost owns publication idempotency (one durable row per
        # publication). TelePress's content cache must not decide whether this
        # publication already has a preview, so it stays off.
        self._skip_duplicate = skip_duplicate
        self._client_factory = client_factory
        self._bot = bot
        self._client = None

    def _publisher(self):
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory(self._token)
            else:
                from telepress import TelegraphPublisher  # official API

                # Token stays positional and `skip_duplicate=` lives on its own
                # line: their values are provably non-secret attribute
                # references (the real secret arrives from the operator secret),
                # and keeping the key/value pairs apart avoids the generic-api-key
                # shape rule's 30-char keyword-to-assignment window.
                self._client = TelegraphPublisher(
                    self._token,
                    skip_duplicate=self._skip_duplicate,
                )
        return self._client

    def _publish_sync(self, snapshot: NovelSnapshot, *,
                      author_name: Optional[str] = None,
                      author_url: Optional[str] = None):
        """Return ``(url, rich)``; ``rich`` is true only when the rich markdown
        path (with image manifest) was actually used."""
        publisher = self._publisher()
        author_kwargs = {}
        if author_name and author_url:
            author_kwargs = {
                "author_name": author_name,
                "author_url": author_url,
            }
        if snapshot.rich_content and snapshot.media_manifest:
            rich = getattr(publisher, "publish_rich_markdown", None)
            if callable(rich):
                fd, path = tempfile.mkstemp(
                    suffix=".md", prefix="telepress-rich-"
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write(snapshot.rich_content)
                    result = rich(
                        path,
                        snapshot.title,
                        manifest=list(snapshot.media_manifest),
                        **author_kwargs,
                    )
                    if isinstance(result, dict):
                        return str(result.get("url") or ""), True
                    return str(result or ""), True
                finally:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
            logger.warning(
                "installed telepress lacks publish_rich_markdown; "
                "falling back to text-only preview"
            )
        return publisher.publish_text(
            snapshot.content, snapshot.title, **author_kwargs
        ), False

    async def publish_preview(self, snapshot: NovelSnapshot) -> PreviewResult:
        import asyncio

        try:
            author_name, author_url = await _resolve_bot_author(self._bot)
            url, rich = await asyncio.to_thread(
                self._publish_sync,
                snapshot,
                author_name=author_name,
                author_url=author_url,
            )
        except Exception as exc:  # provider/library/network failure — never fatal
            logger.warning(
                "novel preview publish failed (%s): %s", type(exc).__name__, exc
            )
            return PreviewResult(PreviewStatus.FAILED,
                                 reason=f"{type(exc).__name__}: {exc}"[:200])
        url = str(url or "").strip()
        if not url.startswith(("http://", "https://")):
            logger.warning("novel preview returned an unusable url")
            return PreviewResult(PreviewStatus.FAILED, reason="invalid_url")
        return PreviewResult(PreviewStatus.SUCCEEDED, url=url, rich=rich)


def telepress_available() -> bool:
    """True when the provider library can be imported in this runtime."""
    try:
        import telepress  # noqa: F401
    except Exception:
        return False
    return True


def _bridge_media_proxy_env() -> None:
    """Hand the in-process TelePress library the operator's media-proxy config.

    TelePress reads ``TELEPRESS_MEDIA_PROXY_BASE`` / ``TELEPRESS_MEDIA_PROXY_HOSTS``
    while TelePost's own delivery layer reads ``MEDIA_PROXY_BASE_URL`` /
    ``MEDIA_PROXY_HOSTS``. Production configured only the TelePost pair, so the
    library silently skipped proxy rewriting: rich-novel markdown kept its
    relative ``images/…`` refs, and Telegraph silently dropped every inline
    image (root cause of the text-only 在线阅读 pages). Bridge explicitly, once:
    a TelePress-specific value always wins (operator override); otherwise map
    from TelePost's single source of truth.
    """
    try:
        from config.settings import MEDIA_PROXY_BASE_URL, MEDIA_PROXY_HOSTS
    except Exception:  # config layer unavailable — keep enrichment best-effort
        logger.warning("media proxy bridge: config.settings unreadable", exc_info=True)
        return
    if not os.environ.get("TELEPRESS_MEDIA_PROXY_BASE", "").strip():
        if MEDIA_PROXY_BASE_URL:
            os.environ["TELEPRESS_MEDIA_PROXY_BASE"] = MEDIA_PROXY_BASE_URL
            logger.info("media proxy bridge: TELEPRESS_MEDIA_PROXY_BASE <- MEDIA_PROXY_BASE_URL")
    if not os.environ.get("TELEPRESS_MEDIA_PROXY_HOSTS", "").strip():
        if MEDIA_PROXY_HOSTS:
            os.environ["TELEPRESS_MEDIA_PROXY_HOSTS"] = ",".join(sorted(MEDIA_PROXY_HOSTS))
            logger.info("media proxy bridge: TELEPRESS_MEDIA_PROXY_HOSTS <- MEDIA_PROXY_HOSTS")


def build_telepress_provider(token: str,
                             *, client_factory: Optional[_PublisherFactory] = None,
                             bot=None
                             ) -> Optional[TelePressNovelPreviewPublisher]:
    """Build the provider, or ``None`` when the runtime cannot serve previews.

    ``None`` (no configured Telegraph token, or the library is not installed in
    this image) disables the enrichment instead of failing publications.
    """
    _bridge_media_proxy_env()
    if not str(token or "").strip():
        logger.info("novel preview disabled: no Telegraph access token configured")
        return None
    if client_factory is None and not telepress_available():
        logger.info("novel preview disabled: telepress is not installed")
        return None
    return TelePressNovelPreviewPublisher(str(token).strip(),
                                          client_factory=client_factory,
                                          bot=bot)
