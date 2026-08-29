# 测试指南

> 最后更新：2026-08。当前基线：**287 passed / 1 skipped**（`--no-cov` 全量），覆盖 15 个测试文件。

## 快速开始

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest -q --no-cov
```

## 常用命令

```bash
./.venv/bin/python -m pytest                       # 全量 + 覆盖率（pytest.ini 已含 --cov）
./.venv/bin/python -m pytest -q --no-cov           # 全量免覆盖率（快）
./.venv/bin/python -m pytest -m unit               # 仅单元测试
./.venv/bin/python -m pytest tests/test_handlers.py
./.venv/bin/python -m pytest -k "caption"          # 按名过滤
```

## 标记（pytest.ini）

`unit` `integration` `slow` `database` `network` `security` `boundary` `robustness` `compatibility` `blacklist` `asyncio`

## 编写约定

- fixtures：`mock_telegram_update` / `mock_telegram_context` / `temp_dir` 等，见 `tests/conftest.py`。
- 异步测试无需装饰器（`asyncio_mode = auto`）。
- mock 数据库时用 `asynccontextmanager` 包装假的 `get_db`；注意 `sqlite3.Row` 语义：无 `.get()`、`"col" in row` 是值成员判断、缺列访问抛 `IndexError`。
- 不要让测试真实写 `data/` 目录：patch `get_db` 或用 `temp_dir`。
- 集成搜索测试用真实 `PostSearchEngine` API（`add_post` / `search`）。

---
最后更新：2026-08
