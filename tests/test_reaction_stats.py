"""Reaction-based heat statistics (message_reaction_count)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from database import db_manager
from handlers import reaction_stats


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


class TestReactionStats:
    @pytest.mark.asyncio
    async def test_single_post_reaction_persists(self, isolated_db, monkeypatch):
        await _insert_post(isolated_db, message_id=100)
        monkeypatch.setattr(reaction_stats, "_refresh_search_indexes", AsyncMock())
        update = SimpleNamespace(
            message_reaction_count=SimpleNamespace(
                message_id=100,
                reactions=[SimpleNamespace(total_count=5)],
            )
        )
        updated = await reaction_stats.handle_message_reaction_count(update)
        assert updated == 1
        row = await _read_post(100)
        assert row["reactions"] == 5
        assert row["heat_score"] > 0
        assert await _msg_count(100) == 5

    @pytest.mark.asyncio
    async def test_album_related_reactions_aggregate(self, isolated_db, monkeypatch):
        import time
        await _insert_post(isolated_db, message_id=200,
                           related=[201], publish_time=time.time())
        monkeypatch.setattr(reaction_stats, "_refresh_search_indexes", AsyncMock())

        # Reaction on an album member refreshes the owning post.
        update = SimpleNamespace(
            message_reaction_count=SimpleNamespace(
                message_id=201,
                reactions=[SimpleNamespace(total_count=3)],
            )
        )
        updated = await reaction_stats.handle_message_reaction_count(update)
        assert updated == 1
        row = await _read_post(200)
        assert row["reactions"] == 3

        # Main message reaction counts too.
        update.message_reaction_count.message_id = 200
        update.message_reaction_count.reactions = [SimpleNamespace(total_count=2)]
        updated = await reaction_stats.handle_message_reaction_count(update)
        assert updated == 1
        row = await _read_post(200)
        assert row["reactions"] == 5

    @pytest.mark.asyncio
    async def test_no_update_ignored(self, isolated_db, monkeypatch):
        update = SimpleNamespace(message_reaction_count=None)
        assert await reaction_stats.handle_message_reaction_count(update) == 0
