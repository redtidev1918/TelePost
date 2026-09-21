"""Reaction history backfill projection."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from database import db_manager
from telepost.application.reaction_backfill import normalize_counts, project_counts


@pytest.fixture
async def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "stats.db"))
    await db_manager.init_db()
    return tmp_path


async def _insert_post(conn, *, message_id, related=None, publish_time=None):
    import time
    from database.db_manager import get_db
    async with get_db() as c:
        cur = await c.cursor()
        await cur.execute(
            """
            INSERT INTO published_posts
              (message_id, user_id, username, title, tags, link, note,
               content_type, file_ids, caption, filename, publish_time,
               last_update, related_message_ids)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (message_id, 7, "tester", "标题", "", "", "", "image",
             "[]", "标题", "", float(publish_time or time.time()),
             time.time(), repr(related or [])),
        )


async def _read_post(message_id):
    from database.db_manager import get_db
    async with get_db() as conn:
        cur = await conn.cursor()
        await cur.execute(
            "SELECT reactions, heat_score FROM published_posts WHERE message_id=?",
            (message_id,),
        )
        return await cur.fetchone()


async def _msg_count(message_id):
    from database.db_manager import get_db
    async with get_db() as conn:
        cur = await conn.cursor()
        await cur.execute(
            "SELECT total_count FROM message_reaction_counts WHERE message_id=?",
            (message_id,),
        )
        row = await cur.fetchone()
        return int(row["total_count"]) if row else None


def test_normalize_counts_drops_bad_rows():
    assert normalize_counts([(100, "7"), (101, 3), ("x", 2), (0, 1), (102, "bad")]) == {
        100: 7,
        101: 3,
    }


@pytest.mark.asyncio
async def test_dry_run_does_not_write(isolated_db, monkeypatch):
    await _insert_post(None, message_id=100)
    monkeypatch.setattr("handlers.reaction_stats._refresh_search_indexes", AsyncMock())

    result = await project_counts({100: 9}, dry_run=True)
    assert result == {"dry_run": True, "messages": 1, "posts": 1}
    assert await _msg_count(100) is None
    row = await _read_post(100)
    assert row["reactions"] == 0


@pytest.mark.asyncio
async def test_apply_upserts_and_recomputes_post(isolated_db, monkeypatch):
    await _insert_post(None, message_id=100)
    monkeypatch.setattr("handlers.reaction_stats._refresh_search_indexes", AsyncMock())

    result = await project_counts({100: 6})
    assert result["dry_run"] is False
    assert await _msg_count(100) == 6
    row = await _read_post(100)
    assert row["reactions"] == 6
    assert row["heat_score"] > 0


@pytest.mark.asyncio
async def test_apply_aggregates_album_members(isolated_db, monkeypatch):
    import time
    await _insert_post(None, message_id=100, related=[101, 102],
                       publish_time=time.time())
    monkeypatch.setattr("handlers.reaction_stats._refresh_search_indexes", AsyncMock())

    await project_counts({100: 3, 101: 2, 102: 5})
    row = await _read_post(100)
    assert row["reactions"] == 10
    assert await _msg_count(101) == 2
