# 运维手册

> 最后更新：2026-09

## 多 bot 部署（一台机承载多个频道）

- **原理**：容器入口 run.py 检测到 `BOT1_TOKEN`/`BOT2_TOKEN`/… 时，为每个 bot 派生独立子进程（互不影响，崩溃自动重启），webhook 模式下由内置路由按 /webhook/botN 路径分发到对应子进程端口
- **配置**：沿用 BOT{n}_TOKEN / BOT{n}_CHANNEL_ID / BOT{n}_OWNER_ID 系列变量；数据按 bot 隔离在 data/botN/（同卷不同目录）
- **省电**：RUN_MODE=WEBHOOK + auto_stop_machines=true + min_machines_running=0 → 空闲自动停机（计算费 0），来消息自动唤醒；代价是每条消息约 1-2 秒唤醒延迟，且统计定时任务只在机器醒着的时候跑
- **部署**：`fly deploy -c deploy.fly-multi-bot.toml --now`

## Fly 512 MiB：PixivFlow + TelePost 双 Bot

`deploy.fly-multi-bot.toml` 可构建联合镜像：TelePost 的 Python supervisor
维持 Bot1、Bot2 两个隔离子进程，并可选监督一个 `pixivflow scheduler` Node 子进程。
PixivFlow 内部的多个 Cron 共享认证、SQLite、文件服务，并串行下载，不再为每个
任务启动一套 Node 运行时。WebUI 不安装也不启动。

关键资源设置已经写进示例：`SEARCH_ENABLED=false`、`DB_CACHE_KB=1024`、
`PIXIV_DB_CACHE_KB=4096`、`NODE_OPTIONS=--max-old-space-size=128`。PixivFlow 模板
使用 `download.concurrency=1`、缓存交付和错峰 Cron。512 MiB 是经过约束后的运行
档，不应开启 jieba、搜索索引、WebUI或并发计划；如出现 OOM，应先升至 1 GiB，
不要用减少重试或删除 outbox 换取表面稳定。

### 部署顺序

1. 创建并挂载一个持久卷到 `/app/data`。内部 Cron 无外部请求可唤醒机器，所以
   必须保持 `auto_stop_machines=false`、`min_machines_running=1`。
2. 配置双 Bot 与 Pixiv 凭据后部署：

   ```bash
   fly secrets set -a <app> \
     BOT1_TOKEN=... BOT1_CHANNEL_ID=... BOT1_OWNER_ID=... \
     BOT2_TOKEN=... BOT2_CHANNEL_ID=... BOT2_OWNER_ID=... \
     PIXIV_REFRESH_TOKEN=... \
     WEBHOOK_URL=https://<app>.fly.dev
   fly deploy -a <app> -c deploy.fly-multi-bot.toml --now
   ```

3. 分别在两个 Bot 私聊执行 `/gen_token`，得到各自的 `tp_...`，再写入：

   ```bash
   fly secrets set -a <app> \
     TELEPOST_BOT1_SUBMIT_TOKEN=tp_... \
     TELEPOST_BOT2_SUBMIT_TOKEN=tp_...
   ```

   若只让 PixivFlow/API 投稿进审核群、普通聊天投稿仍按原流程发布，再按 Bot 设置：

   ```bash
   fly secrets set -a <app> \
     BOT1_API_REVIEW_REQUIRED=true BOT1_CHAT_REVIEW_REQUIRED=false BOT1_REVIEW_CHAT_ID=-100... \
     BOT2_API_REVIEW_REQUIRED=true BOT2_CHAT_REVIEW_REQUIRED=false BOT2_REVIEW_CHAT_ID=-100...
   ```

4. 首次启动会把 PixivFlow 双 Bot 模板复制到持久卷；两个计划默认关闭，避免占位
   tag 误执行。下载、修改 `TAG_A`～`TAG_D`、Cron 和 `enabled`：

   ```bash
   fly ssh sftp get -a <app> /app/data/pixivflow/config.json ./pixivflow.json
   # 本地编辑并用 python3 -m json.tool pixivflow.json 校验
   ./scripts/update_pixivflow_config.sh <app> ./pixivflow.json
   ```

上传脚本先写 `.upload` 临时文件，再在同一卷内 `mv`，避免半截 JSON。PixivFlow
看到替换后会完整校验并原子切换调度表；错误 Cron、重复 id、未知 target id 会被
拒绝，旧计划继续运行。可热更新 `schedules`、`targets`、`delivery`、`download`；
修改 Pixiv 凭据、代理或存储路径后需 `fly machine restart`。

### 从 Mac 远程切换时间、tag、Bot 和频道

PixivFlow 策略是真热重载。先把线上配置拉回 Mac，编辑后原子上传：

```bash
fly ssh sftp get -a <app> /app/data/pixivflow/config.json ./pixivflow.json
# 修改 target.filterTag、schedule.cron/enabled/targetIds；修改
# target.delivery.target 可在 telepost-bot1 / telepost-bot2 之间切换。
python3 -m json.tool ./pixivflow.json >/dev/null
./scripts/update_pixivflow_config.sh <app> ./pixivflow.json
```

上传后正在运行的任务不被中断，新配置从下一次触发生效。临时只运行一次的计划可
设 `maxExecutions: 1`；要让今天与明天使用不同 tag/时刻，也可以预先建立两个
schedule，分别设置不同 Cron 与 `targetIds`，不必每天登录服务器。

TelePost 的 Bot Token 永远保留在 Fly Secret 中。频道和审核群策略从 Mac 使用
非敏感 JSON 更新，示例见 `config/fly-telepost-policy.example.json`：

```bash
cp config/fly-telepost-policy.example.json ./telepost-policy.json
# 编辑 channelId / reviewChatId / 两个审核开关
./scripts/update_telepost_policy.sh <app> ./telepost-policy.json
```

频道策略会先完整校验、再作为 staged secrets 一次部署，因此只重启 Machine 一次，
无需重建镜像，SQLite、PixivFlow 配置和 outbox 都保留。频道切换前应先处理完旧审核
群里的 pending 项；pending 项在批准时会使用切换后的当前频道配置，否则旧稿可能
误发到刚切换的新频道。

### 在 Telegram 中更新当前 Bot

只有该 Bot 的 `OWNER_ID` 可以使用：

```text
/botconfig
/botconfig channel @频道用户名
/botconfig channel -1001234567890
/botconfig review here
/botconfig review -1001234567890
/botconfig api_review on|off
/botconfig chat_review on|off
/botconfig show_submitter on|off
/botconfig reset
```

`/botconfig` 面板也可直接切换三个布尔开关，或把当前群设为审核群。频道和群组会先
通过 Telegram 校验类型、Bot 管理员身份及频道发帖权限；切频道、切审核群或恢复部署
配置时，如果仍有 pending 投稿会拒绝操作。策略原子写入当前 Bot 的持久数据目录，
随后只重载该 Bot 子进程，通常约 6 秒恢复，不重启另一个 Bot、PixivFlow 或 Fly Machine。

这里的 `show_submitter off` 表示所有投稿都不显示投稿人；保持为 `on` 时，投稿者仍可
在发布预览中逐稿打开“匿名”。Bot Token、Owner、管理员列表和 Webhook 密钥故意不允许
从 Telegram 修改。

每个 tag 的“昨日最热插画 + 小说”需要两条 target；tag 列表增加一项时，复制这
一对 target，设置唯一 `id`，再把 id 加入对应 schedule 的 `targetIds`。模板使用
`rankingDate: "YESTERDAY"`，会在每天实际执行前重新计算日期。

### 观测与回退

```bash
curl https://<app>.fly.dev/health
fly logs -a <app>
fly machine status -a <app> <machine-id>   # 检查 OOM 事件
```

`/health` 返回 Python/Node 各进程 RSS、系统可用内存、卷使用率、PixivFlow cache、
delivery outbox 待处理文件数/大小、累计尝试次数、最老任务年龄和 API 临时上传大小。目录统计缓存 15 秒，避免
Fly 健康探针频繁遍历文件。建议正常空闲时至少留 100 MiB 可用空间；下载峰值后
持续低于 60 MiB 或出现 OOM 时升级到 1 GiB。

## 发布流程（Tag → GHCR 镜像 → GitHub Release）

1. 把 `CHANGELOG.md` 的 `[Unreleased]` 内容整理进新版本段 `## [x.y.z] - 日期`
2. 必要时同步 `utils/helper_functions.py` 的 `CONFIG["VERSION"]`
3. 发布：
   ```bash
   git tag vx.y.z
   git push origin main --tags
   ```
4. GitHub Actions 自动完成：构建 amd64/arm64 镜像 → 推送
   `ghcr.io/redtidev1918/telepost:{x.y.z, x.y, latest}` → 创建 GitHub Release
   （正文取 CHANGELOG 对应版本段，缺失时回退 `[Unreleased]`）
5. 首次发布后：GitHub → Packages → `telepost` → Package settings 改为 Public（否则匿名 `docker pull` 需 `docker login ghcr.io`）

`docker-compose.yml` 已内置 `image: ghcr.io/redtidev1918/telepost:latest`，
不想本地构建的用户删掉 `build:` 段即可直接拉镜像运行。

### 一键部署（推荐）

Fly.io / Docker Compose 部署与升级可用跨平台一键工具
`pixivflow-telepost-deploy/deploy.py`（Python 3.8+ 标准库，macOS/Linux/Windows）：

```bash
python3 deploy.py tp 2.10.33   # 升级 TelePost 到 2.10.33 并部署
python3 deploy.py tp latest    # 升级到最新并部署
python3 deploy.py status       # 状态/健康
python3 deploy.py doctor       # 环境自检
```

见 [pixivflow-telepost-deploy README](https://github.com/redtidev1918/pixivflow-telepost-deploy#一键部署工具deploypy)。

## 启动 / 停止 / 重启

```bash
./start.sh          # 前台或后台启动（以脚本内实现为准）
./restart.sh        # 重启
./update.sh         # 拉取更新并重启
```
Docker 部署使用 `docker compose {up -d|restart|down}`；systemd 部署用 `systemctl` 管理。

## 日志

- 位置：`logs/`；每天 03:00 自动清理过期日志（`main.py` 定时任务）。
- `/health`：Polling 与 Webhook 都提供健康检查和投稿 API；多 Bot 父路由在两种模式下都保持 `/api/botN/v1/*` 地址不变。

## 数据库维护

`cleanup_old_data()` 只删除过期会话、超过 `REVIEW_RETENTION_DAYS` 的
已决审核记录和 API 通知幂等记录；未决 `pending` 审核稿由
`PENDING_REVIEW_RETENTION_DAYS` 单独控制。通知表不保存媒体，通常只占用少量 SQLite
页面。

- **备份**：先 `sqlite3 data/submissions.db "PRAGMA wal_checkpoint(FULL);"`，再拷贝 `submissions.db`（连同 `-wal`/`-shm` 更稳妥）。
- **优化**：`python3 optimize_database.py`（VACUUM/ANALYZE）。
- **重复数据清理**：`python3 cleanup_duplicates.py`（先 `--help` 核对参数）。
- **统计诊断**：`python3 diagnose_stats.py`。

## 搜索索引维护

```bash
python3 -m utils.index_manager status     # 查看索引与库的差异
python3 -m utils.index_manager sync       # 增量同步
python3 -m utils.index_manager rebuild    # 全量重建（可加 --no-clear）
python3 -m utils.index_manager optimize   # 合并索引段
```
对应机器人内管理员命令：`/index_stats` `/sync_index` `/rebuild_index` `/optimize_index`。
何时需要重建：Schema 变更、`status` 显示持续不同步、搜索结果明显缺失。

## 内置定时任务（main.py）

| 任务 | 周期 | 说明 |
|---|---|---|
| 过期投稿清理 | 每 5 分钟 | 删除 `TIMEOUT`（默认 300s）前的 submissions |
| 帖子统计更新 | 不启用 | Bot API 无法无副作用回读任意频道帖的浏览/转发统计 |
| 已删除消息检查 | 不启用 | Bot API 不推送频道删帖事件；TelePost 自身删帖会同步软删除 |
| 日志清理 | 每天 03:00（`TZ`） | 仅主 Bot 注册；文件操作在线程中执行，不阻塞更新处理 |
| PixivFlow 维护 | 每天 04:00（`TZ`） | 仅主 Bot 注册；子进程在线程中等待，最长 15 分钟 |

JobQueue 会 `await` 所有回调，因此定时任务入口必须是 `async def`。耗时的同步文件或
子进程操作应通过工作线程执行；否则会出现 `NoneType can't be used in 'await'`
或阻塞 Telegram 更新处理。多 Bot supervisor 通过 `TELEPOST_PRIMARY_BOT` 保证全局
维护只注册一次。
维护时间是带时区的 wall-clock 时间，默认 `Asia/Shanghai`；不会再被 JobQueue 当作
UTC 而在北京时间 11:00/12:00 执行。无效 `TZ` 会记录警告并回退到 UTC。

## 迁移与配置工具

`migrate_to_search.py` / `migrate_add_filename.py` / `migrate_extract_filenames.py`（一次性迁移）、`check_config.py`（配置自检）、`setup_wizard.py`（交互式配置）、`scripts/crawl_channel_history.py`（频道历史抓取）。

---
最后更新：2026-09
