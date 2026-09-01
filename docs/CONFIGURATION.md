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
| `RUN_MODE` | `AUTO` | `AUTO` / `POLLING` / `WEBHOOK`；AUTO 有有效公网 HTTPS URL 时选 Webhook，否则 Polling |
| `WEBHOOK_URL` | 空 | Webhook 公网入口，如 `https://app.fly.dev`；AUTO 模式下可留空 |
| `WEBHOOK_PORT` / `WEBHOOK_PATH` | `8080` / `/webhook` | 监听端口与路径 |
| `WEBHOOK_SECRET_TOKEN` | 自动生成 | Telegram 回调校验令牌 |
| `SEARCH_ENABLED` | `true` | 关闭后完全不建/写索引 |
| `SEARCH_INDEX_DIR` | `data/search_index` | 索引目录 |
| `SEARCH_ANALYZER` | `jieba` | `jieba`（未安装时自动回退 `simple`）/ `simple` |
| `SEARCH_HIGHLIGHT` | `false` | 搜索结果高亮 |
| `DB_CACHE_KB` | `4096` | SQLite page cache（KB） |
| `TIMEOUT` | `300` | 过期投稿数据清理截止（秒） |
| `SESSION_TIMEOUT` | `900` | 投稿会话不活动超时（秒），超时清理会话并提示 |
| `HEALTH_PORT` | `8080` | Polling 模式健康检查端口（多 bot 时自动错开为 8081/8082/…） |
| `DB_PATH` | `data/submissions.db` | 数据库文件路径（多 bot 时默认按 bot 隔离） |
| `RUNTIME_POLICY_PATH` | 与数据库同目录的 `runtime-policy.json` | `/botconfig` 保存的非敏感运行策略；多 bot 自动隔离，优先于部署环境变量 |
| `API_ENABLED` | `true` | 是否启用 HTTP API（Polling/Webhook 均支持 `/api/v1`，供外部项目投稿） |
| `API_REVIEW_REQUIRED` | `false` | HTTP API 投稿是否进入审核队列 |
| `CHAT_REVIEW_REQUIRED` | `false` | Telegram `/submit` 投稿是否进入审核队列 |
| `REVIEW_CHAT_ID` | 空 | 私有审核群 ID；任一审核开关为 `true` 时必填 |
| `REVIEW_PREVIEW_INTERVAL_SECONDS` | `0.75` | 多文件审核预览发送间隔；低配实例建议保持默认值以减少 Telegram flood |
| `REVIEW_PREVIEW_TIMEOUT_SECONDS` | `120` | 单个审核预览上传/响应超时；大图、多页作品或慢网络不要设得过低 |
| `REVIEW_PREVIEW_THREAD` | `1` | 审核群后续相册/文件/控制消息是否回复上一批（回复链）；置 `0` 取消回复关系 |
| `PENDING_REVIEW_RETENTION_DAYS` | `0` | 待审核投稿自动过期天数；`0` 表示永久保留。过期会删除审核群预览并保留轻量审计记录；Telegram Bot API 仅保证删除 48 小时内消息，需要清群时建议设 `1` |
| `PENDING_REVIEW_CLEANUP_BATCH_SIZE` | `100` | 每轮最多过期的待审核投稿数（1–200），避免集中调用 Telegram 删除接口 |
| `REVIEW_RETENTION_DAYS` | `30` | 已发布、已删除、拒绝、失败或过期记录的数据库保留天数；不负责待审核队列过期 |
| `PIXIVFLOW_ENABLED` | `false` | 多 Bot supervisor 是否同时监督 PixivFlow 子进程 |
| `PIXIVFLOW_CONFIG` | `/app/data/pixivflow/config.json` | 持久化配置路径；支持文件监听热重载 |
| `PIXIVFLOW_CONFIG_TEMPLATE` | npm 包内双 Bot 模板 | 首次启动时复制到持久卷的模板路径 |
| `PIXIVFLOW_COMMAND` | `pixivflow scheduler` | PixivFlow 子进程命令，通常无需修改 |
| `PIXIV_DB_CACHE_KB` | `8192` | PixivFlow SQLite 页缓存；512 MiB 联合档建议 `4096` |

## 多 bot 模式（BOT{n}_*）

设置 `BOT1_TOKEN` 即进入多 bot 模式：容器入口 `run.py` 会为每个 bot 派生独立子进程，
数据目录自动隔离为 `data/botN/`（数据库与搜索索引互不干扰）。

每个子进程的 `/botconfig` 策略默认保存在 `data/botN/runtime-policy.json`。修改后只让
当前 Bot 子进程退出并由 supervisor 拉起，另一个 Bot 和 PixivFlow 不受影响；使用
`/botconfig reset` 可删除覆盖并恢复部署环境变量。

每个 bot 可用 `BOT{n}_<KEY>` 覆盖以下全局项：

| 前缀变量 | 覆盖目标 |
|---|---|
| `BOT{n}_TOKEN` / `BOT{n}_CHANNEL_ID` | 必填，各 bot 的凭据与目标频道 |
| `BOT{n}_OWNER_ID` / `BOT{n}_ADMIN_IDS` | 各 bot 的管理员 |
| `BOT{n}_SHOW_SUBMITTER` / `BOT{n}_NOTIFY_OWNER` / `BOT{n}_BOT_MODE` / `BOT{n}_ALLOWED_FILE_TYPES` | 各 bot 的行为开关 |
| `BOT{n}_DB_PATH` / `BOT{n}_SEARCH_INDEX_DIR` / `BOT{n}_SEARCH_ENABLED` / `BOT{n}_SEARCH_ANALYZER` | 各 bot 的存储与搜索 |
| `BOT{n}_SUBMIT_LIMIT_PER_HOUR` / `BOT{n}_HEALTH_PORT` / `BOT{n}_TIMEOUT` | 各 bot 的限频/端口/超时 |
| `BOT{n}_API_REVIEW_REQUIRED` / `BOT{n}_CHAT_REVIEW_REQUIRED` / `BOT{n}_REVIEW_CHAT_ID` | 各 bot 的 API/聊天审核开关与私有审核群 |

Webhook 模式下回调路径自动分配为 `/webhook/botN`（详见 [WEBHOOK_MODE.md](WEBHOOK_MODE.md)）。

## config.ini

节与键与上表一一对应：`[BOT]`（TOKEN/CHANNEL_ID/OWNER_ID/ADMIN_IDS/BOT_MODE/RUN_MODE/ALLOWED_FILE_TYPES/SHOW_SUBMITTER/NOTIFY_OWNER/SUBMIT_LIMIT_PER_HOUR/API_REVIEW_REQUIRED/CHAT_REVIEW_REQUIRED/REVIEW_CHAT_ID/TIMEOUT/ALLOWED_TAGS/DB_PATH）、`[WEBHOOK]`（URL/PORT/PATH/SECRET_TOKEN）、`[SEARCH]`（ENABLED/INDEX_DIR/ANALYZER/HIGHLIGHT）、`[DB]`（CACHE_SIZE_KB）。
`ALLOWED_TAGS` 默认 30；`DB_PATH` 默认 `data/submissions.db`。完整带注释模板见 [`config.ini.example`](../config.ini.example)。

## 数据库

- `data/submissions.db`（WAL 模式）：`submissions`（进行中的聊天投稿会话）、`pending_reviews`（API/聊天审核记录与 Telegram `file_id`）、`api_notifications`（可跨重启的通知幂等记录）与 `published_posts`（已发布帖子）。`published_posts.is_deleted=1` 时，关联审核状态会同步为 `deleted`；`published` 不再包含已删除帖子。
- 备份时请连同 `-wal`/`-shm` 文件或先执行 checkpoint。

## 常见误区

- ❌ 只设置 `BOT_TOKEN`：旧版代码只读 `TOKEN` 会启动失败；当前版本两者皆可，但文档统一推荐 `TOKEN`。
- ❌ 数据目录无写权限：数据库/索引/日志均写在项目 `data/`、`logs/` 下。
- `jieba` 未安装时搜索引擎自动退回 `simple` 分词（整词匹配中文），见 [PERFORMANCE](PERFORMANCE.md)。

---
最后更新：2026-08
