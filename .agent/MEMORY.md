# 长期记忆（TelePost）

> 只放跨任务稳定事实。临时进度看 STATUS/TASKS；跨仓权威在 deploy 仓
> （`pixivflow-telepost-deploy/docs/architecture/ecosystem-platform.md` 等）与本仓 AGENTS.md。

- 串行消费者的头等风险：Durable Inbox worker（`telepost/telegram/inbox_worker.py`）每 Bot
  严格按 update_id 串行，**任何 handler 内的无界外部 I/O 都是全 Bot 队头阻塞源**。新回调/
  命令 handler 必须先应答（或 ≤10s 内应答）再做慢工作；慢工作走 durable 意图 + 后台提交。
- Telegram `retry_after` 是最低等待：足额支付、永不截断（2026-10-25 事故根因之一，
  `review_stager._send_throttled` 现为实现基准）。
- 空 `media_json` ≠ 无外部副作用；staging 失败保留 preview ids 作证据，
  受控恢复资格基于「不可达发布」论证（`review_queue._recoverable_failed_row`）。
- 上游执行机空闲自停、公网 HTTPS 唤醒实测 ~8s（部署观测，权威在 deploy 仓）——
  TelePost 侧一切同步等待它的路径都会先付这笔冷启动税。
- 生产取证限制：本开发机 Fly SSH（WireGuard DNS）不可用；用 HTTP 探针 + `fly logs`（HTTPS）
  替代，机内 doctor 标记 BLOCKED 而非跳过。
- 发布节奏：fix PR → CI 双平台绿 → squash merge → release-please PR → 合并即 tag/
  产物/deploy-fly 自动部署；deploy 仓基线由 update-telepost 自动 PR 同步。全程证据留
  current-state.md（deploy 仓）。
