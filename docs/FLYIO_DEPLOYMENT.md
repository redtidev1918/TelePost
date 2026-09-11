# Fly.io 部署

TelePost 在 Fly.io 使用 Webhook 模式并**常驻**运行：投稿要求即时响应，平台侧空闲停机
带来的冷启动延迟不可接受。省成本由独立的 PixivFlow 执行端承担（平时停止、按需唤醒）。

两份 Fly 配置、Secrets 与部署脚本的完整契约在
[pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy)；
本页只说明 TelePost 侧的部署与核对。

## 拓扑

```text
┌────────────────────────────────┐
│ PixivFlow（执行端）             │
│ shared-cpu-1x · 256 MiB        │
│ 平时 stopped · 按需唤醒 · 独立卷 │
│ scheduler / downloader         │
└───────────────┬────────────────┘
                │ HTTP via Flycast
                ▼
┌────────────────────────────────┐
│ TelePost（业务端）              │
│ shared-cpu-1x · 512 MiB        │
│ 常驻 · min=1 · 双 Bot           │
└───────────────▲────────────────┘
                │ Telegram Webhook / API
```

- TelePost 常驻（`auto_stop_machines = false`、`min_machines_running = 1`）：它持有频道
  发布凭据与审核队列，停机期间的投稿无法及时响应。
- PixivFlow 独立成 App，用「平时停止、按需唤醒」省钱。不要把它和 TelePost 合到一台
  机器：那会共用内存、进程生命周期与故障域，是历史混部问题的根源。
- 两个 App 与各自 Volume 放在同一区域，减少延迟和跨区域流量。

## 单 Bot 部署

### 1. 准备

安装并登录 `flyctl`：

```bash
flyctl auth login
git clone https://github.com/redtidev1918/TelePost.git
cd TelePost
```

复制或直接编辑仓库内 [`fly.toml`](https://github.com/redtidev1918/TelePost/blob/main/fly.toml)，填写 `app` 和 `primary_region`。
区域应按用户延迟、容量和数据位置选择；各区价格可能变化，以
[Fly.io 定价](https://fly.io/docs/about/pricing/)为准，不在配置里假定“最便宜区域”。

### 2. 创建 App 与 Volume

```bash
flyctl apps create <app>
flyctl volumes create data --size 1 --region <region> --app <app>
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
  --image ghcr.io/redtidev1918/telepost:<version>
```

`<version>` 取 [Releases](https://github.com/redtidev1918/TelePost/releases) 中的具体标签。
TelePost 启动时会自行调用 Telegram `setWebhook`；不需要手工注册。

### 5. 验证

```bash
flyctl status --app <app>
flyctl logs --app <app>
curl -fsS https://<app>.fly.dev/live
curl -fsS https://<app>.fly.dev/ready
curl -fsS https://<app>.fly.dev/api/v1/health
curl -fsS 'https://api.telegram.org/bot<TOKEN>/getWebhookInfo'
```

三个探针语义不同：

| 端点 | 语义 | 用途 |
|---|---|---|
| `/live` | 路由进程存活即 200，恒不阻塞 | 存活探针、外部保活 ping |
| `/ready` | 数据库迁移、Bot、投稿服务和审核恢复完成才 200，未就绪时 503 | 上游投递的消费屏障；**不是** Fly 健康检查路径 |
| `/health` | 路由存活 + 容量/存储指标，恒 200 | Fly 长期健康检查与观测入口，**不**代表业务就绪 |

API 健康响应中的 `bot_version` 应等于部署版本。
`getWebhookInfo` 应核对 URL、`pending_update_count`、`last_error_date` 和
`last_error_message`，不要把完整响应连同 Token 贴到公开 Issue。

## 多 Bot TelePost

多 Bot 直接使用部署套件的 `fly/deploy.telepost.toml`。核心 Secrets：

```bash
flyctl secrets set --app <telepost-app> \
  BOT1_TOKEN='...' BOT1_CHANNEL_ID='@channel_one' BOT1_OWNER_ID='123456789' \
  BOT2_TOKEN='...' BOT2_CHANNEL_ID='@channel_two' BOT2_OWNER_ID='123456789' \
  WEBHOOK_URL='https://<telepost-app>.fly.dev'
```

父路由监听 8080，Bot 子进程使用 8081、8082……；公网路径为：

- `/webhook/bot1`、`/webhook/bot2`
- `/api/bot1/v1/*`、`/api/bot2/v1/*`

双 Bot 生产实例使用 512 MiB，并在受限环境关闭搜索。关键片段：

```toml
[env]
  RUN_MODE = "WEBHOOK"
  SEARCH_ENABLED = "false"
  SEARCH_ANALYZER = "simple"
  DB_CACHE_KB = "1024"

[http_service]
  internal_port = 8080
  # PixivFlow 通过 Flycast 私网以明文 HTTP 调用投稿接口；force_https=true 会把它
  # 301 到 HTTPS 并打断投递。Telegram webhook 仍走公网 HTTPS。
  force_https = false
  auto_stop_machines = false
  auto_start_machines = true
  min_machines_running = 1

[[http_service.checks]]
  grace_period = "60s"
  interval = "30s"
  timeout = "10s"
  path = "/health"

[[vm]]
  cpu_kind = "shared"
  cpus = 1
  memory_mb = 512
```

## 为什么业务端常驻

- 常驻保留长期健康检查（`/health`），它只确认进程还能接活，不会与停机互相拉扯——
  本服务本来就不应该停。
- 每次冷启动都要重新建立 PTB 会话、Bot 子进程与审核恢复；期间用户会看到「机器人不回复」，
  且上游投递只能延后。省下的钱不值得牺牲可靠性。
- 省钱放在 PixivFlow 执行端：它平时停止、被触发唤醒、跑完自行退出，与 TelePost 的
  即时响应诉求不冲突。

## PixivFlow 执行端

执行端平时停止、按需唤醒，配置在部署套件的 `fly/deploy.pixivflow.toml`。TelePost 侧
需要一次性分配 Flycast 私网地址，供执行端投递：

```bash
flyctl ips allocate-v6 --private --app <telepost-app>
```

执行端通过 `http://<telepost-app>.flycast/api/botN/v1/*` 投递，由 Fly Proxy 转发；
不要使用 `.internal`。TelePost 的 `/ready` 是其消费屏障：未就绪时执行端只延后重试。

## 安全升级

1. 确认目标版本的 GitHub Release、GHCR amd64/arm64 manifest 和 CI 都成功。
2. 对 Volume 建 snapshot。
3. 更新原 Machine 的镜像，不重建 Volume。
4. 检查 Machine ID、Volume ID、内存与常驻参数（`auto_stop_machines`、`min_machines_running`）未变化。
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
