"""Map exceptions (and optional HTTP statuses) to stable error class strings.

Specific exception types win over a coarse HTTP status, so an upstream 502
wrapping a timeout keeps the true cause.
"""
from __future__ import annotations

from typing import Optional


def classify(exc: Optional[BaseException], status: Optional[int] = None) -> str:
    if exc is not None:
        module = type(exc).__module__ or ""
        name = type(exc).__name__ or ""

        if module.startswith("telegram.error") or module.startswith("telegram."):
            if name in ("TimedOut",):
                return "network_timeout"
            if name in ("RetryAfter", "TelegramRetryAfter"):
                return "rate_limited"
            if name == "BadRequest":
                return "telegram_send_failed"

        try:
            import aiosqlite
            if isinstance(exc, aiosqlite.Error):
                return "database_failed"
        except ImportError:  # pragma: no cover
            pass

        code = str(getattr(exc, "code", "") or "")
        if code == "duplicate_publication":
            return "duplicate"
        text = f"{name} {exc}".lower()
        if "decode" in text and "budget" in text:
            return "media_decode_budget_exceeded"
        if module.startswith("PIL.") or name in (
            "UnidentifiedImageError", "DecompressionBombError"
        ):
            return "media_processing_failed"

    if isinstance(status, int):
        if status == 409:
            return "idempotency_conflict"
        if status == 429:
            return "rate_limited"
        if 500 <= status < 600:
            return "remote_5xx"
        if 400 <= status < 500:
            return "remote_4xx"
    return "internal_error"
