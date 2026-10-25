# 当前计划：Durable Inbox 回归审计与恢复链路修复（2026-10-25）

## 问题定义

用户报告：引入 Durable Inbox（v2.84.2，2026-10-22）后系统出现多种异常；当日最新症状为
PixivFlow「再试一次」恢复按钮点击后**无任何响应**，且恢复请求未到达 PixivFlow
（机器从未被唤醒——日志证据）。

## 当前证据（标记：CONFIRMED / LIKELY / UNKNOWN）

- `CONFIRMED` inbox worker 每 Bot 严格串行：`claim_next_due` 只取最低未完成 update_id，
  退避中的队头行阻塞其后一切（`telepost/storage/sqlite/inbox.py` docstring 明示）。
- `CONFIRMED` 恢复回调处理器在应答前同步等待远端 HTTP（≤120s，`handlers/recovery.py` +
  `telepost/application/recovery.py` `RECOVERY_TIMEOUT_SECONDS=120`），运行于串行 worker 内。
- `CONFIRMED` PixivFlow 空闲自停后唤醒需 ~8s（实测）；Telegram 回调应答窗口 ~10s，
  冷启动边缘即可造成「无响应」观感。
- `CONFIRMED` 用户点击期间 PixivFlow 从未被唤醒 → POST 从未发出 → 异常发生在
  TelePost 本地阶段或更新未被消费。
- `CONFIRMED` #209 事故链与 inbox 无关（HTTP API 路径）；#208/#209 均为 inbox 之前
  已存在的业务缺陷被暴露（分类 C）。
- `UNKNOWN` 点击的 update 是否到达 TelePost、是否入 inbox、是否被消费——仪表复现进行中。

## 实施阶段

1. ✅ 稳住在途（PR #352 已合并；release #353 暂挂）
2. 🔄 仪表复现捕获（用户点击 × 实时双端日志）
3. ⬜ Part IV 修复（与复现结果无关，代码证据已足够动手）：
   - 恢复回调 answer-first：先落本地 durable attempt 行 + 立即应答，
     远端 POST 移入后台任务（带界重试 + 300s 周期任务兜底重投，requestId 幂等）
   - inbox 可观测：per-bot API health 暴露 inbox pending 数与最老行年龄
   - 回调处理器兜底 catch：任何非 RecoveryError 异常也要 answer（终态提示）
4. ⬜ Part V：HOL 故障注入测试（慢 handler 不阻塞无关更新——修复前后红绿）+ 全量
5. ⬜ Part VI：并入 release #353 → 2.84.6 → 部署 → 生产验收 → 文档同步 → A–H 报告

## 风险与回滚

- 后台提交的恢复请求必须有界重试且 requestId 幂等（PixivFlow 按 UUID 收敛，契约既有）
- 回滚点：2.84.5（`ghcr.io/redtidev1918/telepost:2.84.5`）；本次改动无 schema 变更计划

## 验收标准（本计划完成条件）

- 恢复点击 ≤10s 内获得明确应答（接受/已在处理/失败原因三态）
- 慢恢复提交不阻塞该 Bot 的其它更新（测试证明）
- 生产 /api/botN/v1/health 可见 inbox 深度；一次真实恢复点击走通全链路
