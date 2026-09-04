"""冻结版（PyInstaller）支持：launcher 子进程命令与首次运行向导。"""
import configparser
import sys

import run  # launcher 模块顶层只 import 标准库与轻量工具，导入安全
from utils import config_wizard as wizard


def test_bot_child_command_source_mode(monkeypatch):
    monkeypatch.setattr(run, "_FROZEN", False)
    assert run.bot_child_command() == [sys.executable, "-u", "main.py"]


def test_bot_child_command_frozen_mode(monkeypatch):
    monkeypatch.setattr(run, "_FROZEN", True)
    assert run.bot_child_command() == [sys.executable, "--frozen-worker"]


def test_write_config_roundtrip(tmp_path):
    path = tmp_path / "config.ini"
    wizard.write_config(str(path), token="123456:AAbb", channel="@ch", owner="42")
    parser = configparser.ConfigParser()
    parser.read(path)
    assert parser.get("BOT", "TOKEN") == "123456:AAbb"
    assert parser.get("BOT", "CHANNEL_ID") == "@ch"
    assert parser.get("BOT", "OWNER_ID") == "42"


def test_config_ready_uses_env_or_ini(monkeypatch):
    monkeypatch.delenv("TOKEN", raising=False)
    monkeypatch.delenv("BOT1_TOKEN", raising=False)
    monkeypatch.setattr(wizard, "app_root", lambda: "/nonexistent-dir")
    assert not wizard.config_ready()

    monkeypatch.setenv("TOKEN", "123:abc")
    assert wizard.config_ready()

    monkeypatch.delenv("TOKEN")
    monkeypatch.setattr(wizard, "app_root", lambda: "/nonexistent-dir")
    assert not wizard.config_ready()
