# 运维手册

## 快速检查

```bash
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8080/api/v1/health       # 单 Bot
curl -fsS http://127.0.0.1:8080/api/bot1/v1/health  # 多 Bot 父路由
```

多 Bot 父路由的 `/health` 会汇总 Bot 序号、Python/Node RSS、系统可用内存、Volume、
API 临时上传、审核队列、PixivFlow cache 和 delivery outbox。单 Bot 子服务的
`/health` 只表示进程可用；版本看 `/api/v1/health`。

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

## Fly.io 省钱拓扑

- PixivFlow：256 MiB，常驻，自己的 Volume。
- TelePost：512 MiB 双 Bot，`auto_stop_machines="stop"`、
  `auto_start_machines=true`、`min_machines_running=0`。
- PixivFlow 向 `http://<telepost-app>.flycast/api/botN/v1/*` 投递，由 Fly Proxy 唤醒
  TelePost；不要使用 `.internal`。

TelePost 2.10.39+ 在停机时保留 Webhook，启动时不丢弃积压更新。完整配置见
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

必须确认 Machine ID、Volume ID、内存、镜像 digest 和 autostop 三项不变。回退只需更新
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
