# 内部设计：删帖与软删除

## 入口

- Owner 在搜索或个人投稿列表点删除并确认。
- Owner/Admin 使用 `/delete_posts <ID...>` 批量处理；支持单 ID、范围，单次最多 50 个。

二者最终都删除频道主消息及关联回复、移除搜索索引，并把
`published_posts.is_deleted` 设为 `1`。审核记录从 `published` 同步为 `deleted`，原
批准人和时间保留。

## 数据约定

- `published_posts.message_id` 是频道主消息 ID。
- `related_message_ids` 是 JSON 数组。
- 时间字段是 Unix 时间 `publish_time`。
- 数据库内部行号用 `rowid`；表中没有通用 `id` 或 `created_at`。

Telegram 不会向 Bot 推送“管理员在客户端删除频道消息”的事件，因此这种外部删除不会
自动同步数据库。应使用 TelePost 的删除入口，或随后手工软删除并同步索引。

## 恢复

恢复数据库可见性：

```sql
UPDATE published_posts SET is_deleted = 0 WHERE message_id = ?;
```

随后运行 `/sync_index`。频道原消息已经被 Telegram 删除时无法原地恢复，只能重新发布。
