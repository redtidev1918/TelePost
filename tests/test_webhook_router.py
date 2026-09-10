"""
多 bot webhook 路由测试（run.py build_router_app / build_bot_env webhook 分支）
"""
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

import run as run_mod


@pytest.mark.asyncio
async def test_wait_for_bot_port_retries_until_ready(monkeypatch):
    writer = MagicMock()
    writer.wait_closed = AsyncMock()
    connect = AsyncMock(side_effect=[OSError("not ready"), (object(), writer)])
    monkeypatch.setattr(run_mod.asyncio, "open_connection", connect)
    sleep = AsyncMock()
    monkeypatch.setattr(run_mod.asyncio, "sleep", sleep)

    await run_mod._wait_for_bot_port(8081)

    assert connect.await_count == 2
    sleep.assert_awaited_once_with(0.1)
    writer.close.assert_called_once_with()
    writer.wait_closed.assert_awaited_once_with()


class TestWebhookMapping:
    def test_port_and_path(self):
        assert run_mod.bot_webhook_port(1) == 8081
        assert run_mod.bot_webhook_port(2) == 8082
        assert run_mod.bot_webhook_path(1) == "/webhook/bot1"
        assert run_mod.bot_webhook_path(2) == "/webhook/bot2"

    def test_webhook_env_mapping(self, monkeypatch):
        monkeypatch.setenv("BOT1_TOKEN", "t1")
        monkeypatch.setenv("BOT1_CHANNEL_ID", "@c1")
        monkeypatch.setenv("RUN_MODE", "WEBHOOK")
        monkeypatch.setenv("WEBHOOK_URL", "https://app.fly.dev/")
        env = run_mod.build_bot_env(1, dict(os.environ))
        assert env["RUN_MODE"] == "WEBHOOK"
        assert env["WEBHOOK_PORT"] == "8081"
        assert env["WEBHOOK_PATH"] == "/webhook/bot1"
        assert env["WEBHOOK_URL"] == "https://app.fly.dev"  # 去掉尾部斜杠

    def test_base_url_suffix_stripped(self, monkeypatch):
        monkeypatch.setenv("BOT1_TOKEN", "t1")
        monkeypatch.setenv("RUN_MODE", "WEBHOOK")
        monkeypatch.setenv("WEBHOOK_URL", "https://app.fly.dev/webhook/bot7")
        env = run_mod.build_bot_env(1, dict(os.environ))
        assert env["WEBHOOK_URL"] == "https://app.fly.dev"

    def test_auto_mode_uses_webhook_mapping_with_public_url(self, monkeypatch):
        monkeypatch.setenv("BOT1_TOKEN", "t1")
        monkeypatch.setenv("RUN_MODE", "AUTO")
        monkeypatch.setenv("WEBHOOK_URL", "https://app.fly.dev")
        env = run_mod.build_bot_env(1, dict(os.environ))
        assert env["RUN_MODE_REQUESTED"] == "AUTO"
        assert env["RUN_MODE"] == "WEBHOOK"
        assert env["WEBHOOK_PORT"] == "8081"
        assert env["WEBHOOK_PATH"] == "/webhook/bot1"

    def test_auto_mode_uses_polling_without_public_url(self, monkeypatch):
        monkeypatch.setenv("BOT1_TOKEN", "t1")
        monkeypatch.setenv("RUN_MODE", "AUTO")
        monkeypatch.delenv("WEBHOOK_URL", raising=False)
        env = run_mod.build_bot_env(1, dict(os.environ))
        assert env["RUN_MODE_REQUESTED"] == "AUTO"
        assert env["RUN_MODE"] == "POLLING"


class TestRouterRelay:
    @pytest.mark.asyncio
    async def test_relay_forwards_path_and_headers(self, monkeypatch):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        received = {}

        async def fake_bot(request):
            received["path"] = request.path
            received["secret"] = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            chunks = []
            async for chunk in request.content.iter_chunked(65536):
                chunks.append(chunk)
            received["body"] = b"".join(chunks)
            return web.json_response({"ok": True})

        async def fake_health(request):
            return web.json_response({"status": "ok"})

        # 假 bot 进程（跑在 8081 对应的临时端口上）
        async def fake_ready(_request):
            return web.json_response({"status": "ok", "ready": True})

        bot_app = web.Application()
        bot_app.router.add_post("/webhook/bot1", fake_bot)
        bot_app.router.add_get("/ready", fake_ready)

        # 让 router 的转发目标端口指向临时端口
        monkeypatch.setattr(run_mod, "bot_webhook_port", lambda i: 8081 if i == 1 else 8082)

        router_app = run_mod.build_router_app([1])

        # The parent router must stream rather than call request.read(), which
        # would duplicate a potentially 500 MiB multipart upload in RAM.
        from aiohttp.web_request import BaseRequest

        async def forbidden_buffered_read(_request):
            raise AssertionError("router buffered the request body")

        monkeypatch.setattr(BaseRequest, "read", forbidden_buffered_read)

        bot_runner = web.AppRunner(bot_app)
        await bot_runner.setup()
        bot_site = web.TCPSite(bot_runner, "127.0.0.1", 8081)
        await bot_site.start()

        router_runner = web.AppRunner(router_app)
        await router_runner.setup()
        router_site = web.TCPSite(router_runner, "127.0.0.1", 18080)
        await router_site.start()

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                payload = b"x" * (2 * 1024 * 1024)
                async with session.post(
                    "http://127.0.0.1:18080/webhook/bot1",
                    data=payload,
                    headers={"X-Telegram-Bot-Api-Secret-Token": "sec123"},
                ) as resp:
                    assert resp.status == 200
                    assert await resp.json() == {"ok": True}

            assert received["path"] == "/webhook/bot1"
            assert received["secret"] == "sec123"
            assert received["body"] == payload
        finally:
            await router_runner.cleanup()
            await bot_runner.cleanup()

    @pytest.mark.asyncio
    async def test_health_endpoint(self, monkeypatch):
        from aiohttp import web
        from aiohttp.test_utils import TestServer
        import aiohttp

        monkeypatch.setattr(
            run_mod,
            "storage_health_snapshot",
            lambda: {"delivery_outbox": {"files": 2, "bytes": 42, "mb": 0.0}},
        )
        router_app = run_mod.build_router_app([1])
        runner = web.AppRunner(router_app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 18081)
        await site.start()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:18081/health") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["status"] == "ok"
                    assert data["storage"]["delivery_outbox"]["files"] == 2
        finally:
            await runner.cleanup()


def test_outbox_metrics_ignores_migrated_archive(tmp_path):
    import json

    outbox = tmp_path / "delivery-outbox"
    (outbox / "migrated").mkdir(parents=True)
    # Active failed manifest: counted.
    (outbox / "active.json").write_text(json.dumps({"attempts": 3, "lastError": "x"}))
    # Archived manifest already replayed to the SQLite outbox: ignored.
    (outbox / "migrated" / "old.json").write_text(
        json.dumps({"attempts": 9, "lastError": "already migrated"})
    )
    metrics = run_mod._outbox_metrics(str(outbox))
    assert metrics["failed_files"] == 1
    assert metrics["total_attempts"] == 3
    assert metrics["files"] == 1


class TestReadinessProbes:
    """/live always answers; /ready aggregates bot-child readiness (503 while
    any child is still warming, 200 once all are initialize()+start() done)."""

    async def _serve(self, app, port):
        from aiohttp import web
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        return runner

    @pytest.mark.asyncio
    async def test_live_is_independent_of_child_warmup(self, monkeypatch):
        from aiohttp import web
        import aiohttp

        # No bot children listening at all.
        monkeypatch.setattr(run_mod, "bot_webhook_port", lambda i: 18100 + i)
        router_app = run_mod.build_router_app([1])
        runner = await self._serve(router_app, 18082)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:18082/live") as resp:
                    assert resp.status == 200
                    assert (await resp.json())["kind"] == "live"
        finally:
            await runner.cleanup()

    @pytest.mark.asyncio
    async def test_ready_503_while_child_not_ready_then_200(self, monkeypatch):
        from aiohttp import web
        import aiohttp

        state = {"ready": False}

        async def ready_handler(_request):
            return web.json_response(
                {"ready": state["ready"]},
                status=200 if state["ready"] else 503,
            )

        child = web.Application()
        child.router.add_get("/ready", ready_handler)

        monkeypatch.setattr(run_mod, "bot_webhook_port", lambda i: 18111)
        router_app = run_mod.build_router_app([1])
        child_runner = await self._serve(child, 18111)
        router_runner = await self._serve(router_app, 18083)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:18083/ready") as resp:
                    assert resp.status == 503
                    data = await resp.json()
                    assert data["bots"]["bot1"] is False

                state["ready"] = True
                async with session.get("http://127.0.0.1:18083/ready") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["bots"]["bot1"] is True
        finally:
            await router_runner.cleanup()
            await child_runner.cleanup()
