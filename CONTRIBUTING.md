# 贡献指南

感谢关注 TelePost！

## 开发环境

```bash
git clone https://github.com/redtidev1918/TelePost.git && cd TelePost
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp config.ini.example config.ini   # 填入测试用 Token/频道
```

## 提交约定

- 分支：从 `main` 拉出功能分支；提交信息使用中文并带前缀：`fix:` / `feat:` / `docs:` / `refactor:`（参考 `git log` 现有风格）。
- 一个提交做一件事；行为变更同步更新 `CHANGELOG.md` 的 `[Unreleased]` 段。

## 提交前检查

1. `./.venv/bin/python -m pytest -q --no-cov` 全绿；
2. 新增/修改行为需补测试；
3. **文档联动**：改动 `main.py` 命令注册 → 同步 [docs/COMMANDS.md](docs/COMMANDS.md)；改动 `config/settings.py` 配置项 → 同步 [docs/CONFIGURATION.md](docs/CONFIGURATION.md) 与 `config.ini.example`；改动部署相关 → 同步 [docs/INSTALL.md](docs/INSTALL.md) 与对应平台指南。

## 提交方式

Fork → 分支 → Pull Request，描述清楚动机、改动点与测试情况。
