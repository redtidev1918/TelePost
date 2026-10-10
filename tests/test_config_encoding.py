"""Config written on Windows must survive locale defaults and a UTF-8 BOM."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import check_config
from utils import config_wizard


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_config_readers_accept_chinese_and_optional_bom(tmp_path, monkeypatch, encoding):
    footer = "投稿频道：中文说明"
    config_wizard.write_config(
        str(tmp_path / "config.ini"), token="test:config-token", channel="@test", owner="1",
    )
    path = tmp_path / "config.ini"
    path.write_text(
        path.read_text(encoding="utf-8") + f"CHANNEL_FOOTER_TEXT = {footer}\n",
        encoding=encoding,
    )
    monkeypatch.chdir(tmp_path)
    assert config_wizard._config_says_ready()
    assert check_config.check_config_file()

    # Import settings in isolation; do not reload the live module used by other tests.
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONIOENCODING": "utf-8"}
    env.pop("CHANNEL_FOOTER_TEXT", None)
    result = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c",
         "import json; from config.settings import CHANNEL_FOOTER_TEXT; "
         "print(json.dumps(CHANNEL_FOOTER_TEXT, ensure_ascii=False))"],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == footer


@pytest.mark.parametrize("version, supported", [((3, 9), False), ((3, 10), True)])
def test_config_check_matches_supported_python_floor(monkeypatch, version, supported):
    monkeypatch.setattr(check_config.sys, "version_info", version)
    assert check_config.check_python_version() is supported
