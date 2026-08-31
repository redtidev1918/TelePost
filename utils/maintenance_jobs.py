"""Async callbacks for process-wide maintenance jobs.

python-telegram-bot's JobQueue always awaits callbacks.  Keep blocking file and
subprocess work off the event loop and expose the callbacks here so their async
contract can be regression-tested.
"""

import asyncio
import logging
import os
import subprocess
from datetime import time as datetime_time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from utils.logging_config import cleanup_old_logs


logger = logging.getLogger(__name__)


def scheduled_time(hour: int, minute: int = 0) -> datetime_time:
    """Return an aware JobQueue time using the deployment's configured timezone."""
    timezone_name = os.getenv("TZ", "Asia/Shanghai").strip() or "Asia/Shanghai"
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning("无效 TZ=%s，定时维护任务回退到 UTC", timezone_name)
        timezone = ZoneInfo("UTC")
    return datetime_time(hour=hour, minute=minute, tzinfo=timezone)


async def clean_logs_job(context: Any) -> None:
    """Delete expired log files without blocking Telegram update handling."""
    del context
    logger.info("执行定期日志清理任务")
    await asyncio.to_thread(cleanup_old_logs, "logs")


def _run_pixivflow_maintain() -> subprocess.CompletedProcess[str]:
    config_path = os.getenv("PIXIVFLOW_CONFIG", "")
    command = ["pixivflow", "maintain"]
    if config_path:
        command += ["--config", config_path]

    logger.info("开始执行 PixivFlow 维护: %s", " ".join(command))
    return subprocess.run(
        command,
        cwd="/app",
        timeout=900,
        capture_output=True,
        text=True,
    )


async def pixivflow_maintain_job(context: Any) -> None:
    """Run PixivFlow maintenance off the bot event loop."""
    del context
    try:
        process = await asyncio.to_thread(_run_pixivflow_maintain)
        output_tail = (process.stdout or "")[-400:]
        if process.returncode == 0:
            logger.info("PixivFlow 维护完成")
        else:
            logger.warning(
                "PixivFlow 维护异常退出 %s: %s",
                process.returncode,
                output_tail,
            )
    except Exception as exc:
        logger.warning("PixivFlow 维护失败: %s", exc, exc_info=True)
