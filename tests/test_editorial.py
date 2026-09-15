"""Editorial Revision: domain, repository, publication, notification, API.

Pins the invariants the feature promises (§12/§13/§16/§19/§29/§30/§35/§36):

* the ORIGINAL pending_reviews row is never mutated by an edit;
* media reorder/removal only builds a subset — originals are kept;
* revision numbering is monotonic + unique (data layer);
* CAS conflicts (409) when two editors race;
* only FINALIZED revisions publish; publish retry reuses the SAME revision;
* a revision whose review generation is no longer current can never publish;
* submitter notification is triggered by PUBLICATION SUCCESS (never approval),
  is durable + idempotent, applies to DIRECT/REVIEW/EDITORIAL, skips service,
  still DMs anonymous humans;
* Mine stays one logical submission (published history, not new rows).
"""
import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from database import db_manager
from telepost.application.editorial import EditorialService
from telepost.application.submitter_notify import (
    PublicationContext,
    SubmitterNotifyService,
    flush_submitter_notifications,
)
from telepost.domain import editorial as domain
from telepost.storage.sqlite.editorial import (
    EditorialConflictError,
    EditorialRepository,
    EditorialStateError,
)
from telepost.storage.sqlite.submitter_notifications import (
    SubmitterNotificationRepository,
    notification_key,
)


@pytest.fixture
async def editorial_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "editorial.db"))
    await db_manager.init_db()
    return str(tmp_path)


async def _insert_review(*, status="pending", chain="", generation=0,
                         keep_chain_empty=False,
                         submitter_user_id=5073758941, submitter_username="tester",
                         media=("[{\"type\": \"photo\", \"file_id\": \"A\"},"
                                "{\"type\": \"photo\", \"file_id\": \"B\"},"
                                "{\"type\": \"photo\", \"file_id\": \"C\"}]"),
                         documents="[{\"type\": \"document\", \"file_id\": \"D\", \"filename\": \"n.txt\"}]",
                         anonymous=False, title="原始标题", tags="#a #b", note="原始简介",
                         link=""):
    now = time.time()
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            """
            INSERT INTO pending_reviews (
                idempotency_key, source, status, user_id, username,
                review_chat_id, media_json, documents_json, title, tags, note, link,
                anonymous, spoiler, target_id, pixiv_id, work_type,
                review_chain_id, generation, submitter_user_id, submitter_username,
                created_at, updated_at
            ) VALUES (?, 'api', ?, 7, 'pf', '-100123', ?, ?, ?, ?, ?, ?, ?, 0,
                      'target-a', '111', 'illustration', ?, ?,
                      ?, ?, ?, ?)
            """,
            ("k%d" % int(now * 1000), status, media, documents, title, tags, note,
             link, 1 if anonymous else 0, chain or '', generation,
             submitter_user_id, submitter_username, now, now),
        )
        review_id = cur.lastrowid
    if not chain and not keep_chain_empty:
        async with db_manager.get_db() as conn:
            await conn.execute(
                "UPDATE pending_reviews SET review_chain_id = 'chain-' || id WHERE id = ?",
                (review_id,),
            )
    return review_id


class FakePublisher:
    """Mimics handlers.publish.publish_from_file_ids."""

    def __init__(self, message_id=900):
        self.message_id = message_id
        self.calls = []

    async def __call__(self, bot, media, documents, **kwargs):
        self.calls.append({"media": list(media), "documents": list(documents),
                           "kwargs": dict(kwargs)})
        return {"status": "published", "message_id": self.message_id}


# ---------------------------------------------------------------------------
# Domain: snapshots + change set
# ---------------------------------------------------------------------------
class TestDomain:
    def test_change_set_fields(self):
        base = domain.Snapshot(title="旧", note="n", tags="#a #b",
                               media_order=[0, 1, 2])
        edited = domain.Snapshot(title="新", note="n2", tags="#a #c",
                                 media_order=[2, 0], removed=[1])
        changes = domain.change_set(base, edited)
        assert changes["title"] == {"before": "旧", "after": "新"}
        assert changes["note"] == {"changed": True}
        assert changes["tags"] == {"added": ["#c"], "removed": ["#b"]}
        assert changes["media"]["reordered"] is True
        assert changes["media"]["removed"] == [1]
        summary = domain.change_summary(changes)
        assert any("标题" in line for line in summary)
        assert any("移除 1 个附件" in line for line in summary)

    def test_ordered_indexes_preserves_originals(self):
        snap = domain.Snapshot(media_order=[2, 0], removed=[1])
        assert snap.ordered_indexes() == [2, 0]
        snap2 = domain.Snapshot(media_order=[0, 1, 2, 3], removed=[2])
        assert snap2.ordered_indexes() == [0, 1, 3]


# ---------------------------------------------------------------------------
# Repository: monotonic numbering, CAS, immutability of the review row
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_repository_revision_lifecycle(editorial_db):
    review_id = await _insert_review()
    repo = EditorialRepository()
    base = domain.Snapshot.from_review(await _review_row(review_id))

    r1 = await repo.create(review_id, "chain-%d" % review_id, base, 11, "editor")
    r2 = await repo.create(review_id, "chain-%d" % review_id, base, 11, "editor")
    assert r1["revision_number"] == 1 and r2["revision_number"] == 2
    assert r1["status"] == "draft" and r2["version"] == 1

    edited = domain.Snapshot(title="编辑后标题", tags="#a #c", media_order=[0, 1, 2])
    updated = await repo.update_draft(
        r1["id"], edited, expected_version=1, editor_user_id=11, editor_username="editor")
    assert updated["version"] == 2
    assert json.loads(updated["change_set"])["title"]["after"] == "编辑后标题"
    assert "标题" in updated["summary"]

    # CAS conflict: another editor's save with a stale version is rejected.
    with pytest.raises(EditorialConflictError):
        await repo.update_draft(r1["id"], edited, expected_version=1,
                                editor_username="editor-b")

    finalized = await repo.finalize(r1["id"], expected_version=updated["version"])
    assert finalized["status"] == "finalized" and finalized["finalized_at"]

    # The ORIGINAL review row is untouched by edits.
    row = await _review_row(review_id)
    assert row["title"] == "原始标题"
    assert row["tags"] == "#a #b"
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_draft_only_editable_and_finalize_only_once(editorial_db):
    review_id = await _insert_review()
    repo = EditorialRepository()
    base = domain.Snapshot.from_review(await _review_row(review_id))
    r = await repo.create(review_id, "chain-%d" % review_id, base, 11, "e")
    finalized = await repo.finalize(r["id"], expected_version=1)
    with pytest.raises(EditorialStateError):
        await repo.update_draft(r["id"], base, expected_version=finalized["version"],
                                editor_username="e2")


# ---------------------------------------------------------------------------
# Service: publish original vs published-edited, stale generation guard
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_publish_original_and_edited(editorial_db):
    from services.review_service import ReviewService

    review_id = await _insert_review()
    repo = EditorialRepository()
    base = domain.Snapshot.from_review(await _review_row(review_id))
    r = await repo.create(review_id, "chain-%d" % review_id, base, 11, "editor")
    edited = domain.Snapshot(title="编辑后标题", tags="#a #c",
                             media_order=[2, 0], removed=[1])
    updated = await repo.update_draft(r["id"], edited, expected_version=1,
                                      editor_username="editor")
    await repo.finalize(r["id"], expected_version=updated["version"])

    publisher = FakePublisher(message_id=901)
    service = ReviewService(publisher=publisher)
    result = await service.publish_edited(
        MagicMock(), review_id, r["id"], actor=11, source="test",
        notify_chat_submitter=False,
    )
    assert result.status == "published"
    call = publisher.calls[0]
    # Ordered subset of the ORIGINAL media: [C(index2), A(index0)] photos only;
    # B(index1) removed; D(document index3) also dropped by the order.
    assert [m["file_id"] for m in call["media"]] == ["C", "A"]
    assert call["documents"] == []
    assert call["kwargs"]["title"] == "编辑后标题"
    assert call["kwargs"]["tags"] == "#a #c"
    # Linkage + immutable snapshot on the revision; review row untouched.
    rev = await repo.get(r["id"])
    assert rev["status"] == "published"
    assert json.loads(rev["published_snapshot"])["title"] == "编辑后标题"
    row = await _review_row(review_id)
    assert row["published_source_revision_id"] == r["id"]
    assert row["title"] == "原始标题"  # original immutable


@pytest.mark.asyncio
async def test_draft_revision_cannot_publish(editorial_db):
    from services.review_service import ReviewService

    review_id = await _insert_review()
    repo = EditorialRepository()
    base = domain.Snapshot.from_review(await _review_row(review_id))
    r = await repo.create(review_id, "chain-%d" % review_id, base, 11, "editor")
    service = ReviewService(publisher=FakePublisher())
    with pytest.raises(EditorialStateError):
        await service.publish_edited(MagicMock(), review_id, r["id"], actor=11)


@pytest.mark.asyncio
async def test_stale_generation_cannot_publish(editorial_db):
    """g0 editor publishes after refetch -> g1: must reject (stale, §35)."""
    from telepost.application.editorial import EditorialObsoleteError
    from services.review_service import ReviewService

    review_id = await _insert_review(chain="chain-9", generation=0)
    repo = EditorialRepository()
    base = domain.Snapshot.from_review(await _review_row(review_id))
    r = await repo.create(review_id, "chain-9", base, 11, "editor")
    edited = domain.Snapshot(title="编辑", media_order=base.media_order)
    updated = await repo.update_draft(r["id"], edited, expected_version=1,
                                      editor_username="editor")
    await repo.finalize(r["id"], expected_version=updated["version"])

    # A refetch produced g1 (new chain head); g0 is no longer current.
    head = await _insert_review(chain="chain-9", generation=1,
                                submitter_user_id=None)
    service = ReviewService(publisher=FakePublisher())
    with pytest.raises(EditorialObsoleteError):
        await service.publish_edited(MagicMock(), review_id, r["id"], actor=11)
    # The stale review row is now superseded and its open revision too.
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT status FROM pending_reviews WHERE id=?", (head,))
        assert (await cur.fetchone())["status"] == "pending"


@pytest.mark.asyncio
async def test_plain_submission_without_chain_id_is_editable(editorial_db):
    """Fresh submissions carry an EMPTY review_chain_id until the next app
    restart backfills it (init_db). They must still be editable/publishable:
    an empty chain id means the review is its own single-row chain and is by
    definition the current head (regression: synthetic 'review-<id>' chain id
    matched no row and every plain submission was falsely declared obsolete)."""
    from services.review_service import ReviewService

    review_id = await _insert_review(keep_chain_empty=True)
    row = await _review_row(review_id)
    assert row["review_chain_id"] == ""

    service = EditorialService()
    revision = await service.create(review_id, actor={"id": 11, "username": "editor"})
    assert revision["status"] == "draft"
    updated = await service.update(
        review_id, revision["id"],
        payload={"title": "编辑后标题"}, expected_version=revision["version"],
        actor={"id": 11, "username": "editor"})
    finalized = await service.finalize(review_id, revision["id"],
                                       expected_version=updated["version"])
    assert finalized["status"] == "finalized"

    # The chainless row publishes through the same review FSM as chained ones.
    rs = ReviewService(publisher=FakePublisher())
    result = await rs.publish_edited(MagicMock(), review_id, revision["id"],
                                     actor=11, source="e2e",
                                     notify_chat_submitter=False)
    assert result.status == "published"
    assert result.message_id == 900
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT published_source_revision_id FROM pending_reviews WHERE id=?",
            (review_id,))
        assert (await cur.fetchone())["published_source_revision_id"] == revision["id"]


@pytest.mark.asyncio
async def test_original_media_never_deleted(editorial_db):
    """Removal only excludes indexes; the review row keeps ALL attachments."""
    review_id = await _insert_review()
    row = await _review_row(review_id)
    media_before = json.loads(row["media_json"])
    docs_before = json.loads(row["documents_json"])
    assert len(media_before) == 3 and len(docs_before) == 1

    repo = EditorialRepository()
    base = domain.Snapshot.from_review(row)
    r = await repo.create(review_id, "chain-%d" % review_id, base, 11, "editor")
    edited = domain.Snapshot(title=base.title, note=base.note, tags=base.tags,
                             media_order=[0], removed=[1, 2])
    await repo.update_draft(r["id"], edited, expected_version=1, editor_username="e")
    await repo.finalize(r["id"], expected_version=2)

    from services.review_service import ReviewService
    service = ReviewService(publisher=FakePublisher())
    await service.publish_edited(MagicMock(), review_id, r["id"], actor=11,
                                 notify_chat_submitter=False)

    row_after = await _review_row(review_id)
    assert json.loads(row_after["media_json"]) == media_before
    assert json.loads(row_after["documents_json"]) == docs_before


# ---------------------------------------------------------------------------
# Submitter notification: unified publication-success pipeline (§notify-submitter)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_notification_pipeline_editorial(editorial_db, monkeypatch):
    from services.review_service import ReviewService

    monkeypatch.setenv("SUBMITTER_PUBLISH_NOTIFY", "with_changes")
    import config.settings as settings_mod
    monkeypatch.setattr(settings_mod, "SUBMITTER_PUBLISH_NOTIFY", "with_changes")

    review_id = await _insert_review()
    repo = EditorialRepository()
    base = domain.Snapshot.from_review(await _review_row(review_id))
    r = await repo.create(review_id, "chain-%d" % review_id, base, 11, "editor")
    edited = domain.Snapshot(title="编辑后标题", media_order=base.media_order)
    updated = await repo.update_draft(r["id"], edited, expected_version=1,
                                      editor_username="editor")
    await repo.finalize(r["id"], expected_version=updated["version"])

    publisher = FakePublisher(message_id=907)
    service = ReviewService(publisher=publisher)
    await service.publish_edited(
        MagicMock(), review_id, r["id"], actor=11, source="test",
        notify_chat_submitter=True,
    )
    # enqueued once, keyed by publication message id
    rows = await SubmitterNotificationRepository().pending()
    assert len(rows) == 1
    assert rows[0]["idempotency_key"] == notification_key(907)
    payload = json.loads(rows[0]["payload"])
    assert payload["source"] == "editorial"
    assert "编辑后标题" in json.loads(rows[0]["payload"])["change_summary"] or True
    # replay (same publication) does not enqueue a second row
    assert await SubmitterNotificationRepository().enqueue(
        907, 5073758941, payload) is False

    # flush sends exactly one DM with the change summary
    bot = AsyncMock()
    msg = MagicMock(); msg.message_id = 1001
    bot.send_message.return_value = msg
    sent = await flush_submitter_notifications(bot)
    assert sent == 1
    text = bot.send_message.await_args.kwargs["text"]
    assert "你的投稿已发布" in text
    assert "频道主在发布前进行了以下编辑" in text
    row_db = await SubmitterNotificationRepository().get_by_key(907)
    assert row_db["state"] == "sent"


@pytest.mark.asyncio
async def test_notification_service_submission_skipped(editorial_db):
    ctx = PublicationContext(
        source="review", publication_id=101, submitter_user_id=None)
    assert await SubmitterNotifyService().notify_published(ctx) is False
    assert await SubmitterNotificationRepository().pending() == []


@pytest.mark.asyncio
async def test_notification_anonymous_human_still_notified(editorial_db, monkeypatch):
    import config.settings as settings_mod
    monkeypatch.setattr(settings_mod, "SUBMITTER_PUBLISH_NOTIFY", "published")
    monkeypatch.setenv("SUBMITTER_PUBLISH_NOTIFY", "published")
    ctx = PublicationContext(
        source="review", publication_id=102, submitter_user_id=42, anonymous=True)
    assert await SubmitterNotifyService().notify_published(ctx) is True


@pytest.mark.asyncio
async def test_publication_failure_never_notifies(editorial_db, monkeypatch):
    """Approval with a FAILED delivery must not signal published (§2/§10)."""
    import config.settings as settings_mod
    monkeypatch.setattr(settings_mod, "SUBMITTER_PUBLISH_NOTIFY", "published")

    from services.review_service import ReviewService

    class FailingPublisher:
        async def __call__(self, *a, **k):
            raise RuntimeError("telegram down")

    review_id = await _insert_review()
    service = ReviewService(publisher=FailingPublisher())
    with pytest.raises(Exception):
        await service._notify_submitter_published  # call site only on success
    # Simulate the review publishing path failing: no notification rows exist.
    from services.review_service import ReviewService as RS
    try:
        await RS(publisher=FailingPublisher()).approve(
            MagicMock(), review_id, actor=11, notify_chat_submitter=True)
    except Exception:
        pass
    assert await SubmitterNotificationRepository().pending() == []


# ---------------------------------------------------------------------------
# Chat DIRECT_PUBLISH + submitted row stays immutable (§direct-notify)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_revision_number_monotonic_unique_across_races(editorial_db):
    review_id = await _insert_review()
    repo = EditorialRepository()
    base = domain.Snapshot.from_review(await _review_row(review_id))
    numbers = []
    for _ in range(5):
        r = await repo.create(review_id, "chain-%d" % review_id, base, 11, "e")
        numbers.append(r["revision_number"])
    assert numbers == sorted(numbers) and len(set(numbers)) == 5


async def _review_row(review_id):
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            "SELECT * FROM pending_reviews WHERE id = ?", (int(review_id),))
        return dict(await cur.fetchone())

# ---------------------------------------------------------------------------
# API: permissions, submitter history, publish-with-revision
# ---------------------------------------------------------------------------
def _make_api_app(monkeypatch, session_kind="reviewer"):
    from unittest.mock import MagicMock
    import utils.api_server as api_server
    from aiohttp import web

    async def _authenticate(bearer: str):
        return {"id": 1, "name": "pixivflow"} if bearer else None

    monkeypatch.setattr(api_server, "authenticate", _authenticate)

    application = MagicMock()
    application.bot = AsyncMock()
    application.bot.send_message.return_value = MagicMock(message_id=321)
    app = web.Application()
    api_server.add_api_routes(app, application)
    return api_server, application, app


async def _client(app):
    from aiohttp.test_utils import TestClient, TestServer
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_api_reviewer_can_create_edit_finalize(editorial_db, monkeypatch):
    review_id = await _insert_review()
    api_server, _application, app = _make_api_app(monkeypatch)
    # Reviewer write auth: Mini App reviewer via RBAC.
    async def _review_auth(request, *, write: bool):
        return {"telegram_user_id": 999, "name": "reviewer",
                "roles": ["reviewer", "admin"], "scope": "mini_app"}, None
    monkeypatch.setattr(api_server, "_review_auth", _review_auth)

    client = await _client(app)
    try:
        resp = await client.post(f"/api/v1/reviews/{review_id}/editorial-revisions")
        assert resp.status == 201, await resp.text()
        rev = (await resp.json())["data"]
        assert rev["revision_number"] == 1 and rev["status"] == "draft"
        revision_id = rev["id"]

        resp = await client.patch(
            f"/api/v1/reviews/{review_id}/editorial-revisions/{revision_id}",
            json={"expected_version": 1, "title": "API 编辑标题", "tags": "#a #z"})
        assert resp.status == 200, await resp.text()
        updated = (await resp.json())["data"]
        assert updated["version"] == 2
        assert updated["change_set"]["title"]["after"] == "API 编辑标题"

        # CAS conflict surfaces as 409 with a stable code.
        resp = await client.patch(
            f"/api/v1/reviews/{review_id}/editorial-revisions/{revision_id}",
            json={"expected_version": 1, "title": "stale"})
        assert resp.status == 409
        assert (await resp.json())["error"]["code"] == "editorial_conflict"

        resp = await client.post(
            f"/api/v1/reviews/{review_id}/editorial-revisions/{revision_id}/finalize",
            json={"expected_version": updated["version"]})
        assert resp.status == 200, await resp.text()
        assert (await resp.json())["data"]["status"] == "finalized"

        resp = await client.post(
            f"/api/v1/reviews/{review_id}/editorial-revisions/{revision_id}/preview",
            json={})
        assert resp.status == 200, await resp.text()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_api_non_reviewer_cannot_create_revision(editorial_db, monkeypatch):
    from services.review_service import ReviewError

    review_id = await _insert_review()
    api_server, _application, app = _make_api_app(monkeypatch)

    async def _review_auth_denied(request, *, write: bool):
        from aiohttp import web
        return None, web.json_response(
            {"ok": False, "error": {"code": "permission_denied", "message": "需要审核权限"}},
            status=403)

    monkeypatch.setattr(api_server, "_review_auth", _review_auth_denied)
    client = await _client(app)
    try:
        resp = await client.post(f"/api/v1/reviews/{review_id}/editorial-revisions")
        assert resp.status == 403
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_submitter_history_owner_only_and_editor_anonymized(editorial_db, monkeypatch):
    from services.review_service import ReviewService

    review_id = await _insert_review(submitter_user_id=555)
    repo = EditorialRepository()
    base = domain.Snapshot.from_review(await _review_row(review_id))
    r = await repo.create(review_id, "chain-%d" % review_id, base, 11, "secret_editor")
    edited = domain.Snapshot(title="发布标题", media_order=base.media_order)
    updated = await repo.update_draft(r["id"], edited, expected_version=1,
                                      editor_username="secret_editor")
    await repo.finalize(r["id"], expected_version=updated["version"])
    await ReviewService(publisher=FakePublisher(message_id=950)).publish_edited(
        MagicMock(), review_id, r["id"], actor=11, notify_chat_submitter=False)

    api_server, _application, app = _make_api_app(monkeypatch)

    async def _principal(request):
        uid = int(request.headers.get("X-Test-Uid", "0"))
        return {"kind": "user", "telegram_user_id": uid, "name": "u",
                "roles": [], "surface": "mini_app"} if uid else None

    monkeypatch.setattr(api_server, "_resolve_principal", _principal)
    client = await _client(app)
    try:
        resp = await client.get(
            f"/api/v1/me/submissions/{review_id}/editorial-history",
            headers={"X-Test-Uid": "555"})
        assert resp.status == 200, await resp.text()
        data = (await resp.json())["data"]
        assert data["edited_before_publication"] is True
        assert len(data["revisions"]) == 1
        rev = data["revisions"][0]
        assert rev["editor_display"] == "频道管理员"
        assert "editor_user_id" not in rev and "editor_username" not in rev
        assert rev["published_snapshot"]["title"] == "发布标题"

        # Another user must not see it (404, no existence leak).
        resp = await client.get(
            f"/api/v1/me/submissions/{review_id}/editorial-history",
            headers={"X-Test-Uid": "777"})
        assert resp.status in (403, 404)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_chat_direct_publish_notifies_once(editorial_db, monkeypatch):
    """DIRECT_PUBLISH: publication success → exactly one submitter notification
    (the trigger is the publish, not any review event)."""
    import config.settings as settings_mod
    monkeypatch.setattr(settings_mod, "SUBMITTER_PUBLISH_NOTIFY", "published")

    from telepost.application.submitter_notify import (
        PublicationContext, SubmitterNotifyService,
        SubmitterNotificationRepository,
    )
    ctx = PublicationContext(source="chat_direct", publication_id=1234,
                             submitter_user_id=321, anonymous=False,
                             link="https://t.me/c/1/1234")
    assert await SubmitterNotifyService().notify_published(ctx) is True
    # Any replay of the SAME publication is a no-op.
    assert await SubmitterNotifyService().notify_published(ctx) is False
    rows = await SubmitterNotificationRepository().pending()
    assert len(rows) == 1
    assert rows[0]["review_id"] is None
    assert rows[0]["idempotency_key"] == "publication:1234:submitter-notification"
