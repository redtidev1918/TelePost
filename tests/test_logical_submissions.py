"""Logical human-owned submissions ("我的投稿") — chain collapse + isolation."""
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from database import db_manager
from telepost.storage.sqlite.reviews import ReviewRepository


async def _db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "mine.db"))
    await db_manager.init_db()


async def _row(*, chain, status="pending", user_id=7, username="alice",
               submitter=None, submitter_name="", generation=0,
               supersedes=None, title="", created=None, updated=None,
               source="api", target_id=""):
    now = created or time.time()
    # updated_at orders the logical list; make it explicit per row.
    upd = updated if updated is not None else now
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            """
            INSERT INTO pending_reviews (
                idempotency_key, source, status, user_id, username,
                review_chat_id, media_json, documents_json, target_id,
                review_chain_id, generation, supersedes_review_id,
                submitter_user_id, submitter_username,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, -1001, '[]', '[]', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (f"k-{chain}-{generation}-{int(now*1000)}", source, status, user_id,
             username, target_id, chain, generation, supersedes,
             submitter, submitter_name, now, upd),
        )
        return cur.lastrowid


@pytest.mark.asyncio
async def test_logical_list_collapses_refetch_generations(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    base = time.time() - 300
    a = await _row(chain="chain-1", created=base, updated=base + 0,
                   submitter=7, submitter_name="alice", generation=0)
    b = await _row(chain="chain-1", created=base + 60, updated=base + 60,
                   submitter=7, submitter_name="alice", generation=1,
                   supersedes=a)
    c = await _row(chain="chain-1", created=base + 120, updated=base + 120,
                   submitter=7, submitter_name="alice", generation=2,
                   supersedes=b)
    # a superseded old generation is no longer the head
    async with db_manager.get_db() as conn:
        await conn.execute(
            "UPDATE pending_reviews SET status='superseded' WHERE id IN (?, ?)",
            (a, b),
        )

    rows = await ReviewRepository().list_logical_submissions(7, limit=10)
    assert len(rows) == 1
    head = rows[0]
    assert head["id"] == c                       # current head only
    assert head["review_chain_id"] == "chain-1"
    assert head["generation_count"] == 3         # refetch_count = 2


@pytest.mark.asyncio
async def test_logical_list_excludes_service_and_other_users(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    base = time.time() - 100
    await _row(chain="chain-mine", created=base, updated=base,
               submitter=7, submitter_name="alice")
    # PixivFlow/service submission created by an API token bound to user 7 but
    # with NO human submitter → must never appear.
    await _row(chain="chain-svc", created=base + 1, updated=base + 1,
               submitter=None, user_id=7, username="pixivflow",
               target_id="bot1-illust-botefuku")
    await _row(chain="chain-other", created=base + 2, updated=base + 2,
               submitter=8, submitter_name="bob", user_id=8)

    mine = await ReviewRepository().list_logical_submissions(7, limit=10)
    assert [r["review_chain_id"] for r in mine] == ["chain-mine"]
    bob = await ReviewRepository().list_logical_submissions(8, limit=10)
    assert [r["review_chain_id"] for r in bob] == ["chain-other"]


@pytest.mark.asyncio
async def test_logical_pagination_is_stable_across_chains(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    base = time.time() - 500
    chains = []
    for index in range(5):
        chain = f"chain-{index}"
        chains.append(chain)
        await _row(chain=chain, created=base + index, updated=base + index,
                   submitter=7, submitter_name="alice")

    repo = ReviewRepository()
    page1 = await repo.list_logical_submissions(7, limit=2)
    assert len(page1) == 2
    cursor = (page1[-1]["updated_at"], page1[-1]["id"])
    page2 = await repo.list_logical_submissions(
        7, limit=2, updated_cursor=cursor[0], id_cursor=cursor[1]
    )
    cursor2 = (page2[-1]["updated_at"], page2[-1]["id"])
    page3 = await repo.list_logical_submissions(
        7, limit=2, updated_cursor=cursor2[0], id_cursor=cursor2[1]
    )
    seen = [r["review_chain_id"] for r in page1 + page2 + page3]
    assert len(seen) == len(set(seen)) == 5      # no duplicates, no gaps
    assert set(seen) == set(chains)


@pytest.mark.asyncio
async def test_head_of_chain_returns_latest_generation(monkeypatch, tmp_path):
    await _db(monkeypatch, tmp_path)
    base = time.time() - 200
    a = await _row(chain="chain-9", created=base, updated=base, submitter=7,
                   submitter_name="alice")
    b = await _row(chain="chain-9", created=base + 5, updated=base + 5,
                   submitter=7, submitter_name="alice", generation=1,
                   supersedes=a)
    head = await ReviewRepository().head_of_chain("chain-9")
    assert head["id"] == b
    assert head["generation_count"] == 2