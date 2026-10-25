# 工程状态（TelePost）

> 本文件是会话恢复入口之一。Git/GitHub/Fly 是状态的权威来源；本文件与实时查询冲突时，以实时为准并立即更新本文件。

- 最近核验：2026-10-25 07:0x UTC（本日多轮）
- 当前主要工作：Durable Inbox 回归审计与恢复链路修复——**代码与测试已完成**，待 2.84.6 发版部署验收（见 [PLAN.md](PLAN.md)）
- 工作树：干净（main，与 origin/main 同步）；进行中改动在新分支
- 活跃 PR：release PR #353（2.84.6，**暂挂**——等待恢复链路修复并入同一班次）
- 已核实生产版本：v2.84.5（commit `161b039`，deploy-fly run 40164930153，/health + /ready + smoke 4/4 已验）
- 阻塞项：
  - `BLOCKED` Fly SSH（WireGuard DNS i/o timeout）——机内 doctor/账本直读不可用，HTTP 探针替代
  - `UNKNOWN` 用户「再试一次」点击在 TelePost 内的真实处理路径（仪表复现进行中，见 HANDOFF）
- 今日已完成：五项 P0 缺陷（PR #350 → 2.84.5）、无 payload 发布预检（PR #352 已合并待发版）、PixivFlow 错误归因（#210 → 3.7.1 已部署）
- 未完成验收：2.84.6 发版/部署/验收（answer-first 修复 + inbox 可观测 + PR #352 无 payload 守卫同班次）
