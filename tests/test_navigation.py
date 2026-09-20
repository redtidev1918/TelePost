"""Submission navigation (channel publication footer) tests (§submission-entrypoint).

Contract:
* the footer is derived from the OWNING bot's context (never a global
  hardcoded bot);
* BOT_SUBMIT always uses ``?start=submit``; MINI_APP_SUBMIT uses
  ``?startapp=miniapp`` — the two intents must never be mixed;
* each navigation action appears exactly once in the caption footer;
* READ_ONLINE appears only when a real http(s) Telegraph preview URL exists,
  and never duplicates the legacy body ``🔗 在线阅读`` line;
* missing/invalid config never produces a malformed URL or a broken footer;
* the footer respects the caption budget.
"""
import html

import pytest

from telepost.application.publication import (
    _publication_navigation,
    channel_caption,
)
from telepost.domain.navigation import (
    BOT_SUBMIT_ACTION,
    BOT_SUBMIT_LABEL,
    MINI_APP_SUBMIT_ACTION,
    MINI_APP_SUBMIT_LABEL,
    READ_ONLINE_ACTION,
    READ_ONLINE_LABEL,
    bot_submission_url,
    bot_username_from_link,
    miniapp_submission_url,
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


def test_bot_submission_url_is_per_bot_and_uses_start():
    assert bot_submission_url("https://t.me/xgdPost_bot") == \
        "https://t.me/xgdPost_bot?start=submit"
    assert bot_submission_url("https://t.me/vorePost_bot") == \
        "https://t.me/vorePost_bot?start=submit"
    assert bot_submission_url("") is None
    assert bot_submission_url("https://example.org/x") is None
    # `start` intent only — any identity material would be a bug.
    for url in bot_submission_url("https://t.me/xgdPost_bot"), \
               bot_submission_url("https://t.me/vorePost_bot"):
        assert "user" not in url
        assert "token" not in url
        assert "id=" not in url


def test_miniapp_submission_url_falls_back_to_bot_start():
    assert miniapp_submission_url("https://t.me/xgdPost_bot") == \
        "https://t.me/xgdPost_bot?startapp=miniapp"
    assert miniapp_submission_url("https://t.me/vorePost_bot") == \
        "https://t.me/vorePost_bot?startapp=miniapp"
    assert miniapp_submission_url("") is None
    assert miniapp_submission_url("https://example.org/x") is None
    for url in miniapp_submission_url("https://t.me/xgdPost_bot"), \
               miniapp_submission_url("https://t.me/vorePost_bot"):
        assert "user" not in url
        assert "token" not in url
        assert "id=" not in url


def test_miniapp_direct_short_name_form_when_provided():
    url = miniapp_submission_url("https://t.me/xgdPost_bot", short_name="submitapp")
    assert url == "https://t.me/xgdPost_bot/submitapp?startapp=submit"
    # Invalid short name → private-chat Web App launch fallback.
    assert miniapp_submission_url("https://t.me/xgdPost_bot", short_name="not allowed!") == \
        "https://t.me/xgdPost_bot?startapp=miniapp"


def _patch(monkeypatch, *, link, cta):
    from config import settings
    monkeypatch.setattr(settings, "CHANNEL_FOOTER_LINK", link)
    monkeypatch.setattr(settings, "MINIAPP_SUBMIT_CTA", cta)


def test_navigation_is_typed_exactly_once_full_miniapp(monkeypatch):
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=True)
    items = _publication_navigation({"novel_preview_url": "https://telegra.ph/x-1"})
    kinds = [it.action for it in items]
    assert kinds == [READ_ONLINE_ACTION, BOT_SUBMIT_ACTION, MINI_APP_SUBMIT_ACTION]
    assert items[0].url == "https://telegra.ph/x-1"
    assert items[1].url == "https://t.me/xgdPost_bot?start=submit"
    assert items[2].url == "https://t.me/xgdPost_bot?startapp=miniapp"


def test_navigation_unconfigured_or_disabled(monkeypatch):
    _patch(monkeypatch, link="", cta=True)
    assert _publication_navigation({}) == []
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=False)
    items = _publication_navigation({})
    assert [it.action for it in items] == [BOT_SUBMIT_ACTION]
    assert items[0].url == "https://t.me/xgdPost_bot?start=submit"
    assert "startapp" not in items[0].url


def test_navigation_read_online_only_for_real_url(monkeypatch):
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=False)
    items = _publication_navigation({})
    assert [it.action for it in items] == [BOT_SUBMIT_ACTION]
    items = _publication_navigation({"novel_preview_url": "telegra.ph/x"})
    assert [it.action for it in items] == [BOT_SUBMIT_ACTION]
    items = _publication_navigation({"novel_preview_url": "https://telegra.ph/x"})
    assert [it.action for it in items] == [READ_ONLINE_ACTION, BOT_SUBMIT_ACTION]
    assert items[0].url == "https://telegra.ph/x"


def test_caption_footer_opens_owning_bot_semantics(monkeypatch):
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=True)
    caption = channel_caption({
        "tags": "#test",
        "title": "标题",
        "novel_preview_url": "https://telegra.ph/x-1",
    })
    assert 'href="https://telegra.ph/x-1"' in caption
    assert 'href="https://t.me/xgdPost_bot?start=submit"' in caption
    assert 'href="https://t.me/xgdPost_bot?startapp=miniapp"' in caption
    # Each label appears exactly once; READ_ONLINE only in the footer.
    for label in (READ_ONLINE_LABEL, BOT_SUBMIT_LABEL, MINI_APP_SUBMIT_LABEL):
        assert caption.count(label) == 1
    assert "🔗 在线阅读" not in caption  # no duplicate body block

    # BOT_SUBMIT and MINI_APP directly follow each other when no preview.
    caption2 = channel_caption({"tags": "#test", "title": "标题"})
    assert 'href="https://t.me/xgdPost_bot?start=submit"' in caption2
    assert 'href="https://t.me/xgdPost_bot?startapp=miniapp"' in caption2
    assert READ_ONLINE_LABEL not in caption2


def test_caption_footer_is_per_bot(monkeypatch):
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=True)
    caption1 = channel_caption({"tags": "#test"})
    _patch(monkeypatch, link="https://t.me/vorePost_bot", cta=True)
    caption2 = channel_caption({"tags": "#test"})
    assert "xgdPost_bot" in caption1 and "xgdPost_bot" not in caption2
    assert "vorePost_bot" in caption2 and "vorePost_bot" not in caption1
    assert "start=submit" in caption1
    assert "startapp=miniapp" in caption1
    assert caption1.count(BOT_SUBMIT_LABEL) == 1
    assert caption1.count(MINI_APP_SUBMIT_LABEL) == 1
    assert caption2.count(BOT_SUBMIT_LABEL) == 1
    assert caption2.count(MINI_APP_SUBMIT_LABEL) == 1


def test_caption_footer_miniapp_disabled_has_only_bot_submit(monkeypatch):
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=False)
    caption = channel_caption({"tags": "#test"})
    assert 'href="https://t.me/xgdPost_bot?start=submit"' in caption
    assert BOT_SUBMIT_LABEL in caption
    assert "startapp=miniapp" not in caption
    assert MINI_APP_SUBMIT_LABEL not in caption
    assert caption.count(BOT_SUBMIT_LABEL) == 1


def test_caption_footer_absent_and_safe_when_no_config(monkeypatch):
    _patch(monkeypatch, link="", cta=True)
    caption = channel_caption({"tags": "#test", "novel_preview_url": "https://telegra.ph/x"})
    assert BOT_SUBMIT_LABEL not in caption
    assert "TG 投稿" not in caption
    # No footer ⇒ normal build_caption behavior preserves the legacy body link.
    assert "🔗 在线阅读" in caption


def test_caption_respects_budget_with_footer(monkeypatch):
    import re
    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=True)
    near_limit = {"tags": "#test", "title": "标题", "note": "内容" * 400}
    caption = channel_caption(near_limit)
    visible = re.sub(r"<[^>]+>", "", caption)
    assert len(visible) <= 1024
    assert 'href="https://t.me/xgdPost_bot?start=submit"' in caption
    assert 'href="https://t.me/xgdPost_bot?startapp=miniapp"' in caption

    _patch(monkeypatch, link="https://t.me/xgdPost_bot", cta=False)
    caption_off = channel_caption({"tags": "#test", "title": "标题", "note": "内容" * 200})
    visible_off = re.sub(r"<[^>]+>", "", caption_off)
    assert len(visible_off) <= 1024
    assert 'href="https://t.me/xgdPost_bot?start=submit"' in caption_off
    assert "startapp=miniapp" not in caption_off


def test_labels_are_fixed_and_html_safe():
    for label in (READ_ONLINE_LABEL, BOT_SUBMIT_LABEL, MINI_APP_SUBMIT_LABEL):
        out = html.escape(label, quote=False)
        assert out == label


def _keyboard_to_tuples(kb):
    return [
        [(b.text, b.url or b.callback_data) for b in row]
        for row in kb.inline_keyboard
    ]


def test_review_keyboard_has_no_submission_cta_and_keeps_moderation():
    """Review control cards NEVER expose the public submission CTA, even when
    the owning bot's submission entrypoints are configured (§review-cta)."""
    from telepost.telegram.review_keyboard import review_keyboard
    kb = review_keyboard(
        55, "https://www.pixiv.net/artworks/1", source="api", pixiv_id="1",
    )
    rows = _keyboard_to_tuples(kb)
    # Moderation controls unchanged.
    assert rows[0] == [("✅ 发布到频道", "review_approve:55"), ("❌ 拒绝", "review_reject:55")]
    assert any(text == "🔇 遮罩：关" for text, _ in rows[1])
    # No public submission-acquisition CTA (TG 投稿 / Mini App / start).
    assert all(BOT_SUBMIT_LABEL not in text for row in rows for text, _ in row)
    assert all("startapp=miniapp" not in str(entry) for row in rows for entry in row)


def test_review_keyboard_omits_cta_when_no_submission_url():
    from telepost.telegram.review_keyboard import review_keyboard
    kb = review_keyboard(55, "https://www.pixiv.net/artworks/1")
    rows = _keyboard_to_tuples(kb)
    assert all(BOT_SUBMIT_LABEL not in text for row in rows for text, _ in row)


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
            assert BOT_SUBMIT_LABEL not in text


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


def test_private_keyboard_uses_webapp_when_enabled(monkeypatch):
    from config import settings
    from ui.keyboards import Keyboards

    monkeypatch.setenv("TELEPOST_BOT_INDEX", "2")
    monkeypatch.setattr(settings, "MINIAPP_ENABLED", True)
    monkeypatch.setattr(settings, "MINIAPP_PUBLIC_URL", "https://telepost.example/app/")

    markup = Keyboards.main_menu()
    button = markup.keyboard[-1][0]
    assert button.web_app.url == "https://telepost.example/app?bot=bot2"
