"""A new installation must initialize storage before any Telegram connection."""
import sqlite3
from contextlib import closing

import pytest

from database import db_manager


@pytest.mark.asyncio
async def test_init_db_creates_missing_parent_and_preserves_existing_data(tmp_path, monkeypatch):
    path = tmp_path / "新安装" / "data" / "submissions.db"
    monkeypatch.setattr(db_manager, "DB_PATH", str(path))
    await db_manager.init_db()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("INSERT INTO submissions (user_id, title) VALUES (1, '保留原稿')")
        connection.commit()
    await db_manager.init_db()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT title FROM submissions WHERE user_id = 1").fetchone() == ("保留原稿",)
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'pending_reviews'").fetchone()
