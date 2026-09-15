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

from telepost.application.publication import _channel_footer, channel_caption
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
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=True)
    caption = channel_caption({"tags": "#test", "title": "标题"})
    assert caption.count("startapp=submit") == 1
    assert caption.count("✉️ 我要投稿") == 1


def test_channel_caption_respects_caption_budget(monkeypatch):
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=True)
    near_limit = {"tags": "#test", "title": "标题", "note": "内容" * 400}
    caption = channel_caption(near_limit)
    # strip HTML tags from the caption before measuring Telegram-visible length
    import re
    visible = re.sub(r"<[^>]+>", "", caption)
    assert len(visible) <= 1024
    # The body was truncated rather than the CTA being dropped or overflowed.
    assert "我要投稿" in caption


def test_online_reading_and_submission_cta_coexist(monkeypatch):
    """TelePress novel preview (在线阅读 → Telegraph) and the submission CTA
    are two different actions; the TXT itself is a delivery artifact."""
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=True)
    caption = channel_caption({
        "tags": "#novel",
        "title": "短篇",
        "note": "📖 <a href=\"https://telegra.ph/example-123\">在线阅读</a>",
    })
    assert "telegra.ph/example-123" in caption
    assert "在线阅读" in caption
    assert "startapp=submit" in caption
    assert "我要投稿" in caption


def test_exported_label_constant_is_simple_and_stable():
    assert DEFAULT_CTA_LABEL == "✉️ 我要投稿"
    # Escape round-trip stays valid HTML for Telegram parse mode.
    out = html.escape("✉️ 我要投稿", quote=False)
    assert out == "✉️ 我要投稿"