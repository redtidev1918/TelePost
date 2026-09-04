# 测试指南

## 安装与全量测试

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest -q --no-cov -o log_cli=false
```

2.10.39 发布时的基线是 437 passed、1 skipped；以后以当前测试输出为准，不要把固定
数量当成功条件。CI 使用 Python 3.11 和同一条免覆盖率命令。

## 常用选择

```bash
./.venv/bin/python -m pytest tests/test_conversation_flow.py
./.venv/bin/python -m pytest tests/test_api_server.py tests/test_streaming_uploads.py
./.venv/bin/python -m pytest tests/test_run_mode.py tests/test_webhook_router.py
./.venv/bin/python -m pytest tests/test_shutdown.py tests/test_webhook_secret_logging.py
./.venv/bin/python -m pytest -k 'caption'
```

不加 `--no-cov` 时，`pytest.ini` 会启用覆盖率报告。异步测试使用
`asyncio_mode=auto`，不需要逐个加 marker。

## 改动与最小回归

| 改动 | 至少运行 |
|---|---|
| 投稿状态机 | `test_conversation_flow.py`、`test_logic_flow.py` |
| Webhook/Polling | `test_run_mode.py`、`test_webhook_router.py`、`test_shutdown.py` |
| 多 Bot supervisor | `test_run_dispatcher.py`、`test_run_supervisor.py` |
| HTTP API | `test_api_server.py`、`test_streaming_uploads.py` |
| 审核 | `test_review.py`、`test_publish_regressions.py` |
| 数据库 | `test_database.py`、`test_database_operations.py` |
| 权限/安全 | `test_security.py`、`test_api_commands.py`、`test_no_owner_probe.py` |

提交前仍要跑全量，表格只用于开发迭代。

## 测试约定

- 使用 `tests/conftest.py` 的临时目录和 Telegram fake/mock。
- 不要写仓库真实 `data/`；patch 数据库路径或使用 `temp_dir`。
- `sqlite3.Row` 没有 `.get()`，缺列访问会抛 `IndexError`。
- 模拟 `get_db()` 时使用 `asynccontextmanager`。
- 不发真实 Telegram 请求；需要网络语义时 mock 边界。
- 状态机测试复用生产的 `build_submission_conversation()`，不要复制一套 handler 图。

## 发布验证

Tag 流程除测试外还会：

- 校验 tag 与代码版本一致
- 构建 GHCR `linux/amd64`、`linux/arm64`
- 构建 Linux x64、Windows x64、macOS arm64 单文件程序
- 创建 GitHub Release 并上传三个资产

全部 job 成功并核对远端产物后，才算正式发布。
