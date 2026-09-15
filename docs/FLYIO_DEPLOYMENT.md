# Fly.io 部署

Fly.io 只是 TelePost 的一种部署方式，不是使用项目的前提。本页只介绍 TelePost 自身；
PixivFlow + TelePost 的组合拓扑、调度和成本优化由
[pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) 维护。

## TelePost 在 Fly.io 上如何运行

```text
Telegram ── Webhook ──→ Fly Proxy ──→ TelePost（常驻）
                                           │
                                           └── 持久卷 /app/data
```

- TelePost 使用 Webhook 接收 Telegram 更新，同时提供 Mini App 和 HTTP API。
- 投稿、审核与发布状态保存在持久卷中。
- 服务保持常驻，避免用户私聊投稿时遇到冷启动延迟。
- 单 Bot 与多 Bot 使用同一个运行程序；是否启用 Mini App、审核和外部 API 由配置决定。

仓库根目录的 [`fly.toml`](../fly.toml) 是 TelePost 的参考配置。它不包含 PixivFlow、外部调度器
或作者生产环境的应用名称。

## 1. 创建 App 和 Volume

安装并登录 `flyctl`，再创建应用和同区域的持久卷：

```bash
flyctl auth login
flyctl apps create <app>
flyctl volumes create data --size 1 --region <region> --app <app>
```

复制 `fly.toml` 后填写自己的 `app` 和 `primary_region`。Volume 不能省略：SQLite、API Token、
审核状态、会话和运行时策略都在 `/app/data`。

## 2. 设置 Secrets

单 Bot 最少配置：

```bash
flyctl secrets set --app <app> \
  TOKEN='123456:replace-me' \
  CHANNEL_ID='@your_channel' \
  OWNER_ID='123456789' \
  WEBHOOK_URL='https://<app>.fly.dev'
```

需要审核时再添加 `REVIEW_CHAT_ID` 和对应审核开关；需要 Mini App 时按
[Mini App 文档](MINIAPP.md)添加 session secret。不要把 Token 写入 `fly.toml`。

多 Bot 使用连续的 `BOT1_*`、`BOT2_*` 配置：

```bash
flyctl secrets set --app <app> \
  BOT1_TOKEN='...' BOT1_CHANNEL_ID='@channel_one' BOT1_OWNER_ID='123456789' \
  BOT2_TOKEN='...' BOT2_CHANNEL_ID='@channel_two' BOT2_OWNER_ID='123456789' \
  WEBHOOK_URL='https://<app>.fly.dev'
```

完整变量见[配置参考](CONFIGURATION.md)。

## 3. 部署

可以从仓库构建：

```bash
flyctl deploy --app <app> --config fly.toml --ha=false
```

也可以部署固定版本的公开镜像：

```bash
flyctl deploy --app <app> --config fly.toml --ha=false \
  --image ghcr.io/redtidev1918/telepost:<version>
```

生产部署应使用明确版本，不要使用会随时间变化的 `latest`。

## 4. 验证

```bash
flyctl status --app <app>
flyctl logs --app <app>
curl -fsS https://<app>.fly.dev/live
curl -fsS https://<app>.fly.dev/ready
curl -fsS https://<app>.fly.dev/api/v1/health
```

多 Bot 还要逐个检查 `/api/botN/v1/health`。三个通用探针含义不同：

| 端点 | 含义 |
| --- | --- |
| `/live` | 路由进程存活 |
| `/ready` | 数据库、Bot 和审核服务已就绪 |
| `/health` | 运行状态与资源指标 |

最后在 Telegram 内执行一次 `/start` 和测试投稿，并通过 `getWebhookInfo` 核对 URL、
待处理数量与最近错误。不要把包含 Bot Token 的完整 URL 或响应贴到公开 Issue。

## 生命周期与健康检查

TelePost 是用户可见的投稿入口，参考配置保持：

```toml
[http_service]
  auto_stop_machines = false
  auto_start_machines = true
  min_machines_running = 1

  [[http_service.checks]]
    path = "/health"
```

不要把 TelePost 的生命周期与可选内容采集器混为一谈。若上游任务适合按需运行，应让上游独立管理
自己的生命周期，TelePost 继续负责即时投稿、审核和发布。

## 与 PixivFlow 组合（可选）

```text
PixivFlow（发现 / 下载 / 调度）
              │ HTTP API
              ▼
TelePost（投稿 / 审核 / 发布，常驻）
              │
              ▼
Telegram
```

TelePost 不依赖 PixivFlow，PixivFlow 也可以投递到其他接收端。需要在 Fly.io 上组合两者时，
使用部署仓库提供的独立 App、独立卷和凭据边界：

- [选择部署架构](https://github.com/redtidev1918/pixivflow-telepost-deploy/blob/main/docs/getting-started/choose-architecture.md)
- [Fly.io 平台指南](https://github.com/redtidev1918/pixivflow-telepost-deploy/blob/main/docs/platforms/flyio.md)

不要把 PixivFlow 进程塞回 TelePost 容器；组合部署的资源、调度器和上游生命周期也不要写进
TelePost 的产品配置。

## 升级与回退

1. 部署前为 Volume 创建 snapshot。
2. 更新到明确版本镜像。
3. 核对原 Machine 和 Volume 仍在使用，检查健康端点与一次真实投稿。
4. 需要回退时更新回上一版本镜像；只有数据损坏时才恢复旧 snapshot。

```bash
flyctl volumes snapshots create <volume-id> --app <app>
flyctl machine update <machine-id> --app <app> \
  --image ghcr.io/redtidev1918/telepost:<version> --yes
```

有状态服务不要通过随意增加 Machine 数量实现高可用：一个 Volume 不能同时挂载到多台 Machine，
同一个 Telegram Token 也不能由多个进程同时消费。备份、数据库检查和故障处理见
[运维手册](OPERATIONS.md)。
