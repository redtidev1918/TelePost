"""Telegram Mini App ``initData`` verification (server-side, §9-§11, §60).

The Mini App frontend sends ``window.Telegram.WebApp.initData`` (a signed query
string). The backend MUST validate it against the bot token before trusting any
identity: ``initDataUnsafe`` on the client is for display only and can be
forged by anyone.

We use the maintained MIT-licensed ``init-data-py`` package for the web-app
signature (HMAC-SHA256 over the sorted key-value pairs, Telegram's documented
algorithm) and do not re-implement cryptography ourselves. The dependency is
pinned to a known-good release; see ``requirements.txt``.

Only the *server-verified* fields may ever influence authorization. The raw
``initData`` is never logged and never persisted as a session credential.
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

from config.settings import TOKEN as BOT_TOKEN

logger = logging.getLogger(__name__)

# init-data-py 1.0+ exposes a function API. Import lazily so a missing optional
# dependency fails only when Mini App auth is actually used, never at import
# time for the Bot-only path.
_INIT_DATA_LIBRARY = "init_data_py"

# How old an initData signature may be before we treat it as expired.
INIT_DATA_MAX_AGE_SECONDS = 60 * 60  # Telegram signs auth_date at open time


class InitDataError(Exception):
    """Base class: initData rejected (reason is user-safe, never raw data)."""

    code = "invalid_init_data"

    def __init__(self, message: str, *, code: Optional[str] = None):
        super().__init__(message)
        self.code = type(self).code if code is None else code


class InitDataMissingError(InitDataError):
    code = "missing_init_data"


class InitDataSignatureError(InitDataError):
    code = "invalid_init_data_signature"


class InitDataExpiredError(InitDataError):
    code = "init_data_expired"


class InitDataMalformedError(InitDataError):
    code = "invalid_init_data_format"


def _verify_library_available() -> None:
    import importlib.util

    if importlib.util.find_spec(_INIT_DATA_LIBRARY) is None:
        raise InitDataError(
            "Mini App 登录组件未安装（init-data-py），请先安装依赖",
            code="init_data_library_missing",
        )


def validate_init_data(init_data: str) -> dict:
    """Validate raw ``initData`` and return the server-trusted user payload.

    Returns ``{"telegram_user_id": int, "username": str | None, ...}``.
    Raises a user-safe :class:`InitDataError` subclass when invalid.
    """
    _verify_library_available()
    if not init_data or not isinstance(init_data, str):
        raise InitDataMissingError("缺少 Telegram initData")

    from init_data_py import errors
    from init_data_py.validation import validate_by_hash

    if not BOT_TOKEN:
        raise InitDataError(
            "Telegram Bot Token 未配置，无法验证小程序身份",
            code="init_data_bot_token_missing",
        )

    try:
        data = validate_by_hash(
            init_data,
            BOT_TOKEN,
            expires_in=INIT_DATA_MAX_AGE_SECONDS,
        )
    except errors.ExpiredError as exc:
        raise InitDataExpiredError("initData 已过期，请重新打开小程序") from exc
    except errors.HashInvalidError as exc:
        raise InitDataSignatureError("initData 签名校验失败") from exc
    except errors.InvalidInitDataError as exc:
        raise InitDataMalformedError("initData 格式无效") from exc
    except ValueError as exc:
        raise InitDataMalformedError(f"initData 解析失败: {exc}") from exc

    # ``data.user`` is a parsed user model; the validated object guarantees it
    # came from Telegram (the hash covers every field, including ``user``).
    user = getattr(data, "user", None) or {}
    telegram_user_id = int(getattr(user, "id", 0) or 0)
    if telegram_user_id <= 0:
        raise InitDataMalformedError("initData 缺少有效用户 ID")
    username = getattr(user, "username", None) or ""
    first_name = getattr(user, "first_name", None) or ""
    last_name = getattr(user, "last_name", None) or ""
    return {
        "telegram_user_id": telegram_user_id,
        "username": str(username)[:64],
        "first_name": str(first_name)[:128],
        "last_name": str(last_name)[:128],
    }


def init_data_enabled() -> bool:
    """Mini App auth is on when MINIAPP_ENABLED is not literally 'false'."""
    return os.getenv("MINIAPP_ENABLED", "true").strip().lower() not in {
        "false",
        "0",
        "no",
    }