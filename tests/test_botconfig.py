import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import run
from handlers import botconfig as botconfig_module
from utils.runtime_policy import load_runtime_policy, update_runtime_policy


def test_runtime_policy_is_atomic_and_validated(tmp_path):
    path = tmp_path / "runtime-policy.json"
    update_runtime_policy(path, {"CHANNEL_ID": "-100123", "API_REVIEW_REQUIRED": True})

    assert load_runtime_policy(path) == {
        "CHANNEL_ID": "-100123",
        "API_REVIEW_REQUIRED": "true",
    }
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        update_runtime_policy(path, {"TOKEN": "must-not-be-configurable"})


def test_build_bot_env_loads_only_its_persisted_policy(tmp_path):
    db_path = tmp_path / "bot1" / "submissions.db"
    policy_path = db_path.parent / "runtime-policy.json"
    update_runtime_policy(policy_path, {
        "CHANNEL_ID": "-100999",
        "SHOW_SUBMITTER": False,
    })
    env = run.build_bot_env(1, {
        "BOT1_TOKEN": "token",
        "BOT1_CHANNEL_ID": "@old",
        "BOT1_DB_PATH": str(db_path),
    })

    assert env["CHANNEL_ID"] == "-100999"
    assert env["SHOW_SUBMITTER"] == "false"
    assert env["RUNTIME_POLICY_PATH"] == str(policy_path)
    assert env["TELEPOST_MANAGED_RESTART"] == "true"


@pytest.mark.parametrize("raw, expected", [
    ("https://t.me/c/4318193445/12", "-1004318193445"),
    ("https://t.me/example_channel", "@example_channel"),
    ("-100123456789", "-100123456789"),
])
def test_parse_chat_reference(raw, expected):
    assert botconfig_module._parse_chat_reference(raw) == expected


@pytest.mark.asyncio
async def test_botconfig_is_owner_only(mock_telegram_update, mock_telegram_context):
    mock_telegram_context.args = []
    with patch.object(botconfig_module, "is_owner", return_value=False):
        await botconfig_module.botconfig(mock_telegram_update, mock_telegram_context)

    mock_telegram_update.effective_message.reply_text.assert_awaited_once_with(
        "⛔ 此命令仅限 Bot 所有者使用"
    )


@pytest.mark.asyncio
async def test_channel_change_persists_after_validation(tmp_path, mock_telegram_update, mock_telegram_context):
    policy_path = tmp_path / "runtime-policy.json"
    mock_telegram_context.args = ["channel", "@new_channel"]
    with patch.object(botconfig_module, "is_owner", return_value=True), \
         patch.object(botconfig_module, "_policy_path", return_value=str(policy_path)), \
         patch.object(botconfig_module, "_require_empty_review_queue", new=AsyncMock()), \
         patch.object(botconfig_module, "_validated_chat_id", new=AsyncMock(return_value="-100777")):
        await botconfig_module.botconfig(mock_telegram_update, mock_telegram_context)

    assert json.loads(policy_path.read_text(encoding="utf-8"))["CHANNEL_ID"] == "-100777"
    assert "单独重载" not in mock_telegram_update.effective_message.reply_text.await_args.args[0]
