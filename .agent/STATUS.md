# 工程状态（TelePost）

## 当前核验：2026-10-31 晚（Asia/Shanghai）

- 当前任务：**DONE（发版闭环；#224 生产归因待 flyctl）**——多图相册发布布局修复已发行部署验收。
- 修复 PR #367（squash `1baed23`）：相册确定性拒绝不再静默拆单图（executor + legacy_runner），
  失败可重试且保留 checkpoint；`publish.layout` 审计记录实际相册分组（media_group_id /
  reply_to_message_id 贯通 ledger 与重启恢复）；`fly.toml` 显式 `CHANNEL_ALBUM_REPLY=discussion`、
  `MEDIA_GROUP_CAPACITY=10`。全量 1621 passed + 投递契约 VERIFIED + PR CI 全绿（含 Windows）。
- 生产：**2.84.10**（release PR #368，/health commit `61ae2c7`，deploy run 41194897817 成功，
  自动部署）；`/ready`、bot1/bot2 health 200（inbox pending=0/dead=0）、smoke 4/4。
  deploy 仓基线 PR #291 已合并。回滚点：**2.84.9**（镜像 `ghcr.io/redtidev1918/telepost:v2.84.9`）。
- 发版基建两处既有回归（非本次修复引入，#364 门禁 / #366 VitePress 迁移遗留），
  v2.84.10 当次以 PR #370 人工合规补推下载页，随后已由 **PR #372 修复**：
  `deploy-docs` postRelease 改 dispatch `docs.yml`（原 `static.yml` 已退役 → 422）；
  `update-download-page.yml` v3 改为 PAT（RELEASE_PLEASE_TOKEN）开 PR + 等必需检查
  绿后自动 squash 合并（25 分钟上限 < releasegraph 30 分钟 max_wait），PAT 合并的
  push 经 `docs/**` 路径自动重建 Pages，超时显式失败可修复重入。
- #224 只读取证：公开嵌入页显示 t.me/xgdShare/3328 为 **8 图完整相册**（占 3328–3335），
  与「散落单图」描述不符；3336/3337 与前日「1 图+5 文档」模式同构，疑为另一投稿。
  生产账本归因（原稿总数/progress_json/机器 env）**BLOCKED：本机 flyctl 未登录**。

## 文档站迁移：2026-10-31

- VitePress 1.6.4 接入完成；保留原 Markdown、Docsify index/vendor 与停用的 static workflow 备份。
- 文档构建及无 .git fixture 的 docsite check/navcheck 已通过；独立文档 PR 待审核，未合并。
- 本次仅文档工具链变更，未发布应用，未触碰运行配置或 Telegram。

## 当前核验：2026-10-25 晚（Asia/Shanghai）

- 当前任务：**DONE**——恢复执行权威收敛切片已发行部署验收（2.84.9 / b3a97e1）。
- 生产：TelePost 2.84.9（自动部署 run 40210954825）；上游 PixivFlow 版本基线以
  deploy 仓 `docs/operations/current-state.md` 为准（本文件不维护跨仓版本快照）。
- 验收：/health、/ready（含 per-bot 注册门控）、smoke 4/4、verify-webhooks、
  机内 doctor（integrity/refetch/账本全净；inbox dead=8/7 为历史遗留）全过。
- #209 只读核查：failed、空载荷、控制卡在；恢复待人工审核授权，媒体未触碰。
- 内容分类争议：无持久化分类结论可查，工程记录不需更正；安全限制保留。
- 回滚点：TelePost 2.84.8（上游回滚基线同见 deploy 仓）。

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
