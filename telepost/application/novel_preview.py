"""Publication enrichment orchestration: optional novel TXT Telegraph preview.

Called by :class:`~telepost.application.publication.PublicationService` with
the FINAL publication snapshot (the ordered items a publication actually
carries) right before the channel caption is built, so a successful preview can
be presented as one extra "read online" line without touching the TXT delivery
itself.

Contract (AGENTS.md §telepress-preview):

* a preview is an enrichment — its failure/timeout/absence never fails a
  publication, and the TXT document is always still delivered;
* the enrichment is idempotent per publication: an existing record (success or
  failure) is reused, so a retry never creates a second Telegraph page;
* the provider call is bounded by a strict timeout;
* only publication-safe snapshot fields (title + TXT body) reach the provider,
  so anonymous submissions cannot leak identity into the Telegraph page.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Iterable, Optional

from ..domain.delivery import MediaItem
from ..domain.novel_preview import (
    NovelPreviewPublisher,
    NovelSnapshot,
    PreviewResult,
    PreviewStatus,
    TxtFetcher,
    fallback_title,
    first_novel_txt,
)
from ..storage.sqlite.novel_preview import PublicationPreviewRepository

logger = logging.getLogger(__name__)

DEFAULT_PREVIEW_TIMEOUT_SECONDS = 15.0
DEFAULT_PREVIEW_MAX_BYTES = 4 * 1024 * 1024


def _status_of(value: str) -> PreviewStatus:
    try:
        return PreviewStatus(str(value))
    except ValueError:
        return PreviewStatus.FAILED


class NovelPreviewEnricher:
    def __init__(self, provider: NovelPreviewPublisher, *,
                 repo: Optional[PublicationPreviewRepository] = None,
                 enabled: bool = True,
                 timeout_seconds: float = DEFAULT_PREVIEW_TIMEOUT_SECONDS,
                 max_bytes: int = DEFAULT_PREVIEW_MAX_BYTES):
        self._provider = provider
        self._repo = repo or PublicationPreviewRepository()
        self._enabled = bool(enabled)
        self._timeout = max(1.0, float(timeout_seconds))
        self._max_bytes = max(1024, int(max_bytes))

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def timeout_seconds(self) -> float:
        return self._timeout

    async def enrich(self, *, publication_key: str, title: str,
                     items: Optional[Iterable[MediaItem]] = None,
                     fetch: Optional[TxtFetcher] = None) -> PreviewResult:
        """Ensure this publication has (at most) one novel preview."""
        if not self._enabled:
            return PreviewResult(PreviewStatus.DISABLED)
        key = str(publication_key or "").strip()
        if not key:
            # Without a stable publication identity the enrichment could not be
            # idempotent, so it is not attempted at all.
            return PreviewResult(PreviewStatus.NOT_APPLICABLE)

        existing = await self._repo.find(key)
        if existing is not None:
            # A recorded outcome is terminal for this publication: a retry of
            # the same publication reuses it instead of calling the provider
            # again (never a second Telegraph page for the same publication).
            if existing.status == PreviewStatus.SUCCEEDED.value and existing.url:
                return PreviewResult(PreviewStatus.SUCCEEDED, url=existing.url)
            return PreviewResult(_status_of(existing.status), reason="reused_record")

        document = first_novel_txt(items)
        if document is None:
            return PreviewResult(PreviewStatus.NOT_APPLICABLE)

        content = await self._read_txt(document, fetch)
        if content is None:
            return await self._record(
                key, PreviewResult(PreviewStatus.FAILED, reason="unreadable_txt")
            )
        if not content.strip():
            return await self._record(
                key, PreviewResult(PreviewStatus.FAILED, reason="empty_txt")
            )

        snapshot = NovelSnapshot(
            title=str(title or "").strip() or fallback_title(document),
            content=content,
        )
        result = await self._publish_bounded(snapshot)
        logger.info("novel preview for publication %s: %s", key[:80], result.status.value)
        return await self._record(key, result)

    async def _publish_bounded(self, snapshot: NovelSnapshot) -> PreviewResult:
        try:
            result = await asyncio.wait_for(
                self._provider.publish_preview(snapshot), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            return PreviewResult(PreviewStatus.TIMEOUT, reason="preview_timeout")
        except Exception as exc:  # provider contract violation — never propagate
            logger.warning("novel preview provider error: %s", type(exc).__name__)
            return PreviewResult(PreviewStatus.FAILED,
                                 reason=f"{type(exc).__name__}")
        if not isinstance(result, PreviewResult):
            return PreviewResult(PreviewStatus.FAILED, reason="invalid_result")
        return result

    async def _record(self, key: str, result: PreviewResult) -> PreviewResult:
        try:
            await self._repo.upsert(
                key,
                provider="telepress",
                status=result.status.value,
                url=result.url if result.succeeded else "",
                title="",
            )
        except Exception as exc:  # a bookkeeping failure is still not a publication failure
            logger.warning("novel preview record failed: %s", type(exc).__name__)
        return result

    async def _read_txt(self, document: MediaItem,
                        fetch: Optional[TxtFetcher]) -> Optional[str]:
        raw = await self._read_bytes(document, fetch)
        if raw is None:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("novel preview skipped: TXT is not UTF-8 text")
            return None

    async def _read_bytes(self, document: MediaItem,
                          fetch: Optional[TxtFetcher]) -> Optional[bytes]:
        path = document.local_path
        if path:
            try:
                size = os.path.getsize(path)
            except OSError:
                return None
            if size <= 0 or size > self._max_bytes:
                return None
            try:
                with open(path, "rb") as handle:
                    raw = handle.read(self._max_bytes + 1)
            except OSError:
                return None
            return raw if len(raw) <= self._max_bytes else None

        if fetch is None:
            # file_id attachments can only be read through the Telegram
            # adapter; without one the enrichment is simply not available.
            return None
        try:
            raw = await asyncio.wait_for(fetch(document), timeout=self._timeout)
        except asyncio.TimeoutError:
            logger.warning("novel preview skipped: TXT download timed out")
            return None
        except Exception as exc:
            logger.warning("novel preview skipped: TXT download failed (%s)",
                           type(exc).__name__)
            return None
        if not raw:
            return None
        return bytes(raw) if len(raw) <= self._max_bytes else None
