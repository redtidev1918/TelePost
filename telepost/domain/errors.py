"""Typed errors shared by domain + application layers (never PTB-specific)."""
from __future__ import annotations

from typing import Any, Dict, Optional


class TelePostError(Exception):
    """Base class for typed application errors."""

    code = "telepost_error"
    http_status = 500

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(TelePostError):
    code = "not_found"
    http_status = 404


class ConflictError(TelePostError):
    code = "conflict"
    http_status = 409


class InvalidStateError(ConflictError):
    code = "invalid_state"


class AlreadyClaimedError(ConflictError):
    """Another actor currently owns the publishing claim."""

    code = "review_busy"


class DuplicatePublicationError(ConflictError):
    """An idempotency replay / historical duplicate was detected."""

    code = "duplicate_publication"

    def __init__(self, message: str, *, reuse_reason: str = "idempotent_replay",
                 result: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.reuse_reason = reuse_reason
        self.result = result or {}


class PublicationError(TelePostError):
    code = "publication_failed"
    http_status = 502

    def __init__(self, message: str, *, retryable: bool = True, uncertain: bool = False,
                 original: Optional[BaseException] = None):
        super().__init__(message)
        self.retryable = retryable
        self.uncertain = uncertain
        self.original = original


class DeliveryUncertainError(PublicationError):
    """The request may or may not have reached Telegram; never blind-retry."""

    code = "delivery_uncertain"

    def __init__(self, message: str, *, original: Optional[BaseException] = None):
        super().__init__(message, retryable=False, uncertain=True, original=original)


class ValidationError(TelePostError):
    code = "validation_error"
    http_status = 400
