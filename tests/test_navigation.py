"""Submission entry point CTA tests (§submission-entrypoint).

Contract:
* the footer CTA is derived from the OWNING bot's context (never a global
  hardcoded bot) and only carries the navigation intent startapp=submit;
* one CTA per publication, shared across all publication paths;
* missing Mini App config never produces a malformed URL or a broken footer;
* the CTA respects the caption budget.
"""
import html

import pytest

from telepost.application.publication import (
    _channel_footer,
    channel_caption,
    channel_submission_action,
)
from telepost.domain.navigation import (
    DEFAULT_CTA_LABEL,
    bot_username_from_link,
    submission_entrypoint_url,
)


@pytest.mark.parametrize("link,username", [
    ("https://t.me/xgdPost_bot", "xgdPost_bot"),
    ("https://t.me/vorePost_bot", "vorePost_bot"),
    ("", None),
    ("https://example.org/not-telegram", None),
    ("https://t.me/ab", None),  # too short to be a real bot username
    ("not a url", None),
])
def test_bot_username_from_link(link, username):
    assert bot_username_from_link(link) == username


def test_entrypoint_is_per_bot_and_never_hardcoded():
    bot1 = submission_entrypoint_url("https://t.me/xgdPost_bot",
                                     mini_app_enabled=True)
    bot2 = submission_entrypoint_url("https://t.me/vorePost_bot",
                                     mini_app_enabled=True)
    assert bot1 == "https://t.me/xgdPost_bot?startapp=submit"
    assert bot2 == "https://t.me/vorePost_bot?startapp=submit"
    assert bot1 != bot2
    # Navigation intent only — any identity material would be a bug.
    for url in (bot1, bot2):
        assert "user" not in url
        assert "token" not in url
        assert "id=" not in url


def test_direct_mini_app_short_name_form_when_provided():
    url = submission_entrypoint_url(
        "https://t.me/xgdPost_bot", mini_app_enabled=True,
        short_name="submitapp",
    )
    assert url == "https://t.me/xgdPost_bot/submitapp?startapp=submit"


def test_disabled_or_missing_config_is_no_broken_link():
    # Mini App disabled → caller falls back to the plain bot deep link.
    assert submission_entrypoint_url("https://t.me/xgdPost_bot",
                                     mini_app_enabled=False) is None
    # Enabled but no configured link → None (no malformed URL).
    assert submission_entrypoint_url("", mini_app_enabled=True) is None
    assert submission_entrypoint_url("https://example.org/x",
                                     mini_app_enabled=True) is None


def _patch(monkeypatch, *, link, cta, text=None):
    from config import settings
    monkeypatch.setattr(settings, "CHANNEL_FOOTER_LINK", link)
    monkeypatch.setattr(settings, "MINIAPP_SUBMIT_CTA", cta)
    if text is not None:
        monkeypatch.setattr(settings, "CHANNEL_FOOTER_TEXT", text)


def test_footer_opens_owning_bot_mini_app_submit(monkeypatch):
    # bot1 runtime context → bot1 Mini App URL.
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=True)
    footer = _channel_footer()
    assert 'href="https://t.me/xgdPost_bot?startapp=submit"' in footer
    assert "✉️ 我要投稿" in footer
    assert "https://t.me/xgdPost_bot?startapp=submit" in footer
    # Exactly ONE CTA anchor.
    assert footer.count("<a ") == 1

    _patch(monkeypatch, link="https://t.me/vorePost_bot", cta=True)
    footer2 = _channel_footer()
    assert 'href="https://t.me/vorePost_bot?startapp=submit"' in footer2
    assert "xgdPost_bot" not in footer2


def test_footer_falls_back_to_bot_deep_link_when_miniapp_off(monkeypatch):
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=False)
    footer = _channel_footer()
    assert 'href="https://t.me/xgdPost_bot"' in footer
    assert "startapp" not in footer
    assert "点击投稿" in footer


def test_footer_absent_and_safe_when_no_config(monkeypatch):
    _patch(monkeypatch, link="", cta=True)
    assert _channel_footer() == ""


def test_channel_caption_has_exactly_one_submission_cta(monkeypatch):
    """Mini App CTA active ⇒ the CTA is an INLINE BUTTON, so the caption
    carries NO textual submission link (never both)."""
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=True)
    caption = channel_caption({"tags": "#test", "title": "标题"})
    assert "startapp=submit" not in caption
    assert "我要投稿" not in caption
    # The one CTA lives in the action handed to the delivery adapter.
    action = channel_submission_action()
    assert action == ("✉️ 我要投稿", "https://t.me/xgdPost_bot?startapp=submit")


def test_channel_submission_action_is_per_bot(monkeypatch):
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=True)
    bot1 = channel_submission_action()
    _patch(monkeypatch, link="https://t.me/vorePost_bot", cta=True)
    bot2 = channel_submission_action()
    assert bot1 == ("✉️ 我要投稿", "https://t.me/xgdPost_bot?startapp=submit")
    assert bot2 == ("✉️ 我要投稿", "https://t.me/vorePost_bot?startapp=submit")
    assert bot1 != bot2
    # Navigation intent only.
    for _, url in (bot1, bot2):
        assert "startapp=submit" in url
        assert "user" not in url and "token" not in url


def test_channel_submission_action_none_when_disabled_or_missing(monkeypatch):
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=False)
    assert channel_submission_action() is None
    _patch(monkeypatch, link="", cta=True)
    assert channel_submission_action() is None


def test_channel_caption_respects_caption_budget(monkeypatch):
    # Mini App ON: no submission footer text in the caption, but the body still
    # respects the 1024 limit with room to spare.
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=True)
    import re
    near_limit = {"tags": "#test", "title": "标题", "note": "内容" * 400}
    caption = channel_caption(near_limit)
    visible = re.sub(r"<[^>]+>", "", caption)
    assert len(visible) <= 1024

    # Mini App OFF: the textual footer is used AND the budget is preserved.
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=False)
    caption_off = channel_caption({"tags": "#test", "title": "标题", "note": "内容" * 200})
    visible_off = re.sub(r"<[^>]+>", "", caption_off)
    assert len(visible_off) <= 1024
    assert "点击投稿" in caption_off


def test_online_reading_and_submission_cta_coexist(monkeypatch):
    """TelePress novel preview (在线阅读 → Telegraph) and the submission CTA
    are two different actions; the TXT itself is a delivery artifact. With the
    Mini App CTA active, 在线阅读 stays in the caption and the submission CTA
    is the inline button action."""
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=True)
    caption = channel_caption({
        "tags": "#novel",
        "title": "短篇",
        "note": "📖 <a href=\"https://telegra.ph/example-123\">在线阅读</a>",
    })
    assert "telegra.ph/example-123" in caption
    assert "在线阅读" in caption
    action = channel_submission_action()
    assert action is not None
    assert action[1].startswith("https://t.me/xgdPost_bot?startapp=")
    assert "telegra.ph" not in action[1]


def test_exported_label_constant_is_simple_and_stable():
    assert DEFAULT_CTA_LABEL == "✉️ 我要投稿"
    # Escape round-trip stays valid HTML for Telegram parse mode.
    out = html.escape("✉️ 我要投稿", quote=False)
    assert out == "✉️ 我要投稿"

def _keyboard_to_tuples(kb):
    return [
        [(b.text, b.url or b.callback_data) for b in row]
        for row in kb.inline_keyboard
    ]


def test_review_keyboard_has_no_submission_cta_and_keeps_moderation():
    """Review control cards NEVER expose the public submission CTA, even when
    the owning bot's Mini App submission entrypoint is configured (§review-cta)."""
    from telepost.telegram.review_keyboard import review_keyboard
    kb = review_keyboard(
        55, "https://www.pixiv.net/artworks/1", source="api", pixiv_id="1",
    )
    rows = _keyboard_to_tuples(kb)
    # Moderation controls unchanged.
    assert rows[0] == [("✅ 发布到频道", "review_approve:55"), ("❌ 拒绝", "review_reject:55")]
    assert any(text == "🔇 遮罩：关" for text, _ in rows[1])
    # No public submission-acquisition CTA (✉️ 我要投稿 / Mini App startapp).
    assert all("我要投稿" not in text for row in rows for text, _ in row)
    assert all("startapp=submit" not in str(entry) for row in rows for entry in row)


def test_review_keyboard_omits_cta_when_no_submission_url():
    from telepost.telegram.review_keyboard import review_keyboard
    kb = review_keyboard(55, "https://www.pixiv.net/artworks/1")
    rows = _keyboard_to_tuples(kb)
    assert all("我要投稿" not in text for row in rows for text, _ in row)


@pytest.mark.asyncio
async def test_superseded_notice_has_no_submission_cta_and_no_moderation(monkeypatch):
    """Superseded ⇒ BOTH moderation buttons AND the public submission CTA are
    removed (backend stale guard stays authoritative). A review card never
    exposes a public submission CTA, even when the CTA is configured."""
    from unittest.mock import AsyncMock

    from telepost.telegram.review_stager import TelegramReviewStager

    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=True)
    bot = AsyncMock()
    stager = TelegramReviewStager(bot, -100123)
    stager._timeouts_now = lambda: {}
    stager._send_throttled = lambda fn: fn()
    bot.edit_message_text = AsyncMock()
    await stager.notify_superseded(
        source_review_id=7, old_message_ids=[101], new_review_id=9
    )
    kwargs = bot.edit_message_text.await_args.kwargs
    assert "已被重抓结果替代" in kwargs["text"]
    # No buttons at all survive on a superseded review card.
    rows = _keyboard_to_tuples(kwargs["reply_markup"])
    assert not kwargs["reply_markup"].inline_keyboard
    for row in rows:
        for text, _ in row:
            assert "review_" not in text
            assert "我要投稿" not in text


@pytest.mark.asyncio
async def test_superseded_notice_clears_all_buttons_when_no_cta(monkeypatch):
    from unittest.mock import AsyncMock

    from telepost.telegram.review_stager import TelegramReviewStager

    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=False)
    bot = AsyncMock()
    stager = TelegramReviewStager(bot, -100123)
    stager._timeouts_now = lambda: {}
    stager._send_throttled = lambda fn: fn()
    bot.edit_message_text = AsyncMock()
    await stager.notify_superseded(
        source_review_id=7, old_message_ids=[101], new_review_id=9
    )
    kwargs = bot.edit_message_text.await_args.kwargs
    assert not kwargs["reply_markup"].inline_keyboard
