# 故障排查

先记录版本、部署方式和发生时间，再按症状检查。不要公开 Token、Webhook Secret 或
完整配置。

## Bot 完全无响应

1. 确认只有一个实例使用该 Token。
2. 看进程/Machine 状态和最近日志。
3. 请求 `/health` 与 `/api/botN/v1/health`。
4. Webhook 模式查看 `getWebhookInfo`。

```bash
curl -fsS https://<app>.fly.dev/health
curl -fsS https://<app>.fly.dev/api/v1/health       # 单 Bot
curl -fsS https://<app>.fly.dev/api/bot1/v1/health  # 多 Bot 的 Bot 1
curl -fsS 'https://api.telegram.org/bot<TOKEN>/getWebhookInfo'
```

重点看 URL、`pending_update_count`、`last_error_date`、`last_error_message`。部署或冷启动
期间的旧 502 可能继续显示；待处理数归零且新消息成功时，不是当前故障。

Fly auto-stop 必须满足：

```toml
auto_stop_machines = "stop"
auto_start_machines = true
min_machines_running = 0
```

并使用 TelePost 2.10.39+。更早版本停机时会删除 Webhook，Machine 随后没有唤醒来源。
PixivFlow 跨 App 投递应使用 `.flycast`，不是 `.internal`。

## 启动失败

| 错误 | 处理 |
|---|---|
| `TOKEN 未设置` | 设置 `TOKEN`；兼容别名仅用于迁移 |
| `CHANNEL_ID 未设置` | 设置 `@channel` 或 `-100…` |
| 审核已开启但无 `REVIEW_CHAT_ID` | 配置独立私有审核群 |
| `REVIEW_CHAT_ID 不能等于 CHANNEL_ID` | 把审核群与发布频道分开 |
| `config.ini 不存在` 警告 | 纯环境变量部署可忽略 |
| Bot 无发帖权限 | 把 Bot 设为频道管理员并允许发帖 |

多 Bot 的 `BOT1_TOKEN`、`BOT2_TOKEN` 必须从 1 连续编号；缺号后的 Bot 不会启动。

## Webhook 404/403/502

- 404：单 Bot 是 `/webhook`，父路由/多 Bot 是 `/webhook/botN`。
- 403：`WEBHOOK_SECRET_TOKEN` 与 Telegram 当前设置不一致。
- 502：父路由已等待子进程端口最多 5 秒；若仍失败，检查健康宽限、子进程崩溃和同一时段日志。
- 待处理数持续增加：查看同一时段应用日志，不要先 `deleteWebhook`。

切换配置后由 TelePost 重新 `setWebhook`。手工删除 Webhook 会让已停止的 Fly Machine
失去 Telegram 唤醒请求。

## Polling conflict

日志出现 conflict 通常表示第二个进程在 `getUpdates`。停止本地测试进程、旧容器或旧
Machine；同一 Token 不能同时 Polling，也不能同时使用 Polling 与 Webhook。

## HTTP API

| 状态 | 常见原因 |
|---|---|
| 401 | Token 缺失、错误或已吊销 |
| 404 | 多 Bot 地址漏了 `/api/botN` |
| 409 | 通知接口未配置审核群 |
| 413 | 单文件 50 MiB 或累计 500 MiB 上限 |
| 429 | `SUBMIT_LIMIT_PER_HOUR` |
| 502 | Telegram/审核群投递失败 |
| 超时 | 父路由、反代或客户端 timeout 低于大型上传耗时 |

幂等投稿收到非 2xx 时保留任务重试；不要在不确定服务端状态时直接丢弃 outbox。

## 投稿会话

- 会话外发媒体会提示先 `/submit`。
- `SESSION_TIMEOUT` 默认 900 秒；超时后重新 `/submit`。
- 正常重启和 auto-stop 会从 `persistence.pickle` 恢复状态。
- persistence 或 SQLite 无写权限时，先修复整个 `data/` 的所有者/挂载。
- 审核预览出现 FloodWait/timeout 时，保持默认节流和 120 秒超时，避免并发重发。

## 搜索与统计

- 搜索为空：确认 `SEARCH_ENABLED=true`，再运行 `python -m utils.index_manager status`。
- 索引不同步：先 `sync`，Schema 不匹配或损坏再 `rebuild`。
- 中文匹配太粗：安装 `jieba` 并设 `SEARCH_ANALYZER=jieba`。
- `/hot` 不更新实时浏览数是平台限制：Bot API 不能无副作用回读任意频道帖统计。

## OOM 或磁盘增长

```bash
curl -fsS https://<app>.fly.dev/health
flyctl machine status <machine-id> --app <app>
```

- 双 Bot 用 512 MiB；低配关闭搜索并把 `DB_CACHE_KB` 设为 1024。
- PixivFlow 拆到 256 MiB 常驻 Machine，计划错开且下载并发为 1。
- `api_uploads` 持续增长说明请求被强制中断。
- outbox 增长先修复投递；不要直接删除引用的缓存。
- Volume 使用率高时先扩容/备份，不要边写入边 VACUUM。

## 数据库

```bash
sqlite3 'file:data/submissions.db?mode=ro' 'PRAGMA quick_check;'
```

非 `ok`：停止写入、制作副本或 Volume snapshot，再修复。不要把旧 snapshot 直接覆盖
仍在更新的生产库。备份与回退见 [OPERATIONS.md](OPERATIONS.md)。

## 收集 Issue 信息

提供：TelePost 版本、部署方式、运行模式、单/多 Bot、问题时间、脱敏日志、健康响应和
复现步骤。隐藏所有 Token、频道私密链接、用户数据与 Webhook Secret。
