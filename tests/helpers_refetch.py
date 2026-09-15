"""Shared fixtures/helpers for refetch + review state-machine tests."""
import json
import time
from unittest.mock import AsyncMock, MagicMock

from database import db_manager
from handlers import review
from telepost.storage.sqlite.refetch import RefetchRepository


def callback_update(data, user_id=123456789, callback_id=7001):
    update = MagicMock()
    update.effective_user.id = user_id
    update.callback_query.data = data
    update.callback_query.id = callback_id
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def photo_message(message_id=10, file_id="STAGED_PHOTO"):
    message = MagicMock()
    message.message_id = message_id
    message.photo = [MagicMock(file_id=file_id)]
    message.video = None
    message.animation = None
    message.audio = None
    message.document = None
    return message


async def insert_review(*, status="pending", pixiv_id="111", target_id="target-a",
                        chain="", generation=0, source="api",
                        user_id=7, username="pf",
                        submitter_user_id=None, submitter_username="",
                        anonymous=False, spoiler=False,
                        media_json="[]", documents_json="[]",
                        title="标题", tags="#t", note="", link="",
                        control_message_id=None, review_message_ids="[]"):
    """Insert a review row in the production shape (chain bootstrap included)."""
    now = time.time()
    async with db_manager.get_db() as conn:
        cur = await conn.execute(
            """
            INSERT INTO pending_reviews (
                idempotency_key, source, status, user_id, username,
                review_chat_id, media_json, documents_json, title, tags, note, link,
                anonymous, spoiler, target_id, pixiv_id, work_type,
                review_chain_id, generation,
                submitter_user_id, submitter_username,
                review_message_ids, control_message_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'illustration',
                      ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("k%d-%s" % (int(now * 1000), pixiv_id), source, status, user_id, username,
             str(review.REVIEW_CHAT_ID), media_json, documents_json, title, tags, note,
             link, 1 if anonymous else 0, 1 if spoiler else 0, target_id, pixiv_id,
             chain or "", generation, submitter_user_id, submitter_username,
             str(review_message_ids), control_message_id, now, now),
        )
        review_id = cur.lastrowid
    if pixiv_id:
        chain_id = chain or "chain-%d" % review_id
        async with db_manager.get_db() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO refetch_seen_candidates "
                "(review_chain_id, candidate_id, source, generation, created_at) "
                "VALUES (?, ?, 'original', ?, ?)",
                (chain_id, pixiv_id, generation, now),
            )
    return review_id


async def attempt(*, chain_id, source_review_id, generation=1, callback_id=7001,
                  request_id=None, source_candidate_id="111"):
    return await RefetchRepository().create_attempt(
        callback_key=f"cb:{source_review_id}:{callback_id}",
        review_chain_id=chain_id, generation=generation,
        source_review_id=source_review_id,
        source_candidate_id=source_candidate_id,
        request_id=request_id,
    )
