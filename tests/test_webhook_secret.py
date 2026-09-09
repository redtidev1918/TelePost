"""Stable webhook secret must survive restart so Telegram-delivered updates in
the stop/restart window still validate (a random per-boot secret rejected them)."""
import os
import stat

import main


def test_webhook_secret_is_persisted_and_stable(tmp_path, monkeypatch):
    db_path = tmp_path / "submissions.db"
    monkeypatch.setattr(main, "DB_PATH", str(db_path))

    first = main._load_or_create_persisted_webhook_secret()
    assert first and len(first) >= 20

    secret_file = tmp_path / "webhook.secret"
    assert secret_file.read_text().strip() == first
    mode = stat.S_IMODE(os.stat(secret_file).st_mode)
    assert mode == 0o600

    # A fresh process reading the same location gets the SAME secret.
    second = main._load_or_create_persisted_webhook_secret()
    assert second == first
