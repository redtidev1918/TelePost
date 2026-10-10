# 测试指南

## 安装与全量测试

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest -q --no-cov -o log_cli=false
```

测试不需要真实 Telegram Token，以当前测试输出和退出码判断结果。
`Mini App CI` 在 Linux 和 Windows 上使用 Python 3.12 运行全量测试；本地使用上面的命令可省略覆盖率统计。
Windows 用 `py -3 -m venv .venv` 创建环境，将 `./.venv/bin/python` 换成 `.\.venv\Scripts\python.exe`。

全量测试可在 Windows 运行。supervisor 测试调用实际注册的信号处理器验证关闭流程，
避免 `os.kill(..., SIGTERM)` 终止 Windows 测试解释器；`0600` 文件权限只在 POSIX 上断言，
Windows 文件访问权限由目录 ACL 决定。数据库测试会显式关闭连接，释放临时文件。
运行策略在没有 `os.fchmod` 的 Windows Python 上使用路径式 `os.chmod`，保持原子替换。
平台差异见 [Python 文件权限接口](https://docs.python.org/3.13/library/os.html#os.fchmod)；
子进程中文输出测试显式使用 UTF-8，避免依赖系统代码页。
配置读取支持 UTF-8 与带 BOM 的 UTF-8，中文配置不依赖 Windows 系统代码页。
协议目录固定 LF，保留上游样本的字节校验；压力测试使用受控时钟与固定场景分配，
排队和停滞预算不受 Windows 建库耗时影响。
并发落盘测试验证所有线程的数据完整性，不设与 runner 磁盘绑定的秒数门槛；
Python CI 整体限时 20 分钟。Linux 保留覆盖率报告，Windows 省略覆盖率和实时日志。

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

## Presentation contract

Telegram 文案和 Mini App 布局各有可执行契约：

- `tests/test_telegram_golden_messages.py` 用精确文本 golden 锁定 `/start`、`/help`、
  `/about`、投稿预览和 caption 的排版。命令保持独立一行、无多余空行。
- `webapp/e2e/visual.spec.ts` 生成并比较移动端截图，锁定首页、投稿、我的投稿、
  审核队列和管理面板的视觉基线。
- 更新截图前先确认 DOM 已经稳定：

```bash
cd webapp
npx playwright test e2e/visual.spec.ts --update-snapshots --project=mobile
npx playwright test --project=mobile
```

## 测试约定

- 使用 `tests/conftest.py` 的临时目录和 Telegram fake/mock。
- 不要写仓库真实 `data/`；patch 数据库路径或使用 `temp_dir`。
- `sqlite3.Row` 没有 `.get()`，缺列访问会抛 `IndexError`。
- 模拟 `get_db()` 时使用 `asynccontextmanager`。
- 不发真实 Telegram 请求；需要网络语义时 mock 边界。
- 状态机测试复用生产的 `build_submission_conversation()`；更新路由测试还要复用
  `setup_application()`，验证会话处理后停止向后续 handler groups 传播。

配置自检使用 `python check_config.py`，只报告凭据是否配置。该脚本检查基础依赖与
单 Bot 配置，不能代替多 Bot、审核和 Mini App 的启动验证。

## 发布验证

Tag 流程除测试外还会：

- 校验 tag 与代码版本一致
- 构建 GHCR `linux/amd64`、`linux/arm64`
- 构建 Linux x64、Windows x64、macOS arm64 单文件程序
- 创建 GitHub Release 并上传三个资产

全部 job 成功并核对远端产物后，才算正式发布。
