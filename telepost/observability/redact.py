"""Best-effort secret redaction for logs and audit details.

Stdlib only. The rules deliberately stay simple: miss an obscure leak rather
 than build a secret-detection engine.
"""
from __future__ import annotations

import re
from typing import Any

_MASK = "***"

# Telegram bot tokens look like 123456789:AA... (8-10 digit bot id, colon,
# 35-ish base64url-ish chars).
_BOT_TOKEN_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{20,}")
_BEARER_RE = re.compile(r"(?i:\bBearer\s+)[A-Za-z0-9._\-]+")
# token=/secret=/password= URL/form params.
_PARAM_RE = re.compile(
    r"(?i:((?:^|[?;&\s])(?:token|secret|password|api[_-]?key)\s*=\s*))[^&\s;\"']+"
)

_SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "x-telegram-bot-api-secret-token",
}
_SENSITIVE_KEY_PARTS = ("token", "secret", "password", "api_key", "apikey")


def redact_text(value: str) -> str:
    """Mask bot tokens, Bearer credentials and secret-ish params in a string."""
    if not isinstance(value, str):
        value = str(value)
    value = _BOT_TOKEN_RE.sub(_MASK, value)
    value = _BEARER_RE.sub(lambda m: m.group(0).split()[0] + " " + _MASK, value)
    value = _PARAM_RE.sub(lambda m: m.group(1) + _MASK, value)
    return value


def redact_headers(headers: Any) -> dict:
    """Return a copy of a headers mapping with sensitive values masked."""
    try:
        items = headers.items()
    except AttributeError:
        return {}
    out = {}
    for key, value in items:
        lname = str(key).lower()
        if lname in _SENSITIVE_HEADERS or any(
            part in lname for part in _SENSITIVE_KEY_PARTS
        ):
            out[key] = _MASK
        else:
            out[key] = redact_text(str(value))
    return out


def sanitize_detail(obj: Any) -> Any:
    """Recursively mask sensitive keys and redact string values."""
    if isinstance(obj, dict):
        return {
            key: _MASK if _is_sensitive_key(key) else sanitize_detail(value)
            for key, value in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [sanitize_detail(value) for value in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def _is_sensitive_key(key: Any) -> bool:
    lname = str(key).lower()
    return lname in _SENSITIVE_HEADERS or any(
        part in lname for part in _SENSITIVE_KEY_PARTS
    )
