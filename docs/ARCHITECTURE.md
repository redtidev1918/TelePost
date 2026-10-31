# TelePost 架构（本仓内部视图）

> 跨仓权威（生态拓扑/边界/契约）在 deploy 仓 `docs/architecture/ecosystem-platform.md`；
> 本文只描述 TelePost 进程内视图与不变量，冲突时以代码与跨仓文档为准。

## 更新处理链（webhook 模式，§durable-inbox）

```text
Telegram POST /webhook/botN（secret 校验）
  → webhook_inbox 落库（synchronous=FULL，独立连接）      ← HTTP 200 的前置条件
  → UpdateInboxWorker（每 Bot 一个，严格按 update_id ASC 串行）
      claim_next_due（队头退避未到期 ⇒ 其后一切等待，5 次后 dead）
      → Application.process_update（PTB handlers）
      → update_persistence()（会话状态刷盘）
      → mark_done
  → 失败：几何退避重试 ≤5 → 死信（保留 30 天，不自动重放）
```

- Polling 模式不经 inbox（表空、worker 空转），生产 ingress 以部署清单为准。
- **串行是有意的**（会话顺序、重放安全）。代价：handler 内任何慢 I/O 都是队头阻塞源
  ——新 handler 必须 ≤10s 应答，慢工作写 durable 意图后转后台（见 handlers/recovery.py
  的 answer-first 模式）。

## 投稿 → 审核 → 发布状态机

```text
enqueue(key)
  ├─ 同键 pending/已发布 → 复用（idempotent_replay / duplicate_existing）
  ├─ 同键 failed（从未可操作*）→ claim_failed_recovery CAS → 原身份重暂存
  └─ 新建 preparing → [240s 预算] 预览暂存 → update_staged
        → [新预算] 控制卡 → finalize_control → pending
审核（ADMIN 回调，全部后端 CAS 权威）：approve → publishing → published / failed
调度兜底：reconcile_incomplete（60s 陈旧）——0 媒体行收敛 failed 不造卡；
        有媒体行重建控制卡 failed→pending
```

\* 从未可操作 = 无控制卡、无已暂存媒体、未发布、非 refetch（`_recoverable_failed_row`）；
发布仅可从 pending 经 approve CAS 到达，故此类行外部副作用上限为私有审核群预览消息。

## 关键并发边界

- 每 Bot：inbox worker ×1（串行）+ APScheduler 周期任务（refetch 心跳 30s、清理 300s…）
- 心跳续命：staging 期间 `touch_preparing` 每 20s，防止 reconcile 劫持活请求
- 发布互斥：`claim_for_publishing`（pending/failed→publishing CAS；publishing 超时可重认领）
- 恢复互斥：`recovery_attempts` partial UNIQUE（每 target 一个 active）

## 恢复执行的权威边界

恢复执行状态以远端（上游执行系统）为唯一事实源。TelePost 的
`recovery_attempts` 保存稳定 requestId、回调去重和远端结果投影，不运行第二套
执行超时状态机。`requested` 表示尚未确认受理（也可能是受理响应丢失），
`accepted` 表示远端已受理；运行阶段由 `remote_state` 表达。
查询失败或本地超龄只设置 `reconciliation_required`，保留每 target 的活动约束，
绝不据此推断远端已停止。终态仅由关联到同一 requestId/target 的结果确认。
执行成功表示投递到审核流程；是否已发布仍由 Review/Publication 账本决定。

复用决策：远端查询走上游已有的认证 recover API（按 requestId，契约见部署仓库），
本地复用既有 SQLite CAS 与周期扫描；不引入新任务框架、队列、中央服务或跨语言同进程集成。
网络访问与审计写入都在恢复数据库事务外执行，避免自锁和长事务。

## 持久化

SQLite（按 Bot 分库，W + 卷 /app/data）：pending_reviews、webhook_inbox、recovery_attempts、
refetch_*、publication 账本、审计。迁移全部幂等 additive（db_manager）。

## 生产拓扑

常驻 Fly App `telesubmit-multi-bot`（512MB，force_https=false，auto_stop=false）：
路由进程 WEBHOOK_PORT + botN 子进程 +N。PixivFlow 为独立 App（空闲自停，~8s 冷启动）。
