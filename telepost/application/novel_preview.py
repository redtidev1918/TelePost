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
import re
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
_REVIEW_KEY_RE = re.compile(r"(?:publication:)?review:(\d+):")
_URL_EXT_RE = re.compile(r"\.(jpg|jpeg|png|gif|webp)$", re.IGNORECASE)


def _asset_extension(source_url: str) -> str:
    match = _URL_EXT_RE.search(str(source_url))
    return match.group(1).lower() if match else "jpg"


def build_rich_snapshot(snapshot: NovelSnapshot,
                        media_assets: Iterable[dict]) -> NovelSnapshot:
    """Turn a raw TXT snapshot into rich markdown + Delivery Asset manifest.

    ``[uploadedimage:<id>]`` / ``[pixivimage:<id>]`` markers that have a
    canonical ``media_asset_refs`` row are rewritten to relative
    ``![](images/<id>.<ext>)`` refs; the matching manifest lets TelePress
    rewrite them to the media proxy without uploading to any image host.
    Markers without a ref stay untouched (the page still publishes).
    """
    content = str(snapshot.content)
    manifest: list = []
    for asset in media_assets or []:
        asset_id = str(asset.get("asset_id") or "").strip()
        source_url = str(asset.get("source_url") or "").strip()
        if not asset_id or not source_url.startswith(("http://", "https://")):
            continue
        source_id = str(asset_id).rsplit(":", 1)[-1]
        if not source_id:
            continue
        local = f"images/{source_id}.{_asset_extension(source_url)}"
        replaced = False
        for marker in (f"[uploadedimage:{source_id}]",
                       f"[pixivimage:{source_id}]"):
            if marker in content:
                content = content.replace(marker, f"![]({local})")
                replaced = True
        if replaced:
            manifest.append({
                "local": local,
                "sourceUrl": source_url,
                "assetId": asset_id,
            })
    if not manifest:
        return snapshot
    return NovelSnapshot(
        title=snapshot.title,
        content=snapshot.content,
        rich_content=content,
        media_manifest=tuple(manifest),
    )


async def _attach_review_media_assets(
    snapshot: NovelSnapshot,
    publication_key: str,
) -> NovelSnapshot:
    """Attach canonical media refs from the owning review chain when available."""
    match = _REVIEW_KEY_RE.search(str(publication_key))
    if not match:
        return snapshot
    try:
        from telepost.storage.sqlite.media_assets import (
            chain_id_for_review,
            list_for_chain,
        )
        review_id = int(match.group(1))
        chain_id = await chain_id_for_review(review_id)
        if not chain_id:
            return snapshot
        assets = await list_for_chain(chain_id)
        if not assets:
            return snapshot
        return build_rich_snapshot(
            snapshot,
            [asset.to_dict() for asset in assets],
        )
    except Exception:  # preview is an enrichment; DB hiccups never break publish
        logger.warning("rich novel preview media lookup failed", exc_info=True)
        return snapshot


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
        snapshot = await _attach_review_media_assets(snapshot, key)
        if snapshot.media_manifest:
            logger.info(
                "novel preview rich media attached: assets=%s",
                len(snapshot.media_manifest),
            )

        existing = await self._repo.find(key)
        if existing is not None:
            # A recorded outcome is terminal for this publication: a retry of
            # the same publication reuses it instead of calling the provider
            # again (never a second Telegraph page for the same publication).
            # Exception: a legacy text-only page may be upgraded when the same
            # publication now resolves canonical media refs for the first time.
            if existing.status == PreviewStatus.SUCCEEDED.value and existing.url:
                if existing.title == "rich" or not snapshot.media_manifest:
                    return PreviewResult(PreviewStatus.SUCCEEDED, url=existing.url)
                logger.info(
                    "upgrading legacy text-only preview to rich form: %s",
                    key[:80],
                )
            else:
                return PreviewResult(
                    _status_of(existing.status), reason="reused_record"
                )

        result = await self._publish_bounded(snapshot)
        logger.info("novel preview for publication %s: %s", key[:80], result.status.value)
        return await self._record(
            key,
            result,
            rich=bool(snapshot.media_manifest),
        )

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

    async def _record(self, key: str, result: PreviewResult,
                      *, rich: bool = False) -> PreviewResult:
        try:
            await self._repo.upsert(
                key,
                provider="telepress",
                status=result.status.value,
                url=result.url if result.succeeded else "",
                title="rich" if rich else "",
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
