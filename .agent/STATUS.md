# 工程状态（TelePost）

> 本文件是会话恢复入口之一。Git/GitHub/Fly 是状态的权威来源；本文件与实时查询冲突时，以实时为准并立即更新本文件。

- 最近核验：2026-10-25 07:5x UTC
- 当前主要工作：Durable Inbox 回归审计与恢复链路修复——**已闭环**（v2.84.6 已部署验收）
- 工作树：干净（main，与 origin/main 同步）；进行中改动在新分支
- 活跃 PR：无（#352/#354 已并入 v2.84.6；deploy 仓 #266/#267 已合并）
- 已核实生产版本：v2.84.6（commit `cc93746`，/health + /ready 双 true + smoke 4/4 + bot1 inbox 字段（pending=0/dead_24h=0）已验；PyPI 2.84.6）
- 阻塞项：
  - `BLOCKED` Fly SSH（WireGuard DNS i/o timeout）——机内 doctor/账本直读不可用，HTTP 探针替代
  - `UNKNOWN` 用户「再试一次」点击在 TelePost 内的真实处理路径（仪表复现进行中，见 HANDOFF）
- 今日已完成：五项 P0 缺陷（PR #350 → 2.84.5）、无 payload 发布预检（PR #352 已合并待发版）、PixivFlow 错误归因（#210 → 3.7.1 已部署）
- 未完成验收：无阻塞项。遗留观察：用户原始「再试一次」点击未在捕获窗口复现（UNKNOWN）；
  2.84.6 后恢复按钮应即时应答，若仍无响应用 /api/botN/v1/health 的 inbox 字段一步定位
