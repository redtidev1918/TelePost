# 内部设计：删帖与软删除

> 面向开发者。最后更新：2026-08

## 概览

删帖采用**软删除**：`published_posts.is_deleted` 置 1，保留历史记录；同时删除频道消息（含关联消息）与搜索索引条目。入口有两类：

1. OWNER 通过搜索/`/myposts` 列表的 🗑️ 按钮 → `delete_post_` 回调 → 确认键盘 → `confirm_delete_post_<id>` → `execute_delete_post`（`handlers/callback_handlers.py`）；
2. OWNER `/delete_posts` 批量命令 → `delete_posts_batch`（`handlers/search_handlers.py`），支持 `100-110 150` 范围+单 ID 混合，单次上限 50。

## 执行步骤（execute_delete_post）

1. `SELECT rowid AS post_id, message_id, related_message_ids, is_deleted ... WHERE message_id=?`（不存在/已删则提前返回）；
2. 删除频道主消息与 `related_message_ids` 中的关联消息；"消息不存在/无法删除"视为已达目标；
3. `search_engine.delete_post(message_id)` 移除索引（含关联消息；`SEARCH_ENABLED=false` 时跳过）；
4. `UPDATE published_posts SET is_deleted=1 WHERE rowid=?`；数据库触发器同步将关联
   `pending_reviews.status` 从 `published` 改为 `deleted`；
5. 汇总各步结果回复 OWNER。

## 频道外部删帖限制

Telegram Bot API 不提供无副作用读取任意频道消息或接收频道删帖事件的接口，因此不再通过
“转发给 OWNER 后立即删除”探测消息是否存在。由 TelePost 的删除入口执行时会正常软删除；若在
Telegram 客户端直接删除频道消息，数据库不会自动感知，可用 `/delete_posts` 同步软删除记录。

## 表结构相关列

`published_posts`：`message_id`（频道消息 ID，主键）、`related_message_ids`（JSON 数组，多组媒体的其余消息）、`is_deleted`（0/1，默认 0）、`publish_time`（Unix REAL）。
注意：表中没有 `id`/`created_at` 列——按库内 ID 查询用 `rowid`，时间用 `publish_time`。

`pending_reviews`：`published_message_id` 关联频道主消息。`status=published`
表示审核后发布且当前未软删除；频道消息软删除后为 `deleted`。
`decided_at` / `decided_by` 保留原批准审核信息，不会被删除时间覆盖。

## 恢复

```sql
UPDATE published_posts SET is_deleted = 0 WHERE message_id = ?;
```
随后 `/sync_index` 重建索引条目；频道消息如需恢复须另行重发（机器人不缓存媒体原文以外的 file_id 之外的下载副本）。

---
最后更新：2026-08
