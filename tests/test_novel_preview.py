"""Novel TXT Telegraph preview — optional publication enrichment invariants.

Hard requirements covered here (§telepress-preview in AGENTS.md):

* a Telegraph preview is an OPTIONAL enrichment — the downloadable TXT
  document stays the authoritative Telegram artifact in every outcome
  (succeeded / failed / timeout / disabled);
* a preview failure or timeout must never turn an otherwise successful TXT
  publication into a failed Publication;
* eligibility comes from the FINAL publication snapshot (reviewer-removed TXT
  → no preview; photo-only or non-TXT document → not applicable);
* generation is publication-idempotent: a delivery retry reuses the recorded
  preview instead of creating a second Telegraph page;
* provider exceptions never pierce the publication path;
* the provider only ever sees publication-safe snapshot fields (title + TXT
  body), so anonymous submissions cannot leak identity onto the page.
"""
import asyncio

import pytest

from database import db_manager
from telepost.application.novel_preview import NovelPreviewEnricher
from telepost.application.publication import PublicationService, PublishCommand
from telepost.domain.delivery import (
    DeliveryRequest,
    DeliveryResult,
    DeliveredMessage,
    MediaItem,
    MediaKind,
)
from telepost.domain.novel_preview import (
    NovelSnapshot,
    PreviewResult,
    PreviewStatus,
    fallback_title,
    first_novel_txt,
    is_novel_txt_item,
)
from telepost.storage.sqlite.ledger import DeliveryLedgerRepository
from telepost.storage.sqlite.novel_preview import (
    PublicationPreviewRepository,
    preview_key,
)
from utils.helper_functions import build_caption

PREVIEW_URL = "https://telegra.ph/novel-preview-01-01"


# ---- fakes ---------------------------------------------------------------

class FakeProvider:
    """Records every preview request; can fail, raise or stall on demand."""

    def __init__(self, *, url=PREVIEW_URL, status=PreviewStatus.SUCCEEDED,
                 raises=None, delay=0.0):
        self.snapshots = []
        self._url = url
        self._status = status
        self._raises = raises
        self._delay = delay

    @property
    def calls(self) -> int:
        return len(self.snapshots)

    async def publish_preview(self, snapshot: NovelSnapshot) -> PreviewResult:
        self.snapshots.append(snapshot)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises is not None:
            raise self._raises
        return PreviewResult(self._status, url=self._url if self._status is PreviewStatus.SUCCEEDED else "")


class RecordingDelivery:
    """Delivery port fake: keeps the request and always confirms one message."""

    def __init__(self, *, kind=MediaKind.DOCUMENT, message_id=1000):
        self.requests = []
        self._kind = kind
        self._message_id = message_id

    @property
    def calls(self) -> int:
        return len(self.requests)

    @property
    def last(self) -> DeliveryRequest:
        return self.requests[-1]

    async def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        self.requests.append(request)
        return DeliveryResult.delivered([
            DeliveredMessage(
                chat_id=request.chat_id, message_id=self._message_id,
                kind=self._kind, file_id="doc-1",
            )
        ])


# ---- helpers -------------------------------------------------------------

@pytest.fixture
async def db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "preview.db"))
    await db_manager.init_db()
    return PublicationPreviewRepository()


def _enricher(provider, *, enabled=True, timeout=15.0, repo=None) -> NovelPreviewEnricher:
    return NovelPreviewEnricher(
        provider, repo=repo or PublicationPreviewRepository(),
        enabled=enabled, timeout_seconds=timeout,
    )


def _txt_file(tmp_path, name="novel.txt", body="第一章 测试正文"):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _document_item(path=None, *, filename="novel.txt", file_id=None) -> MediaItem:
    if path is not None:
        return MediaItem.local("document", str(path), filename)
    return MediaItem.file_id("document", file_id or "FILE-ID", filename=filename)


def _command(items, key="pixivflow:bot1:novel:1:slot:t1", *, title="原稿标题"):
    return PublishCommand(
        chat_id="@channel",
        items=list(items),
        caption_data={"tags": "#novel", "title": title},
        user_id=7,
        idempotency_key=key,
        target_id="bot1-novel",
        work_type="novel",
        pixiv_id="12345",
    )


# ---- eligibility (final snapshot) ---------------------------------------

def test_first_novel_txt_selects_only_txt_documents():
    photo = MediaItem.file_id("photo", "P1")
    pdf = _document_item(filename="novel.pdf")
    txt = _document_item(filename="novel.txt")

    assert is_novel_txt_item(txt) is True
    assert is_novel_txt_item(pdf) is False
    assert is_novel_txt_item(photo) is False
    assert first_novel_txt([photo, pdf, txt]) is txt
    assert first_novel_txt([photo, pdf]) is None


def test_fallback_title_uses_filename_never_internal_ids():
    assert fallback_title(_document_item(filename="我的小说.txt")) == "我的小说"
    assert fallback_title(MediaItem.file_id("document", "F", filename="")) == ""


@pytest.mark.asyncio
async def test_txt_novel_is_eligible_and_records_preview(db, tmp_path):
    provider = FakeProvider()
    result = await _enricher(provider, repo=db).enrich(
        publication_key="pub-1", title="标题",
        items=[_document_item(_txt_file(tmp_path), filename="正文.txt")],
    )

    assert result.succeeded and result.url == PREVIEW_URL
    assert provider.calls == 1
    assert provider.snapshots[0].content == "第一章 测试正文"
    assert provider.snapshots[0].title == "标题"
    assert (await db.find("pub-1")).status == PreviewStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_photo_only_publication_is_not_applicable(db):
    provider = FakeProvider()
    result = await _enricher(provider, repo=db).enrich(
        publication_key="pub-photo", title="t",
        items=[MediaItem.file_id("photo", "P1"), MediaItem.file_id("video", "V1")],
    )

    assert result.status is PreviewStatus.NOT_APPLICABLE
    assert provider.calls == 0
    assert (await db.find("pub-photo")) is None


@pytest.mark.asyncio
async def test_non_txt_document_is_not_applicable(db):
    provider = FakeProvider()
    result = await _enricher(provider, repo=db).enrich(
        publication_key="pub-pdf", title="t",
        items=[_document_item(filename="novel.pdf")],
    )

    assert result.status is PreviewStatus.NOT_APPLICABLE
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_txt_removed_by_editorial_is_not_applicable(db, tmp_path):
    """The final snapshot wins: a reviewer dropping the TXT drops the preview."""
    provider = FakeProvider()
    remaining = [_document_item(_txt_file(tmp_path, "kept.txt"), filename="kept.txt")]
    result = await _enricher(provider, repo=db).enrich(
        publication_key="pub-edited-away", title="t",
        items=[MediaItem.file_id("photo", "P1")],  # TXT removed by the revision
    )
    assert result.status is PreviewStatus.NOT_APPLICABLE
    assert provider.calls == 0

    # ... and the same publication still enriches when the TXT stays in.
    kept = await _enricher(provider, repo=db).enrich(
        publication_key="pub-edited-kept", title="t", items=remaining,
    )
    assert kept.succeeded and provider.calls == 1


# ---- provider failure / timeout isolation -------------------------------

@pytest.mark.asyncio
async def test_provider_exception_becomes_failed_not_publication_failure(db, tmp_path):
    provider = FakeProvider(raises=RuntimeError("telepress exploded"))
    result = await _enricher(provider, repo=db).enrich(
        publication_key="pub-raise", title="t",
        items=[_document_item(_txt_file(tmp_path))],
    )
    assert result.status is PreviewStatus.FAILED
    assert result.url == ""
    assert (await db.find("pub-raise")).status == PreviewStatus.FAILED.value


@pytest.mark.asyncio
async def test_preview_timeout_is_bounded(db, tmp_path):
    provider = FakeProvider(delay=5.0)
    started = asyncio.get_running_loop().time()
    result = await _enricher(provider, repo=db, timeout=0.1).enrich(
        publication_key="pub-slow", title="t",
        items=[_document_item(_txt_file(tmp_path))],
    )
    elapsed = asyncio.get_running_loop().time() - started

    assert result.status is PreviewStatus.TIMEOUT
    assert elapsed < 2.0, "preview attempt must be strictly bounded"
    assert (await db.find("pub-slow")).status == PreviewStatus.TIMEOUT.value


@pytest.mark.asyncio
async def test_unreadable_txt_is_recorded_as_failed(db, tmp_path):
    provider = FakeProvider()
    path = tmp_path / "binary.txt"
    path.write_bytes(b"\xff\xfe\x00\x01binary")
    result = await _enricher(provider, repo=db).enrich(
        publication_key="pub-binary", title="t",
        items=[_document_item(path)],
    )
    assert result.status is PreviewStatus.FAILED
    assert provider.calls == 0


# ---- idempotency ---------------------------------------------------------

@pytest.mark.asyncio
async def test_repeated_enrichment_calls_provider_once(db, tmp_path):
    provider = FakeProvider()
    items = [_document_item(_txt_file(tmp_path))]
    first = await _enricher(provider, repo=db).enrich(
        publication_key="pub-idem", title="t", items=items)
    second = await _enricher(provider, repo=db).enrich(
        publication_key="pub-idem", title="t", items=items)

    assert first.succeeded and second.succeeded
    assert second.url == first.url == PREVIEW_URL
    assert provider.calls == 1, "the provider must not be called twice"


@pytest.mark.asyncio
async def test_recorded_failure_is_reused_without_recalling_provider(db, tmp_path):
    failing = FakeProvider(raises=RuntimeError("boom"))
    items = [_document_item(_txt_file(tmp_path))]
    first = await _enricher(failing, repo=db).enrich(
        publication_key="pub-fail-once", title="t", items=items)
    second = await _enricher(FakeProvider(), repo=db).enrich(
        publication_key="pub-fail-once", title="t", items=items)

    assert first.status is PreviewStatus.FAILED
    assert second.status is PreviewStatus.FAILED and second.url == ""
    assert failing.calls == 1


@pytest.mark.asyncio
async def test_empty_publication_key_is_never_attempted(db, tmp_path):
    provider = FakeProvider()
    result = await _enricher(provider, repo=db).enrich(
        publication_key="", title="t", items=[_document_item(_txt_file(tmp_path))])
    assert result.status is PreviewStatus.NOT_APPLICABLE
    assert provider.calls == 0


def test_preview_key_is_publication_scoped():
    assert preview_key("review:5:pixiv:9") == \
        "publication:review:5:pixiv:9:novel-preview"


# ---- publication integration -------------------------------------------

@pytest.fixture
async def ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "ledger.db"))
    await db_manager.init_db()
    return DeliveryLedgerRepository()


def _service(delivery, ledger, enricher=None, *, fetch=None):
    return PublicationService(
        delivery=delivery, ledger=ledger, link_builder=lambda mid: f"/{mid}",
        novel_preview=enricher, txt_fetch=fetch,
    )


@pytest.mark.asyncio
async def test_published_txt_publication_carries_preview_url_and_document(
        ledger, tmp_path):
    provider = FakeProvider()
    delivery = RecordingDelivery()
    service = _service(delivery, ledger, _enricher(provider))

    outcome = await service.publish(_command([_document_item(_txt_file(tmp_path))]))

    assert outcome.status == "published"
    assert "🔗 在线阅读" in delivery.last.caption
    assert PREVIEW_URL in delivery.last.caption
    # The authoritative artifact is still the TXT document.
    assert [i.kind for i in delivery.last.items] == [MediaKind.DOCUMENT]
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_photo_only_publication_stays_published_without_preview(ledger):
    provider = FakeProvider()
    delivery = RecordingDelivery(kind=MediaKind.PHOTO)
    service = _service(delivery, ledger, _enricher(provider))

    outcome = await service.publish(_command([MediaItem.file_id("photo", "P1")]))

    assert outcome.status == "published"
    assert "在线阅读" not in delivery.last.caption
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_provider_failure_still_publishes_txt_document(ledger, tmp_path):
    provider = FakeProvider(raises=RuntimeError("telepress unavailable"))
    delivery = RecordingDelivery()
    service = _service(delivery, ledger, _enricher(provider))

    outcome = await service.publish(_command([_document_item(_txt_file(tmp_path))]))

    assert outcome.status == "published", "preview failure must not fail the publication"
    assert "在线阅读" not in delivery.last.caption
    assert [i.kind for i in delivery.last.items] == [MediaKind.DOCUMENT]


@pytest.mark.asyncio
async def test_preview_timeout_still_publishes_txt_document(ledger, tmp_path):
    provider = FakeProvider(delay=5.0)
    delivery = RecordingDelivery()
    service = _service(delivery, ledger, _enricher(provider, timeout=0.1))

    outcome = await service.publish(_command([_document_item(_txt_file(tmp_path))]))

    assert outcome.status == "published"
    assert "在线阅读" not in delivery.last.caption
    assert [i.kind for i in delivery.last.items] == [MediaKind.DOCUMENT]


@pytest.mark.asyncio
async def test_disabled_enrichment_keeps_txt_delivery(ledger, tmp_path):
    provider = FakeProvider()
    delivery = RecordingDelivery()
    service = _service(delivery, ledger, _enricher(provider, enabled=False))

    outcome = await service.publish(_command([_document_item(_txt_file(tmp_path))]))

    assert outcome.status == "published"
    assert provider.calls == 0
    assert [i.kind for i in delivery.last.items] == [MediaKind.DOCUMENT]


@pytest.mark.asyncio
async def test_missing_enricher_keeps_txt_delivery(ledger, tmp_path):
    delivery = RecordingDelivery()
    service = _service(delivery, ledger, None)
    outcome = await service.publish(_command([_document_item(_txt_file(tmp_path))]))
    assert outcome.status == "published"
    assert [i.kind for i in delivery.last.items] == [MediaKind.DOCUMENT]


@pytest.mark.asyncio
async def test_retry_reuses_preview_and_retries_txt_delivery_independently(
        ledger, tmp_path):
    """Telegram delivery retry for the same publication: no second Telegraph
    page, while the TXT delivery is retried on its own."""
    provider = FakeProvider()

    class FailThenSucceed(RecordingDelivery):
        async def deliver(self, request):
            self.requests.append(request)
            if self.calls == 1:
                return DeliveryResult.failed("network down", retryable=True)
            return DeliveryResult.delivered([
                DeliveredMessage(chat_id=request.chat_id, message_id=2001,
                                 kind=MediaKind.DOCUMENT, file_id="doc")
            ])

    delivery = FailThenSucceed(kind=MediaKind.DOCUMENT)
    service = _service(delivery, ledger, _enricher(provider))
    command = _command([_document_item(_txt_file(tmp_path))])

    first = await service.publish(command)
    second = await service.publish(command)

    assert first.status == "failed", "a Telegram failure is still a failure"
    assert second.status == "published"
    assert delivery.calls == 2, "TXT delivery retry stays independent"
    assert provider.calls == 1, "the retry must reuse the existing preview"
    assert PREVIEW_URL in delivery.last.caption
    assert [i.kind for i in delivery.last.items] == [MediaKind.DOCUMENT]


@pytest.mark.asyncio
async def test_file_id_txt_is_fetched_through_adapter_and_previewed(ledger):
    provider = FakeProvider()
    delivery = RecordingDelivery()

    async def fetch(item):
        assert item.telegram_file_id == "FILE-ID"
        return "正文来自 Telegram 下载".encode("utf-8")

    service = _service(delivery, ledger, _enricher(provider), fetch=fetch)
    outcome = await service.publish(_command([_document_item(file_id="FILE-ID")]))

    assert outcome.status == "published"
    assert provider.snapshots[0].content == "正文来自 Telegram 下载"
    assert PREVIEW_URL in delivery.last.caption


@pytest.mark.asyncio
async def test_file_id_txt_without_fetch_adapter_stays_published(ledger):
    provider = FakeProvider()
    delivery = RecordingDelivery()
    service = _service(delivery, ledger, _enricher(provider), fetch=None)

    outcome = await service.publish(_command([_document_item(file_id="FILE-ID")]))

    assert outcome.status == "published"
    assert provider.calls == 0
    assert "在线阅读" not in delivery.last.caption


@pytest.mark.asyncio
async def test_editorial_title_is_the_preview_title(ledger, tmp_path):
    """The preview derives its title from the FINAL publication snapshot."""
    provider = FakeProvider()
    delivery = RecordingDelivery()
    service = _service(delivery, ledger, _enricher(provider))

    await service.publish(_command(
        [_document_item(_txt_file(tmp_path))], key="review:9:orig",
        title="审核员改过的标题",
    ))

    assert provider.snapshots[0].title == "审核员改过的标题"
    assert "审核员改过的标题" in delivery.last.caption


@pytest.mark.asyncio
async def test_anonymous_publication_never_leaks_identity_to_provider(
        ledger, tmp_path):
    """Anonymous policy applies to the Telegraph page too: the snapshot the
    provider receives carries no submitter identity at all."""
    provider = FakeProvider()
    delivery = RecordingDelivery()
    service = _service(delivery, ledger, _enricher(provider))

    command = _command([_document_item(_txt_file(tmp_path))])
    command.caption_data.update({
        "anonymous": "true",
        "submitter_user_id": 424242,
        "submitter_username": "alice",
        "submitter_display_name": "Alice",
    })
    await service.publish(command)

    snapshot = provider.snapshots[0]
    rendered = f"{snapshot.title}\n{snapshot.content}"
    assert "alice" not in rendered.lower()
    assert "424242" not in rendered
    assert "Alice" not in rendered
    # ... while the channel caption still hides the anonymous submitter.
    assert "投稿人" not in delivery.last.caption


@pytest.mark.asyncio
async def test_refetch_generation_b_preview_does_not_apply_to_superseded_a(
        ledger, tmp_path):
    """A superseded generation never publishes, so it never creates a preview;
    only the current head's publication owns one."""
    provider = FakeProvider()
    delivery = RecordingDelivery()
    repo = PublicationPreviewRepository()
    service = _service(delivery, ledger, _enricher(provider, repo=repo))

    await service.publish(_command([_document_item(_txt_file(tmp_path))],
                                   key="review:20:gen-b"))

    assert (await repo.find("review:20:gen-b")) is not None
    assert (await repo.find("review:10:gen-a")) is None
    assert provider.calls == 1


# ---- presentation -------------------------------------------------------

def test_caption_renders_read_online_only_with_a_real_url():
    data = {"tags": "#novel", "title": "标题"}

    without = build_caption(dict(data))
    assert "在线阅读" not in without

    with_url = build_caption({**data, "novel_preview_url": PREVIEW_URL})
    assert "🔗 在线阅读" in with_url
    assert PREVIEW_URL in with_url

    # A malformed/absent value never produces a broken link line.
    broken = build_caption({**data, "novel_preview_url": "telegra.ph/x"})
    assert "在线阅读" not in broken


def test_caption_keeps_read_online_line_when_truncating_long_notes():
    long_note = "很长的简介" * 200
    caption = build_caption({
        "title": "标题", "note": long_note, "tags": "#novel",
        "novel_preview_url": PREVIEW_URL,
    }, max_length=300)

    assert len(caption) <= 300
    assert "在线阅读" in caption


# ---- provider adapter (thin port over the official TelePress API) --------

def test_telepress_provider_calls_official_library_api():
    from telepost.application.telepress_provider import (
        TelePressNovelPreviewPublisher,
    )

    class FakePublisher:
        def __init__(self):
            self.calls = []

        def publish_text(self, content, title):
            self.calls.append((content, title))
            return "https://telegra.ph/Novel-01-01"

    fake = FakePublisher()
    provider = TelePressNovelPreviewPublisher(
        "telegraph-token", client_factory=lambda token: fake)

    result = asyncio.run(provider.publish_preview(
        NovelSnapshot(title="标题", content="正文")))

    assert result.succeeded and result.url == "https://telegra.ph/Novel-01-01"
    assert fake.calls == [("正文", "标题")]


def test_telepress_provider_wraps_library_failures():
    from telepost.application.telepress_provider import (
        TelePressNovelPreviewPublisher,
    )

    class Boom:
        def publish_text(self, content, title):
            raise RuntimeError("telegraph unreachable")

    provider = TelePressNovelPreviewPublisher(
        "telegraph-token", client_factory=lambda token: Boom())
    result = asyncio.run(provider.publish_preview(
        NovelSnapshot(title="t", content="c")))

    assert result.status is PreviewStatus.FAILED
    assert result.url == ""


def test_telepress_provider_rejects_unusable_url():
    from telepost.application.telepress_provider import (
        TelePressNovelPreviewPublisher,
    )

    class NotAUrl:
        def publish_text(self, content, title):
            return ""

    provider = TelePressNovelPreviewPublisher(
        "telegraph-token", client_factory=lambda token: NotAUrl())
    result = asyncio.run(provider.publish_preview(
        NovelSnapshot(title="t", content="c")))
    assert result.status is PreviewStatus.FAILED


def test_provider_builder_is_disabled_without_a_token():
    from telepost.application.telepress_provider import build_telepress_provider

    assert build_telepress_provider("") is None
    assert build_telepress_provider("   ") is None
