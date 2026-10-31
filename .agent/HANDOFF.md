# 会话交接（TelePost 可靠性专项）

## 当前交接：2026-10-31 晚——多图相册发布布局修复已发版闭环

生产 **2.84.10**（release PR #368，`61ae2c7`，deploy run 41194897817，验收全绿）。
修复内容/验证/回滚点（2.84.9）见 `STATUS.md` 同日条目。两件遗留：

- **#224 生产归因待 flyctl**：公开页证据（3328 = 8 图完整相册）与事故描述不符，
  需只读账本（原稿媒体总数、progress_json、机器 env）定论；本机 `flyctl auth login` 后可做。
- **发版基建待修**：finalize 的 deploy-docs dispatch 已退役 `static.yml`（422），
  应改读 `download-page.json` → `docs.yml`；`update-download-page` 直推 main 必被
  ruleset 拒，需改为自动开 PR（本次由 PR #370 人工合规补推）。

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
