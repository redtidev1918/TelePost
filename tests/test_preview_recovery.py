"""No real Telegram/Telegraph writes: failure, concurrency and repair contracts."""
import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from database import db_manager
from telepost.application.novel_preview import NovelPreviewEnricher
from telepost.application.preview_recovery import recover_preview, PreviewRecoveryError
from telepost.domain.delivery import MediaItem
from telepost.domain.novel_preview import NovelSnapshot, PreviewResult, PreviewStatus
from telepost.storage.sqlite.novel_preview import PublicationPreviewRepository

URL = "https://telegra.ph/Example-11-02"
KEY = "review:42:example"


@pytest.fixture
async def repo(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "preview.db"))
    await db_manager.init_db()
    return PublicationPreviewRepository()


def provider():
    return SimpleNamespace(publish_preview=AsyncMock(return_value=PreviewResult(
        PreviewStatus.SUCCEEDED, url=URL)))


def document():
    return MediaItem.file_id("document", "FILE", filename="example.txt")


@pytest.mark.asyncio
async def test_concurrent_generation_claims_once(repo):
    p = provider()
    e1, e2 = NovelPreviewEnricher(p, repo=repo), NovelPreviewEnricher(p, repo=repo)
    await asyncio.gather(*(e.enrich(publication_key=KEY, title="Example",
                                   items=[document()], fetch=AsyncMock(return_value=b"body"))
                           for e in [e1, e2]))
    assert p.publish_preview.await_count == 1
    assert (await repo.find(KEY)).attempts == 1


@pytest.mark.asyncio
async def test_success_reused_before_missing_txt_or_fetch(repo):
    await repo.upsert(KEY, provider="telepress", status="succeeded", url=URL)
    p = provider()
    fetch = AsyncMock(side_effect=RuntimeError("no file"))
    result = await NovelPreviewEnricher(p, repo=repo).enrich(
        publication_key=KEY, title="Example", items=[], fetch=fetch, retry=True)
    assert result.url == URL
    fetch.assert_not_awaited()
    p.publish_preview.assert_not_awaited()


@pytest.mark.asyncio
async def test_txt_failure_is_recoverable_and_reason_is_durable(repo):
    p = provider()
    e = NovelPreviewEnricher(p, repo=repo)
    await e.enrich(publication_key=KEY, title="Example", items=[document()])
    record = await repo.find(KEY)
    assert (record.reason, record.stage) == ("txt_fetch_unavailable", "txt_read")
    result = await e.enrich(publication_key=KEY, title="Example", items=[document()],
                            fetch=AsyncMock(return_value=b"body"), retry=True)
    assert result.url == URL
    assert (await repo.find(KEY)).attempts == 2
    p.publish_preview.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "timeout"])
async def test_legacy_failure_requires_reconciliation(repo, status):
    await repo.upsert(KEY, provider="telepress", status=status)
    p = provider()
    result = await NovelPreviewEnricher(p, repo=repo).enrich(
        publication_key=KEY, title="Example", items=[document()], retry=True)
    assert result.reason == "reconciliation_required"
    p.publish_preview.assert_not_awaited()
    result = await NovelPreviewEnricher(p, repo=repo).enrich(
        publication_key=KEY, title="Example", items=[document()], retry=True,
        confirmed_absent=True, fetch=AsyncMock(return_value=b"body"))
    assert result.url == URL
    p.publish_preview.assert_awaited_once()


@pytest.mark.asyncio
async def test_timeout_keeps_late_success_and_blocks_overlapping_compensation(repo):
    started, finish = asyncio.Event(), asyncio.Event()
    async def publish(snapshot):
        started.set()
        await finish.wait()
        return PreviewResult(PreviewStatus.SUCCEEDED, url=URL)
    p = SimpleNamespace(publish_preview=AsyncMock(side_effect=publish))
    e = NovelPreviewEnricher(p, repo=repo)
    e._timeout = .03
    result = await e.enrich(publication_key=KEY, title="Example", items=[document()],
                            fetch=AsyncMock(return_value=b"body"))
    assert result.status == PreviewStatus.TIMEOUT
    assert (await repo.find(KEY)).stage == "provider"
    blocked = await e.enrich(publication_key=KEY, title="Example", items=[document()],
                              retry=True, confirmed_absent=True)
    assert blocked.reason == "preview_in_progress"
    finish.set()
    from telepost.application.novel_preview import _PENDING_PREVIEWS
    await _PENDING_PREVIEWS[KEY]
    assert (await repo.find(KEY)).url == URL
    p.publish_preview.assert_awaited_once()


@pytest.mark.asyncio
async def test_crash_left_claim_is_not_reclaimed_by_time(repo):
    assert await repo.claim(KEY)
    p = provider()
    result = await NovelPreviewEnricher(p, repo=repo).enrich(
        publication_key=KEY, title="Example", items=[document()], retry=True,
        confirmed_absent=True)
    assert result.reason == "preview_in_progress"
    p.publish_preview.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_attempt_cannot_overwrite_success(repo):
    a = await repo.claim(KEY)
    await repo.finish(KEY, a, status="failed", reason="network_error", stage="provider")
    b = await repo.claim(KEY, retry=True)
    await repo.finish(KEY, b, status="succeeded", url=URL)
    await repo.finish(KEY, a, status="failed", reason="late_error")
    await repo.upsert(KEY, provider="telepress", status="failed")
    assert (await repo.find(KEY)).url == URL


@pytest.fixture
async def published(repo):
    from telepost.storage.sqlite.reviews import ReviewRepository, NewReview
    from telepost.storage.sqlite.ledger import DeliveryLedgerRepository
    review = NewReview(idempotency_key="example", source="api", user_id=7,
                       username="", title="Example", tags="", note="", link="https://example.org/novel",
                       anonymous=False, spoiler=False, media=[],
                       documents=[{"file_id": "FILE", "filename": "example.txt"}],
                       review_chat_id="-1007", review_message_ids=[])
    # Fixtures use SQL only to establish a past confirmed publication.
    async with db_manager.get_db() as conn:
        await conn.execute("INSERT INTO pending_reviews (id,idempotency_key,status,user_id,title,"
                           "media_json,documents_json,published_message_id,created_at,updated_at,review_chat_id) "
                           "VALUES (42,'example','published',7,'Example','[]',?,100,1000,1000,'-1007')",
                           (json.dumps(review.documents),))
        await conn.execute("INSERT INTO published_posts (message_id,user_id,title,caption,publish_time) "
                           "VALUES (100,7,'Example','Example',1000)")
    ledger = DeliveryLedgerRepository()
    await ledger.record_published(KEY, target_id="example", pixiv_id="1", work_type="novel",
                                  message_id=100, related_message_ids=[100,101], user_id=7)
    async with db_manager.get_db() as conn:
        await conn.execute("UPDATE delivery_ledger SET created_at=1000 WHERE idempotency_key=?", (KEY,))
    from telegram import Message
    root = Message.de_json({"message_id": 100, "date": 1000, "chat": {"id": -1001,"type":"channel"},
                            "text":"Example"}, None)
    bot = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(id=-1001,type="channel")),
                          get_me=AsyncMock(return_value=SimpleNamespace(id=55)),
                          get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator",can_edit_messages=True)),
                          edit_message_reply_markup=AsyncMock())
    async def edit(**kwargs):
        return SimpleNamespace(reply_markup=kwargs["reply_markup"])
    bot.edit_message_reply_markup.side_effect = edit
    return repo, root, bot


@pytest.mark.asyncio
async def test_lost_button_reuses_url_and_repeat_is_idempotent(published):
    repo, root, bot = published
    await repo.upsert(KEY, provider="telepress", status="succeeded", url=URL)
    e = SimpleNamespace(enrich=AsyncMock())
    result = await recover_preview(review_id=42, bot=bot, channel_id=-1001,
                                    observed_root=root, enricher=e, actor="operator", dry_run=False)
    assert result["status"] == "restored"
    markup = bot.edit_message_reply_markup.call_args.kwargs["reply_markup"]
    root._unfreeze()
    root.reply_markup = markup
    root._freeze()
    await recover_preview(review_id=42, bot=bot, channel_id=-1001,
                          observed_root=root, enricher=e, actor="operator", dry_run=False)
    bot.edit_message_reply_markup.assert_awaited_once()
    e.enrich.assert_not_awaited()
    assert markup.inline_keyboard[0][0].url == URL


@pytest.mark.asyncio
async def test_generation_compensation_sends_only_keyboard(published):
    repo, root, bot = published
    await repo.upsert(KEY, provider="telepress", status="failed", stage="txt_read")
    p = provider()
    result = await recover_preview(review_id=42, bot=bot, channel_id=-1001, observed_root=root,
                                    enricher=NovelPreviewEnricher(p, repo=repo),
                                    fetch=AsyncMock(return_value=b"body"), actor="operator",
                                    dry_run=False, retry=True)
    assert result["status"] == "restored"
    assert bot.edit_message_reply_markup.call_args.kwargs["message_id"] == 100
    p.publish_preview.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["channel", "message", "permission", "album", "content", "date"])
async def test_unsafe_root_never_generates_or_edits(published, failure):
    repo, root, bot = published
    p = provider()
    root._unfreeze()
    if failure == "channel":
        root.chat = SimpleNamespace(id=-1002)
    elif failure == "message":
        root.message_id = 101
    elif failure == "permission":
        bot.get_chat_member.return_value.can_edit_messages = False
    elif failure == "album":
        root.media_group_id = "album"
    elif failure == "content":
        root.text = "Something else"
    elif failure == "date":
        root.date = datetime.fromtimestamp(2000, timezone.utc)
    root._freeze()
    with pytest.raises(PreviewRecoveryError):
        await recover_preview(review_id=42, bot=bot, channel_id=-1001, observed_root=root,
                              enricher=NovelPreviewEnricher(p,repo=repo), actor="operator",
                              dry_run=False, retry=True, confirmed_absent=True)
    p.publish_preview.assert_not_awaited()
    bot.edit_message_reply_markup.assert_not_awaited()


@pytest.mark.asyncio
async def test_dry_run_has_no_writes(published):
    repo, root, bot = published
    result = await recover_preview(review_id=42, bot=bot, channel_id=-1001,
                                    observed_root=root, actor="operator")
    assert result["dry_run"]
    assert await repo.find(KEY) is None
    bot.edit_message_reply_markup.assert_not_awaited()


def test_bot_preview_configuration_is_isolated():
    from run import build_bot_env
    base = {"BOT1_TOKEN":"one","BOT2_TOKEN":"two", "BOT1_CHANNEL_ID":"@one",
            "BOT2_CHANNEL_ID":"@two", "BOT1_TELEGRAPH_ACCESS_TOKEN":"graph-one",
            "BOT2_TELEGRAPH_ACCESS_TOKEN":"graph-two", "BOT1_NOVEL_PREVIEW_ENABLED":"true",
            "BOT2_NOVEL_PREVIEW_ENABLED":"false"}
    a,b = (build_bot_env(i,base) for i in [1,2])
    assert a["DB_PATH"] != b["DB_PATH"]
    assert a["TELEGRAPH_ACCESS_TOKEN"] == "graph-one"
    assert b["TELEGRAPH_ACCESS_TOKEN"] == "graph-two"
    assert a["NOVEL_PREVIEW_ENABLED"] == "true"
    assert b["NOVEL_PREVIEW_ENABLED"] == "false"
    assert "BOT2_TELEGRAPH_ACCESS_TOKEN" not in a


@pytest.mark.asyncio
async def test_txt_get_retry_is_safe_and_error_codes_survive(repo):
    from handlers.publish import _make_txt_fetch
    from telegram.error import TimedOut
    p = provider()
    file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=b"body"))
    bot = SimpleNamespace(get_file=AsyncMock(side_effect=[TimedOut(), file]))
    result = await NovelPreviewEnricher(p,repo=repo).enrich(
        publication_key=KEY,title="Example",items=[document()],fetch=_make_txt_fetch(bot))
    assert result.succeeded and bot.get_file.await_count == 2
    bot.get_file.side_effect = TimedOut()
    result = await NovelPreviewEnricher(p,repo=repo).enrich(
        publication_key="other",title="Example",items=[document()],fetch=_make_txt_fetch(bot))
    assert not result.succeeded
    assert (await repo.find("other")).reason == "txt_get_file_timeout"


@pytest.mark.asyncio
async def test_keyboard_failure_retry_reuses_generated_page(published):
    repo, root, bot = published
    await repo.upsert(KEY, provider="telepress", status="failed", stage="txt_read")
    p = provider()
    e = NovelPreviewEnricher(p, repo=repo)
    bot.edit_message_reply_markup.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        await recover_preview(review_id=42, bot=bot, channel_id=-1001, observed_root=root,
                              enricher=e,fetch=AsyncMock(return_value=b"body"),actor="operator",
                              dry_run=False,retry=True)
    assert (await repo.find(KEY)).url == URL
    bot.edit_message_reply_markup.side_effect = lambda **kw: SimpleNamespace(reply_markup=kw["reply_markup"])
    result = await recover_preview(review_id=42,bot=bot,channel_id=-1001,observed_root=root,
                                    enricher=e,actor="operator",dry_run=False,retry=True)
    assert result["status"] == "restored"
    p.publish_preview.assert_awaited_once()


@pytest.mark.asyncio
async def test_adopt_existing_page_never_calls_generator(published):
    repo,root,bot = published
    await repo.upsert(KEY,provider="telepress",status="failed")
    e = SimpleNamespace(enrich=AsyncMock())
    result = await recover_preview(review_id=42,bot=bot,channel_id=-1001,observed_root=root,
                                    enricher=e,actor="operator",dry_run=False,reconciled_url=URL)
    assert result["url"] == URL
    assert (await repo.find(KEY)).stage == "reconciled"
    e.enrich.assert_not_awaited()


@pytest.mark.asyncio
async def test_preserve_unrelated_buttons(published):
    from telegram import InlineKeyboardButton,InlineKeyboardMarkup
    repo,root,bot = published
    await repo.upsert(KEY,provider="telepress",status="succeeded",url=URL)
    unrelated = InlineKeyboardButton("Details",url="https://example.org/details")
    root._unfreeze()
    root.reply_markup = InlineKeyboardMarkup([[unrelated]])
    root._freeze()
    await recover_preview(review_id=42,bot=bot,channel_id=-1001,observed_root=root,
                          actor="operator",dry_run=False)
    markup = bot.edit_message_reply_markup.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[1] == (unrelated,)


def test_public_message_probe_is_scoped_and_rejects_album():
    from telepost.telegram.preview_recovery import parse_public_root
    html = ('<div data-post="ExampleChannel/99"><div class="tgme_widget_message_text">Wrong</div></div>'
            '<div data-post="ExampleChannel/100"><div class="tgme_widget_message_text">Example<br>Body</div>'
            '<time datetime="2026-11-02T03:18:40+00:00"></time></div>')
    root = parse_public_root(html,"ExampleChannel",-1001,100,None)
    assert root.text == "Example\nBody"
    assert root.reply_markup is None
    with pytest.raises(ValueError,match="current_single_root_unavailable"):
        parse_public_root(html,"ExampleChannel",-1001,101,None)
    with pytest.raises(ValueError,match="current_single_root_unavailable"):
        parse_public_root(html.replace('<time','<div class="tgme_widget_message_grouped_wrap"></div><time'),
                          "ExampleChannel",-1001,100,None)


@pytest.mark.asyncio
async def test_abandoned_owner_requires_reconciliation_then_recovers(repo, monkeypatch):
    import telepost.storage.sqlite.novel_preview as storage
    attempt = await repo.claim(KEY)
    monkeypatch.setattr(storage, "attempt_alive", lambda _a: False)
    e = NovelPreviewEnricher(provider(),repo=repo)
    blocked = await e.enrich(publication_key=KEY,title="Example",items=[document()],retry=True)
    assert blocked.reason == "preview_in_progress"
    result = await e.enrich(publication_key=KEY,title="Example",items=[document()],retry=True,
                            confirmed_absent=True,fetch=AsyncMock(return_value=b"body"))
    assert result.succeeded
    assert (await repo.find(KEY)).attempts == 2


@pytest.mark.asyncio
async def test_unknown_exception_details_are_not_persisted(repo):
    p = SimpleNamespace(publish_preview=AsyncMock(side_effect=RuntimeError("token=private")))
    result = await NovelPreviewEnricher(p,repo=repo).enrich(
        publication_key=KEY,title="Example",items=[document()],fetch=AsyncMock(return_value=b"body"))
    assert not result.succeeded
    assert (await repo.find(KEY)).reason == "RuntimeError"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["existing", "partial", "lookup_failed", "incomplete"])
async def test_remote_page_reconciliation_fails_closed(published, monkeypatch, mode):
    from telepost.telegram.preview_recovery import _verify_pages
    get = Mock()
    if mode == "existing":
        get.return_value = {"pages":[{"title":"Example","url":URL}],"total_count":1}
    elif mode == "partial":
        get.return_value = {"pages":[{"title":"Example (1/2)","url":URL}],"total_count":1}
    elif mode == "incomplete":
        get.return_value = {"pages":[],"total_count":1}
    else:
        get.side_effect = RuntimeError()
    monkeypatch.setattr("telegraph.Telegraph",lambda **kw:SimpleNamespace(get_page_list=get))
    with pytest.raises((ValueError, RuntimeError)):
        await _verify_pages(42)


@pytest.mark.asyncio
async def test_recovery_keeps_original_failure_in_audit_history(repo):
    p = provider()
    e = NovelPreviewEnricher(p,repo=repo)
    await e.enrich(publication_key=KEY,title="Example",items=[document()])
    await e.enrich(publication_key=KEY,title="Example",items=[document()],retry=True,
                   fetch=AsyncMock(return_value=b"body"))
    async with db_manager.get_db() as conn:
        rows = await (await conn.execute(
            "SELECT detail FROM audit_events WHERE event='preview.attempt_outcome' ORDER BY id"
        )).fetchall()
    assert [json.loads(row[0])["status"] for row in rows] == ["failed","succeeded"]
    assert json.loads(rows[0][0])["reason"] == "txt_fetch_unavailable"


def test_owner_probe_fails_closed_without_same_boot(monkeypatch):
    import telepost.storage.sqlite.novel_preview as storage
    monkeypatch.setattr(storage.Path, "read_text", Mock(side_effect=FileNotFoundError()))
    assert storage.attempt_alive("boot:123:456:attempt")
    monkeypatch.setattr(storage.Path, "read_text", Mock(return_value="other-boot"))
    assert storage.attempt_alive("boot:123:456:attempt")


@pytest.mark.asyncio
async def test_live_timeout_cannot_be_adopted_or_reclaimed_from_stale_observation(repo, monkeypatch):
    import telepost.storage.sqlite.novel_preview as storage
    first = await repo.claim(KEY)
    await repo.finish(KEY, first, status="failed", stage="provider")
    second = await repo.claim(KEY, retry=True, expected_attempt=first)
    await repo.finish(KEY, second, status="timeout", stage="provider")
    monkeypatch.setattr(storage, "attempt_alive", lambda _attempt: True)
    assert not await repo.adopt(KEY, URL)
    assert not await repo.claim(KEY, retry=True, expected_attempt=first)
    await repo.finish(KEY, second, status="succeeded", url=URL)
    assert (await repo.find(KEY)).url == URL
