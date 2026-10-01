"""Mini App 入口架构回归测试。

生产回归（m16459）：Reply Keyboard 的 web_app 按钮（keyboardButtonSimpleWebView）
不向 Mini App 传递 Telegram 用户身份，导致 AuthProvider 的 launch initData
校验永远失败，显示"请在 Telegram 中打开"。同时 `t.me/<bot>?startapp=` Deep Link
要求 Bot 有 Main Mini App 关联，硬编码 MenuButtonDefault 会剥离该关联。

本文件锁定正确入口模型：
1. Reply Keyboard 的「📱 Mini App」是**普通文本按钮**（绝不带 web_app=）。
2. 点击该文本由 handle_menu_shortcuts 回复 Inline Web App 按钮（miniapp_launch）。
3. miniapp_launch() 是 Inline Web App 按钮，URL 带正确的 ?bot=botN。
4. MINIAPP 未启用或缺 URL 时不渲染任何 Mini App 按钮，菜单回退默认按钮。
"""
import os
import pytest

from telegram import InlineKeyboardButton, KeyboardButton


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr("config.settings.MINIAPP_ENABLED", True)
    monkeypatch.setattr("config.settings.MINIAPP_PUBLIC_URL",
                        "https://telesubmit.test/app/")
    monkeypatch.setenv("TELEPOST_BOT_INDEX", "2")


@pytest.fixture
def disabled(monkeypatch):
    monkeypatch.setattr("config.settings.MINIAPP_ENABLED", False)
    monkeypatch.setattr("config.settings.MINIAPP_PUBLIC_URL",
                        "https://telesubmit.test/app/")


def _all_buttons(markup):
    """Flatten all buttons in a Reply/Inline markup."""
    rows = getattr(markup, "keyboard", None) or getattr(markup, "inline_keyboard")
    return [b for row in rows for b in row]


def test_reply_keyboard_mini_app_is_plain_text_not_web_app(enabled):
    """Reply Keyboard「📱 Mini App」必须是不带 web_app 的普通文本按钮。"""
    from ui.keyboards import Keyboards
    markup = Keyboards.main_menu()
    btn = next(b for b in _all_buttons(markup)
               if isinstance(b, KeyboardButton) and "Mini App" in (b.text or ""))
    assert getattr(btn, "web_app", None) is None, \
        "Reply Keyboard 的 Mini App 按钮绝不能挂 web_app（Simple WebView 不带用户身份）"


def test_reply_keyboard_has_miniapp_entry_when_enabled(enabled):
    from ui.keyboards import Keyboards
    markup = Keyboards.main_menu()
    labels = [b.text for b in _all_buttons(markup) if isinstance(b, KeyboardButton)]
    assert Keyboards.MINI_APP_INVITE_TEXT in labels


def test_reply_keyboard_omits_miniapp_when_disabled(disabled):
    from ui.keyboards import Keyboards
    markup = Keyboards.main_menu()
    for b in _all_buttons(markup):
        assert not (isinstance(b, KeyboardButton) and "Mini App" in (b.text or ""))


def test_miniapp_launch_is_inline_web_app_with_bot_param(enabled):
    """Inline Web App 入口带正确的 ?bot=botN 且是 Inline web_app 按钮。"""
    from ui.keyboards import Keyboards
    markup = Keyboards.miniapp_launch()
    assert markup is not None
    btn = _all_buttons(markup)[0]
    assert isinstance(btn, InlineKeyboardButton)
    assert "打开投稿" in (btn.text or "")
    assert btn.web_app is not None
    assert btn.web_app.url == "https://telesubmit.test/app?bot=bot2"


def test_miniapp_launch_none_when_disabled(disabled):
    from ui.keyboards import Keyboards
    assert Keyboards.miniapp_launch() is None


def test_miniapp_launch_none_without_public_url(monkeypatch):
    monkeypatch.setattr("config.settings.MINIAPP_ENABLED", True)
    monkeypatch.setattr("config.settings.MINIAPP_PUBLIC_URL", "")
    from ui.keyboards import Keyboards
    assert Keyboards.miniapp_launch() is None
    assert Keyboards._miniapp_url() is None


async def test_handle_menu_shortcuts_renders_inline_for_miniapp_text(enabled, monkeypatch):
    """点击键盘的「📱 Mini App」文本由菜单捷径回复 Inline Web App 按钮。"""
    from unittest.mock import AsyncMock, MagicMock
    update_message = MagicMock()
    update_message.reply_text = AsyncMock()
    update_message.text = "📱 Mini App"
    update_message.chat.type = "private"
    update = MagicMock()
    update.message = update_message
    update.channel_post = None
    update.edited_channel_post = None
    update.effective_user.id = 42
    mock_ctx = MagicMock()
    mock_ctx.user_data = {}

    from telegram.ext import ApplicationHandlerStop
    from handlers.command_handlers import handle_menu_shortcuts
    with pytest.raises(ApplicationHandlerStop):
        await handle_menu_shortcuts(update, mock_ctx)
    update_message.reply_text.assert_awaited_once()
    kw = update_message.reply_text.call_args.kwargs
    assert kw.get("reply_markup") is not None
    btn = _all_buttons(kw["reply_markup"])[0]
    assert btn.web_app is not None
    assert btn.web_app.url == "https://telesubmit.test/app?bot=bot2"