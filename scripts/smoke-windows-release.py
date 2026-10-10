"""Verify frozen Windows setup and first startup without contacting Telegram."""
import os
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile


def main() -> None:
    source = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="telepost-windows-release-") as directory:
        exe = Path(directory) / source.name
        shutil.copy2(source, exe)
        env = {**os.environ, "PYTHONIOENCODING": "gbk", "PYTHONUTF8": "0"}

        def setup(data: bytes) -> str:
            result = subprocess.run(
                [str(exe), "--setup"], input=data, capture_output=True,
                env=env, timeout=45,
            )
            if result.returncode:
                raise RuntimeError(
                    f"frozen setup exited {result.returncode}: "
                    + result.stderr.decode("utf-8", errors="replace")
                )
            return result.stdout.decode("utf-8")

        output = setup(b"123456:synthetic-config-test\n@synthetic_channel\n1\n")
        assert "✅ 已写入" in output
        config = exe.parent / "config.ini"
        config.write_text(
            config.read_text(encoding="utf-8") + "CHANNEL_FOOTER_TEXT = 投稿频道：中文说明\n",
            encoding="utf-8-sig",
        )
        before = config.read_bytes()
        assert "保持现有配置" in setup(b"n\n")
        assert config.read_bytes() == before
        env.update({
            "TOKEN": "synthetic-invalid-token", "CHANNEL_ID": "@synthetic_channel",
            "OWNER_ID": "1", "MINIAPP_ENABLED": "false", "SEARCH_ENABLED": "false",
            "API_REVIEW_REQUIRED": "false",
            "CHAT_REVIEW_REQUIRED": "false", "MINIAPP_REVIEW_REQUIRED": "false",
        })
        result = subprocess.run(
            [str(exe), "--frozen-worker"], capture_output=True, env=env, timeout=45,
        )
        output = result.stdout.decode("utf-8") + result.stderr.decode("utf-8")
        # An invalid token stops locally before Telegram requests, after storage setup.
        assert result.returncode != 0 and "InvalidToken" in output, output
        database = exe.parent / "data" / "submissions.db"
        assert database.is_file()
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'pending_reviews'"
            ).fetchone()
    print("Windows frozen setup, UTF-8 BOM and first-start storage smoke passed")


if __name__ == "__main__":
    main()
