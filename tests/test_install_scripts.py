"""Installer / compose / example-config contracts (m00694 items 5, 6, 7).

The installers' Python gate is tested by executing the *exact* predicate the
scripts use, with ``sys.version_info`` stubbed to 3.9 and to the real
interpreter version. Bash/cmd are not required, so this runs everywhere.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = ROOT / "install.sh"
INSTALL_BAT = ROOT / "install.bat"
COMPOSE = ROOT / "docker-compose.yml"
DOCKERFILE = ROOT / "Dockerfile"
EXAMPLE = Path(ROOT / "config.ini.example")
INSTALL_DOC = ROOT / "docs" / "INSTALL.md"
INSTALL_DOC_EN = ROOT / "docs" / "en" / "INSTALL.md"
MINIAPP_DOC = ROOT / "docs" / "MINIAPP.md"


def _shell_predicate(pattern, text):
    match = re.search(pattern, text)
    assert match, "version predicate not found"
    return match.group(1)


def _code_lines(text):
    """Non-comment lines only (the exemptions are documented in comments)."""
    keep = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("REM ") or stripped.startswith("::"):
            continue
        keep.append(line)
    return "\n".join(keep)


def _run(predicate, version=None):
    prelude = ""
    if version is not None:
        prelude = f"import sys; sys.version_info = {version}; "
    return subprocess.run(
        [sys.executable, "-c", prelude + predicate], capture_output=True, text=True
    )


class TestPythonFloor:
    def test_shell_predicate_accepts_310_and_rejects_39(self):
        predicate = _shell_predicate(
            r"python3 -c '([^']*exit[^']*version_info[^']*)'",
            INSTALL_SH.read_text(encoding="utf-8"),
        )
        assert _run(predicate).returncode == 0
        for version in ("(3, 10, 0)", "(3, 11, 0)", "(3, 14, 0)"):
            assert _run(predicate, version=version).returncode == 0
        assert _run(predicate, version="(3, 9, 0)").returncode != 0

    def test_batch_predicate_accepts_310_and_rejects_39(self):
        predicate = _shell_predicate(
            r'python -c "([^"]*version_info[^"]*)"', INSTALL_BAT.read_text(encoding="utf-8")
        )
        assert _run(predicate).returncode == 0
        for version in ("(3, 10, 0)", "(3, 11, 0)", "(3, 14, 0)"):
            assert _run(predicate, version=version).returncode == 0
        assert _run(predicate, version="(3, 9, 0)").returncode != 0

    def test_scripts_do_not_advertise_39(self):
        for path in (INSTALL_SH, INSTALL_BAT):
            code = _code_lines(path.read_text(encoding="utf-8"))
            assert "3.9" not in code, f"{path.name} still advertises Python 3.9"
            assert "3.10" in code

    def test_shell_uses_interpreter_comparison_not_version_sort(self):
        code = _code_lines(INSTALL_SH.read_text(encoding="utf-8"))
        assert "sort -V" not in code
        assert "sys.version_info[:2] >= (3, 10)" in code

    def test_dependency_and_config_failures_are_fatal(self):
        sh = INSTALL_SH.read_text(encoding="utf-8")
        assert "if ! pip3 install -r requirements.txt -q; then" in sh
        bat = INSTALL_BAT.read_text(encoding="utf-8")
        # Dependency install and config verification both abort with a non-zero code.
        assert re.search(r"pip install -q -r requirements\.txt \|\|", bat)
        assert re.search(r"check_config\.py \|\|", bat)


class TestLibvipsIsOptional:
    def test_installers_probe_instead_of_requiring(self):
        sh = INSTALL_SH.read_text(encoding="utf-8")
        assert "import pyvips" in sh
        assert re.search(r"log_warning .*libvips", sh)
        bat = INSTALL_BAT.read_text(encoding="utf-8")
        assert "import pyvips" in bat
        assert "[!]" in bat

    def test_install_docs_mark_it_optional(self):
        text = INSTALL_DOC.read_text(encoding="utf-8")
        assert "可选" in text.split("### libvips")[1][:400]
        assert "optional" in INSTALL_DOC_EN.read_text(encoding="utf-8").split("### libvips")[1][:400]


class TestComposeHealthcheck:
    def test_no_host_port_is_published_by_default(self):
        text = COMPOSE.read_text(encoding="utf-8")
        assert not re.search(r"^\s*ports\s*:", text, re.MULTILINE), (
            "compose must not publish the submission API on all interfaces"
        )

    def test_healthcheck_is_in_container_and_uses_python(self):
        text = COMPOSE.read_text(encoding="utf-8")
        assert "healthcheck:" in text
        assert "urllib.request.urlopen('http://127.0.0.1:8080/health'" in text
        assert "curl" not in text.split("healthcheck:")[1].split("deploy:")[0]

    def test_image_has_an_equivalent_healthcheck_and_exposes_port(self):
        text = DOCKERFILE.read_text(encoding="utf-8")
        assert "HEALTHCHECK" in text and "urlopen('http://localhost:8080/health')" in text
        assert re.search(r"^EXPOSE 8080$", text, re.MULTILINE)

    def test_docs_do_not_promise_a_host_curl_for_compose(self):
        for path in (INSTALL_DOC, INSTALL_DOC_EN):
            text = path.read_text(encoding="utf-8")
            # The container-internal recipe and the loopback-bound override exist.
            assert "docker compose exec" in text
            assert "docker-compose.override.yml" in text
            assert "127.0.0.1:8080:8080" in text


class TestConfigExample:
    def test_miniapp_review_required_is_documented(self):
        text = EXAMPLE.read_text(encoding="utf-8")
        assert "MINIAPP_REVIEW_REQUIRED" in text
        assert "MINIAPP_ENABLED" in text

    def test_session_secret_is_marked_env_only(self):
        text = EXAMPLE.read_text(encoding="utf-8")
        assert "MINIAPP_SESSION_SECRET" in text
        assert re.search(r"MINIAPP_SESSION_SECRET.{0,200}(环境变量|env)", text, re.S)

    def test_no_placeholder_secret_is_shipped(self):
        text = EXAMPLE.read_text(encoding="utf-8")
        assert "MINIAPP_SESSION_SECRET =" not in text
        assert "TELEGRAPH_ACCESS_TOKEN = " not in text

    def test_legacy_switch_is_documented_as_compatibility_only(self):
        text = EXAMPLE.read_text(encoding="utf-8")
        assert "API_REVIEW_REQUIRED = true" in text
        assert "API 绕过审核" in text or "绕过审核" in text

    def test_csp_policy_documented_matches_the_implementation(self):
        doc = MINIAPP_DOC.read_text(encoding="utf-8")
        from utils.security_headers import MINIAPP_CSP

        for directive in ("blob:", "media-src", "frame-ancestors"):
            assert directive in doc
        assert "script-src 'self'" in doc
        assert MINIAPP_CSP.count("img-src") == 1
        # The doc must not still claim a CSP that would break local previews.
        assert "img-src 'self' data:;" not in doc
