"""Reproduce GBK streams independently of host locale and frozen bootloaders."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LEGACY_STREAMS = """
import os, sys, types
from utils import console
console.os = types.SimpleNamespace(name='nt', environ=os.environ)
for stream in (sys.stdin, sys.stdout, sys.stderr):
    stream.reconfigure(encoding='gbk')
"""


def invoke(code, *, cwd, data=None):
    return subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", LEGACY_STREAMS + code],
        cwd=cwd, input=data, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "gbk"},
        timeout=30,
    )


def test_setup_wizard_handles_legacy_streams_and_preserves_bom(tmp_path):
    code = "\nimport run; sys.argv = ['run.py', '--setup']; run.main()\n"
    result = invoke(code, cwd=tmp_path, data="123456:synthetic-config-test\n@test\n1\n")
    assert result.returncode == 0, result.stderr
    assert "✅ 已写入" in result.stdout
    path = tmp_path / "config.ini"
    path.write_text(path.read_text(encoding="utf-8") + "CHANNEL_FOOTER_TEXT = 中文说明\n",
                    encoding="utf-8-sig")
    before = path.read_bytes()
    result = invoke(code, cwd=tmp_path, data="n\n")
    assert result.returncode == 0, result.stderr
    assert "保持现有配置" in result.stdout
    assert path.read_bytes() == before


def test_configuration_check_handles_legacy_streams():
    result = invoke("\nimport check_config; sys.exit(check_config.main())\n", cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert "✅ 所有检查通过" in result.stdout


def test_console_preserves_error_handlers_and_propagates_to_children(tmp_path):
    code = """
import json
sys.stderr.reconfigure(errors='backslashreplace')
console.configure_windows_stdio()
print(json.dumps({'encoding': sys.stderr.encoding, 'errors': sys.stderr.errors,
                  'child_encoding': os.environ['PYTHONIOENCODING']}))
"""
    result = invoke(code, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "encoding": "utf-8", "errors": "backslashreplace", "child_encoding": "utf-8",
    }
