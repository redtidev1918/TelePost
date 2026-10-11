"""§review-chat-contract: REVIEW_CHAT_ID empty-value contract and admission.

Root cause (m00694 item 3): ``review_chat_id=str(REVIEW_CHAT_ID)`` persisted the
literal string ``"None"`` whenever the review group was unconfigured, which the
readers then treated as a real chat id (``t.me/c/None/<id>`` links, edits sent to
a non-existent chat). The contract is: ``""`` means "not configured", and a
review-required submission must not be admitted without a review group.
"""
from unittest.mock import AsyncMock, MagicMock
import os
import subprocess
import sys

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from telepost.application.submitter_notify import format_manager_acceptance
from telepost.domain.review_chat import is_configured, normalize_review_chat_id
from telepost.domain.submission import SubmissionDisposition, entry_disposition
from utils import api_server

_SERVICE_PRINCIPAL = {
    "kind": "service", "telegram_user_id": 5073758941, "name": "script",
    "username": "", "display_name": "", "roles": None, "surface": "api",
    "token_id": 1, "token_name": "script", "actor_subject": "api_token:1",
}
_MINIAPP_PRINCIPAL = {
    "kind": "user", "telegram_user_id": 5073758941, "name": "human",
    "username": "human", "display_name": "Human", "roles": None,
    "surface": "mini_app", "actor_subject": "telegram:5073758941",
}


class TestNormalizeReviewChatId:
    @pytest.mark.parametrize("value", [None, "", "   ", "none", "None", "NULL"])
    def test_empty_markers_become_empty_string(self, value):
        assert normalize_review_chat_id(value) == ""
        assert is_configured(value) is False

    @pytest.mark.parametrize(
        "value,expected",
        [(-1001234567890, "-1001234567890"), ("-1001234567890", "-1001234567890"),
         (" 12345 ", "12345"), ("@review_group", "@review_group")],
    )
    def test_real_ids_are_preserved_as_text(self, value, expected):
        assert normalize_review_chat_id(value) == expected
        assert is_configured(value) is True


class TestQueueCommandContract:
    def test_command_normalises_legacy_none_string(self):
        from telepost.application.review_queue import QueueCommand

        cmd = QueueCommand(
            user_id=1, username="u", tags="t", title="x", note="", link="",
            anonymous=False, spoiler=False, review_chat_id="None",
        )
        assert cmd.review_chat_id == ""

    def test_command_keeps_real_chat(self):
        from telepost.application.review_queue import QueueCommand

        cmd = QueueCommand(
            user_id=1, username="u", tags="t", title="x", note="", link="",
            anonymous=False, spoiler=False, review_chat_id=-1001234567890,
        )
        assert cmd.review_chat_id == "-1001234567890"


class TestHandlerRowContract:
    def test_unconfigured_review_chat_rejects_enqueue(self, monkeypatch):
        from handlers import review as review_handlers

        monkeypatch.setattr(review_handlers, "REVIEW_CHAT_ID", None)
        with pytest.raises(ValueError, match="REVIEW_CHAT_ID is required"):
            review_handlers._row_review_chat_id("chat")

    def test_configured_review_chat_stores_its_id(self, monkeypatch):
        from handlers import review as review_handlers

        monkeypatch.setattr(review_handlers, "REVIEW_CHAT_ID", -1001234567890)
        assert review_handlers._row_review_chat_id("chat") == "-1001234567890"


class TestManagerNotificationContract:
    def _payload(self, review_chat_id, control_message_id=55):
        return {
            "source": "api", "status": "pending", "review_id": 7,
            "review_chat_id": review_chat_id,
            "control_message_id": control_message_id,
        }

    def test_legacy_none_value_never_renders_a_broken_link(self):
        text, _ = format_manager_acceptance(self._payload("None"))
        assert "t.me/c/None" not in text
        assert "链接不可用" in text

    def test_configured_chat_renders_the_link(self):
        text, _ = format_manager_acceptance(self._payload(-1001234567890))
        assert "t.me/c/1234567890/55" in text


class TestAdmissionFailsClosedWithoutReviewChat:
    def _app(self, monkeypatch, principal, review_chat_id):
        monkeypatch.setattr(api_server, "authenticate", AsyncMock(return_value={"id": 1}))
        monkeypatch.setattr(api_server, "_resolve_principal", AsyncMock(return_value=principal))
        monkeypatch.setattr(api_server, "REVIEW_CHAT_ID", review_chat_id)
        publish_mock = AsyncMock(return_value={"status": "published", "message_id": 1})
        monkeypatch.setattr("handlers.publish.publish_from_files", publish_mock)
        errors = []
        real_error = api_server._error

        def _record(status, code, message=""):
            errors.append((status, code))
            return real_error(status, code, message)

        monkeypatch.setattr(api_server, "_error", _record)
        application = MagicMock()
        application.bot = AsyncMock()
        app = web.Application()
        api_server.add_api_routes(app, application)
        return app, publish_mock, errors

    async def _post(self, app):
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            return await client.post(
                "/api/v1/submissions",
                headers={"Authorization": "Bearer tp_ok"},
                data={"tags": "x"},
            )
        finally:
            await client.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("chat_id", [None, "", "None", "null", "   "])
    async def test_api_submission_is_rejected_when_review_chat_missing(self, monkeypatch, chat_id):
        app, publish, errors = self._app(monkeypatch, _SERVICE_PRINCIPAL, chat_id)
        resp = await self._post(app)
        assert resp.status == 409
        assert (409, "review_chat_not_configured") in errors
        publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_miniapp_submission_is_rejected_when_review_chat_missing(self, monkeypatch):
        import config.settings as settings

        # Production default: Mini App submissions are review-required.
        monkeypatch.setattr(settings, "MINIAPP_REVIEW_REQUIRED", True)
        app, publish, errors = self._app(monkeypatch, _MINIAPP_PRINCIPAL, None)
        resp = await self._post(app)
        assert resp.status == 409
        assert (409, "review_chat_not_configured") in errors
        publish.assert_not_awaited()


class TestLegacySwitchDoesNotChangeRouting:
    def test_api_entry_is_always_review_required(self):
        assert entry_disposition("api") == SubmissionDisposition.REVIEW_REQUIRED
        assert api_server._entry_review_required("api") is True

    def test_legacy_switch_value_is_irrelevant(self, monkeypatch):
        import config.settings as settings

        monkeypatch.setattr(settings, "API_REVIEW_REQUIRED", False)
        assert api_server._entry_review_required("api") is True

    def test_miniapp_entry_follows_its_own_switch(self, monkeypatch):
        import config.settings as settings

        monkeypatch.setattr(settings, "MINIAPP_REVIEW_REQUIRED", True)
        assert api_server._entry_review_required("miniapp") is True
        monkeypatch.setattr(settings, "MINIAPP_REVIEW_REQUIRED", False)
        assert api_server._entry_review_required("miniapp") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_id,expected", [(None, ""), ("None", ""), (-100123, "-100123")])
async def test_repository_normalizes_review_chat_before_insert(monkeypatch, tmp_path, chat_id, expected):
    from database import db_manager
    from telepost.storage.sqlite.reviews import NewReview, ReviewRepository

    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "review.db"))
    await db_manager.init_db()
    repo = ReviewRepository()
    review_id = await repo.insert(NewReview(
        idempotency_key="chat-contract", source="api", user_id=1, username="",
        title="", tags="", note="", link="", anonymous=False, spoiler=False,
        media=[], documents=[], review_chat_id=chat_id, review_message_ids=[],
    ))
    assert (await repo.get(review_id))["review_chat_id"] == expected


@pytest.mark.parametrize("switches,configured,accepted", [
    (("false", "false", "false"), False, True),
    (("true", "false", "false"), False, False),
    (("false", "true", "false"), False, False),
    (("false", "false", "true"), False, False),
    (("true", "true", "true"), True, True),
])
def test_startup_retains_review_chat_safety_gate(tmp_path, switches, configured, accepted):
    from pathlib import Path

    env = os.environ.copy()
    env.update(TOKEN="test_token", CHANNEL_ID="@test", OWNER_ID="1",
               CONFIG_FILE=str(tmp_path / "missing.ini"),
               PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    for key, value in zip(("API_REVIEW_REQUIRED", "MINIAPP_REVIEW_REQUIRED", "CHAT_REVIEW_REQUIRED"), switches):
        env[key] = value
    env["REVIEW_CHAT_ID"] = "-100123" if configured else ""
    result = subprocess.run([sys.executable, "-c", "import config.settings"],
                            env=env, cwd=tmp_path, capture_output=True)
    assert (result.returncode == 0) is accepted
    if not accepted:
        assert b"REVIEW_CHAT_ID" in result.stderr
