"""Resolve TelePost's requested update transport into an effective mode."""

import ipaddress
from urllib.parse import urlparse


VALID_RUN_MODES = ("AUTO", "POLLING", "WEBHOOK")


def webhook_url_is_public(url: str) -> bool:
    """Return whether a URL is a plausible Telegram-reachable HTTPS endpoint."""
    try:
        parsed = urlparse((url or "").strip())
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return False
        hostname = parsed.hostname.lower().rstrip(".")
        if hostname == "localhost" or hostname.endswith(".local"):
            return False
        try:
            return ipaddress.ip_address(hostname).is_global
        except ValueError:
            return "." in hostname
    except (TypeError, ValueError):
        return False


def resolve_run_mode(requested: str, webhook_url: str) -> str:
    """Resolve AUTO from configuration; explicit modes are returned unchanged."""
    mode = (requested or "AUTO").strip().upper()
    if mode not in VALID_RUN_MODES:
        raise ValueError(
            f"RUN_MODE 必须是 {' / '.join(VALID_RUN_MODES)}，当前值为 {requested!r}"
        )
    if mode == "AUTO":
        return "WEBHOOK" if webhook_url_is_public(webhook_url) else "POLLING"
    return mode
