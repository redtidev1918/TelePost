# 贡献指南

## 开发环境

先阅读 [AGENTS.md](AGENTS.md) 及其列出的跨仓库权威文档，确认业务和部署边界。
使用 Python 3.10+；Windows 用 `py -3 -m venv .venv` 创建环境，
把下面的 `./.venv/bin/python` 换成 `.\.venv\Scripts\python.exe`。

```bash
git clone https://github.com/redtidev1918/TelePost.git
cd TelePost
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
```

测试不需要真实 Token。实际运行时执行 `./.venv/bin/python run.py --setup`，不要提交
`config.ini`、Token、数据库、日志或下载内容。

## 提交前

```bash
./.venv/bin/python -m pytest -q --no-cov -o log_cli=false
./.venv/bin/python -m pytest -q tests/test_api_server.py tests/test_conversation_flow.py
./.venv/bin/python check_config.py
```

- 一个提交只解决一个问题，并为行为变更留下最小回归测试。
- 命令变更同步 `docs/COMMANDS.md`。
- 配置变更同步 `docs/CONFIGURATION.md` 与 `config.ini.example`。
- API 变更同步 `docs/API.md`。
- 中英文用户文档同步更新；下载页由发版工作流生成。
- 部署行为变更同步对应平台文档。
- 发布说明与版本由 release-please 管理，不手工改写已发布版本的记录。

提交信息沿用仓库现有前缀：`fix:`、`feat:`、`docs:`、`refactor:`、`test:`、`ci:`。

## Pull Request

从 `main` 建分支，PR 中写明动机、影响范围、验证命令和结果。不要附带无关格式化或
重构；涉及持久数据时说明迁移、回退和备份策略。

纯文档改动检查本地链接、章节锚点、代码块与表格，并核对命令和默认值是否符合代码。
文档改动不需要发布应用版本。
