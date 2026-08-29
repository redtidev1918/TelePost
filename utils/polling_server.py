"""Polling mode HTTP server exposing health and the same submission API."""

import logging
import os

from aiohttp import web

logger = logging.getLogger(__name__)


def build_polling_app(application) -> web.Application:
    """Build the local HTTP surface used while Telegram updates use polling."""
    from utils.api_server import API_CLIENT_MAX_BYTES

    async def health(_request: web.Request) -> web.Response:
        payload = {
            "status": "ok",
            "service": "telepost-bot",
            "mode": "polling",
        }
        try:
            import psutil

            proc = psutil.Process()
            payload["process_rss_mb"] = round(
                proc.memory_info().rss / 1048576, 1
            )
            payload["system_available_mb"] = round(
                psutil.virtual_memory().available / 1048576, 1
            )
        except Exception:
            pass
        return web.json_response(payload)

    app = web.Application(client_max_size=API_CLIENT_MAX_BYTES)
    app.router.add_get("/health", health)
    if os.getenv("API_ENABLED", "true").lower() != "false":
        from utils.api_server import add_api_routes

        add_api_routes(app, application)
    return app


class PollingApiServer:
    """Keep Telegram long polling and the automation API on one event loop."""

    def __init__(self, application, port: int):
        self.application = application
        self.port = port
        self.runner = None

    async def start(self) -> None:
        app = build_polling_app(self.application)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "0.0.0.0", self.port)
        await site.start()
        logger.info(
            "Polling HTTP server started: http://0.0.0.0:%s "
            "(/health, /api/v1/*)",
            self.port,
        )

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
