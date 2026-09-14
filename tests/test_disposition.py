"""
Submission disposition contract (§submission-disposition).

Native chat submissions default to DIRECT_PUBLISH — the classic "投稿发布到频道"
flow. Review routing for chat is an explicit, configurable policy
(CHAT_REVIEW_REQUIRED). The HTTP API (Mini App / service) defaults to the review
queue in production (API_REVIEW_REQUIRED=true). The two entry points share the
same domain/service but their defaults are independent: refactoring one must
never silently change the other.

Preview copy (button + confirm text) must reflect the ACTUAL disposition, so a
user in review mode is never told "确认发布" and a direct-publish user is never
told their content will be reviewed.
"""
import pytest

from telepost.domain.submission import (
    SubmissionDisposition,
    api_disposition,
    chat_disposition,
)
import config.settings as settings


@pytest.fixture
def chat_flag(monkeypatch):
    def set_flag(value: bool):
        monkeypatch.setattr(settings, "CHAT_REVIEW_REQUIRED", value)
    return set_flag


@pytest.fixture
def api_flag(monkeypatch):
    def set_flag(value: bool):
        monkeypatch.setattr(settings, "API_REVIEW_REQUIRED", value)
    return set_flag


def test_chat_default_is_direct_publish(chat_flag):
    chat_flag(False)
    assert chat_disposition() == SubmissionDisposition.DIRECT_PUBLISH


def test_chat_review_is_an_explicit_policy(chat_flag):
    chat_flag(True)
    assert chat_disposition() == SubmissionDisposition.REVIEW_REQUIRED


def test_api_disposition_tracks_api_review_required(api_flag):
    api_flag(True)
    assert api_disposition() == SubmissionDisposition.REVIEW_REQUIRED
    api_flag(False)
    assert api_disposition() == SubmissionDisposition.DIRECT_PUBLISH


class TestPreviewCopyReflectsChatDisposition:
    def _handlers(self):
        from handlers import preview_handlers
        return preview_handlers

    def test_direct_publish_preview_says_publish(self, chat_flag):
        chat_flag(False)
        row = {"tags": "#a", "title": "x", "note": "", "link": "",
               "anonymous": "false", "spoiler": "false",
               "image_id": "[]", "document_id": "[]"}
        text = self._handlers()._build_preview_text(row)
        assert "发布到频道" in text
        keyboard = self._handlers()._build_preview_keyboard(
            {"tags": "#a", "anonymous": "false", "spoiler": "false"}
        )
        assert keyboard.inline_keyboard[0][0].text == "✅ 确认发布"

    def test_review_required_preview_says_submit_for_review(self, chat_flag):
        chat_flag(True)
        row = {"tags": "#a", "title": "x", "note": "", "link": "",
               "anonymous": "false", "spoiler": "false",
               "image_id": "[]", "document_id": "[]"}
        text = self._handlers()._build_preview_text(row)
        assert "提交审核" in text
        keyboard = self._handlers()._build_preview_keyboard(
            {"tags": "#a", "anonymous": "false", "spoiler": "false"}
        )
        assert keyboard.inline_keyboard[0][0].text == "✅ 提交审核"


def test_api_review_routing_uses_domain_disposition(api_flag):
    """The HTTP submission branch is the domain disposition, not a stray flag."""
    import utils.api_server as api_server
    api_flag(True)
    assert api_server._api_review() is True
    api_flag(False)
    assert api_server._api_review() is False
