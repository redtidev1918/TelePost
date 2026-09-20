"""Exact-text contracts for deterministic Telegram presentation.

These are *layout* goldens: semantic tests still prove the business meaning,
while these files catch stray blank lines, broken ordering, changed command
formatting and accidental internal vocabulary.
"""
from pathlib import Path

from utils.helper_functions import build_caption
from ui.messages import MessageFormatter

GOLDEN_DIR = Path(__file__).parent / "golden" / "telegram"


def assert_golden(name: str, actual: str) -> None:
    expected = (GOLDEN_DIR / f"{name}.html").read_text(encoding="utf-8")
    assert actual == expected, f"Telegram presentation changed for {name}"


def test_golden_welcome_user():
    assert_golden("welcome-user", MessageFormatter.welcome_message("hezzn", False))


def test_golden_welcome_admin():
    assert_golden("welcome-admin", MessageFormatter.welcome_message("hezzn", True))


def test_golden_help_user():
    assert_golden("help-user", MessageFormatter.help_message(False))


def test_golden_help_admin():
    assert_golden("help-admin", MessageFormatter.help_message(True))


def test_golden_about():
    assert_golden("about", MessageFormatter.about_message())


def test_golden_submission_preview():
    assert_golden(
        "submission-preview",
        MessageFormatter.submission_preview("第一行\n第二行", ["#pixiv", "#novel"], 2),
    )


def test_golden_caption_channel_complete():
    assert_golden(
        "caption-channel-complete",
        build_caption({
            "title": "夜色下的港口",
            "note": "这是第一段。\n这是第二段。",
            "tags": "#night #city",
            "link": "https://www.pixiv.net/artworks/12345",
            "spoiler": "false",
            "submitter_user_id": 123456789,
            "submitter_username": "hezzn",
            "submitter_display_name": "Hezzn",
        }),
    )


def test_golden_caption_miniapp_spoiler():
    assert_golden(
        "caption-miniapp-spoiler",
        build_caption({
            "title": "R18 例",
            "note": "预览文本",
            "tags": "#r18",
            "link": "",
            "spoiler": "true",
            "media_types": ["photo", "document"],
            "user_id": 123456789,
            "username": "hezzn",
            "submitter_user_id": 123456789,
            "submitter_username": "hezzn",
            "submitter_display_name": "Hezzn",
        }, surface="miniapp"),
    )


def test_golden_caption_review_service_source():
    assert_golden(
        "caption-review-service-source",
        build_caption({
            "title": "自动投稿",
            "note": "",
            "tags": "#service",
            "link": "",
            "source": "pixivflow",
            "source_label": "PixivFlow",
            "submitter_user_id": None,
            "submitter_username": "",
            "submitter_display_name": "",
        }, surface="review"),
    )


def test_golden_caption_long_note_truncates_without_extra_blanks():
    assert_golden(
        "caption-channel-long-note",
        build_caption({
            "title": "长文投稿",
            "note": ("很长的说明。" * 80),
            "tags": "#long",
            "link": "",
            "spoiler": "false",
            "submitter_user_id": 123456789,
            "submitter_username": "hezzn",
            "submitter_display_name": "Hezzn",
        }),
    )
