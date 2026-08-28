"""API token 管理命令测试。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ConversationHandler

from handlers.api_commands import gen_token


@pytest.mark.asyncio
async def test_gen_token_rejects_non_owner():
    update = MagicMock()
    update.effective_user.id = 99999
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = ["自动化脚本"]

    with (
        patch("handlers.api_commands.is_owner", return_value=False),
        patch("handlers.api_commands.api_tokens.generate_token", new_callable=AsyncMock) as generate,
    ):
        result = await gen_token(update, context)

    assert result == ConversationHandler.END
    generate.assert_not_awaited()
    update.message.reply_text.assert_awaited_once_with("⛔ 此命令仅限机器人所有者使用")


@pytest.mark.asyncio
async def test_gen_token_allows_owner():
    update = MagicMock()
    update.effective_user.id = 123456789
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = ["自动化", "脚本"]

    with (
        patch("handlers.api_commands.is_owner", return_value=True),
        patch(
            "handlers.api_commands.api_tokens.generate_token",
            new_callable=AsyncMock,
            return_value="tp_secret",
        ) as generate,
    ):
        result = await gen_token(update, context)

    assert result == ConversationHandler.END
    generate.assert_awaited_once_with(123456789, "自动化 脚本")
    reply = update.message.reply_text.await_args
    assert "tp_secret" in reply.args[0]
    assert reply.kwargs["parse_mode"] == "HTML"
