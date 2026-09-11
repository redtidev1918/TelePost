# AGENTS.md —— 本仓库是「业务平面」

这份文件写给任何进入本仓库的智能体或工程师。先读
`pixivflow-telepost-deploy/docs/ARCHITECTURE.md`，它是三仓库职责契约的唯一权威描述；
本文件只回答「什么该做、什么绝对不该做」。

## 职责

TelePost 决定 **「投稿如何审核与发布到 Telegram」**：Telegram 更新的接收与路由、会话式私聊投稿、
HTTP 投稿接口、幂等键、审核队列、批准/驳回、发布与发布恢复。

- 它是**唯一持有 Telegram 凭据**（`BOTn_TOKEN` / `BOTn_CHANNEL_ID` / `BOTn_OWNER_ID`）的服务，
  也是**唯一能发布到频道**的服务。
- 它**不决定要抓哪些 Pixiv 作品**——那是 PixivFlow。本仓库里没有 Pixiv refresh token，
  也不应该出现调度 cron / occurrence / 槽位。

## 目录职责

| 路径 | 是什么 | 不是什么 |
| --- | --- | --- |
| `utils/webhook_server.py` | webhook 模式下的 `setWebhook` 与更新接收 | 不是第二个发布器 |
| `utils/api_server.py` | `/api/botN/v1/submissions` 投稿接口与幂等语义 | 不抓取 Pixiv、不排调度 |
| `utils/submission.py` / `handlers/` | 会话式投稿、媒体收集、预览、取消 | 不绕过审核直发 |
| `telepost/application/review_queue.py` | 审核队列与 `normalize_idempotency_key` | 不是调度账本 |
| `fly.toml` | **本仓库唯一的部署拓扑**（常驻参数、独立卷、健康检查） | 不包含 PixivFlow 与调度配置 |
| `deploy.fly-multi-bot.toml` | **已删除**：它曾在同一容器里再拉起一个 PixivFlow 调度器 | 不要重新引入 |

## 绝对不要做

1. **不要让本服务休眠。** 投稿要求即时响应，auto-stop 的冷启动唤醒会让用户看到「机器人不回复」。
   必须常驻：`auto_stop_machines = false`、`min_machines_running = 1`，并保留 `/health` 长期健康检查。
   成本由 PixivFlow 侧「平时 stopped、按需唤醒」来省。
2. **不要在同一个容器/机器里再跑一个 PixivFlow。** 那会共用内存、进程生命周期、机器生命周期与
   故障域，是历史混部问题的根源。PixivFlow 现在是部署仓库里的独立 App + 独立卷。
3. **不要有任何自动批准或直发频道的路径。** 任何作品（包括 PixivFlow 每日自动投稿）都必须进入
   审核队列，**人工批准后**才由本服务发布。不接受自动批准、不接受 bypass。
4. **不要删掉 `force_https = false`。** 独立 PixivFlow App 通过 Flycast 私网以明文 HTTP 调用投稿
   接口，`force_https = true` 会把它 301 到 HTTPS 并直接打断投递（Flycast/6PN 本身在 WireGuard 上加密）。
5. **不要在别处注册/删除 webhook。** webhook 的负责人只有本仓库的 `webhook_server.py`。
6. **不要在日志、响应或报告里打印任何令牌**（Bot token、投稿 token、webhook secret）。
   只输出「已配置 / 缺失 / 就绪」。
7. **不要在 256MB 上跑。** 512MB 是实测规格；降内存前必须先做真实内存验收（见 `fly.toml` 注释）。

## 改动前必须保持的行为

- **投稿幂等**：投稿接口接受 `idempotency_key`（也接受表单/字段形式），经
  `normalize_idempotency_key(user_id, raw_key, "api")` 归一；已受理/已发布的重放返回
  `idempotent_replay`，**不重复通知、不重复发布**。这是 PixivFlow 槽位账本之外的最后一道防线。
- **重启可恢复**：审核队列、幂等记录、会话状态都在持久卷 `/app/data` 上；重启后账本存活，
  同一作品不产生重复审核，同一审核不重复发布。
- **审核保留策略**：`PENDING_REVIEW_RETENTION_DAYS=1` 与
  `PENDING_REVIEW_CLEANUP_BATCH_SIZE=20` 与代码默认值不同，必须显式声明（否则会静默改变
  用户可见行为）。
- **多 Bot 同机**：只有 webhook 模式能保证多 bot 同机 + 即时响应；`run.py` 路由进程占
  `WEBHOOK_PORT`，`botN` 子进程占 `WEBHOOK_PORT+N`，路径 `/webhook/botN`。

## 改完请自证

```bash
pytest -q                          # 全量测试
pytest -q tests/test_api_server.py tests/test_conversation_flow.py
python check_config.py             # 配置自检
```

跨仓库的只读生产校验在部署仓库：`./scripts/verify-production.sh`、`./scripts/smoke-telepost.sh`、
`./scripts/verify-webhooks.sh`（不会打印密钥）。

## 已知待办（本仓库范围）

- `ROADMAP.md` 中与「拆分拓扑」相关的条目若已完成，请更新；不要再保留第二份 Fly 拓扑文件。
