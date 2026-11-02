# 2026-11-02 小说在线阅读恢复（IN_PROGRESS）

先核对 STATUS.md 与运行版本。实现和操作流程见 docs/OPERATIONS.md「小说在线阅读补偿」。
交接时生产 2.84.11 / TelePress 0.17.2，不能从 requirements==0.18.0 推断已部署。
旧 TXT fetch 适配器吞异常；failed 行永久复用，未持久化 reason，具体原始网络异常不可追溯。
新实现不得重复建页、重发原主贴或 TXT；超时/未知结果先对账，成功 URL 优先复用。
尚未发布，原帖恢复未执行。本次早先 Fly host_status=unreachable / exec 408，后恢复 ok。公网和机内 /health 已重新确认 TelePost 2.84.11 / 进程内 TelePress 0.17.2；bot2 #184 仍 published、root 676、preview failed / 空 URL。原帖键盘与远端页面仍待补偿前对账。发布必须按本仓 release skill，经 PR/CI/Release/自动部署。
已恢复到正常本地环境，补丁基于最新 main；GitHub 身份核验通过。TelePress 上游不确定建页重试修复 PR #83 已创建并等待用户合并；本仓 PR/CI 正在完成。由用户合并功能和 Release PR，禁止自动合并。

---

# 会话交接（TelePost 可靠性专项）

## 当前交接：2026-10-31 晚——#224 归因闭环，专项全部完成

生产 **2.84.11**（`5bfbc72`，post-release 五项全 success）。#224 定论：**发布本就正确**
（8 图一次成功完整相册 3328–3335，账本/审计/公开页三方一致；生产 env 实测
discussion/10 从无漂移）。修复 2.84.10/2.84.11 属防御性加固 + 基建恢复，全部验收。
遗留仅历史项：inbox 死信 bot1:8/bot2:7（10-25 已知）。详见 `STATUS.md` 同日条目。

## 以下为已被替代的 2.84.9 历史交接（2026-10-25 晚）

生产 2.84.9（`b3a97e1`）；上游 PixivFlow 版本基线以 deploy 仓
`docs/operations/current-state.md` 为准（本文件不维护跨仓版本快照）。本地超龄裁决已删除，
sweep 先查远端（GET recover）再投影；查询失败只置 reconciliation_required 并保留
target 互斥。Webhook 60s 自核验 + /ready 门控已生产实证。
#209 恢复待人工审核授权（failed、空载荷、控制卡在）；无持久化内容分类结论。
回滚点：TelePost 2.84.8（上游回滚基线同见 deploy 仓）。
下一会话若做恢复收尾：操作者点击恢复按钮 → 正常审核 → 人工批准 → 发布；
或用 /restore_card 处理旧卡。架构证据见 Deploy recovery-ownership.md。
架构证据见 Deploy docs/architecture/recovery-ownership.md。

## 以下为已被替代的 2.84.6 历史交接

更新：2026-10-25 07:5x UTC（专项闭环）。下一会话先核对本文件仍有效（对照 STATUS.md 与 GitHub 实时状态）再继续。

## 当前任务与阶段

已闭环：PR #354（answer-first）并入 release #353 → **v2.84.6 已部署验收**。
后续会话从新任务开始；本文件保留为交接样例。

## 已确认结论（VERIFIED）

- PR #352 已合并 main（`3b71b30`）；release-please 已开 #353（2.84.6）。
- 生产运行 2.84.5（`161b039`）+ PixivFlow 3.7.1（`b12a82f`），HTTP 探针全过。
- PixivFlow 唤醒通路正常（实测 ~8s）；用户恢复点击期间该机器从未被唤醒（fly 日志）。
- inbox 严格串行 + 恢复回调应答前等待 ≤120s 远端 HTTP（代码证据，见 PLAN）。

## 推断（INFERRED）

- 「无响应」最可能 = 应答晚于 Telegram 回调窗口（冷启动+串行等待），或处理器在本地
  阶段抛出非 RecoveryError（无兜底 answer）。两者都由 T-02/T-03 修复，无需进一步区分
  即可动手；仪表复现用于事后确认。

## 未知（UNKNOWN）

- 用户点击的 update 是否到达 TelePost webhook（最近日志窗口无 POST /webhook 记录，
  但窗口太短不足以下结论）。

## 最近关键命令/结果

- `fly logs`（双端）多次；PixivFlow 机器状态 `stopped`→手动唤醒 200。
- 本地全量 pytest：1585 passed / 1 skipped（含 PR #352）。
- 仪表复现捕获进行中：`/tmp/tp_repro.log`、`/tmp/pf_repro.log`（后台任务）。

## 未提交文件

- `.agent/`（本套文档）、docs/ARCHITECTURE.md、docs/TESTING.md、.agents/skills/release/
  （将随 Part IV 修复同一 PR 提交）

## 下一步最小动作

1. 读 `/tmp/tp_repro.log` / `/tmp/pf_repro.log`（用户点击复现结果）
2. 实现 T-02/T-03/T-04（handlers/recovery.py + telepost/application/recovery.py +
   api health），先写 T-05 故障注入测试（当前代码应红）
3. 全量 → PR（含 .agent/ 与 docs）→ 合并 → 合并 release #353 → 部署 → 验收
