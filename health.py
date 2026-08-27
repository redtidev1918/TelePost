"""
健康检查 HTTP 服务器（用于 Polling 模式部署）

背景：
- Webhook 模式下，utils/webhook_server.py 会在同一端口上同时提供
  /webhook 与 /health 端点，无需本模块。
- Polling 模式（Docker、VPS 部署）没有任何 HTTP 端点，但
  Dockerfile 的 HEALTHCHECK 与 docker-compose 的健康检查都会请求
  http://localhost:8080/health。缺少本模块时容器会被判定为
  unhealthy 并陷入重启循环。

实现说明：
- 在独立的守护线程中运行自带事件循环的 aiohttp 服务器，
  完全不干扰机器人主事件循环；
- /health 返回 200 与简单 JSON；任何其他路径返回 404。
"""

import logging
import threading

from aiohttp import web

logger = logging.getLogger(__name__)


async def _health_handler(request: web.Request) -> web.Response:
    """健康检查端点"""
    return web.json_response({
        "status": "ok",
        "service": "telesubmit-bot",
        "mode": "polling",
    })


def _run_server(port: int) -> None:
    """在独立线程中运行事件循环与 aiohttp 服务器"""
    loop = __import__("asyncio").new_event_loop()
    __import__("asyncio").set_event_loop(loop)

    app = web.Application()
    app.router.add_get("/health", _health_handler)

    try:
        web.run_app(
            app,
            host="0.0.0.0",
            port=port,
            print=None,
            access_log=None,
            handle_signals=False,
        )
    except Exception as e:  # pragma: no cover - 部署环境相关
        logger.error(f"健康检查服务器异常退出: {e}")


def start_health_server(port: int = 8080) -> threading.Thread:
    """
    启动健康检查服务器（非阻塞，立即返回）

    Args:
        port: 监听端口，默认 8080（与 Dockerfile HEALTHCHECK 一致）

    Returns:
        服务线程（daemon）
    """
    thread = threading.Thread(
        target=_run_server,
        args=(port,),
        name="health-server",
        daemon=True,
    )
    thread.start()
    logger.info(f"健康检查服务器已启动: http://0.0.0.0:{port}/health")
    return thread
