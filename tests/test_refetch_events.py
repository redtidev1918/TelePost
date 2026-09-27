"""§events consumer-side tests (TelePost as protocol consumer).

Covers the exact phase-E contracts:
  * events ingress POST /api/v1/jobs/events persists a bare Event and dedupes
    idempotently on ``event_id`` (an at-least-once replay is a no-op)
  * ingress auth = the SAME per-bot bot auth as ``/api/v1/refetch/outcomes``
  * the reconcile loop backfills a missed Event (pulled + persisted + acked)
    and feeds a terminal Event through the shared outcome seam exactly once —
    callback and poll converge on ``protocol_job_events`` so no double-notify
  * ack is a no-op for a stale/unknown cursor (never an error, never a state
    change) at the port level
  * a submitted Task carries a ``callback_url`` pointing at the consumer's own
    ingress ONLY under the protocol transport and only when the base is
    configured (Task bodies stay byte-stable otherwise)
"""
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from database import db_manager
from handlers import review
from telepost.application import pixivflow_jobs as port
from telepost.storage.sqlite.protocol_events import ProtocolEventRepository
from telepost.storage.sqlite.refetch import RefetchRepository


@pytest.fixture
async def refetch_db(monkeypatch, tmp_path):
    """Isolated refetch/events DB with the refetch remote configured."""
    db_path = str(tmp_path / "refetch.db")
    monkeypatch.setattr(db_manager, "DB_PATH", db_path)
    monkeypatch.setattr(review, "REVIEW_CHAT_ID", -100123)
    monkeypatch.setattr(review, "ADMIN_IDS", [123456789])
    monkeypatch.setenv("PIXIVFLOW_REFETCH_BASE_URL", "https://pixivflow.example")
    monkeypatch.setenv("PIXIVFLOW_REFETCH_TOKEN", "secret")
    await db_manager.init_db()
    return db_path


async def _insert_review(*, pixiv_id="111", chain="", generation=0):
    now = time.time()
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            """
            INSERT INTO pending_reviews (
                idempotency_key, source, status, user_id, username,
                review_chat_id, media_json, documents_json,
                target_id, pixiv_id, work_type, review_chain_id, generation,
                created_at, updated_at
            ) VALUES (?, 'api', 'pending', 7, 'pf', ?, '[]', '[]', ?, ?, 'illustration', ?, ?, ?, ?)
            """,
            ("k%d" % int(now * 1000), str(review.REVIEW_CHAT_ID),
             "target-a", pixiv_id, chain or "", generation, now, now),
        )
        return cur.lastrowid


async def _attempt(*, chain_id, source_review_id, callback_id=9001, request_id=None):
    repo = RefetchRepository()
    row, refused = await repo.create_attempt(
        callback_key=f"cb:{source_review_id}:{callback_id}",
        review_chain_id=chain_id, generation=1,
        source_review_id=source_review_id,
        source_candidate_id="111",
        request_id=request_id,
    )
    return row, refused


def _make_api_app(monkeypatch):
    import utils.api_server as api_server

    async def _authenticate(bearer: str):
        # Realistic: only a presented bearer token authenticates.
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


_CAPABILITIES_V1 = {
    "protocol_versions": ["1"],
    "job_types": [
        {
            "name": "candidate_search",
            "params_schema": "#/$defs/CandidateSearchParams",
            "result_schema": "#/$defs/Result_CandidateSearch",
        }
    ],
}


def _protocol_job(status, *, job_id="job-13866d8b", key="key-1", error=None,
                  heartbeat_at=1790481310000):
    payload = {
        "protocol_version": "1",
        "job_id": job_id,
        "job_type": "candidate_search",
        "status": status,
        "idempotency_key": key,
        "created_at": 1790481174570,
        "updated_at": 1790481318920,
        "heartbeat_at": heartbeat_at,
    }
    if error is not None:
        payload["error"] = error
    return payload


def _response(payload, status=200):
    response = MagicMock()
    response.__enter__.return_value.status = status
    response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
    return response


def _event(event_id, event_type, job_id, *, job_status=None, error=None,
           correlation_id="", at=1790483374570):
    """One bare ``$defs/Event`` (the exact body a producer callback POSTs)."""
    payload = {}
    if job_status is not None:
        payload["job"] = {
            "protocol_version": "1",
            "job_id": job_id,
            "job_type": "candidate_search",
            "status": job_status,
            "created_at": at,
            "updated_at": at,
        }
    if error is not None:
        payload["error"] = error
    return {
        "protocol_version": "1",
        "event_id": event_id,
        "job_id": job_id,
        "type": event_type,
        "at": at,
        "correlation_id": correlation_id,
        "labels": {},
        "payload": payload,
    }


class _EventsTransport:
    """``urlopen``-compatible stub for the events/generic-job routes.

    Routes:
      /capabilities                              -> capabilities
      /jobs?{idempotency_key}=…                  -> job lookup (by key)
      GET  /jobs/{job_id}/events[?after=&unacked] -> event page
      POST /jobs/{job_id}/events/ack             -> ack result (captured)
    """

    def __init__(self, *, job=None, events=None, ack=None):
        self.job = job
        self.events = events
        self.ack = ack
        self.requests = []
        self.ack_through_values = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        url = request.full_url
        if url.endswith("/capabilities"):
            return _response(_CAPABILITIES_V1)
        if "/events/ack" in url:
            body = json.loads(request.data.decode()) if request.data else {}
            self.ack_through_values.append(body.get("ack_through"))
            ack = self.ack if self.ack is not None else {
                "job_id": "", "acked": 0, "unacked": 0,
            }
            return _response(ack)
        if "/events" in url:
            page = self.events if self.events is not None else {
                "job_id": "", "events": [], "next_after": "", "unacked": 0,
            }
            return _response(page)
        if "idempotency_key=" in url:
            return _response({"jobs": [] if self.job is None else [self.job]})
        return _response({})


# ---------------------------------------------------------------------------
# Port: callback_url on the submitted Task under protocol transport
# ---------------------------------------------------------------------------
def test_protocol_submit_omits_callback_url_when_base_not_configured(monkeypatch):
    """No TELEPOST_API_BASE_URL -> Task stays byte-stable (no callback_url)."""
    monkeypatch.setenv("PIXIVFLOW_REFETCH_BASE_URL", "https://pixivflow.example")
    monkeypatch.setenv("PIXIVFLOW_REFETCH_TOKEN", "secret")
    monkeypatch.delenv("TELEPOST_API_BASE_URL", raising=False)
    transport = _EventsTransport()
    client = port.HttpPixivFlowJobClient(transport=transport)
    client.submit("candidate_search", "key-1", params={"target_id": "target-a"})
    body = json.loads(transport.requests[1].data.decode())
    assert "callback_url" not in body
    assert port.consumer_callback_url() == ""


def test_protocol_submit_carries_callback_url_under_protocol_transport(monkeypatch):
    """Configured base -> callback_url points at this bot's ingress."""
    monkeypatch.setenv("PIXIVFLOW_REFETCH_BASE_URL", "https://pixivflow.example")
    monkeypatch.setenv("PIXIVFLOW_REFETCH_TOKEN", "secret")
    monkeypatch.setenv("TELEPOST_API_BASE_URL", "http://telepost:8080")
    monkeypatch.setenv("TELEPOST_BOT_INDEX", "2")
    transport = _EventsTransport()
    client = port.HttpPixivFlowJobClient(transport=transport)
    client.submit("candidate_search", "key-1", params={"target_id": "target-a"})
    body = json.loads(transport.requests[1].data.decode())
    assert body["callback_url"] == "http://telepost:8080/api/bot2/v1/jobs/events"


def test_protocol_submit_omits_callback_url_for_unusable_base(monkeypatch):
    """A non-URL base resolves to nothing -> field omitted, submit still works."""
    monkeypatch.setenv("PIXIVFLOW_REFETCH_BASE_URL", "https://pixivflow.example")
    monkeypatch.setenv("PIXIVFLOW_REFETCH_TOKEN", "secret")
    monkeypatch.setenv("TELEPOST_API_BASE_URL", "not-a-url")
    transport = _EventsTransport()
    client = port.HttpPixivFlowJobClient(transport=transport)
    client.submit("candidate_search", "key-1", params={"target_id": "target-a"})
    body = json.loads(transport.requests[1].data.decode())
    assert "callback_url" not in body


def test_events_require_protocol_transport_fails_loudly_under_legacy(monkeypatch):
    """events()/ackEvents() are protocol-only; legacy transport raises."""
    monkeypatch.setenv("PIXIVFLOW_REFETCH_BASE_URL", "https://pixivflow.example")
    monkeypatch.setenv("PIXIVFLOW_REFETCH_TOKEN", "secret")
    monkeypatch.setenv("PIXIVFLOW_JOB_TRANSPORT", "legacy")
    transport = _EventsTransport()
    client = port.HttpPixivFlowJobClient(transport=transport)
    with pytest.raises(port.PixivFlowJobError) as ei:
        client.events("job-1")
    assert ei.value.code == "remote_rejected"
    with pytest.raises(port.PixivFlowJobError):
        client.ackEvents("job-1", ack_through="evt-1")


# ---------------------------------------------------------------------------
# Port: ack no-op semantics (stale/unknown cursor is never an error)
# ---------------------------------------------------------------------------
def test_ack_noop_on_unknown_or_stale_cursor(monkeypatch):
    """Stale/unknown cursors don't raise — producer answers HTTP 200 acked=0."""
    monkeypatch.setenv("PIXIVFLOW_REFETCH_BASE_URL", "https://pixivflow.example")
    monkeypatch.setenv("PIXIVFLOW_REFETCH_TOKEN", "secret")
    transport = _EventsTransport(ack={"job_id": "job-1", "acked": 0, "unacked": 0,
                                      "server_time": 1790481000.0})
    client = port.HttpPixivFlowJobClient(transport=transport)
    result = client.ackEvents("job-1", ack_through="evt-NEVER-EXISTED")
    assert isinstance(result, port.AckResult)
    assert result.job_id == "job-1"
    assert result.acked == 0
    assert transport.ack_through_values == ["evt-NEVER-EXISTED"]


# ---------------------------------------------------------------------------
# Ingress: persist + idempotent dedupe on event_id
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ingress_persists_and_replay_is_a_noop(refetch_db, monkeypatch):
    evt = _event("evt-ing-1", "job.started", "job-ing", job_status="running")
    api_server, application, app = _make_api_app(monkeypatch)
    client = await _client(app)
    try:
        r1 = await client.post("/api/v1/jobs/events",
                               headers={"Authorization": "Bearer tp_test"}, json=evt)
        assert r1.status == 200
        d1 = await r1.json()
        assert d1["ok"] is True
        assert d1["data"]["deduplicated"] is False

        # At-least-once replay of the SAME event_id -> no-op, no duplicate row.
        r2 = await client.post("/api/v1/jobs/events",
                               headers={"Authorization": "Bearer tp_test"}, json=evt)
        assert r2.status == 200
        d2 = await r2.json()
        assert d2["data"]["deduplicated"] is True
    finally:
        await client.close()

    events_repo = ProtocolEventRepository()
    row = await events_repo.find("evt-ing-1")
    assert row is not None
    assert json.loads(row["raw"])["event_id"] == "evt-ing-1"
    assert row["job_id"] == "job-ing"
    assert row["event_type"] == "job.started"


@pytest.mark.asyncio
async def test_ingress_requires_bot_auth(refetch_db, monkeypatch):
    evt = _event("evt-ing-2", "job.started", "job-ing", job_status="running")
    api_server, application, app = _make_api_app(monkeypatch)
    client = await _client(app)
    try:
        # No bearer token -> 401, nothing persisted.
        r = await client.post("/api/v1/jobs/events", json=evt)
        assert r.status == 401
        data = await r.json()
        assert data["error"]["code"] == "invalid_token"
    finally:
        await client.close()

    assert await ProtocolEventRepository().find("evt-ing-2") is None


@pytest.mark.asyncio
async def test_ingress_rejects_missing_event_id(refetch_db, monkeypatch):
    api_server, application, app = _make_api_app(monkeypatch)
    client = await _client(app)
    try:
        r = await client.post("/api/v1/jobs/events",
                              headers={"Authorization": "Bearer tp_test"},
                              json={"type": "job.started"})
        assert r.status == 400
        data = await r.json()
        assert data["error"]["code"] == "missing_event_id"
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Reconcile: backfill a missed event + exactly-once terminal notify
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reconcile_backfills_missed_events_and_acks(refetch_db, monkeypatch):
    review_id = await _insert_review(pixiv_id="111")
    chain = "chain-%d" % review_id
    attempt, _ = await _attempt(chain_id=chain, source_review_id=review_id, callback_id=9001)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")
    request_id = attempt["request_id"]
    job_id = "job-c78dd065"

    # Producer has two events the callback missed (accepted + started).
    page = {
        "job_id": job_id,
        "events": [
            _event("evt-acc-1", "job.accepted", job_id, job_status="accepted",
                   at=1790481174570),
            _event("evt-start-1", "job.started", job_id, job_status="running",
                   at=1790481179130),
        ],
        "next_after": "",
        "unacked": 2,
        "server_time": 1790482000.0,
    }
    transport = _EventsTransport(
        job=_protocol_job("running", job_id=job_id, key=request_id),
        events=page,
    )
    monkeypatch.setattr(review, "urlopen", transport)
    bot = AsyncMock()

    acted = await review.reconcile_refetch_events(bot)
    assert acted == 0  # neither event is terminal -> no terminal applied

    events_repo = ProtocolEventRepository()
    assert (await events_repo.find("evt-acc-1")) is not None
    assert (await events_repo.find("evt-start-1")) is not None
    # The stream was advanced to the newest durable event id.
    assert await events_repo.ack_through(job_id) == "evt-start-1"
    assert transport.ack_through_values == ["evt-start-1"]

    # Re-run with an identical page (replay) -> nothing new persisted, no ack.
    acted_2 = await review.reconcile_refetch_events(bot)
    assert acted_2 == 0
    assert await events_repo.ack_through(job_id) == "evt-start-1"


@pytest.mark.asyncio
async def test_reconcile_terminal_event_notifies_exactly_once(refetch_db, monkeypatch):
    review_id = await _insert_review(pixiv_id="111")
    chain = "chain-%d" % review_id
    attempt, _ = await _attempt(chain_id=chain, source_review_id=review_id, callback_id=9001)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")
    request_id = attempt["request_id"]
    job_id = "job-13866d8b"

    # The terminal event is the one REAL producer fixture for a failed search.
    evt = _event("evt-term-1", "job.failed", job_id, job_status="failed",
                 error={"code": "search_failed", "message": "no result",
                        "retryable": False})
    page = {
        "job_id": job_id,
        "events": [evt],
        "next_after": "",
        "unacked": 1,
        "server_time": time.time(),
    }
    transport = _EventsTransport(
        job=_protocol_job("running", job_id=job_id, key=request_id),
        events=page,
    )
    monkeypatch.setattr(review, "urlopen", transport)
    bot = AsyncMock()

    acted = await review.reconcile_refetch_events(bot)
    assert acted == 1  # terminal event applied this tick

    repo = RefetchRepository()
    row = await repo.find_by_request_id(request_id)
    assert row["state"] == "failed"
    assert "重抓失败，当前稿件未变" in bot.send_message.await_args.kwargs["text"]

    # Replay the SAME terminal event -> new Event is deduped, and even if it
    # were re-fed the outcome seam is already terminal (changed=False) -> the
    # notification stays EXACTLY ONE.
    acted_2 = await review.reconcile_refetch_events(bot)
    assert acted_2 == 0
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_reconcile_and_poll_share_one_terminal_outcome(refetch_db, monkeypatch):
    """The reconcile loop and the poll agree: poll applying it first means the
    reconcile tick makes NO further notification (no double-notify)."""
    review_id = await _insert_review(pixiv_id="111")
    chain = "chain-%d" % review_id
    attempt, _ = await _attempt(chain_id=chain, source_review_id=review_id, callback_id=9001)
    await RefetchRepository().mark_admitted(attempt["request_id"], "slot-1")
    request_id = attempt["request_id"]
    job_id = "job-13866d8b"

    evt = _event("evt-term-2", "job.succeeded", job_id, job_status="succeeded",
                 at=1790481318920)
    page = {
        "job_id": job_id,
        "events": [evt],
        "next_after": "",
        "unacked": 1,
        "server_time": time.time(),
    }
    transport = _EventsTransport(
        job=_protocol_job("succeeded", job_id=job_id, key=request_id),
        events=page,
    )
    monkeypatch.setattr(review, "urlopen", transport)
    bot = AsyncMock()

    # The existing POLL already saw the terminal Succeded state first.
    polled = await review.poll_refetch_jobs(bot)
    assert polled == 1  # poll applied the outcome + notified (第1条)

    # The reconcile tick arrives late with the same terminal event: the durable
    # outbox row is unchanged and NO second notification is sent.
    acted = await review.reconcile_refetch_events(bot)
    assert acted == 0
    assert bot.send_message.await_count == 1