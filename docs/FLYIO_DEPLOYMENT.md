# Fly.io 部署

本页介绍 TelePost 在 Fly.io 上的部署；
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

仓库根目录的 [`fly.toml`](https://github.com/redtidev1918/TelePost/blob/main/fly.toml) 是 TelePost 的参考配置。它不包含 PixivFlow、外部调度器
或作者生产环境的应用名称。

## 0. 正式发版路径（唯一推荐）

合并 Release PR 之后，不需要任何人执行命令。流水线按固定顺序完成：

```text
Release PR
   ↓  merge（人工闸门）
vX.Y.Z
   ↓
ReleaseGraph
   ↓
GHCR image ghcr.io/redtidev1918/telepost:<version>
   ↓
Fly.io deployment
   ↓
health / version / bot1 / bot2 verification
```

同一条链产出：

```text
merge Release PR
   └─ ReleaseGraph
        ├─ 二进制 / release assets ──────────────► GitHub Release
        ├─ GHCR 镜像 ghcr.io/…/telepost:<version>
        ├─ postRelease: download page
        ├─ postRelease: PyPI telepost-bot
        ├─ postRelease: Fly 生产部署 ────────────► 验证 image / health / bot1 / bot2
        └─ postRelease: docs
```

每一步都要求上一步成功：**GHCR 或 PyPI 失败会让 ReleaseGraph 报告 release 不完整，
生产部署根本不会被触发**；反过来，部署后未能达到验收条件的 release 会明确失败。
这条链由 [`.release-policy.yml`](https://github.com/redtidev1918/TelePost/blob/main/.release-policy.yml) 的 `release.postRelease`
按数组顺序串行驱动（ReleaseGraph 会逐个 dispatch 并等待结束）。

版本来源是 `telepost/build_info.py`（release-please 改写）。发版验收要求以下六处一致：
Git tag / GitHub Release / `build_info.py` / PyPI / GHCR image tag / 生产 `/health`。

```text
v<version> → <version> → <version> → <version> → <version> → <version>
```

<small>依次为 Git tag、GitHub Release、`build_info.py`、PyPI、GHCR 镜像 tag、生产 `/health`。示例
`<version>` 形如 `2.x.y`。</small>

生产部署的固定行为：

| 约束 | 说明 |
| --- | --- |
| 部署依据 | `ghcr.io/redtidev1918/telepost:<version>`，**不用 `latest`，不用分支** |
| 不重新构建 | `fly deploy --image` 只部署 ReleaseGraph 已验证的那个镜像，`fly.toml` 的 `[build]` 段不参与 |
| 拓扑来源 | 仍是 `fly.toml`，工作流只提供 app 名与镜像 |
| 验收 | 每台 machine 的 image 命中该 tag、`/health` 返回该 version 且 `bots` 含 1 和 2、`/ready` 的 `bot1`/`bot2` 均为 true |
| 凭据 | 仓库 secret `FLY_API_TOKEN`（app 级 deploy token），不落盘、不打印 |

部署失败或需要重新验证某个已发布版本时，可以直接重跑同一个 tag，不需要 bump 版本：

```bash
gh workflow run deploy-fly.yml --ref main -f tag=v<version>   # 例如 tag=v2.x.y
```

只想确认各项门禁而不动生产，加 `-f dry_run=true`。

下面第 1～4 节描述的是**自建实例**。给 TelePost 生产推新版不需要、也不应该走手工路径。

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
  REVIEW_CHAT_ID='<review-group-id>' \
  WEBHOOK_URL='https://<app>.fly.dev'
```

默认 API 与 Mini App 审核开启，因此需要独立的 `REVIEW_CHAT_ID`；仅私聊直发的配置见
[安装指南](INSTALL.md#1-pip-安装推荐)。需要 Mini App 时按
[Mini App 文档](MINIAPP.md)添加 session secret。不要把 Token 写入 `fly.toml`。

多 Bot 使用连续的 `BOT1_*`、`BOT2_*` 配置：

```bash
flyctl secrets set --app <app> \
  BOT1_TOKEN='...' BOT1_CHANNEL_ID='@channel_one' BOT1_OWNER_ID='123456789' \
  BOT2_TOKEN='...' BOT2_CHANNEL_ID='@channel_two' BOT2_OWNER_ID='123456789' \
  BOT1_REVIEW_CHAT_ID='<bot1-review-group-id>' BOT2_REVIEW_CHAT_ID='<bot2-review-group-id>' \
  WEBHOOK_URL='https://<app>.fly.dev'
```

完整变量见[配置参考](CONFIGURATION.md)。

## 3. 手动部署（自建实例 / 故障恢复）

> 生产发布已由自动化接管这一步，见 §0。以下命令只在自建实例、或自动化不可用需要
> 手动恢复时使用；给 TelePost 生产推新版不要走这条手工路径。

手动部署的固定形态是「指定版本的公开镜像」：

```bash
flyctl deploy --app <app> --config fly.toml --ha=false \
  --image ghcr.io/redtidev1918/telepost:<version>
```

`<version>` 必须替换为正式 Release 的版本号（形如 `2.x.y`）。生产部署不要使用会随时间变化的
`latest`，也不要从分支或 commit 部署。

只有在改代码后需要就地构建时才从仓库构建（同样不是生产发布路径）：

```bash
flyctl deploy --app <app> --config fly.toml --ha=false
```

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
  force_https = false
  auto_stop_machines = false
  auto_start_machines = true
  min_machines_running = 1

  [[http_service.checks]]
    path = "/health"
```

`force_https=false` 保留 Flycast 私网 HTTP 投稿路径，避免重定向打断上游投递；
Telegram Webhook 仍走公网 HTTPS。参考配置使用 512 MiB 内存，降配前验收真实上传与发布峰值，
见 [性能与容量](PERFORMANCE.md)。

待审保留策略在 `fly.toml` 显式设置为 `PENDING_REVIEW_RETENTION_DAYS=1`、
`PENDING_REVIEW_CLEANUP_BATCH_SIZE=20`，与代码默认值不同。迁移配置时一并保留。

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

富媒体小说可使用 TelePress 渲染 Telegraph 阅读页。它既可作为独立预览服务供上游调用，
也可通过 TelePost 的 Python provider 为最终发布快照生成预览。媒体代理与上传方式由 provider
选择；预览失败不影响 TXT 发布。TelePress 不持有 Telegram 凭据或投稿审核状态。

TelePost 不依赖 PixivFlow，PixivFlow 也可以投递到其他接收端。需要在 Fly.io 上组合两者时，
使用部署仓库提供的独立 App、独立卷和凭据边界：

- [选择部署架构](https://github.com/redtidev1918/pixivflow-telepost-deploy/blob/main/docs/getting-started/choose-architecture.md)
- [Fly.io 平台指南](https://github.com/redtidev1918/pixivflow-telepost-deploy/blob/main/docs/platforms/flyio.md)

不要把 PixivFlow 进程塞回 TelePost 容器；组合部署的资源、调度器和上游生命周期也不要写进
TelePost 的产品配置。

## 升级与回退

正式发版不需要手工走本节：流水线会用 GHCR 上同一个 release version 的镜像完成部署并验收，
失败即判定发版失败（见 §0 与[运维手册 · 正式发布流程](OPERATIONS.md#正式发布流程)）。

1. 部署前为 Volume 创建 snapshot。
2. 更新到明确版本镜像（`ghcr.io/redtidev1918/telepost:<version>`，不是 `latest`）。
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
