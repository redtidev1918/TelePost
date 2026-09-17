"""
Submission disposition contract (§submission-disposition).

Admission is decided by SOURCE TRUST, not entry form:
* API (service token) submissions are ALWAYS review-required.
* Mini App human submissions follow the independent MINIAPP_REVIEW_REQUIRED.
* Native Telegram chat defaults to DIRECT_PUBLISH (CHAT_REVIEW_REQUIRED=false).

Preview copy must reflect the ACTUAL disposition.
"""
import pytest

from telepost.domain.submission import (
    SubmissionDisposition,
    api_disposition,
    chat_disposition,
    entry_disposition,
    miniapp_disposition,
)
import config.settings as settings


@pytest.fixture
def chat_flag(monkeypatch):
    def set_flag(value: bool):
        monkeypatch.setattr(settings, "CHAT_REVIEW_REQUIRED", value)
    return set_flag


@pytest.fixture
def miniapp_flag(monkeypatch):
    def set_flag(value: bool):
        monkeypatch.setattr(settings, "MINIAPP_REVIEW_REQUIRED", value)
    return set_flag


def test_chat_default_is_direct_publish(chat_flag):
    chat_flag(False)
    assert chat_disposition() == SubmissionDisposition.DIRECT_PUBLISH


def test_chat_review_is_an_explicit_policy(chat_flag):
    chat_flag(True)
    assert chat_disposition() == SubmissionDisposition.REVIEW_REQUIRED


def test_api_disposition_is_always_review():
    # API is an automated source: review cannot be disabled by a flag.
    assert api_disposition() == SubmissionDisposition.REVIEW_REQUIRED


def test_miniapp_disposition_tracks_independent_flag(miniapp_flag):
    miniapp_flag(True)
    assert miniapp_disposition() == SubmissionDisposition.REVIEW_REQUIRED
    miniapp_flag(False)
    assert miniapp_disposition() == SubmissionDisposition.DIRECT_PUBLISH


def test_entry_disposition_decides_by_source(chat_flag, miniapp_flag):
    miniapp_flag(True)
    assert entry_disposition("api") == SubmissionDisposition.REVIEW_REQUIRED
    assert entry_disposition("miniapp") == SubmissionDisposition.REVIEW_REQUIRED
    chat_flag(False)
    assert entry_disposition("chat_direct") == SubmissionDisposition.DIRECT_PUBLISH
    miniapp_flag(False)
    assert entry_disposition("miniapp") == SubmissionDisposition.DIRECT_PUBLISH


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


def test_entry_review_helper_decides_by_source(miniapp_flag):
    import utils.api_server as api_server
    assert api_server._entry_review_required("api") is True
    miniapp_flag(True)
    assert api_server._entry_review_required("miniapp") is True
    miniapp_flag(False)
    assert api_server._entry_review_required("miniapp") is False
