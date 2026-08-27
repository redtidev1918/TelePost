# 配置参考

> 本文档与 `config/settings.py`、`config.ini.example` 逐项对齐。最后更新：2026-08

## 优先级

**环境变量 > config.ini > 内置默认值**。环境变量即使为空字符串也视为"已设置"。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `TOKEN` | 无（必填） | 机器人 Token。**兼容别名**：`BOT_TOKEN`、`TELEGRAM_BOT_TOKEN`（Fly.io 旧文档写的是 `BOT_TOKEN`，现已互通，推荐统一用 `TOKEN`） |
| `CHANNEL_ID` | 无（必填） | 目标频道，`@用户名` 或 `-100…` 数字 ID；兼容别名 `CHANNEL` |
| `OWNER_ID` | 无 | 所有者 User ID（整数），自动并入管理员列表 |
| `ADMIN_IDS` | 空 | 管理员 ID 列表，逗号分隔 |
| `BOT_MODE` | `MIXED` | `MEDIA` / `DOCUMENT` / `MIXED` |
| `ALLOWED_FILE_TYPES` | `*` | 文档模式允许的类型（扩展名或 MIME，逗号分隔） |
| `SHOW_SUBMITTER` | `true` | 发布的帖子是否显示投稿人 |
| `NOTIFY_OWNER` | `true` | 新投稿是否私聊通知所有者 |
| `SUBMIT_LIMIT_PER_HOUR` | `10` | 每用户每小时投稿次数上限，`0` 关闭 |
| `RUN_MODE` | `POLLING` | `POLLING` / `WEBHOOK` |
| `WEBHOOK_URL` | 空 | Webhook 模式必填，如 `https://app.fly.dev` |
| `WEBHOOK_PORT` / `WEBHOOK_PATH` | `8080` / `/webhook` | 监听端口与路径 |
| `WEBHOOK_SECRET_TOKEN` | 自动生成 | Telegram 回调校验令牌 |
| `SEARCH_ENABLED` | `true` | 关闭后完全不建/写索引 |
| `SEARCH_INDEX_DIR` | `data/search_index` | 索引目录 |
| `SEARCH_ANALYZER` | `jieba` | `jieba`（未安装时自动回退 `simple`）/ `simple` |
| `SEARCH_HIGHLIGHT` | `false` | 搜索结果高亮 |
| `DB_CACHE_KB` | `4096` | SQLite page cache（KB） |
| `TIMEOUT` | `300` | 过期投稿数据清理截止（秒） |
| `SESSION_TIMEOUT` | `900` | 投稿会话不活动超时（秒），超时清理会话并提示 |

## config.ini

节与键与上表一一对应：`[BOT]`（TOKEN/CHANNEL_ID/OWNER_ID/ADMIN_IDS/BOT_MODE/ALLOWED_FILE_TYPES/SHOW_SUBMITTER/NOTIFY_OWNER/SUBMIT_LIMIT_PER_HOUR/TIMEOUT/ALLOWED_TAGS/DB_PATH）、`[WEBHOOK]`（URL/PORT/PATH/SECRET_TOKEN）、`[SEARCH]`（ENABLED/INDEX_DIR/ANALYZER/HIGHLIGHT）、`[DB]`（CACHE_SIZE_KB）。
`ALLOWED_TAGS` 默认 30；`DB_PATH` 默认 `data/submissions.db`。完整带注释模板见 [`config.ini.example`](../config.ini.example)。

## 数据库

- `data/submissions.db`（WAL 模式）：`submissions`（进行中的投稿会话）与 `published_posts`（已发布帖子：`message_id` 主键、`publish_time`、`heat_score`、`related_message_ids`、`is_deleted` 软删标记等）。
- 备份时请连同 `-wal`/`-shm` 文件或先执行 checkpoint。

## 常见误区

- ❌ 只设置 `BOT_TOKEN`：旧版代码只读 `TOKEN` 会启动失败；当前版本两者皆可，但文档统一推荐 `TOKEN`。
- ❌ 数据目录无写权限：数据库/索引/日志均写在项目 `data/`、`logs/` 下。
- `jieba` 未安装时搜索引擎自动退回 `simple` 分词（整词匹配中文），见 [PERFORMANCE](PERFORMANCE.md)。

---
最后更新：2026-08
