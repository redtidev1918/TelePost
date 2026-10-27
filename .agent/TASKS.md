# 任务队列

## 当前队列（取代下方历史记录）

| 任务 | 状态 | 证据 |
| --- | --- | --- |
| 原 answer-first / durable admission / inbox health / release 2.84.6 | DONE | 当前生产已是 2.84.9，下方旧 TODO 不再有效 |
| 组件、复杂度、职责与方案矩阵 | DONE | Deploy recovery-ownership.md（#272，Status: DELIVERED） |
| 恢复执行权威收敛与结果投影 | DONE | PR #360 → 2.84.9 部署验收；红→绿 3 例 + 全量 1601P/1S |
| Webhook 注册核验与故障注入 | DONE | PR #360 内 verify_webhook + 60s 任务；机内 per-bot /ready 门控实证 |
| 全量、PR/CI、Release、只读生产验收 | DONE | 2.84.9（#361）+ 3.7.2（PF #213 + pin #273）；验收见 deploy current-state |
| 大型插件框架、跨语言合并部署 | DEFERRED | 无真实多实现需求或实测收益 |
| 2026-10-27 双仓投递契约 + 媒体适配器合并门禁 | IN_PROGRESS | .github/workflows/delivery-contract.yml；共用 PixivFlow harness，真实 HTTP/SQLite + 80 项媒体回归；本机全量 1601P/1S |

## 历史队列（已失效，仅保留追踪依据）

> 状态：TODO / IN_PROGRESS / BLOCKED / DONE / CANCELLED。完成页适度归档到文末。

| ID | 任务 | 优先级 | 状态 | 依赖 | 证据/验收 |
|----|------|--------|------|------|-----------|
| T-2026-10-25-01 | 仪表复现恢复点击（双端日志捕获） | P0 | IN_PROGRESS | — | 捕获到点击的 inbox/回调/HTTP 轨迹，或确认无投递 |
| T-2026-10-25-02 | 恢复回调 answer-first + 后台持久化提交 | P0 | TODO | — | 点击 ≤10s 有应答；POST 后台完成；requestId 幂等重投 |
| T-2026-10-25-03 | 回调处理器非预期异常兜底应答 | P0 | TODO | — | 任何异常路径用户都能看到提示，不静默 |
| T-2026-10-25-04 | inbox 深度可观测（per-bot API health） | P1 | TODO | — | /api/botN/v1/health 返回 pending 数 + 最老年龄 |
| T-2026-10-25-05 | HOL 阻塞故障注入测试（红→绿） | P0 | TODO | T-02 | 慢 handler 期间无关更新正常消费 |
| T-2026-10-25-06 | docs/ARCHITECTURE.md + docs/TESTING.md + release skill | P1 | IN_PROGRESS | — | 与代码一致，不与 deploy 仓权威重复 |
| T-2026-10-25-07 | 2.84.6 发版 + 部署 + 生产验收 | P0 | BLOCKED | T-02..05 | release #353 合并后全链路验证 |
| T-2026-10-25-08 | current-state/DECISIONS/LESSONS 收尾 + A–H 报告 | P1 | TODO | T-07 | 证据标记齐全 |

## 归档（当日已完成）

- T-00 五项 P0 缺陷修复 → PR #350 → v2.84.5 已部署验证（DONE）
- T-09 无 payload 发布预检 → PR #352 已合并 `3b71b30`（DONE，待 2.84.6 发版）
- T-10 PixivFlow 错误归因 → PR #210 → v3.7.1 已部署验证（DONE）
