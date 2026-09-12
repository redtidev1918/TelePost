"""Telegram Bot API 的 token 位于**请求 URL 的路径**中，因此任何 HTTP 客户端请求日志
都会把凭据原样写进日志（httpx 默认会对每次轮询打一条 INFO）：

    HTTP Request: POST https://api.telegram.org/bot<TOKEN>/getMe "HTTP/1.1 200 OK"

``utils/logging_config.setup_logging`` 通过把 ``httpx`` / ``httpcore`` 两个 logger
压到 WARNING 以上来消除这个泄露面。

本文件是该控制的**回归守卫**：如果去掉那段降噪，下面这些断言会立刻失败——
一个形态合法但明显合成（synthetic）的假 token 会出现在 stdout / stderr /
caplog / 日志文件里。

注意：本测试**不调用**任何真实 Telegram API；它只验证日志路径。
"""

import logging

import pytest

# 故意做成不可误认的合成值：既是 Bot API 的 "<数字>:<35+ 位 URL-safe 字符>" 形态
# （校验器需要它看起来合法），又带有 SYNTHETIC / NOT_A_REAL / DUMMY / PLACEHOLDER
# 明确标记，是任何真实服务都不会接受的假凭据。
SYNTHETIC_BOT_TOKEN = "0000000000:SYNTHETIC_NOT_A_REAL_TOKEN_DUMMY_PLACEHOLDER"


@pytest.fixture
def configured_logging(monkeypatch, tmp_path):
    """在一次性 cwd 中执行真实的 setup_logging()，并在结束后完整还原 logger 状态。

    setup_logging() 会创建 ``logs/`` 目录并往 root logger 挂 handler；
    这里做快照/还原，避免污染同进程中的其它测试。
    """
    root = logging.getLogger()
    httpx_logger = logging.getLogger("httpx")
    httpcore_logger = logging.getLogger("httpcore")

    snapshot = {
        "root_handlers": root.handlers[:],
        "root_level": root.level,
        "httpx_handlers": httpx_logger.handlers[:],
        "httpx_level": httpx_logger.level,
        "httpcore_handlers": httpcore_logger.handlers[:],
        "httpcore_level": httpcore_logger.level,
    }

    monkeypatch.chdir(tmp_path)
    from utils.logging_config import setup_logging

    setup_logging()
    try:
        yield tmp_path
    finally:
        root.handlers[:] = snapshot["root_handlers"]
        root.setLevel(snapshot["root_level"])
        httpx_logger.handlers[:] = snapshot["httpx_handlers"]
        httpx_logger.setLevel(snapshot["httpx_level"])
        httpcore_logger.handlers[:] = snapshot["httpcore_handlers"]
        httpcore_logger.setLevel(snapshot["httpcore_level"])


def test_httpx_and_httpcore_loggers_are_muted(configured_logging):
    """核心控制：两个 HTTP logger 的有效级别必须 >= WARNING。"""
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_request_url_token_not_in_stdout_stderr_or_caplog(configured_logging, caplog, capsys):
    """即使外部把 root 提到 DEBUG，httpx/httpcore 的请求行也不得进入任何输出。"""
    url = f"https://api.telegram.org/bot{SYNTHETIC_BOT_TOKEN}/getWebhookInfo"

    with caplog.at_level(logging.DEBUG):
        for name in ("httpx", "httpcore"):
            logger = logging.getLogger(name)
            logger.info('HTTP Request: POST %s "HTTP/1.1 200 OK"', url)
            logger.debug("connection trace for %s", url)

    captured = capsys.readouterr()
    assert SYNTHETIC_BOT_TOKEN not in caplog.text
    assert SYNTHETIC_BOT_TOKEN not in captured.out
    assert SYNTHETIC_BOT_TOKEN not in captured.err


def test_token_not_persisted_into_log_files(configured_logging):
    """落盘日志同样不得包含 token（bot.log / error.log 及其轮转备份）。"""
    url = f"https://api.telegram.org/bot{SYNTHETIC_BOT_TOKEN}/getMe"

    for name in ("httpx", "httpcore"):
        logging.getLogger(name).info("HTTP Request: POST %s", url)

    for handler in logging.getLogger().handlers:
        handler.flush()

    log_dir = configured_logging / "logs"
    assert log_dir.is_dir()
    for log_file in log_dir.glob("*.log*"):
        content = log_file.read_text(encoding="utf-8", errors="replace")
        assert SYNTHETIC_BOT_TOKEN not in content, f"token leaked into {log_file.name}"


def test_warning_level_httpx_records_still_reachable(configured_logging, caplog):
    """降噪只压住 INFO/DEBUG，不得把真正的 WARNING 也一起吞掉（避免误伤可观测性）。"""
    with caplog.at_level(logging.WARNING, logger="httpx"):
        logging.getLogger("httpx").warning("httpx degraded: retrying transport")

    assert "httpx degraded" in caplog.text
