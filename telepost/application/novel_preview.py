"""Publication enrichment orchestration: optional novel TXT Telegraph preview.

Called by :class:`~telepost.application.publication.PublicationService` with
the FINAL publication snapshot (the ordered items a publication actually
carries) right before the channel caption is built, so a successful preview can
be presented as a root "read online" button without touching the TXT delivery
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
    TxtReadError,
    fallback_title,
    first_novel_txt,
)
from ..storage.sqlite.novel_preview import PublicationPreviewRepository, attempt_alive

logger = logging.getLogger(__name__)

DEFAULT_PREVIEW_TIMEOUT_SECONDS = 15.0
DEFAULT_PREVIEW_MAX_BYTES = 4 * 1024 * 1024
_REVIEW_KEY_RE = re.compile(r"(?:publication:)?review:(\d+):")
_URL_EXT_RE = re.compile(r"\.(jpg|jpeg|png|gif|webp)$", re.IGNORECASE)
_RICH_PREVIEW_TITLE = "rich-v2"
# Strong references keep timeout work alive until its definitive outcome is saved.
_PENDING_PREVIEWS: dict = {}


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
                     fetch: Optional[TxtFetcher] = None,
                     retry: bool = False, confirmed_absent: bool = False) -> PreviewResult:
        """Ensure this publication has (at most) one novel preview."""
        if not self._enabled:
            return PreviewResult(PreviewStatus.DISABLED)
        key = str(publication_key or "").strip()
        if not key:
            # Without a stable publication identity the enrichment could not be
            # idempotent, so it is not attempted at all.
            return PreviewResult(PreviewStatus.NOT_APPLICABLE)

        # URL reuse must precede TXT download: files can disappear after publish.
        existing = await self._repo.find(key)
        if existing and existing.status == "succeeded" and existing.url:
            return PreviewResult(PreviewStatus.SUCCEEDED, url=existing.url)
        if existing:
            if not retry:
                return PreviewResult(_status_of(existing.status), reason=existing.reason or "reused_record")
            if key in _PENDING_PREVIEWS:
                return PreviewResult(PreviewStatus.FAILED, reason="preview_in_progress")
            if existing.status == "generating":
                if not confirmed_absent or not await self._repo.release_abandoned(key, existing.attempt_id):
                    return PreviewResult(PreviewStatus.FAILED, reason="preview_in_progress")
            if existing.status == "timeout" and existing.attempt_id and attempt_alive(existing.attempt_id):
                return PreviewResult(PreviewStatus.FAILED, reason="preview_in_progress")
            # Telegraph has no createPage idempotency key. Legacy failure rows
            # and provider errors/timeouts require an explicit remote reconciliation.
            if existing.stage != "txt_read" and not confirmed_absent:
                return PreviewResult(PreviewStatus.FAILED, reason="reconciliation_required")

        document = first_novel_txt(items)
        if document is None:
            return PreviewResult(PreviewStatus.NOT_APPLICABLE)
        attempt = await self._repo.claim(
            key, retry=retry, expected_attempt=existing.attempt_id if existing else "",
        )
        if not attempt:
            existing = await self._repo.find(key)
            if existing and existing.status == "succeeded" and existing.url:
                return PreviewResult(PreviewStatus.SUCCEEDED, url=existing.url)
            return PreviewResult(PreviewStatus.FAILED, reason="preview_in_progress")
        started = asyncio.get_running_loop().time()
        try:
            content = await self._read_txt(document, fetch)
            read_reason = "unreadable_txt" if content is None else "empty_txt"
        except TxtReadError as exc:
            content, read_reason = None, exc.code
        if content is None or not content.strip():
            result = PreviewResult(PreviewStatus.FAILED,
                                   reason=read_reason)
            await self._finish(key, attempt, result, stage="txt_read")
            return result
        snapshot = NovelSnapshot(
            title=str(title or "").strip() or fallback_title(document), content=content,
        )
        snapshot = await _attach_review_media_assets(snapshot, key)
        # Shield the task, not the synchronous call: cancellation of to_thread
        # does not cancel requests/Telegraph. A late URL must never be discarded.
        remaining = self._timeout - (asyncio.get_running_loop().time() - started)
        if remaining <= 0:
            result = PreviewResult(PreviewStatus.FAILED, reason="txt_read_timeout")
            await self._finish(key, attempt, result, stage="txt_read")
            return result
        await self._repo.set_stage(key, attempt, "provider")
        task = asyncio.create_task(self._generate(key, attempt, snapshot))
        _PENDING_PREVIEWS[key] = task
        task.add_done_callback(lambda done: _PENDING_PREVIEWS.pop(key, None))
        try:
            return await asyncio.wait_for(asyncio.shield(task), remaining)
        except asyncio.TimeoutError:
            result = PreviewResult(PreviewStatus.TIMEOUT, reason="preview_timeout")
            await self._finish(key, attempt, result, stage="provider")
            return result

    async def _generate(self, key, attempt, snapshot):
        try:
            result = await self._provider.publish_preview(snapshot)
            if not isinstance(result, PreviewResult):
                result = PreviewResult(PreviewStatus.FAILED, reason="invalid_result")
        except Exception as exc:
            result = PreviewResult(PreviewStatus.FAILED, reason=type(exc).__name__)
        await self._finish(key, attempt, result, stage="provider")
        return result

    async def _finish(self, key, attempt, result, *, stage):
        # Store bounded codes, never the provider's raw exception/URL credentials.
        reason = re.split(r"[^A-Za-z0-9_]", result.reason or "", maxsplit=1)[0][:80]
        try:
            await self._repo.finish(
                key, attempt, status=result.status.value,
                url=result.url if result.succeeded else "", reason=reason, stage=stage,
                title=_RICH_PREVIEW_TITLE if result.rich else "",
            )
        except Exception as exc:
            logger.warning("novel preview record failed: %s", type(exc).__name__)
        from ..observability import audit
        match = _REVIEW_KEY_RE.search(key)
        await audit.record_event(
            "preview.attempt_outcome", idempotency_key=key,
            review_id=int(match.group(1)) if match else None,
            actor="system:preview",
            detail={"attempt_id": attempt, "stage": stage,
                    "status": result.status.value, "reason": reason},
        )
        logger.info("novel preview outcome key=%s attempt=%s stage=%s status=%s reason=%s",
                    key[:80], attempt, stage, result.status.value, reason)

    async def _read_txt(self, document: MediaItem,
                        fetch: Optional[TxtFetcher]) -> Optional[str]:
        raw = await self._read_bytes(document, fetch)
        if raw is None:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("novel preview skipped: TXT is not UTF-8 text")
            raise TxtReadError("txt_invalid_utf8")

    async def _read_bytes(self, document: MediaItem,
                          fetch: Optional[TxtFetcher]) -> Optional[bytes]:
        path = document.local_path
        if path:
            try:
                size = os.path.getsize(path)
            except OSError as exc:
                raise TxtReadError("txt_local_unavailable") from exc
            if size <= 0:
                raise TxtReadError("txt_empty")
            if size > self._max_bytes:
                raise TxtReadError("txt_too_large")
            try:
                with open(path, "rb") as handle:
                    raw = handle.read(self._max_bytes + 1)
            except OSError as exc:
                raise TxtReadError("txt_local_read_failed") from exc
            if len(raw) > self._max_bytes:
                raise TxtReadError("txt_too_large")
            return raw

        if fetch is None:
            # file_id attachments can only be read through the Telegram
            # adapter; without one the enrichment is simply not available.
            raise TxtReadError("txt_fetch_unavailable")
        try:
            raw = await asyncio.wait_for(fetch(document), timeout=self._timeout)
        except asyncio.TimeoutError:
            raise TxtReadError("txt_read_timeout")
        except TxtReadError:
            raise
        except Exception as exc:
            raise TxtReadError("txt_fetch_failed") from exc
        if not raw:
            raise TxtReadError("txt_empty")
        if len(raw) > self._max_bytes:
            raise TxtReadError("txt_too_large")
        return bytes(raw)
