"""Mini App auth tests (§60): initData validation + session lifecycle.

Uses ``init_data_py.sign`` to build *real* Telegram-format fixtures (same
HMAC algorithm), then verifies the server rejects tampering, expiry, missing
data and never trusts client-supplied identity.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.security

TOKEN = "123456:TESTTOKENabcdefghijklmnopqrstuvwxyz012345"


def _sign(*, auth_date=None, user_id=42, username="alice",
          first_name="Alice", last_name="", **extra):
    from init_data_py.signing import sign
    payload = {
        "user": (
            '{"id":%d,"first_name":"%s","last_name":"%s","username":"%s",'
            '"language_code":"en"}' % (user_id, first_name, last_name, username)
        ),
        **extra,
    }
    return sign(payload, TOKEN, auth_date=auth_date)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CHANNEL_ID", "@test")
    monkeypatch.setenv("OWNER_ID", "1")
    monkeypatch.setenv("MINIAPP_SESSION_SECRET", "s" * 40)
    # auth.BOT_TOKEN is captured at import from config.settings (conftest sets
    # a fake). Point it at the token the fixtures sign with.
    from telepost.miniapp import auth as miniapp_auth
    monkeypatch.setattr(miniapp_auth, "BOT_TOKEN", TOKEN)


def test_valid_init_data_accepted():
    from telepost.miniapp.auth import validate_init_data
    raw = _sign()
    user = validate_init_data(raw)
    assert user["telegram_user_id"] == 42
    assert user["username"] == "alice"


def test_missing_init_data_rejected():
    from telepost.miniapp.auth import (
        InitDataMissingError,
        validate_init_data,
    )
    with pytest.raises(InitDataMissingError):
        validate_init_data("")
    with pytest.raises(InitDataMissingError):
        validate_init_data(None)


def test_tampered_hash_rejected():
    from telepost.miniapp.auth import (
        InitDataSignatureError,
        validate_init_data,
    )
    raw = _sign()
    # Flip one character inside the payload (identity changed, hash stale).
    forged = raw.replace("alice", "eve", 1)
    with pytest.raises(InitDataSignatureError):
        validate_init_data(forged)


def test_tampered_user_id_rejected():
    from telepost.miniapp.auth import (
        InitDataSignatureError,
        validate_init_data,
    )
    import urllib.parse
    raw = _sign(user_id=42)
    params = urllib.parse.parse_qsl(raw, keep_blank_values=True)
    tampered = [
        (key, urllib.parse.unquote(value).replace('"id":42', '"id":999'))
        if key == "user" else (key, value)
        for key, value in params
    ]
    forged = urllib.parse.urlencode(tampered)
    with pytest.raises(InitDataSignatureError):
        validate_init_data(forged)


def test_expired_init_data_rejected():
    from telepost.miniapp.auth import (
        InitDataExpiredError,
        validate_init_data,
    )
    old = int(time.time()) - 7200  # 2h ago, beyond the 1h window
    raw = _sign(auth_date=old)
    with pytest.raises(InitDataExpiredError):
        validate_init_data(raw)


def test_malformed_init_data_rejected():
    from telepost.miniapp.auth import (
        InitDataMalformedError,
        validate_init_data,
    )
    with pytest.raises(InitDataMalformedError):
        validate_init_data("user=%7B%22id%22%3A1%7D&auth_date=123&hash=xyz")
    with pytest.raises(InitDataMalformedError):
        validate_init_data("not-a-query-string-at-all")


def test_disabled_miniapp():
    from telepost.miniapp.auth import init_data_enabled
    os.environ["MINIAPP_ENABLED"] = "false"
    assert not init_data_enabled()
    os.environ["MINIAPP_ENABLED"] = "true"
    assert init_data_enabled()


# ---- session lifecycle --------------------------------------------------

def test_session_roundtrip():
    from telepost.miniapp.session import issue_session, verify_session
    token = issue_session(42, ["submitter"], username="alice")
    principal = verify_session(token)
    assert principal is not None
    assert principal.telegram_user_id == 42
    assert principal.roles == ["submitter"]


def test_session_tamper_rejected():
    from telepost.miniapp.session import issue_session, verify_session
    token = issue_session(42, ["submitter"])
    forged = token[:-6] + "AAAAAA"
    assert verify_session(forged) is None


def test_session_expiry():
    from telepost.miniapp.session import issue_session, verify_session
    token = issue_session(42, ["submitter"], ttl=60)
    now = time.time() + 120
    assert verify_session(token, now=now) is None
    # still valid before expiry
    assert verify_session(token, now=time.time() + 5) is not None


def test_session_garbage_rejected():
    from telepost.miniapp.session import verify_session
    for bad in ("", "nope", "ma_v1.", "ma_v1.abc", "tp_whatever"):
        assert verify_session(bad) is None


def test_session_secret_missing_fails_closed(monkeypatch):
    monkeypatch.delenv("MINIAPP_SESSION_SECRET", raising=False)
    from telepost.miniapp.session import issue_session
    with pytest.raises(RuntimeError):
        issue_session(42, ["submitter"])


def test_session_rejects_non_positive_user():
    from telepost.miniapp.session import issue_session
    with pytest.raises(ValueError):
        issue_session(0, ["submitter"])
    with pytest.raises(ValueError):
        issue_session(-5, ["submitter"])