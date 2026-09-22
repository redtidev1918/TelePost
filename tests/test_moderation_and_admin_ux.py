"""Admin control-plane tests (§moderation, §admin-ux).

Covers the three operator-visible fixes delivered together:
1. Moderation block repository + canonical subject mapping.
2. Manager review notification now carries clickable review / original links.
3. Recovery failure surfaces structured code/stage/retryable/hint.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from database import db_manager
from telepost.application import recovery as recovery_mod
from telepost.storage.sqlite.moderation import (
    ModerationRepository, api_subject, canonical_actor_subject, user_subject,
)


def _test_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "db.sqlite"))
    return db_manager


async def _init(monkeypatch, tmp_path):
    db = _test_db(monkeypatch, tmp_path)
    await db.init_db()
    return db


@pytest.fixture
async def notification_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "notify.db"))
    import config.settings as settings
    monkeypatch.setattr(settings, "NOTIFY_OWNER", True)
    monkeypatch.setattr(settings, "OWNER_ID", 900)
    await db_manager.init_db()


@pytest.mark.asyncio
async def test_moderation_subject_mapping():
    assert canonical_actor_subject("telegram:42") == "user:42"
    assert canonical_actor_subject("telegram_user:7") == "user:7"
    assert canonical_actor_subject("api_token:9") == "api:9"
    assert canonical_actor_subject("api:9") == "api:9"
    assert canonical_actor_subject("user:3") == "user:3"
    assert user_subject("12") == "user:12"
    assert api_subject("8") == "api:8"


@pytest.mark.asyncio
async def test_moderation_repository_block_and_check(monkeypatch, tmp_path):
    await _init(monkeypatch, tmp_path)
    repo = ModerationRepository()
    assert await repo.is_blocked(user_subject(42)) is False
    await repo.add_block(user_subject(42), reason="spam", created_by=1)
    assert await repo.is_blocked(user_subject(42)) is True
    assert await repo.is_blocked("telegram:42") is True  # canonicalized
    # API token subject is independent of user subject.
    assert await repo.is_blocked(api_subject(9)) is False
    await repo.add_block(api_subject(9), created_by=1)
    assert await repo.is_blocked("api_token:9") is True
    row = await repo.find(api_subject(9))
    assert row and row["reason"] == ""


@pytest.mark.asyncio
async def test_manager_review_notification_includes_links():
    from telepost.application.submitter_notify import format_manager_acceptance

    chat = "-100" + "0000000001"
    text, entity = format_manager_acceptance({
        "logical_submission_id": "review:114",
        "review_id": 114,
        "submitter_user_id": 42,
        "submitter_username": "alice",
        "anonymous": False,
        "source": "chat",
        "review_chat_id": chat,
        "control_message_id": 999,
        "link": "https://t.me/pixiv2019/123",
    })
    assert "审核稿：#114" in text
    assert "来源：Telegram 私聊" in text
    assert "🔗 审核：https://t.me/c/0000000001/999" in text
    assert "📂 原投稿：https://t.me/pixiv2019/123" in text
    assert entity is not None
    assert entity["url"] == "tg://user?id=42"


@pytest.mark.asyncio
async def test_recovery_failure_surfaces_structured_error(monkeypatch, tmp_path):
    monkeypatch.setenv("PIXIVFLOW_REFETCH_BASE_URL", "http://pixivflow.test:8090")
    monkeypatch.setenv("PIXIVFLOW_REFETCH_TOKEN", "recover-secret")
    await _init(monkeypatch, tmp_path)

    from telepost.application.recovery import RecoveryError, request_target_recovery

    def boom(request, timeout=None):
        raise RuntimeError("PixivFlow 拒绝恢复（HTTP 500）")

    monkeypatch.setattr(recovery_mod, "urlopen", boom)
    with pytest.raises(RecoveryError) as excinfo:
        await request_target_recovery("bot1-illust-x", retry_mode="relaxed")
    exc = excinfo.value
    assert exc.code == "remote_error"
    assert exc.stage == "recovery_request"
    assert exc.retryable is True
    assert "PixivFlow" in exc.hint


@pytest.mark.asyncio
async def test_review_keyboard_shows_moderation_buttons_for_expected_subjects(monkeypatch,
                                                                              tmp_path):
    await _init(monkeypatch, tmp_path)
    from telepost.telegram.review_keyboard import review_keyboard

    kb = review_keyboard(
        55, "https://www.pixiv.net/artworks/1", source="api",
        pixiv_id="1", submitter_user_id=42, actor_kind="service",
    )
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert "🚫 封禁投稿人" in texts
    assert "🔑 禁用API" in texts

    kb_user = review_keyboard(
        56, "", source="chat", submitter_user_id=7, actor_kind="user",
    )
    texts = [b.text for row in kb_user.inline_keyboard for b in row]
    assert "🚫 封禁投稿人" in texts
    assert "🔑 禁用API" not in texts


@pytest.mark.asyncio
async def test_manager_notification_for_api_service_actor(notification_db):
    from telepost.application.submitter_notify import (
        ManagerAcceptanceContext, ManagerNotifyService, format_manager_acceptance,
    )
    service = ManagerNotifyService()
    assert await service.notify_accepted(ManagerAcceptanceContext(
        logical_submission_id="review:api-1",
        submitter_user_id=0,  # service submission: no human
        review_id=77,
        source="api",
        status="pending_review",
        actor_kind="service",
        actor_subject="api:9",
        review_chat_id="-" + "100" + "0001",
        control_message_id=88,
    )) is True
    text, entity = format_manager_acceptance({
        "source": "api", "status": "pending_review", "review_id": 77,
        "submitter_user_id": 0, "actor_subject": "api:9",
        "review_chat_id": "-100" + "0001", "control_message_id": 88,
    })
    assert "API token：#9" in text
    assert "状态：待审核" in text
    assert "🔗 审核：https://t.me/c/0001/88" in text
    assert entity is None


@pytest.mark.asyncio
async def test_admin_block_callback_writes_user_block(monkeypatch, tmp_path):
    await _init(monkeypatch, tmp_path)
    import config.settings as settings
    monkeypatch.setattr(settings, "ADMIN_IDS", {900})
    from handlers.moderation import admin_block
    query = SimpleNamespace(
        data="admin_block:user:42",
        answer=AsyncMock(),
        message=SimpleNamespace(text="📝 投稿通知"),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(effective_user=SimpleNamespace(id=900),
                             callback_query=query)
    await admin_block(update, MagicMock())
    assert await ModerationRepository().is_blocked("user:42") is True
    assert query.answer.await_args.args[0].startswith("已封禁用户")


@pytest.mark.asyncio
async def test_admin_block_callback_disables_api(monkeypatch, tmp_path):
    await _init(monkeypatch, tmp_path)
    import config.settings as settings
    monkeypatch.setattr(settings, "ADMIN_IDS", {900})
    from handlers.moderation import admin_block
    query = SimpleNamespace(
        data="admin_block:api:5",
        answer=AsyncMock(),
        message=SimpleNamespace(text="📝 投稿通知"),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(effective_user=SimpleNamespace(id=900),
                             callback_query=query)
    await admin_block(update, MagicMock())
    assert await ModerationRepository().is_blocked("api:5") is True
    assert query.answer.await_args.args[0].startswith("已封禁API")


@pytest.mark.asyncio
async def test_ban_user_command_writes_both_stores(monkeypatch, tmp_path):
    await _init(monkeypatch, tmp_path)
    import config.settings as settings
    monkeypatch.setattr(settings, "ADMIN_IDS", {900})
    from utils.blacklist import init_blacklist, is_blacklisted
    await init_blacklist()
    from handlers.moderation import ban_user_command

    reply = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=900),
        message=SimpleNamespace(reply_text=reply),
    )
    context = SimpleNamespace(args=["77", "违规投稿"])
    await ban_user_command(update, context)

    assert await ModerationRepository().is_blocked("user:77") is True
    assert is_blacklisted(77) is True
    assert "已封禁用户 77" in reply.await_args.args[0]


@pytest.mark.asyncio
async def test_ban_api_command_writes_moderation_store(monkeypatch, tmp_path):
    await _init(monkeypatch, tmp_path)
    import config.settings as settings
    monkeypatch.setattr(settings, "ADMIN_IDS", {900})
    from handlers.moderation import ban_api_command

    reply = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=900),
        message=SimpleNamespace(reply_text=reply),
    )
    context = SimpleNamespace(args=["#9", "异常投稿"])
    await ban_api_command(update, context)

    assert await ModerationRepository().is_blocked("api:9") is True
    assert "已禁用 API token #9" in reply.await_args.args[0]


@pytest.mark.asyncio
async def test_ban_commands_deny_non_admin(monkeypatch, tmp_path):
    await _init(monkeypatch, tmp_path)
    import config.settings as settings
    monkeypatch.setattr(settings, "ADMIN_IDS", {900})
    from handlers.moderation import ban_api_command, ban_user_command

    reply = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=901),
        message=SimpleNamespace(reply_text=reply),
    )
    await ban_user_command(update, SimpleNamespace(args=["77"]))
    await ban_api_command(update, SimpleNamespace(args=["9"]))

    assert await ModerationRepository().is_blocked("user:77") is False
    assert await ModerationRepository().is_blocked("api:9") is False
    assert "仅限管理员" in reply.await_args.args[0]
