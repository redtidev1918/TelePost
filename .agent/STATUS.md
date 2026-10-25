# 工程状态（TelePost）

## 当前核验：2026-10-25（Asia/Shanghai）

- 当前任务 IN_PROGRESS：架构复杂度审计与恢复生命周期职责收敛。
- 实际生产为 2.84.8 / `e6cfcde968c3f1da95ebd6e2e8df598c25438a0d`，Fly SSH 已可用。
- 两 Bot webhook 已注册；Doctor 报告历史 inbox dead=8/7，未重放。
- #209 只读确认 failed、有控制卡、媒体 JSON 字段长度均为 2；未读取或发送媒体。
- 修改待交付：远端结果投影、移除本地超龄失败裁决、Webhook 核验与本地故障注入。
- 复核（2026-10-25 晚）：全量 1601 passed / 1 skipped（上次 1 失败已消除）；
  PixivFlow 全量 1605 passed；定向红绿 34/34。审计事件改为仅首次转入待对账时记录。
- 本轮以用户最新架构简化指令为准，不向真实频道发送测试媒体。
- `.session_restore/`、`clear/` 是既有未跟踪内容，保留且不提交。

## 已被替代的历史快照（不代表当前状态）

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
