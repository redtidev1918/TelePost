"""Automatic Polling/Webhook mode selection."""

import pytest

from utils.run_mode import resolve_run_mode, webhook_url_is_public


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://bot.example.com", True),
        ("https://example.fly.dev/path", True),
        ("http://bot.example.com", False),
        ("https://localhost", False),
        ("https://127.0.0.1", False),
        ("https://192.168.1.2", False),
        ("", False),
    ],
)
def test_public_webhook_url_detection(url, expected):
    assert webhook_url_is_public(url) is expected


def test_auto_selects_webhook_with_public_https_url():
    assert resolve_run_mode("AUTO", "https://bot.example.com") == "WEBHOOK"


def test_auto_falls_back_to_polling_without_public_url():
    assert resolve_run_mode("AUTO", "") == "POLLING"
    assert resolve_run_mode("AUTO", "http://bot.example.com") == "POLLING"


def test_explicit_modes_are_preserved():
    assert resolve_run_mode("POLLING", "https://bot.example.com") == "POLLING"
    assert resolve_run_mode("WEBHOOK", "") == "WEBHOOK"


def test_invalid_mode_is_rejected():
    with pytest.raises(ValueError, match="RUN_MODE"):
        resolve_run_mode("magic", "")
