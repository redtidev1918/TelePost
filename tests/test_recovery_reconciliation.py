"""Remote execution truth must survive lost responses and local age limits."""
import time

import pytest

from database import db_manager
from telepost.application import recovery
from telepost.storage.sqlite.recovery import RecoveryRepository


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", ["requested", "accepted"])
async def test_old_attempt_keeps_target_locked_when_remote_is_running(monkeypatch, tmp_path, initial):
    monkeypatch.setattr(db_manager, "DB_PATH", str(tmp_path / "recovery.db"))
    monkeypatch.setenv("PIXIVFLOW_REFETCH_BASE_URL", "http://example.test")
    monkeypatch.setenv("PIXIVFLOW_REFETCH_TOKEN", "test")
    await db_manager.init_db()
    attempt = await recovery.admit_recovery("safe-test", callback_key="click")
    async with db_manager.get_db() as conn:
        await conn.execute("UPDATE recovery_attempts SET created_at=?, state=?", (time.time() - 7200, initial))
    monkeypatch.setattr(recovery, "_get_pixivflow_recovery", lambda *args: {
        "requestId": attempt["request_id"], "slotId": "test-slot",
        "state": "delivery_pending", "business_state": "recovery_running",
    }, raising=False)
    await recovery.resubmit_pending_recoveries()
    async with db_manager.get_db() as conn:
        row = await RecoveryRepository(conn).find_active_by_target("safe-test")
    assert row is not None, "local age must not release a live remote execution"
    assert row["state"] == "accepted"
    with pytest.raises(recovery.RecoveryAlreadyRunningError):
        await recovery.admit_recovery("safe-test", callback_key="second-click")


@pytest.mark.asyncio
@pytest.mark.parametrize('remote,terminal', [('submitted', 'succeeded'), ('failed', 'failed'), ('no_candidate', 'no_candidate')])
async def test_remote_terminal_is_durable_and_late_result_cannot_overwrite(monkeypatch, tmp_path, remote, terminal):
    monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'result.db'))
    monkeypatch.setenv('PIXIVFLOW_REFETCH_BASE_URL', 'http://example.test')
    monkeypatch.setenv('PIXIVFLOW_REFETCH_TOKEN', 'test')
    await db_manager.init_db()
    attempt = await recovery.admit_recovery('safe-test', callback_key='click')
    result = {'requestId': attempt['request_id'], 'slotId': 's', 'state': remote}
    await recovery.reconcile_recovery_result('wrong-target', attempt['request_id'], result)
    async with db_manager.get_db() as conn:
        assert await RecoveryRepository(conn).find_active_by_target('safe-test')
    await recovery.reconcile_recovery_result('safe-test', attempt['request_id'], result)
    await recovery.reconcile_recovery_result('safe-test', attempt['request_id'], {**result, 'state': 'pending'})
    async with db_manager.get_db() as conn:
        row = await RecoveryRepository(conn).find_by_callback_key('click')
    assert row['state'] == terminal
    assert row['finished_at'] is not None


@pytest.mark.asyncio
async def test_lost_response_retains_identity_even_for_synchronous_caller(monkeypatch, tmp_path):
    monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'lost.db'))
    monkeypatch.setenv('PIXIVFLOW_REFETCH_BASE_URL', 'http://example.test')
    monkeypatch.setenv('PIXIVFLOW_REFETCH_TOKEN', 'test')
    await db_manager.init_db()
    def lost(*args):
        raise TimeoutError('response lost')
    monkeypatch.setattr(recovery, '_submit_pixivflow_recover', lost)
    with pytest.raises(recovery.RecoveryError):
        await recovery.request_target_recovery('safe-test', callback_key='click')
    async with db_manager.get_db() as conn:
        result = await RecoveryRepository(conn).find_by_callback_key('click')
    assert result['state'] == 'requested'
    with pytest.raises(recovery.RecoveryAlreadyRunningError):
        await recovery.admit_recovery('safe-test', callback_key='second')
