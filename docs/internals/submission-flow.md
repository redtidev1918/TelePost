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
  `persistence.pickle`，正常重启和发版后可以恢复会话。
- `SESSION_TIMEOUT` 到期后会清理过期会话；恢复能力不等于永久保留。
- 多 Bot 为每个 Bot 使用独立的 `data/botN/`，数据库与 persistence 不共享。

## 消息归类与发布

`utils.submission.classify_message()` 是媒体/文档归类的唯一实现。发布与审核预览共用
`handlers.publish.deliver_items_to_chat()`：图片/视频按最多 10 个分组，GIF、音频和
文档按类型发送，后续消息回复上一段，caption 只放在首条。

## 审核发布与失败恢复

`pending_reviews` 状态机（聊天投稿确认、API/PixivFlow 投稿都汇入）：

```text
pending ──点「发布」──▶ publishing ──成功──▶ published
   ▲                       │
   │                       └──失败──▶ failed ──点「重试发布」──▶ publishing
   └──────────────── pending/failed 都可被 approve 重新认领
```

- 认领用一条 `UPDATE ... WHERE id=? AND status IN ('pending','failed')` 完成，
  并发点击只有一个能抢到。
- **僵尸解锁**：发布中途进程崩溃会把行卡在 `publishing`。approve 的认领 SQL 额外
  允许 `status='publishing' AND now-updated_at > PUBLISHING_STALE_SECONDS`
  （默认 300s）被重新认领，重启后点「重试发布」即可恢复，无需手改数据库。
- 发布失败时控制消息重挂键盘，主按钮标为「🔄 重试发布」（callback 仍是
  `review_approve`，对 `failed` 行可重入）。

## `discussion` 多图发布编排

`CHANNEL_ALBUM_REPLY=discussion` 时，`deliver_items_to_chat()` 进入
`_deliver_discussion()`，分三阶段，**每一步发出的消息都登记，失败完整回滚**：

1. **频道首贴**：只发第 1 张 + caption（`reply_mode="post"`）。
2. **等自动转发锚点**：Webhook 入队前 `capture_discussion_forward()` 抓
   `is_automatic_forward` 更新，得到讨论组里那条转发消息的 id。
3. **评论相册**：其余图片以相册回复该锚点（`on_sent` 回调收集已落地消息）。

失败语义（`DiscussionPublishError`）：

- **确定态失败**（等转发超时、未关联讨论组、非网络错误、回滚删净）：先删干净
  已落地的「频道首贴 + 讨论组锚点 + 已发相册」，再**自动重试一次**。
- **首贴响应丢失**（`NetworkError` 且不知是否到频道）：查 `_recent_forwards`
  近期自动转发；Telegram 实际收下就连首贴带锚点删干净 → 回到确定态 → 重试。
- **评论相册响应丢失**（`uncertain=True`）：无法判断相册是否已建成，重试会重复
  整个相册，因此**不自动重试**；回滚能确定的部分后提示人工核对频道首贴与评论串。
  这是唯一需要人看的情况，文案明确指到评论串而非笼统的「检查频道」。

回滚删除容忍「消息已不存在」（视为成功）；删不净则升级为 `uncertain` 交人工。

## 修改规则

改状态机时至少跑：

```bash
./.venv/bin/python -m pytest -q --no-cov -o log_cli=false \
  tests/test_conversation_flow.py tests/test_run_mode.py tests/test_shutdown.py
```
