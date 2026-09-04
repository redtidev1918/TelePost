# 内部设计：聊天投稿状态机

## 状态与入口

`handlers/conversation.py` 注册唯一的 `ConversationHandler`：

```text
/submit 或“开始投稿”
        │
        ▼
UPLOAD ── /done_media 或 /skip_media ──▶ PREVIEW
  ▲                                      │   │
  └──────────── 补充媒体 ────────────────┘   ├─ 确认 → 发布/审核
                                             └─ 编辑 → EDIT → PREVIEW
```

状态常量在 `models/state.py`；处理器分别位于 `handlers/upload.py`、
`handlers/preview_handlers.py` 和 `handlers/publish.py`。

## 持久化边界

- 投稿字段的真相是 SQLite `submissions` 行；读写集中在 `utils/submission.py`。
- ConversationHandler 状态由 `PicklePersistence` 保存到数据库同目录的
  `persistence.pickle`，正常重启、Fly auto-stop 和发版后可以恢复会话。
- `SESSION_TIMEOUT` 到期后会清理过期会话；恢复能力不等于永久保留。
- 多 Bot 为每个 Bot 使用独立的 `data/botN/`，数据库与 persistence 不共享。

## 消息归类与发布

`utils.submission.classify_message()` 是媒体/文档归类的唯一实现。发布与审核预览共用
`handlers.publish.deliver_items_to_chat()`：图片/视频按最多 10 个分组，GIF、音频和
文档按类型发送，后续消息回复上一段，caption 只放在首条。

## 修改规则

改状态机时至少跑：

```bash
./.venv/bin/python -m pytest -q --no-cov -o log_cli=false \
  tests/test_conversation_flow.py tests/test_run_mode.py tests/test_shutdown.py
```
