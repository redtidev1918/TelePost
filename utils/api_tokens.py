"""
API 访问令牌管理

- 明文 token 只在生成时返回一次，库中仅存 SHA-256 哈希
- token 绑定 Telegram 用户身份，API 投稿按该身份记账与限频
"""
import hashlib
import secrets
from datetime import datetime

from database.db_manager import get_db

PREFIX = "tp_"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def generate_token(telegram_user_id: int, name: str = "") -> str:
    """生成新 token，返回明文（仅此一次）"""
    token = PREFIX + secrets.token_urlsafe(24)
    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute(
            "INSERT INTO api_tokens (token_hash, telegram_user_id, name, created_at, revoked) VALUES (?, ?, ?, ?, 0)",
            (_hash(token), telegram_user_id, (name or "default")[:40], datetime.now().timestamp()),
        )
    return token


async def authenticate(token: str):
    """校验 token，返回绑定行（含 telegram_user_id/name），无效或已吊销返回 None"""
    if not token or not token.startswith(PREFIX):
        return None
    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute(
            "SELECT id, telegram_user_id, name, created_at FROM api_tokens WHERE token_hash=? AND revoked=0",
            (_hash(token),),
        )
        return await c.fetchone()


async def list_tokens(telegram_user_id: int):
    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute(
            "SELECT id, name, created_at, revoked FROM api_tokens WHERE telegram_user_id=? ORDER BY id DESC",
            (telegram_user_id,),
        )
        return await c.fetchall()


async def revoke_token(telegram_user_id: int, token_id: int) -> bool:
    async with get_db() as conn:
        c = await conn.cursor()
        await c.execute(
            "UPDATE api_tokens SET revoked=1 WHERE id=? AND telegram_user_id=?",
            (token_id, telegram_user_id),
        )
        return c.rowcount > 0
