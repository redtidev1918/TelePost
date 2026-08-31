import inspect
from types import SimpleNamespace

import pytest

from utils import maintenance_jobs


def test_jobqueue_callbacks_are_coroutines():
    assert inspect.iscoroutinefunction(maintenance_jobs.clean_logs_job)
    assert inspect.iscoroutinefunction(maintenance_jobs.pixivflow_maintain_job)


def test_scheduled_time_uses_deployment_timezone(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")

    scheduled = maintenance_jobs.scheduled_time(3, 15)

    assert (scheduled.hour, scheduled.minute) == (3, 15)
    assert scheduled.tzinfo.key == "Asia/Shanghai"


def test_scheduled_time_falls_back_to_utc(monkeypatch):
    monkeypatch.setenv("TZ", "not-a-timezone")

    scheduled = maintenance_jobs.scheduled_time(4)

    assert scheduled.tzinfo.key == "UTC"


@pytest.mark.asyncio
async def test_clean_logs_job_uses_worker_thread(monkeypatch):
    calls = []

    def cleanup(path):
        calls.append(path)

    monkeypatch.setattr(maintenance_jobs, "cleanup_old_logs", cleanup)

    await maintenance_jobs.clean_logs_job(None)

    assert calls == ["logs"]


@pytest.mark.asyncio
async def test_pixivflow_maintain_job_uses_configured_path(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="ok")

    monkeypatch.setenv("PIXIVFLOW_CONFIG", "/data/pixivflow.json")
    monkeypatch.setattr(maintenance_jobs.subprocess, "run", run)

    await maintenance_jobs.pixivflow_maintain_job(None)

    assert calls == [
        (
            ["pixivflow", "maintain", "--config", "/data/pixivflow.json"],
            {
                "cwd": "/app",
                "timeout": 900,
                "capture_output": True,
                "text": True,
            },
        )
    ]
