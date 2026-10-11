"""Serve the built Mini App with production HTTP wiring, without Telegram I/O."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# Synthetic local fixtures only; this process never authenticates with Telegram.
os.environ.update(
    TESTING="true", TOKEN="test_token_123456789", CHANNEL_ID="@test",
    OWNER_ID="123456789", REVIEW_CHAT_ID="-1001234567890",
    MINIAPP_DIST_DIR=str(ROOT / "webapp" / "dist"),
)

from aiohttp import web
from utils.webhook_server import WebhookServer, build_web_app

server = WebhookServer(MagicMock(), 3001, "/webhook", "local-test")
server.web_app = build_web_app()
server._mount_miniapp_static()
web.run_app(server.web_app, host="127.0.0.1", port=3001)
