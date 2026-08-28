"""
多 bot webhook 路由测试（run.py build_router_app / build_bot_env webhook 分支）
"""
import os

import pytest

import run as run_mod


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
            received["body"] = await request.read()
            return web.json_response({"ok": True})

        async def fake_health(request):
            return web.json_response({"status": "ok"})

        # 假 bot 进程（跑在 8081 对应的临时端口上）
        bot_app = web.Application()
        bot_app.router.add_post("/webhook/bot1", fake_bot)

        # 让 router 的转发目标端口指向临时端口
        monkeypatch.setattr(run_mod, "bot_webhook_port", lambda i: 8081 if i == 1 else 8082)

        router_app = run_mod.build_router_app([1])

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
                async with session.post(
                    "http://127.0.0.1:18080/webhook/bot1",
                    data=b"payload",
                    headers={"X-Telegram-Bot-Api-Secret-Token": "sec123"},
                ) as resp:
                    assert resp.status == 200
                    assert await resp.json() == {"ok": True}

            assert received["path"] == "/webhook/bot1"
            assert received["secret"] == "sec123"
            assert received["body"] == b"payload"
        finally:
            await router_runner.cleanup()
            await bot_runner.cleanup()

    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        from aiohttp import web
        from aiohttp.test_utils import TestServer
        import aiohttp

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
        finally:
            await runner.cleanup()
