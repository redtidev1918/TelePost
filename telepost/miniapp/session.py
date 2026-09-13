"""Mini App session tokens: short-lived, server-signed, stateless.

Design (security-first, §12/§13 of the Mini App spec):

* The Mini App NEVER sees the bot token or any long-lived API token. It gets a
  session token only after the server validates Telegram ``initData``.
* The token is a compact HMAC-SHA256-signed payload that encodes the verified
  ``telegram_user_id``, the role set at issue time, and an absolute expiry. No
  database row is needed, so sessions survive restarts and are revocable by
  natural expiry (short TTL) or by secret rotation.
* ``MINIAPP_SESSION_SECRET`` must be a fresh random secret; it is never the bot
  token and never a TelePost API token (no secret reuse, §115).
* Tokens are bearer credentials: they must be sent over HTTPS only, and the
  server treats them like any other credential (redacted from logs, §58).

The token format is ``ma_v1.<b64url(payload)>.<b64url(sig)>``. The payload is
``{"uid": int, "roles": [..], "exp": int, "nbf": int, "jti": hex}``.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

_TOKEN_PREFIX = "ma_v1."
_SIG_BYTES = 32
_DEFAULT_TTL_SECONDS = 30 * 60          # short-lived by default (§12)
_MAX_TTL_SECONDS = 12 * 3600            # hard ceiling; longer needs config intent
_CLOCK_SKEW_SECONDS = 30                # tolerate small clock drift


def session_secret() -> str:
    """Return the configured signing secret (fail closed when missing)."""
    secret = os.getenv("MINIAPP_SESSION_SECRET", "").strip()
    if not secret:
        raise RuntimeError(
            "MINIAPP_SESSION_SECRET is not configured; Mini App session "
            "issuing is disabled (fail closed)."
        )
    if len(secret) < 32:
        raise RuntimeError(
            "MINIAPP_SESSION_SECRET must be at least 32 characters."
        )
    return secret


def session_ttl_seconds() -> int:
    """TTL for new sessions: MINIAPP_SESSION_TTL, bounded (default 30 min)."""
    try:
        ttl = int(os.getenv("MINIAPP_SESSION_TTL", str(_DEFAULT_TTL_SECONDS)))
    except (TypeError, ValueError):
        ttl = _DEFAULT_TTL_SECONDS
    return max(60, min(int(ttl), _MAX_TTL_SECONDS))


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


@dataclass(frozen=True)
class MiniAppPrincipal:
    """Verified Mini App identity. Server-side only; never client-trusted."""

    telegram_user_id: int
    username: str = ""
    roles: List[str] = field(default_factory=list)
    session_id: str = ""

    def has_role(self, role: str) -> bool:
        return role in self.roles


def _sign(payload: bytes, secret: str, /) -> bytes:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()


def _encode(uid: int, roles: List[str], ttl: int, now: float, /) -> str:
    jti = secrets.token_hex(8)
    payload = {
        "uid": int(uid),
        "roles": sorted(set(roles)),
        "exp": int(now + ttl),
        "nbf": int(now - _CLOCK_SKEW_SECONDS),
        "jti": jti,
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = _sign(body, session_secret())
    return _TOKEN_PREFIX + _b64url_encode(body) + "." + _b64url_encode(sig)


def issue_session(telegram_user_id: int, roles: List[str], *,
                  ttl: Optional[int] = None, username: str = "") -> str:
    """Issue a signed session for a *server-verified* Telegram user."""
    if not isinstance(telegram_user_id, int) or telegram_user_id <= 0:
        raise ValueError("telegram_user_id must be a positive integer")
    ttl = session_ttl_seconds() if ttl is None else int(ttl)
    return _encode(telegram_user_id, roles, ttl, time.time())


def verify_session(token: str, *, now: Optional[float] = None) -> Optional[MiniAppPrincipal]:
    """Validate a session token; return the principal or None.

    Returns None on any tampering/expiry (caller answers 401). Never raises
    for bad input; only raises if the server secret is misconfigured.
    """
    if not token:
        return None
    if not token.startswith(_TOKEN_PREFIX):
        return None
    try:
        body_b64, sig_b64 = token[len(_TOKEN_PREFIX):].split(".", 1)
        body = _b64url_decode(body_b64)
        sig = _b64url_decode(sig_b64)
    except (ValueError, binascii.Error):
        return None
    expected = _sign(body, session_secret())
    if not hmac.compare_digest(sig, expected):
        return None
    if len(sig) < _SIG_BYTES:
        return None
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return None
    current = time.time() if now is None else float(now)
    try:
        exp = int(payload["exp"])
        nbf = int(payload["nbf"])
        uid = int(payload["uid"])
        roles = [str(r) for r in payload.get("roles", [])]
        jti = str(payload.get("jti", ""))
    except (KeyError, TypeError, ValueError):
        return None
    if uid <= 0:
        return None
    if current > exp or current < nbf:
        return None
    return MiniAppPrincipal(
        telegram_user_id=uid,
        roles=roles,
        session_id=jti,
    )


def principal_from_json(data: dict) -> Optional[MiniAppPrincipal]:
    """Rehydrate a principal from an auditable dict (no re-verification)."""
    try:
        return MiniAppPrincipal(
            telegram_user_id=int(data["telegram_user_id"]),
            username=str(data.get("username", "")),
            roles=[str(r) for r in data.get("roles", [])],
            session_id=str(data.get("session_id", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None