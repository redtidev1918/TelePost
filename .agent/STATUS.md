# 工程状态（TelePost）

## 2026-11-05（晚）频道入口 + 审核状态同步（2.84.14 已发行部署）

- DONE：用户验收 2.84.13 后反馈「打开来源仍跳 Pixiv、有的稿没有、审核页无频道入口、
  更多不显眼、状态不同步」。并行的 codex/miniapp-channel-sync 会话（因用量上限中断）
  已实现全量方案，本班次接手审查、合并与发版。
- 内容（PR #391）：三处「在频道中查看」统一入口（内容详情/我的投稿详情/审核详情，
  标题下方，openTelegramLink 优先）；审核群控制卡终态恢复带「📣 在频道中查看」
  （仅 published）；HTTP 审核 mutation 后原位恢复控制卡（`_sync_review_action`，
  只读投影、失败仅记日志）；Mini App 5 秒前台轮询 + 操作后全缓存失效；
  「更多」改可见按钮。来源链接保留为独立标注（用户方案选择）。
- 审查结论：与 §review-card-recovery / §review-cta 兼容（频道链接不是公共投稿 CTA，
  只出现在 published 终态只读卡）；`_channel_link` 收敛到 2.84.13 的 domain SSOT。
  曾并行提交的 #390（移除来源跳转）经用户确认被 #391 方案取代，已关闭。
- 测试：#391/#392 CI 全绿（17 项，含 Linux/Windows 全量、webapp、browser-e2e、
  delivery contract）；另一会话本地定向回归 216 backend + 95 frontend + 3 e2e。
- VERIFIED（只读验收）：v2.84.14 8 产物 + PyPI 200；deploy-fly 自动部署成功；
  /health 2.84.14 / 9c8752ab；/ready bot1+bot2 ok；botN inbox pending 0 / dead_24h 0；
  生产包 index-C8XZJyo3 实测含 review-channel / post-channel / openTelegramLink /
  轮询标记；smoke 4/4；fly logs 无错误。回滚点：2.84.13。
- deploy 仓收尾：docs PR #306（基线 2.84.14、回滚 2.84.13）。机内 doctor 未执行。

## 2026-11-05 Mini App 频道入口（2.84.13 已发行部署）

- DONE：Mini App 内容详情/我的投稿详情以频道帖链接为主入口（`channel_url`），
  可选来源链接降为次级 attribution；原创稿（无来源）也有频道出口。
- 实现：domain SSOT `channel_message_url`（navigation.py）；
  `_link`/`_legacy_link` 委托；`/posts/hot`、`/posts/{id}`、`/me/submissions/{id}`
  （published 链头经 `published_message_id`）三处 DTO；webapp 两页 UI + i18n +
  openapi 同步（同切片）。PR #387、release PR #388。
- 测试：全量 1632 passed / 2 skipped；webapp 86 tests + typecheck + lint + build
  全绿。gitleaks 首跑误报 `-100`+10 位测试 id，改为仓库既有短 id 风格后通过。
- VERIFIED（只读验收）：v2.84.13 8 产物 + PyPI 200；deploy-fly 自动部署成功；
  公网 /health 2.84.13 / f58419b、/ready bot1+bot2 ok、botN v1/health 200
  （inbox pending 0 / dead_24h 0）、smoke 4/4（首跑 /live 000 为本地网络抖动，
  复跑 4/4）；fly logs 抽查无错误。回滚点：ghcr 镜像 2.84.12。
- deploy 仓收尾：baseline PR #303、state docs PR #304 已合并；
  current-state.md 基线 2.84.13、回滚基线 2.84.12。机内 doctor 未执行（如实记录）。

## 2026-11-02 小说在线阅读恢复

- IN_PROGRESS：实现预览 CAS、失败诊断、安全补偿与原帖键盘修复，未发行。
- VERIFIED（本次只读）：Fly host_status 已恢复 ok；公网与机内 /health 均为 TelePost 2.84.11 / 进程内 TelePress 0.17.2。bot2 审核 #184 仍 published、root 676、预览 failed / 空 URL。
- VERIFIED：故障定位 TXT 读取阶段；旧适配器吞异常，旧预览账本未保存原因。
- IN_PROGRESS：正常环境恢复补丁并复核；已补同一 boot 身份校验、超时采用保护、重试 CAS 旧代保护和 provider 内部不确定建页重试保护。
- 本环境跨仓实际 HTTP/SQLite 投递契约 + 80 个适配回归通过；TelePress 0.17.2 兼容/恢复 36 passed，0.18.0 定向 82 passed；文档构建及 docsite 9/9/navcheck 通过。
- 已解除的临时访问故障：本次早先 Fly host_status=unreachable / exec 408 / health 超时，后恢复 ok；原帖键盘及 Telegraph 页面仍需补偿前再对账。
- VERIFIED：本次最终全量 1657 passed / 2 skipped；TelePress 上游不确定建页重试修复 PR #83 已创建，448 passed / 1 skipped / 覆盖率 81.72%；待用户合并和发行。TelePost 正在升级到 0.18.1 并移除临时兼容保护，正式发行仍在执行。
- 原帖未修改，发行/部署/原帖恢复验收尚未完成。

## 当前核验：2026-10-31 晚（Asia/Shanghai）

- 当前任务：**DONE（全部闭环）**——多图相册修复 + 发版基建修复已发行；**#224 生产归因已完成**。
- **#224 定论（flyctl 只读取证）**：bot1 pending_reviews#224 = PixivFlow 自动稿（pixiv 151463510），
  **8 张 photo、0 文档**；02:36:09 批准 → 02:36:10 **一次成功**发布 message 3328，
  related=3329–3335（8 消息完整相册）；audit 无错误、delivery_ledger 无 partial/uncertain。
  公开频道 3328 同为 8 图相册；8 ≤ 容量 10，任何 reply mode 预期均为单个完整 root 相册。
  **「首贴单图 + 散图」从未在频道发生**（疑为客户端渲染异常或与审核群 8 条独立预览
  消息混淆，preview_messages:8）。本次修复属防御性加固，非该缺陷的实际触发。
- **生产 env 实测**：`CHANNEL_ALBUM_REPLY=discussion`（Fly secret 一直存在，**从无 chain 漂移**）、
  `MEDIA_GROUP_CAPACITY=10`；2.84.10 的 fly.toml 声明与 secret 同值，是配置即代码化。
- 应用修复 2.84.10（PR #367）：相册拒绝不静默拆图 + `publish.layout` 审计 + 拓扑显式化；
  全量 1621 passed + 契约 VERIFIED。生产验收 smoke 4/4。
- 发版基建 2.84.11（PR #372/#374）：`deploy-docs`→docs.yml；download-page v3 自动 PR 链。
  **2.84.11 post-release 五项全 success**（refresh-download-page/pypi/deploy-docs/
  deploy-production/notify），自动 PR #375 检查绿后自动合并、Pages 自动重建。回滚点 2.84.9。
- 机内 doctor：仅 2 CRIT = inbox 死信 bot1:8/bot2:7（10-25 已知历史遗留，非回归），其余全 OK。

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
