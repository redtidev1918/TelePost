"""
run_multi 监督循环回归测试（覆盖曾漏测的执行路径）
"""
import os
import signal

import run


class FakeProc:
    instances = []

    def __init__(self, cmd, env=None):
        self.cmd = cmd
        self.env = env
        self.pid = 100 + len(FakeProc.instances)
        self.returncode = None
        self.terminated = False
        FakeProc.instances.append(self)

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return 0


def test_run_multi_spawns_both_bots_and_stops(monkeypatch):
    FakeProc.instances = []
    monkeypatch.setattr(run.subprocess, "Popen", FakeProc)
    monkeypatch.setenv("BOT1_TOKEN", "t1")
    monkeypatch.setenv("BOT1_CHANNEL_ID", "@c1")
    monkeypatch.setenv("BOT2_TOKEN", "t2")
    monkeypatch.setenv("BOT2_CHANNEL_ID", "@c2")

    ticks = {"n": 0}

    def fake_sleep(seconds):
        ticks["n"] += 1
        if ticks["n"] >= 2:  # 第二轮循环时发 SIGTERM 触发优雅停止
            os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(run.time, "sleep", fake_sleep)

    run.run_multi([1, 2])

    assert len(FakeProc.instances) == 2
    tokens = sorted(p.env["TOKEN"] for p in FakeProc.instances)
    assert tokens == ["t1", "t2"]
    # 数据目录按 bot 隔离
    assert sorted(p.env["DB_PATH"] for p in FakeProc.instances) == [
        "data/bot1/submissions.db",
        "data/bot2/submissions.db",
    ]
    # 收到停止信号后所有子进程都被 terminate
    assert all(p.terminated for p in FakeProc.instances)


def test_run_multi_supervises_pixivflow_without_exposing_bot_tokens(monkeypatch, tmp_path):
    FakeProc.instances = []
    monkeypatch.setattr(run.subprocess, "Popen", FakeProc)
    monkeypatch.setenv("BOT1_TOKEN", "t1")
    monkeypatch.setenv("BOT1_CHANNEL_ID", "@c1")
    monkeypatch.setenv("BOT2_TOKEN", "t2")
    monkeypatch.setenv("BOT2_CHANNEL_ID", "@c2")
    monkeypatch.setenv("PIXIVFLOW_ENABLED", "true")
    config = tmp_path / "pixivflow.json"
    config.write_text('{"schedules": []}', encoding="utf-8")
    monkeypatch.setenv("PIXIVFLOW_CONFIG", str(config))
    monkeypatch.setenv("TELEPOST_BOT1_SUBMIT_TOKEN", "submit-1")

    ticks = {"n": 0}

    def fake_sleep(seconds):
        ticks["n"] += 1
        if ticks["n"] >= 2:
            os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(run.time, "sleep", fake_sleep)

    run.run_multi([1, 2])

    assert len(FakeProc.instances) == 3
    worker = next(p for p in FakeProc.instances if p.cmd == ["pixivflow", "scheduler"])
    assert "BOT1_TOKEN" not in worker.env
    assert "BOT2_TOKEN" not in worker.env
    assert worker.env["TELEPOST_BOT1_SUBMIT_TOKEN"] == "submit-1"
    assert worker.env["PIXIV_DOWNLOADER_CONFIG"] == str(config)
    assert worker.terminated is True
