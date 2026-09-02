# 测试指南

> 最后更新：2026-09。当前基线：**432 passed / 1 skipped**（`--no-cov` 全量），覆盖 17 个测试文件，含端到端集成测试 `tests/test_conversation_flow.py`。

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

## 端到端集成测试

`tests/test_conversation_flow.py` 用真实 `ConversationHandler`（`build_submission_conversation`）
+ 真实 SQLite + FakeBot 跑完整投稿链路 `/submit → 媒体 → 文档 → /done_media → 编辑标签 → 发布`，
断言状态衔接与出站消息。新增/修改投稿状态机时务必保证该测试通过——它能在单元测试之外
捕获"状态机注册失败/状态衔接断裂"这类整链路问题。

---

最后更新：2026-09
