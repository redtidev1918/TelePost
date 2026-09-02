# 内部设计：投稿会话状态机

> 面向开发者。最后更新：2026-09

## 概览

聊天投稿（`/submit`）收敛为**三个阶段、单一数据源**：

```
入口(/submit + 「📝 开始投稿」按钮)
   │
   ▼
UPLOAD ──(媒体/文档消息)──▶ UPLOAD ──(/done_media | /skip_media)──▶ PREVIEW
                                                                     │
                                              ┌──────────────────────┤
                                              ▼ 编辑按钮              ▼ 确认发布
                                            EDIT ──(输入新值)──▶ PREVIEW ──▶ publish_submission
```

状态定义在 `models/state.py`（`UPLOAD`/`PREVIEW`/`EDIT`），状态机注册在
`handlers/conversation.py`（`build_submission_conversation`，生产与测试复用）。

## 单一数据源

会话真相只有一行：`submissions` 表（`user_id` 主键）。内存状态机只描述"当前在
哪个阶段"；用户填写的媒体/文档/标签/标题/简介/链接/匿名/剧透全部写回这一行。

所有读写集中在 `utils/submission.py`：

- `classify_message(message)` —— 消息 → `type:file_id[:filename]`（唯一归类实现）
- `get_session` / `create_session` / `append_entry` / `update_fields`

> 历史上媒体归类在 4 个文件各写一份、状态号 0–18 与 DB `mode` 双轨漂移，
> 曾导致"点按钮只建 DB 行、不进状态机 → 发媒体静默无响应"。重构后归类只有
> 一份、状态只有三个、数据源只有一行。

## 各阶段 handler

| 阶段 | 文件 | handler | 说明 |
|---|---|---|---|
| UPLOAD | `handlers/upload.py` | `handle_upload` / `done_upload` / `skip_upload` / `prompt_upload` | 媒体与文档统一归类追加；`BOT_MODE` 限制媒体/文档模式收哪些 |
| PREVIEW | `handlers/preview_handlers.py` | `show_submission_preview` / `handle_toggle_*` / `handle_edit_field_callback` | 预览面板 + 匿名/剧透开关 + 编辑入口 |
| EDIT | `handlers/preview_handlers.py` | `handle_edit_input` | `context.user_data['edit_field']` 区分字段，输入后回预览 |
| 发布 | `handlers/publish.py` | `publish_submission` | 读会话行 → 审核或 `deliver_items_to_chat` |

## 发布布局

`handlers/publish.deliver_items_to_chat` 是频道与审核群共用的唯一布局引擎
（详见该模块 docstring）：photo/video 相册 → GIF/音频逐条 → 文档组，每条链式回复，
caption 只挂第一条。

## 已知边界

- `BOT_MODE` 为部署级配置（`MEDIA`/`DOCUMENT`/`MIXED`），用户不可在会话内切换；
  `MIXED`（默认）接受媒体与文件混传。
- 会话内存状态不跨进程重启保留；重启后需重新 `/submit`（DB 行由超时清理回收）。
- 会话超时由 `check_conversation_timeout`（`main.py`）按 `SESSION_TIMEOUT` 判定。
