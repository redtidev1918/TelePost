# Fly.io 部署

TelePost 在 Fly.io 必须使用 Webhook。默认推荐固定版本镜像、持久卷和自动休眠；
PixivFlow 有内部 Cron，必须拆到另一台常驻 Machine。

## 推荐拓扑

```text
┌──────────────────────────────┐
│ PixivFlow                    │
│ shared-cpu-1x · 256 MiB      │
│ always-on · min=1            │
│ scheduler / downloader       │
└──────────────┬───────────────┘
               │ HTTP via Flycast/Fly Proxy
               ▼
┌──────────────────────────────┐
│ TelePost                     │
│ shared-cpu-1x · 512 MiB      │
│ Bot 1 + Bot 2                │
│ auto-stop · auto-start · min=0│
└──────────────▲───────────────┘
               │ Telegram Webhook / API
```

- PixivFlow 在自己的 256 MiB Machine 常驻，才能按时运行 scheduler。
- TelePost 只处理入站请求，空闲时可完全停止。
- PixivFlow 使用 `<telepost-app>.flycast`，请求经过 Fly Proxy 后可唤醒 TelePost；不要用
  `.internal` 直连已停止的 Machine。
- 两个 App 与各自 Volume 放在同一区域，减少延迟和跨区域流量。

完整的拆分模板与初始化工具在
[pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy)。

## 单 Bot 部署

### 1. 准备

安装并登录 `flyctl`：

```bash
flyctl auth login
git clone https://github.com/redtidev1918/TelePost.git
cd TelePost
```

复制或直接编辑仓库内 [`fly.toml`](../fly.toml)，填写 `app` 和 `primary_region`。
区域应按用户延迟、容量和数据位置选择；各区价格可能变化，以
[Fly.io 定价](https://fly.io/docs/about/pricing/)为准，不在配置里假定“最便宜区域”。

### 2. 创建 App 与 Volume

```bash
flyctl apps create <app>
flyctl volumes create telepost_data --size 1 --region <region> --app <app>
```

Volume 与 Machine 必须同区。不要省略挂载：数据库、API Token、运行时策略和投稿会话
状态都在 `/app/data`。

### 3. 设置 Secrets

```bash
flyctl secrets set --app <app> \
  TOKEN='123456:replace-me' \
  CHANNEL_ID='@your_channel' \
  OWNER_ID='123456789' \
  WEBHOOK_URL='https://<app>.fly.dev'
```

需要审核时再加 `REVIEW_CHAT_ID` 与审核开关。Token 不要写入 `fly.toml`。

### 4. 部署固定版本

```bash
flyctl deploy --app <app> \
  --image ghcr.io/redtidev1918/telepost:2.10.39
```

TelePost 启动时会自行调用 Telegram `setWebhook`；不需要手工注册。

### 5. 验证

```bash
flyctl status --app <app>
flyctl logs --app <app>
curl -fsS https://<app>.fly.dev/health
curl -fsS https://<app>.fly.dev/api/v1/health
curl -fsS 'https://api.telegram.org/bot<TOKEN>/getWebhookInfo'
```

`/health` 返回 JSON；API 健康响应中的 `bot_version` 应等于部署版本。
`getWebhookInfo` 应核对 URL、`pending_update_count`、`last_error_date` 和
`last_error_message`，不要把完整响应连同 Token 贴到公开 Issue。

## 多 Bot TelePost

推荐直接使用部署套件的 `fly/telepost-split.toml`。核心 Secrets：

```bash
flyctl secrets set --app <telepost-app> \
  BOT1_TOKEN='...' BOT1_CHANNEL_ID='@channel_one' BOT1_OWNER_ID='123456789' \
  BOT2_TOKEN='...' BOT2_CHANNEL_ID='@channel_two' BOT2_OWNER_ID='123456789' \
  WEBHOOK_URL='https://<telepost-app>.fly.dev'
```

父路由监听 8080，Bot 子进程使用 8081、8082……；公网路径为：

- `/webhook/bot1`、`/webhook/bot2`
- `/api/bot1/v1/*`、`/api/bot2/v1/*`

双 Bot 生产实例使用 512 MiB，并在受限环境关闭搜索：

```toml
[env]
  RUN_MODE = "WEBHOOK"
  SEARCH_ENABLED = "false"
  SEARCH_ANALYZER = "simple"
  DB_CACHE_KB = "1024"

[http_service]
  internal_port = 8080
  auto_stop_machines = "stop"
  auto_start_machines = true
  min_machines_running = 0

[[vm]]
  cpu_kind = "shared"
  cpus = 1
  memory_mb = 512
```

Fly Proxy 的 autostop/autostart 只停止或启动现有 Machine，不会删除 Machine 或 Volume；
配置语义见 [Fly.io 官方配置参考](https://fly.io/docs/reference/configuration/)。

## 为什么自动休眠现在可用

从 TelePost 2.10.39 起：

- 正常关机只停止本地 HTTP 服务，不注销 Telegram Webhook。
- 冷启动重新注册 Webhook 时不丢弃待处理更新。
- 多 Bot 父路由会等待子进程端口最多 5 秒，避免刚唤醒时首个请求过早收到 502。
- Telegram Webhook、HTTP API 和 PixivFlow 的 Flycast 请求都会经过 Fly Proxy，触发
  `auto_start_machines=true`。

休眠期间 TelePost 内部定时任务不会运行。这不影响 Telegram/PixivFlow 入站投递；需要
准点执行的 scheduler 必须放在常驻的 PixivFlow App。

验证冷启动：

```bash
flyctl machine stop <machine-id> --app <app>
flyctl machine status <machine-id> --app <app>
curl -fsS -w 'time=%{time_total}s\n' https://<app>.fly.dev/health
```

最后再次检查两个 Webhook URL 和待处理数。

## PixivFlow 常驻 App

使用部署套件的 `fly/pixivflow-split.toml`。核心配置：

```toml
[env]
  PIXIV_DOWNLOADER_CONFIG = "/app/data/pixivflow/config.json"
  TELEPOST_API_BASE_URL = "http://<telepost-app>.flycast"
  NODE_OPTIONS = "--max-old-space-size=96 --expose-gc"

[[vm]]
  cpu_kind = "shared"
  cpus = 1
  memory_mb = 256
```

它没有 HTTP service 和 autostop 配置，Machine 保持运行。TelePost App 需要一次性分配
Flycast 私网地址：

```bash
flyctl ips allocate-v6 --private --app <telepost-app>
```

## 安全升级

1. 确认目标版本的 GitHub Release、GHCR amd64/arm64 manifest 和 CI 都成功。
2. 对 Volume 建 snapshot。
3. 更新原 Machine 的镜像，不重建 Volume。
4. 检查 Machine ID、Volume ID、内存和 autostop 配置未变化。
5. 检查 `/health`、每个 Bot 的 API health、Webhook 和 SQLite `PRAGMA quick_check`。

示例：

```bash
flyctl volumes snapshots create <volume-id> --app <app>
flyctl machine update <machine-id> --app <app> \
  --image ghcr.io/redtidev1918/telepost:<version> --yes
```

不要在有状态部署上用 `fly scale count 2` 做“高可用”：单个 Volume 不能同时挂到两台
Machine，两个进程也不能同时消费同一个 Telegram Token。

## 回退

将原 Machine 更新回上一固定版本镜像即可。只有数据库损坏或错误迁移时才从 snapshot
恢复；普通代码回退不要覆盖更新后的数据。更多检查见 [运维手册](OPERATIONS.md) 与
[故障排查](TROUBLESHOOTING.md)。
