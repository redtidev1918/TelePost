# 运维手册

## 快速检查

```bash
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8080/live
curl -fsS http://127.0.0.1:8080/ready
curl -fsS http://127.0.0.1:8080/version    # 当前版本 + 提交号
curl -fsS http://127.0.0.1:8080/api/v1/health       # 单 Bot
curl -fsS http://127.0.0.1:8080/api/bot1/v1/health  # 多 Bot 父路由
```

`/live` 只证明 runtime 活着；`/ready` 还要求数据库迁移、Bot、投稿服务和审核恢复完成。
多 Bot 父路由的 `/health` 会汇总 Bot 序号、Python/Node RSS、系统可用内存、Volume、
API 临时上传、审核队列、PixivFlow cache 和 delivery outbox。单 Bot 子服务的
`/health` 只表示进程可用。`/version` 与 `/health` 都带 `version`/`commit`，
可以直接确认线上跑的是哪个发行版和哪次提交（Docker 镜像由发布流水线注入）。

多 Bot 父路由还提供人类可读状态页 `GET /status`（纯文本，无鉴权），汇总各 Bot
子服务的最近终态通知：`bot1-daily  last 2026-09-14 19:49 UTC · partial`。适合频道
置顶/收藏，用户不用等时点才知道那次更新是否发了。单 Bot 子服务继续用
`GET /api/v1/schedule/status` 返回同样的 JSON。

## 计划观察（schedule observability）

每天 10:00/22:00 等发布时点由 PixivFlow 执行，TelePost 收到的每条终态
（success/partial/failed）都通过 schedule outbox 投递到审核群。为避免「没等到作品
也没收到通知」的静默故障，TelePost 2.38.0 起增加：

- **前置告知**：空待发池时终态消息增加「前瞻：待发池为空，下一发布时点若仍无新作则
  无法按时更新」，运营提前知晓下一时点仍可能空。
- **Watchdog**：独立周期任务每 30 分钟检查一次，任何 schedule 超过
  `SCHEDULE_WATCHDOG_MAX_HOURS`（默认 `26`）没有新的终态通知，就直接向审核群发一条
  静默告警（按 UTC 日期幂等，每天最多一条），不依赖正常通知链路。
- **状态查询**：`GET /api/botN/v1/schedule/status` 与公共 `/status` 页可随时确认
  「最近一次更新是什么时候、什么状态」。
- **Telegram 命令**：`/status`（公开）在任意对话里查看最近计划终态；
  `/pin_status`（仅 OWNER）把状态消息发送并置顶到当前群/频道，作为「频道置顶区」
  的落地方式。

## 审计与可观测性

每条投稿的完整流水都落进本机 SQLite 表 `audit_events`（每个 Bot 一个库），
日志被轮转或进程重启后依然可查：

| 事件 | 含义 |
| --- | --- |
| `submission.received` / `submission.accepted` / `submission.duplicate` | 收到投稿、被接收、判定为重复（含复用的审核号与原因） |
| `review.created` / `review.preview_staged` / `review.control_created` / `review.pending` | 审核落库、预览与按钮发出、进入待审 |
| `review.approved` / `review.rejected` / `review.failed` | 审核决定或失败（带 `error_class`） |
| `publish.started` / `publish.completed` / `publish.duplicate_suppressed` | 发布开始、成功、重复抑制 |
| `review.reconciled` | 启动修复：补发控制消息、清理无按钮预览、需要人工介入 |
| `media.prepared` | 每张图的处理结论：尺寸、体积、解码预算、最终方式与原因 |

每行都带 `review_id`、`pixiv_id`、`target_id`、`idempotency_key`，
以及来自上游的 `execution_id`，可以把下载、投递、审核、发布串起来对账。

- 日志里每张图还会输出一行 `media decision {...}`，`decision` 取
  `photo_passthrough` / `safe_compress` / `use_preview` / `document_fallback`，
  `reason` 说明原因（体积超限、解码预算超限、尺寸超过 Telegram 照片上限等）。
- 失败会带类型化 `error_class`（如 `dependency_not_ready`、`network_timeout`、
  `telegram_send_failed`），「上游还没就绪」与「真正失败」区分记录。
- 令牌、密钥、请求头在落盘前统一脱敏。
- 保留期由 `AUDIT_RETENTION_DAYS`（默认 30 天）控制；仍在进行中的审核，
  其审计事件不会被清理。

查询某条审核的完整流水：

```bash
python -m telepost.observability.cli reviews inspect <review_id>
python -m telepost.observability.cli reviews inspect <review_id> --bot 2
```

## 启停与日志

| 环境 | 启动/更新 | 日志 |
|---|---|---|
| 源码 | `./.venv/bin/python run.py` | 前台输出或 `logs/` |
| 脚本安装 | `./start.sh`、`./restart.sh`、`./update.sh` | `logs/` |
| Compose | `docker compose up -d` | `docker compose logs -f telepost` |
| Fly.io | `flyctl machine start <id> -a <app>` | `flyctl logs -a <app>` |

同一个 Telegram Token 只能由一个运行实例消费。排障时不要同时启动源码、容器和旧
Machine。

## 持久数据

| 内容 | 默认位置 |
|---|---|
| SQLite | `data/submissions.db` 或 `data/botN/submissions.db` |
| 会话状态 | 数据库同目录的 `persistence.pickle` |
| `/botconfig` 策略 | 数据库同目录的 `runtime-policy.json` |
| 搜索索引 | `data/search_index` 或 `data/botN/search_index` |
| API 临时上传 | `data/api_uploads/` |
| PixivFlow 数据 | 通常 `/app/data/pixivflow/` |

不要只备份数据库而遗漏 `runtime-policy.json` 和 `persistence.pickle`。多 Bot 要备份
整个 `data/`。

## 备份

SQLite 使用 WAL。停机备份最简单；在线备份使用 SQLite 自带 `.backup`：

```bash
mkdir -p backups
sqlite3 data/submissions.db ".backup 'backups/submissions.db'"
cp data/runtime-policy.json backups/ 2>/dev/null || true
cp data/persistence.pickle backups/ 2>/dev/null || true
```

多 Bot 对每个 `data/botN/submissions.db` 分别执行。若直接复制文件，先 checkpoint 并
同时保留 `-wal`、`-shm`。

Fly.io 每次部署前创建 snapshot：

```bash
flyctl volumes list --app <app>
flyctl volumes snapshots create <volume-id> --app <app>
flyctl volumes snapshots list <volume-id> --app <app>
```

snapshot 是回退保障，不替代异地备份。

## Fly.io 生命周期

TelePost 是用户可见的投稿入口，在 Fly.io 上保持常驻：`auto_stop_machines=false`、
`min_machines_running=1`。如果与 PixivFlow 等上游组合，让上游在独立应用中管理自己的调度、
资源和生命周期，不要把它们塞进 TelePost 容器。完整配置见
[FLYIO_DEPLOYMENT.md](FLYIO_DEPLOYMENT.md)。

## 安全升级与回退

### Compose

1. 备份 `data/`。
2. 把镜像改为固定新版本。
3. `docker compose pull && docker compose up -d`。
4. 检查健康、版本、日志和一次测试投稿。

### Fly.io

```bash
flyctl volumes snapshots create <volume-id> --app <app>
flyctl machine update <machine-id> --app <app> \
  --image ghcr.io/redtidev1918/telepost:<version> --yes
```

随后验证：

```bash
flyctl machine status <machine-id> --app <app> --display-config
curl -fsS https://<app>.fly.dev/health
curl -fsS https://<app>.fly.dev/api/bot1/v1/health
curl -fsS https://<app>.fly.dev/api/bot2/v1/health
```

必须确认 Machine ID、Volume ID、资源和生命周期配置不变，镜像 digest 对应目标新版本。
两个 Bot 的 API 健康端点均应返回目标版本。2.10.43 无数据库迁移，无需清理或重建历史数据；
升级前后的只读完整性检查均应为 `ok`。相册降级回复、聊天遮罩和混合文件保存的回归测试见
`tests/test_publish_reply.py`、`tests/test_publish_regressions.py` 和 `tests/test_streaming_uploads.py`。
回退只需更新
为上一版本镜像；除非数据本身损坏，不要用旧 snapshot 覆盖较新的数据库。

## 数据库完整性

停机或只读连接下执行：

```bash
sqlite3 'file:data/submissions.db?mode=ro' 'PRAGMA quick_check;'
```

Fly 多 Bot 示例：

```bash
flyctl ssh console --app <app> --command \
  "python -c 'import sqlite3; [print(p, sqlite3.connect(\"file:\"+p+\"?mode=ro\", uri=True).execute(\"PRAGMA quick_check\").fetchone()[0]) for p in (\"/app/data/bot1/submissions.db\", \"/app/data/bot2/submissions.db\")]'"
```

发现非 `ok` 时先停止写入、复制 Volume/snapshot，再分析；不要先跑 VACUUM。

## 搜索索引

在安装了项目依赖且配置有效的环境运行：

```bash
python -m utils.index_manager status
python -m utils.index_manager sync
python -m utils.index_manager rebuild
python -m utils.index_manager optimize
```

优先 `sync`；Schema 变化或索引损坏时再 `rebuild`。也可以用 Admin 命令
`/index_stats`、`/sync_index`、`/rebuild_index`、`/optimize_index`。

仓库中的历史维护脚本不是统一 CLI，有些脚本不支持 `--help` 且会立即操作默认数据；
运行前必须先阅读源码并备份。生产维护优先使用上面的 SQLite 和索引命令。

## `/botconfig`

Owner 可以在 Telegram 中修改频道、审核群、审核开关和署名开关。策略原子写入当前
Bot 的数据目录；多 Bot supervisor 会只拉起当前子进程。Token、Owner、Admin 和
Webhook Secret 不能通过 Telegram 修改。

切换频道或审核群前，先批准/拒绝所有 pending 投稿，否则命令会拒绝执行。

## 审核队列与 outbox

- `PENDING_REVIEW_RETENTION_DAYS=0` 表示待审永久保留；需要清理 Telegram 预览时建议 1。
- `REVIEW_RETENTION_DAYS` 只清理已决审计记录和 API 通知幂等记录。
- outbox 数量、失败数、累计重试和最老年龄持续增长，说明 TelePost/API 链路异常。
- 不要直接删 outbox 引用的缓存文件；先恢复投递，让上游完成重试。
- PixivFlow 使用 `pixivflow outbox list` / `inspect <id>` 查看状态，使用
  `retry <id>` 或 `retry --dead` 正式重放 dead letter，使用 `cancel <id>` 取消尚未开始的
  intent；不要手改 SQLite `next_attempt_at`，也不要用 `run-once` 代替 outbox retry。
- replay 保留原 idempotency key。TelePost `/ready` 非 200 时 PixivFlow 只延后，不增加 attempt。

## 正式发布

1. 工作区必须干净，`main` 与远端同步。
2. 更新 `utils/helper_functions.py` 版本与 `CHANGELOG.md` 对应版本段。
3. 运行完整测试。
4. 提交并推送 `main`，再创建并推送同版本 `vX.Y.Z` tag。
5. 等待 GitHub Actions 的 test、docker、三平台 bundle、release 全部成功。
6. 核对 Release 的三个资产和 GHCR 的 amd64/arm64 manifest。
7. 有生产环境时，按“安全升级”单独部署固定版本镜像并复核数据。

```bash
./.venv/bin/python -m pytest -q --no-cov -o log_cli=false
git tag vX.Y.Z
git push origin main
git push origin vX.Y.Z
gh run list --workflow release.yml --limit 3
gh release view vX.Y.Z
docker buildx imagetools inspect ghcr.io/redtidev1918/telepost:X.Y.Z
```

Tag 只触发流程，不等于发布完成。Release 缺资产时，从对应 run 下载 bundle 后用
`gh release upload vX.Y.Z <files> --clobber` 补传；不要仅因上传竞态重复发布版本。

## 热度统计

Telegram Bot API 不提供频道帖子的浏览量 / 转发量；这些字段显示为 0 是 API 边界，
不是故障。真实可用信号是频道 `message_reaction_count` 更新：用户对帖子增加或改变
reaction 后，Bot 把 `(message_id, total_count)` 写入 `message_reaction_counts`，
再聚合更新 `published_posts.reactions` 与 `heat_score`。

- Bot 必须是频道管理员，webhook `allowed_updates` 必须包含 `message_reaction_count`。
- 没有用户 reaction 时，计数表为空、热度为 0 是预期状态。
- 验证不要查看公开频道的浏览量，而是看 `message_reaction_counts` 是否出现真实更新。
- 每收到一次 Telegram reaction 更新，webhook 日志会出现
  `🔔 收到频道反应更新`；处理完成后出现 `Reaction projected`。
- `/health` 返回 `reaction_ingest.received_since_start`。该指标从进程启动起算，
  重启后归零；持久事实仍以数据库和日志为准。
- 常用排查命令：

```bash
fly logs -a telesubmit-multi-bot --no-tail | grep "Reaction ingest"
curl -s https://telesubmit-multi-bot.fly.dev/health | jq '.reaction_ingest'
```

```bash
fly ssh console -a telesubmit-multi-bot
```

```python
import sqlite3
c = sqlite3.connect('/app/data/bot1/submissions.db')
print(c.execute('SELECT COUNT(*) FROM message_reaction_counts').fetchone()[0])
```

- 若用户已加 reaction 但日志没有任何 `Reaction ingest`，说明 Telegram 没有推送
  或 webhook 入口失败；若有 ingest 但没有 `Reaction projected`，才查数据库投影。
