"""Bot locales preserve content, callbacks, ownership and per-process isolation."""
import ast
from pathlib import Path
from string import Formatter

import pytest

from config import settings
from ui.i18n import _catalog, normalize_language
from ui.keyboards import Keyboards
from ui.messages import MessageFormatter
import run


@pytest.mark.parametrize("raw, expected", [("zh", "zh"), ("en", "en"), ("ZH-CN", "zh"), ("en_US", "en"), ("", "zh")])
def test_supported_language_aliases(raw, expected):
    assert normalize_language(raw) == expected


def test_invalid_language_fails_before_launch():
    with pytest.raises(ValueError, match="BOT_LANGUAGE must be zh or en"):
        run.build_bot_env(1, {"BOT1_LANGUAGE": "de"})


def test_two_bots_select_languages_independently(tmp_path):
    base = {"BOT_LANGUAGE": "en", "BOT1_LANGUAGE": "zh", "DB_PATH": str(tmp_path / "db.sqlite")}
    assert run.build_bot_env(1, base)["BOT_LANGUAGE"] == "zh"
    assert run.build_bot_env(2, base)["BOT_LANGUAGE"] == "en"
    assert base["BOT_LANGUAGE"] == "en"
    assert "BOT_LANGUAGE" not in run.build_bot_env(1, {"DB_PATH": str(tmp_path / "other.sqlite")})


def test_english_prompts_preserve_user_content_and_callbacks(monkeypatch):
    monkeypatch.setattr(settings, "BOT_LANGUAGE", "en")
    welcome = MessageFormatter.welcome_message("中文名字")
    assert "Hello, 中文名字" in welcome
    assert "Send /submit" in welcome
    assert "Finish uploading" in MessageFormatter.help_message()
    assert "At most 7 files" in MessageFormatter.submit_hint("MIXED", 7)
    assert "原始内容" in MessageFormatter.submission_preview("原始内容", ["#中文"])
    english = Keyboards.search_options().to_dict()
    assert english["inline_keyboard"][0][0]["text"] == "📝 Full-text search"
    monkeypatch.setattr(settings, "BOT_LANGUAGE", "zh")
    chinese = Keyboards.search_options().to_dict()
    assert [[b["callback_data"] for b in row] for row in english["inline_keyboard"]] == [
        [b["callback_data"] for b in row] for row in chinese["inline_keyboard"]
    ]


def test_catalog_has_every_marked_message_and_preserves_placeholders():
    root = Path(__file__).resolve().parents[1]
    catalog = _catalog("en")
    def fields(message):
        return sorted((name, spec, conversion) for _, name, spec, conversion in Formatter().parse(message) if name is not None)
    for directory in ("ui", "handlers", "utils", "services", "telepost"):
        for path in (root / directory).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr":
                    if node.args and isinstance(node.args[0], ast.Constant):
                        message = node.args[0].value
                        assert message in catalog._catalog, (path, message)
                        assert fields(message) == fields(catalog.gettext(message)), (path, message)
