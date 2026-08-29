"""
旧版 Polling 健康检查 HTTP 服务器（兼容外部调用）

背景：
- Webhook 模式下，utils/webhook_server.py 会在同一端口上同时提供
  /webhook 与 /health 端点，无需本模块。
- TelePost 2.9 起，Polling 模式由 ``utils.polling_server`` 在 Bot 主事件循环
  同时提供 ``/health`` 与 ``/api/v1/*``。主程序不再调用本模块。
- 保留 ``start_health_server``，避免依赖旧入口的第三方部署立即失效；新代码
  应使用 ``PollingApiServer``。

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
        "service": "telepost-bot",
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
