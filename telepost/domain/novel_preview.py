"""Novel TXT Telegraph preview — an OPTIONAL publication enrichment.

The invariants this module encodes (see AGENTS.md §telepress-preview):

```text
Telegraph novel preview is an optional Publication enrichment,
not a Publication success prerequisite.

The downloadable TXT document remains an authoritative Telegram
publication artifact even when a Telegraph preview exists.

Telegraph preview failure must never turn an otherwise successful TXT
Telegram publication into a failed Publication.

Novel preview content is derived from the immutable final Publication
Snapshot, not from mutable Submission or stale Review state.

Telegraph preview generation is publication-idempotent: publication
retries reuse an existing successful preview instead of creating
duplicate Telegraph pages.
```

Publication success is decided by the authoritative Telegram delivery
(``Document`` attachment sent); this module only reports whether an extra
"read online" link can be presented next to it.

Eligibility is decided from the FINAL publication snapshot (the ordered
delivery items a publication actually carries), never from the submission
source, the bot, or the target name: a reviewer who removes the TXT from an
editorial revision removes the preview with it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Protocol

from .delivery import MediaItem, MediaKind

#: Extensions we treat as a plain-text novel attachment (v1 scope: .txt only).
NOVEL_TXT_EXTENSIONS = (".txt",)


class PreviewStatus(str, Enum):
    """Internal preview outcome (never a publication outcome)."""

    NOT_APPLICABLE = "not_applicable"   # snapshot carries no supported TXT novel
    DISABLED = "disabled"               # feature/config off
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class PreviewResult:
    status: PreviewStatus
    url: str = ""
    reason: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status is PreviewStatus.SUCCEEDED

    @property
    def unavailable(self) -> bool:
        return not self.succeeded


@dataclass(frozen=True)
class NovelSnapshot:
    """Publication-safe preview input.

    ``title`` comes from the final publication snapshot (editorial revision
    wins over the original submission); ``content`` is the text of the TXT
    document the publication actually carries. No identity field exists here
    on purpose: anonymous policy applies to the Telegraph page too, so the
    page can never leak a submitter (AGENTS.md §telepress-preview).
    """

    title: str
    content: str
    #: Optional rich-novel form: markdown with ``![](images/<id>.<ext>)`` refs
    #: plus the matching Delivery Asset Contract manifest (asset_id/source_url).
    rich_content: str = ""
    media_manifest: tuple = ()


class NovelPreviewPublisher(Protocol):
    """Thin provider port (TelePress implements it; TelePost never
    reimplements Telegraph rendering or pagination)."""

    async def publish_preview(self, snapshot: NovelSnapshot) -> PreviewResult: ...


class TxtFetcher(Protocol):
    """Resolve the bytes of one attached TXT document.

    Local publications read the file directly (no fetcher needed); file_id
    publications (review approval, chat direct) download the document through
    the Telegram adapter injected by the caller.
    """

    async def __call__(self, item: MediaItem) -> Optional[bytes]: ...


def is_novel_txt_item(item: MediaItem) -> bool:
    """A TXT document attachment (the only eligible media type in v1)."""
    if item is None or item.kind is not MediaKind.DOCUMENT:
        return False
    filename = str(item.filename or "").strip().lower()
    return filename.endswith(NOVEL_TXT_EXTENSIONS)


def first_novel_txt(items: Optional[Iterable[MediaItem]]) -> Optional[MediaItem]:
    """First eligible TXT document of the FINAL ordered snapshot."""
    for item in items or []:
        if is_novel_txt_item(item):
            return item
    return None


def fallback_title(item: Optional[MediaItem]) -> str:
    """Title fallback when the publication snapshot carries no title.

    Only the attachment filename is used — never a token alias, a source name
    or an internal id (those are not user-facing titles).
    """
    filename = str(getattr(item, "filename", "") or "").strip()
    if not filename:
        return ""
    stem = filename.rsplit("/", 1)[-1]
    for ext in NOVEL_TXT_EXTENSIONS:
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    return stem.strip()
