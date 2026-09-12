#!/usr/bin/env python3
"""V4 simulation control-plane server.

Boots the REAL TelePost stack (HTTP API adapter -> review queue -> ReviewService
-> PublicationService) against a throwaway SQLite file, with the Telegram Bot API
redirected to a synthetic localhost fake server.

Nothing here re-implements business logic: the aiohttp routes come from
``utils.api_server.add_api_routes`` and every submission is served by the same
``handlers.review`` / ``services.review_service`` code paths production uses.
This file is only a *bootstrap* (env translation + process wiring) so the stack
can run on loopback without production credentials.

Environment (SIM_* namespace, read before TelePost config is imported):
    SIM_DB_PATH             throwaway SQLite path (required)
    SIM_HTTP_PORT           loopback port for /api/v1 (required)
    SIM_TELEGRAM_BASE_URL   e.g. http://127.0.0.1:9999/bot  (required)
    SIM_TELEGRAM_FILE_URL   e.g. http://127.0.0.1:9999/file/bot
    SIM_HANDSHAKE_PATH      file to write the readiness handshake into (required)
    SIM_OWNER_ID, SIM_CHANNEL_ID, SIM_REVIEW_CHAT_ID, SIM_BOT_TOKEN,
    SIM_REVIEW_TOKEN        synthetic identifiers/credentials

Safety: every credential below is synthetic and loopback-scoped. The process
refuses to start if any of them is missing, so it can never silently inherit a
real token from the ambient environment.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import signal

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"sim_server: {name} is required (synthetic value expected)")
    return value


def _translate_env() -> dict:
    """Map SIM_* onto the names TelePost's config layer reads."""

    handshake = _require("SIM_HANDSHAKE_PATH")
    cfg = {
        "db_path": _require("SIM_DB_PATH"),
        "http_port": int(_require("SIM_HTTP_PORT")),
        "telegram_base_url": _require("SIM_TELEGRAM_BASE_URL"),
        "telegram_file_url": os.environ.get("SIM_TELEGRAM_FILE_URL", "").strip(),
        "handshake_path": handshake,
        "owner_id": int(_require("SIM_OWNER_ID")),
        "channel_id": _require("SIM_CHANNEL_ID"),
        "review_chat_id": _require("SIM_REVIEW_CHAT_ID"),
        "bot_token": _require("SIM_BOT_TOKEN"),
        "review_token": _require("SIM_REVIEW_TOKEN"),
    }

    # TelePost reads these at import time; they must be present before any
    # project module is imported.
    os.environ["BOT_TOKEN"] = cfg["bot_token"]
    os.environ["CHANNEL_ID"] = cfg["channel_id"]
    os.environ["DB_PATH"] = cfg["db_path"]
    os.environ["OWNER_ID"] = str(cfg["owner_id"])
    os.environ["REVIEW_CHAT_ID"] = cfg["review_chat_id"]
    os.environ["API_REVIEW_REQUIRED"] = "true"
    os.environ["CHAT_REVIEW_REQUIRED"] = "false"
    os.environ["TELEPOST_REVIEW_TOKEN"] = cfg["review_token"]
    # Keep the simulation from inheriting a real webhook/polling posture.
    os.environ["RUN_MODE"] = "POLLING"
    os.environ["RUN_MODE_REQUESTED"] = "POLLING"
    os.environ.pop("WEBHOOK_URL", None)
    return cfg


CFG = _translate_env()

# Isolation guard: config.ini is gitignored and may carry real credentials on a
# developer machine. Never let the simulation read it silently.
_INI_PATH = os.path.join(REPO_ROOT, "config.ini")
if os.path.exists(_INI_PATH) and os.environ.get("SIM_ALLOW_REPO_CONFIG_INI") != "1":
    raise SystemExit(
        f"sim_server: refusing to start: {_INI_PATH} exists and may contain production "
        "credentials. Move it aside, or set SIM_ALLOW_REPO_CONFIG_INI=1 if you are certain "
        "it holds synthetic values only."
    )

from aiohttp import web  # noqa: E402  (import after env translation, on purpose)
from telegram.ext import Application  # noqa: E402

from config.settings import CHANNEL_ID, REVIEW_CHAT_ID  # noqa: E402
from database.db_manager import init_db  # noqa: E402
from utils.api_server import API_CLIENT_MAX_BYTES, add_api_routes  # noqa: E402
from utils.api_tokens import generate_token  # noqa: E402


async def _serve() -> None:
    await init_db()

    application = (
        Application.builder()
        .token(CFG["bot_token"])
        .base_url(CFG["telegram_base_url"])
        .base_file_url(CFG["telegram_file_url"] or CFG["telegram_base_url"])
        .build()
    )
    # getMe -> synthetic bot identity; nothing else is started (no polling, no
    # webhook) because the API path never needs an update loop.
    await application.initialize()

    web_app = web.Application(client_max_size=API_CLIENT_MAX_BYTES)
    add_api_routes(web_app, application)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", CFG["http_port"])
    await site.start()

    api_token = await generate_token(CFG["owner_id"], "v4-simulation")

    with open(CFG["handshake_path"], "w", encoding="utf-8") as fh:
        json.dump(
            {
                "ready": True,
                "http_port": CFG["http_port"],
                "api_base": f"http://127.0.0.1:{CFG['http_port']}/api/v1",
                "api_token": api_token,
                "review_token": CFG["review_token"],
                "owner_id": CFG["owner_id"],
                "channel_id": str(CHANNEL_ID),
                "review_chat_id": str(REVIEW_CHAT_ID),
                "db_path": CFG["db_path"],
            },
            fh,
        )
    print("sim_server: ready", flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - platform dependent
            pass
    await stop.wait()

    await runner.cleanup()
    await application.shutdown()


def main() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
