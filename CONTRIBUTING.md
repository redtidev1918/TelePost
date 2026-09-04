# 贡献指南

## 开发环境

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
```

- 一个提交只解决一个问题，并为行为变更留下最小回归测试。
- 命令变更同步 `docs/COMMANDS.md`。
- 配置变更同步 `docs/CONFIGURATION.md` 与 `config.ini.example`。
- API 变更同步 `docs/API.md`。
- 部署行为变更同步对应平台文档。
- 用户可见变更写入 `CHANGELOG.md` 的 `[Unreleased]`。

提交信息沿用仓库现有前缀：`fix:`、`feat:`、`docs:`、`refactor:`、`test:`、`ci:`。

## Pull Request

从 `main` 建分支，PR 中写明动机、影响范围、验证命令和结果。不要附带无关格式化或
重构；涉及持久数据时说明迁移、回退和备份策略。
