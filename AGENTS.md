# AGENTS.md —— 本仓库是「业务平面」

这份文件写给任何进入本仓库的智能体或工程师。先读跨仓库权威文档，它们共同定义职责边界与执行纪律：

* `pixivflow-telepost-deploy/AGENTS.md`（PixivFlow Ecosystem Agent Operating Contract）
* `pixivflow-telepost-deploy/docs/architecture/ecosystem-platform.md`（长期架构）
* `pixivflow-telepost-deploy/docs/operations/current-state.md`（当前生产状态）
* `pixivflow-telepost-deploy/CONTRACT.md`（PixivFlow ↔ TelePost 跨仓库契约）

本文件只保留本仓边界与特殊约束；与上面权威冲突时以上面为准，并顺手修正。

## 一句话

TelePost 决定 **「投稿如何审核与发布到 Telegram」**：Telegram 更新的接收与路由、会话式私聊投稿、
HTTP 投稿接口、幂等键、审核队列、批准/驳回、发布与发布恢复。

- 它是**唯一持有 Telegram 凭据**（`BOTn_TOKEN` / `BOTn_CHANNEL_ID` / `BOTn_OWNER_ID`）的服务，
  也是**唯一能发布到频道**的服务。
- 它**不决定要抓哪些 Pixiv 作品**——那是 PixivFlow。本仓库里没有 Pixiv refresh token，
  也不应该出现调度 cron / occurrence / 槽位。
- **命名已来源中立化**：投稿 wire 字段是 `work_id`（`pixiv_id` 为 deprecated 别名），
  域层一律用 `work_id`；`pixivflow_jobs.py` 模块名、`PIXIVFLOW_*` 环境变量与
  `pixiv:` 资产 ID 前缀为兼容而冻结。PixivFlow 是当前的 producer，未来第二来源
  走同一端口形状新增适配器，不改这里。

## 目录职责

| 路径 | 是什么 | 不是什么 |
| --- | --- | --- |
| `utils/webhook_server.py` | webhook 模式下的 `setWebhook` 与更新接收 | 不是第二个发布器 |
| `utils/api_server.py` | `/api/botN/v1/submissions` 投稿接口与幂等语义 | 不抓取 Pixiv、不排调度 |
| `utils/submission.py` / `handlers/` | 会话式投稿、媒体收集、预览、取消 | 不绕过审核直发 |
| `telepost/application/review_queue.py` | 审核队列与 `normalize_idempotency_key` | 不是调度账本 |
| `fly.toml` | **本仓库唯一的部署拓扑**（常驻参数、独立卷、健康检查） | 不包含 PixivFlow 与调度配置 |
| `deploy.fly-multi-bot.toml` | **已删除**：它曾在同一容器里再拉起一个 PixivFlow 调度器 | 不要重新引入 |

## 绝对不要做

1. **不要让本服务休眠。** 投稿要求即时响应，auto-stop 的冷启动唤醒会让用户看到「机器人不回复」。
   必须常驻：`auto_stop_machines = false`、`min_machines_running = 1`，并保留 `/health` 长期健康检查。
   成本由 PixivFlow 侧「平时 stopped、按需唤醒」来省。
2. **不要在同一个容器/机器里再跑一个 PixivFlow。** 那会共用内存、进程生命周期、机器生命周期与
   故障域，是历史混部问题的根源。PixivFlow 现在是部署仓库里的独立 App + 独立卷。
3. **投稿处置必须按来源可信度显式建模（`SubmissionDisposition`）。**
   - Telegram Chat（`chat_direct`）：默认 `DIRECT_PUBLISH`，`CHAT_REVIEW_REQUIRED=true` 才进入审核队列。
   - HTTP API / 自动化服务（`source=api`，含 PixivFlow 自动稿）：**固定 `REVIEW_REQUIRED`**，
     必须人工批准后才发布，`API_REVIEW_REQUIRED` 不能关闭 API 审核（仅保留兼容）。
   - Mini App（`source=miniapp`）：由**独立** `MINIAPP_REVIEW_REQUIRED` 控制，绝不与 API 共用开关。
   入口共享同一 domain/service（`queue_review_*` / `publish_from_*`）；重构任一入口**不得**静默改变
   另一入口的默认处置。
4. **不要删掉 `force_https = false`。** 独立 PixivFlow App 通过 Flycast 私网以明文 HTTP 调用投稿
   接口，`force_https = true` 会把它 301 到 HTTPS 并直接打断投递（Flycast/6PN 本身在 WireGuard 上加密）。
5. **不要在别处注册/删除 webhook。** webhook 的负责人只有本仓库的 `webhook_server.py`。
6. **不要在日志、响应或报告里打印任何令牌**（Bot token、投稿 token、webhook secret）。
   只输出「已配置 / 缺失 / 就绪」。
7. **不要在 256MB 上跑。** 512MB 是实测规格；降内存前必须先做真实内存验收（见 `fly.toml` 注释）。
8. **不要把一次性报告或阶段快照提交进仓库。** 任务完成即清理；确需保留的证据统一放
   `docs/archive/` 并带 docsite 生命周期块，不进入用户侧边栏。

## 改动前必须保持的行为

- **Telegram Update 单一语义属主**：Submission `ConversationHandler` 一旦接管更新，必须用
  `ApplicationHandlerStop(next_state)` 同时提交状态并停止后续 handler groups；普通
  `return next_state` 不会阻止跨 group 传播。通用消息/回调 fallback 只处理真正无人接管的
  update，业务 callback 必须先按明确 namespace/pattern 注册。静态“callback 可路由”测试不能
  代替复用 `setup_application()` 的生产 wiring 测试。

- **投稿幂等**：投稿接口接受 `idempotency_key`（也接受表单/字段形式），经
  `normalize_idempotency_key(user_id, raw_key, "api")` 归一；已受理/已发布的重放返回
  `idempotent_replay`，**不重复通知、不重复发布**。这是 PixivFlow 槽位账本之外的最后一道防线。
- **重启可恢复**：审核队列、幂等记录、会话状态都在持久卷 `/app/data` 上；重启后账本存活，
  同一作品不产生重复审核，同一审核不重复发布。
- **审核保留策略**：`PENDING_REVIEW_RETENTION_DAYS=1` 与
  `PENDING_REVIEW_CLEANUP_BATCH_SIZE=20` 与代码默认值不同，必须显式声明（否则会静默改变
  用户可见行为）。
- **多 Bot 同机**：支持 polling 与 webhook；生产 ingress 以部署清单为准。
  `run.py` 路由进程占 `WEBHOOK_PORT`，`botN` 子进程占 `WEBHOOK_PORT+N`；
  webhook 模式的更新路径是 `/webhook/botN`。

## 改完请自证

```bash
pytest -q                          # 全量测试
pytest -q tests/test_api_server.py tests/test_conversation_flow.py
python check_config.py             # 配置自检
```

跨仓库的只读生产校验在部署仓库：`./scripts/verify-production.sh`、`./scripts/smoke-telepost.sh`、
`./scripts/verify-webhooks.sh`（不会打印密钥）。

## 审核群「重抓」不变量

拆分部署下，TelePost 的重抓是**远程服务间工作流**（`split-worker`）：

- TelePost **绝不**在 Bot 容器里 shell-out 或同容器拉起 PixivFlow 来重抓；
  只调用独立 PixivFlow 的受认证作业 API（默认 Workflow Protocol v1 `POST /jobs`；
  回滚开关 `PIXIVFLOW_JOB_TRANSPORT=legacy` 时才走 `POST /internal/targets/{id}/refetch`）。
- TelePost **绝不**操作 Fly Machines API；唤醒交给 Fly `auto_start_machines` 代理。
- 每次重抓 attempt 都是 durable 的（`refetch_attempts`）；同一审核链同时最多一个活跃 attempt
  （数据层 partial UNIQUE index 强制）。
- 按钮点击幂等：同一 `callback_query.id` 的 webhook 重投收敛到**同一个** attempt / requestId；
  只有新的主动点击（新 callback id）才创建新一代。
- `no_alternative` 只属于某一次 attempt，不代表该审核链永久耗尽；之后可再次点击。
- 当前候选与该链历史候选（`refetch_seen_candidates`）永不重新进入同一链；替换成功后
  旧稿标记 `superseded`，旧按钮直接拒绝，不产生分叉。
- 当前稿件保持有效，直到新稿**已落库成功**才转 `superseded`（commit-after-success）；
  失败的 attempt 之后当前稿件不变。
- 迟到的异步结果（approve/reject/expire 之后到达）只会标记 attempt `obsolete`，
  绝不覆盖终态审核结论。
- PixivFlow 拥有执行状态，TelePost 拥有审核状态。正常重抓必须由替换投稿或
  `refetch/outcomes` 主动到达业务终态；watchdog 只是崩溃兜底。已受理 attempt
  不可仅凭本地时间判失败，应先查 PixivFlow durable slot。
- 替换稿必须在预览和控制卡准备成功后，与旧稿 `superseded`、attempt `replaced`
  同事务提交。来源不明或过期的 `refetch_request_id` 不能作为普通投稿落库。
- 点击重抓后**审核卡本身必须立刻反映该事实**（§refetch-card）：控制卡切到「重抓中」形态
  （隐藏 发布/拒绝/遮罩，保留 重抓 与 查看原链接），进度提醒（默认 2 分钟）带已等待时长，
  任何终态（替换稿到达、`no_alternative`/`failed`、watchdog 取消或 stalled、源审核已决）
  都必须把正常可操作卡片交还审核人。**不得**为了让点击“看起来生效”而提前把源审核标记为
  rejected：`finalize_replacement()` / `apply_outcome()` 只在源审核仍为 `pending` 时落结果，
  提前驳回会让自己的替换稿变 `obsolete`，审核人可能什么都发不出去。
- Workflow Protocol §events 通道（`POST /api/botN/v1/jobs/events` 事件入口 +
  `reconcile_refetch_events` 调和循环）是终态到达的**加速与自愈**通道，不是新的业务状态机：
  事件与 `poll_refetch_jobs` 汇合到同一个 `apply_outcome()` + `terminal_notified_at` 终态缝，
  以 `event_id`（`protocol_job_events` UNIQUE 索引）幂等去重 —— 回调重放、回调丢失后被调和
  拉取补回，都只能各自应用一次终态、最多发一次终态通知，绝无二次通知。Task 上声明的
  `callback_url` 只在 `TELEPOST_API_BASE_URL`「已配置」时携带（见 docs/CONFIGURATION.md）；
  事件入口用的是 `refetch/outcomes` 同款 bot 鉴权，不新增共享凭据。

## Refetch Generation Replacement 不变量（§refetch-replacement）

```text
Refetch is a generation replacement operation, not the creation of
an independent parallel review.

A successful replacement atomically supersedes the previous current
review generation and installs the replacement as the new chain head.

A failed or no-alternative refetch leaves the current review unchanged.

Superseded reviews are terminal history and must reject all moderation
mutations.

Superseded is not rejected and must never trigger submitter rejection
notifications.

Only explicit rejection of the current chain head may produce a
rejection notification.
```

- 原子 replacement（同一事务）：`finalize_control` → B pending 落库 + A
  `superseded` + A/B 链与 generation 链接 + attempt `replaced`；B 变成链头。
- 任何时刻每条链至多一个 active generation（preparing/pending/publishing），
  由数据层 partial UNIQUE + 应用事务保证，测试用 consistency query 复核。
- stale guard 是后端的硬防线（UI 只辅助）：对 `superseded` 代的 approve /
  reject / spoiler / refetch / editorial create-update-finalize-publish 全部拒绝
  （409 review_superseded / editorial_stale），提示「该审核稿已被重抓结果替代，
  请审核最新版本。」Telegram 旧按钮点击同样被挡，绝不发「投稿已拒绝」通知。
- replacement 提交后 best-effort 重写旧审核卡为「♻️ 已被重抓结果替代 + 新稿
  编号」并移除内联键盘；失败不回滚 replacement（后端 stale guard 仍生效）。
- reject 通知只由 current head 的显式拒绝触发：chat 投稿人发到 user_id，
  human Mini App 投稿人发到 submitter_user_id（service 绝不通知）。
- superseded 绝不发拒绝通知；superseded ≠ rejected（历史记录，非审核结论）。
- **连续重抓（A→B→C）**：第二次重抓的源是上一轮的替换结果，链不分裂、代数递增
  （0/1/2）、仍只有一代 active；因果留痕 `111 --replaced_by--> 222 --replaced_by--> 333`
  （`refetch_seen_candidates.outcome='rejected_by_refetch'`，当前候选 `pending_review`）。
  回归测试：`tests/test_refetch_replacement.py::test_chained_refetch_a_to_b_to_c_keeps_one_active_generation`。
- **替换被丢弃时必须可见**：迟到或来源过期的替换稿会被交付入口拒绝
  （`_reserve_replacement` 的两处 400：`refetch attempt is unknown or terminal` /
  `refetch attempt is cancelled or already terminal`，文案是 PixivFlow 的死信契约，
  **逐字不可改**）。拒绝**之前**必须：向源审核控制卡通知「重抓未生效：原审核已结束
  （当前状态：…），替换作品已丢弃，当前稿件保持不变。任务ID：…」，并落一条
  `review.refetch_dropped_replacement` 审计（`error_class` 为
  `refetch_attempt_obsolete` / `refetch_attempt_unknown`，request id 在 `detail`）。
  丢弃允许，静默丢弃不允许——这正是 30 天生产静默的成因之一。

## 已知待办（本仓库范围）

- `ROADMAP.md` 中与「拆分拓扑」相关的条目若已完成，请更新；不要再保留第二份 Fly 拓扑文件。

## 投稿 UX 与预览不变量（§submission-disposition, §preview-ux）

- **Preview 必须 side-effect free**：Mini App 本地媒体预览用浏览器能力
  （`URL.createObjectURL`，remove/unmount/提交成功后 revoke），caption 预览来自服务端
  formatter；绝不为预览上传审核群再删消息（制造垃圾审核消息与 ghost mention）。
- **Tag 前端只做 UX 提示**：helper text 明确空格 / 英文逗号 / 中文逗号分隔；拆分、去重、
  规范化、补 `#`、非法字符处理仍是服务端 `process_tags` 的唯一职责。
- **Mini App Web UI 显示 `@username` 是 DOM 展示**，与 Telegram 消息 entity 无关；审核群 /
  system 消息仍然零 mention-capable entity。显示身份与通知意图始终分离。
- `nickname/display_name` 只是 presentation metadata；ownership 权威仍是 `submitter_user_id`。

## Telegram Mini App 不变量（presentation adapter, §88/§149）

- Mini App 是 **presentation / UI adapter**：业务状态只属于 TelePost 的
  domain / application / storage。禁止为网页方便直接改 DB 或把 review transition
  复制成 TypeScript 状态机。
- Bot 与 Mini App 必须使用**同一组 application service**（例如 `request_refetch`
  同时服务 Bot 按钮和 `POST /api/v1/reviews/{id}/refetch`）。禁止第二个实现。
- 浏览器永不接收：Bot token、长效 TelePost admin token、PixivFlow service secret。
  `POST /api/v1/miniapp/session` 是唯一 Mini App 认证入口，服务器用 `init-data-py`
  验证 `initData`（绝不信任 `initDataUnsafe` 里的身份）。
- 授权只发生在服务器：RBAC 复用 `ADMIN_IDS`/`OWNER_ID`，deep link 只给导航。
- `webapp/` 的测试/构建是独立生命周期（`npm run typecheck|lint|test|build`），
  但业务正确性必须靠 Python 侧的 domain/application 测试保证。
- Telegram launch data 以 `@telegram-apps/sdk` 的原始 initData 为主；
  `window.Telegram.WebApp` 仅为兼容后备。`/app` 返回 HTTP 200 不等于
  真实 Telegram Mini App 完成认证与业务操作。

## Mini App 基础设施不变量（framework-first，硬约束）

- **框架优先**：通用 Telegram/上传/缓存/分页/组件基础设施必须交给已安装的成熟库；
  禁止自己重写 Telegram viewport adapter、safe-area system、文件管理器、upload queue、
  retry manager、pagination framework、modal framework。
- **布局**：底部导航**不得覆盖**路由内容（shell 预留真实测量高度 + SDK safe area，
  不硬编码设备偏移）；Telegram viewport/safe-area 信息来自 `@telegram-apps/sdk`
  （`viewportSafeAreaInsets` / TelegramUI `--tgui--safe_area_inset_*`），不是 iPhone hack。
- **上传**：Uppy 拥有通用附件状态（选择/限制/去重/移除/进度/错误）；TelePost 禁止再维护
  一套并行文件管理器。提交传输是 TelePost 业务 adapter（一次 multipart + 稳定幂等键），
  Uppy 的 XHRUpload/Tus 只在业务契约需要时引入。
- **导航**：`navigationForSpace(isReviewer)` 是底部导航与路由的唯一来源；所有用户都有
  首页/投稿/我的投稿，reviewer/admin 额外看到审核队列并注册 `/review*` 路由。展示层只做
  隐藏，服务端 RBAC 仍是唯一权威；新路由必须走这套导航函数，禁止各页面自拼底部导航。

- **预览两态**：Mini App 用户空间「预览投稿」只做本端预览（可返回修改或提交审核，
  无侧写）；私聊预览是真实 Telegram 渠道预览（首次 `/done_media` 发送一次，刷新不重复）。
  两个入口独立实现、业务管道共用，禁止互相搬运状态。
- **「我的投稿」**：指人类属主的 logical submission（一个 review chain = 一条）；actor /
  token 持有者 / transport / submitter 是四个概念；服务自动化没有人类 submitter；
  refetch 代际折叠为一条投稿；状态映射在服务端完成，内部数据库态不外泄。

## 身份与归属不变量（硬约束，§identity）

四个概念永远是四个，不许折叠：

```text
actor      谁/什么执行了这次请求      user | service | unknown
submitter  稿件在业务上归属哪个用户   pending_reviews.submitter_user_id
source     传输/来源 provenance       chat | api | ...
api token 持有者                     绝不是 submitter
```

1. **Authentication actor is not submission ownership.** 请求主体身份不产生归属。
2. **Service/API principal MUST NOT automatically become submitter.** `api_tokens.telegram_user_id`
   只用于鉴权与审计，永不写入 `submitter_user_id`。
3. **source/transport MUST NOT determine submitter ownership.** Mini App 人投稿走的也是 HTTP。
4. **`/me/submissions` requires explicit verified human submitter attribution.** 查询键是
   `submitter_user_id`；只有 `kind=user` 的 Mini App session 与聊天投稿会写入它。
5. **PixivFlow scheduled/API submissions have no human submitter**（`submitter_user_id IS NULL`）。
6. **User Mini App submissions remain human submissions** even though the transport is HTTP。
7. **Refetch preserves review-chain submission ownership**：替换稿由 service 投递
   （`actor_kind='service'`），但 `submitter_*` 继承 chain 根；human chain 保持 human，
   service chain 保持 unowned。
8. Mini App 是 presentation adapter，绝不是第二套 backend。
9. Bot 与 Mini App 的管理动作必须共享同一 application service（policy/blacklist/status）。
10. 浏览器永不接收长效服务/admin 凭据（Bot token、API token、PixivFlow secret、Fly token）。
11. 每个 admin mutation 都要服务器端 RBAC + audit（必要时幂等 + 二次确认）。
12. 契约变更必须与文档/OpenAPI/测试在同一个逻辑切片里落地。

`pending_reviews` 字段语义（改动前先读这一节）：

| 字段 | 含义 | 是否用于归属 |
| --- | --- | --- |
| `user_id` / `username` | 请求身份（legacy 显示与幂等归一） | 否 |
| `submitter_user_id` / `submitter_username` | 已验证的人类投稿人 | **是（唯一）** |
| `actor_kind` / `actor_subject` | 执行主体（`user` / `service` / `unknown`） | 否 |
| `source` / `target_id` / `source_ref` / `source_label` | provenance | 否 |

禁止再出现 `principal.telegram_user_id → submission.owner` 这种实现。

## Schedule 通知与 mention 不变量

- PixivFlow 拥有 schedule 执行账本；TelePost 只接收并发送终态通知。
- 每个 occurrence 的 success / partial / failed 都要到达审核群；发送失败或 claim
  尚未完成返回可重试错误，不得提前写成功回执或 ACK 让上游 outbox 丢弃 intent。
- Display identity 与 Telegram notification intent 分离；review/system 默认无 mention。
  自动稿不能把 API credential holder 显示为人类投稿人。
- Chat 与 Mini App 都收敛到 QueueCommand / ReviewQueueService；Mini App 发 bytes，
  服务端 Telegram staging 才获得 file_id。

## Refetch 生命周期状态机（§refetch-lifecycle）

```text
REQUESTED → SEARCHING → FILTERING → CANDIDATE_FOUND → REPLACED
     └──────────┴────────────┴──────────────┴──→ FAILED / TIMEOUT / NO_CANDIDATE / CANCELLED
```

- **SSOT**：`telepost/domain/refetch_state.py` 是唯一权威（状态常量、`ALLOWED`
  迁移表、`assert_transition`、`normalize`/`to_legacy`、中文 `label`、
  `REMOTE_CELL_STAGES`/`stage_for_remote_state`）。DB 与 Mini App 继续导出旧词以兼容
  旧客户端：`admitted→searching`、`no_alternative→no_candidate`、`obsolete→cancelled`
  （`timeout` 映射回 legacy `failed`）；`sql_state_list` 让 active/terminal 集合只有一份。
- **唯一写入口**：所有 attempt 状态变更必须经 `apply_transition_on`
  （`telepost/storage/sqlite/refetch.py`）。它强制迁移合法性（非法迁移抛
  `IllegalRefetchTransition`）、写 `updated_at`/`finished_at`/`terminal_reason` 并追加
  `refetch_events` 时间线。**禁止绕过 repository 直接 `UPDATE refetch_attempts`**
  （历史上 5 处写入、2 处绕过，是本轮「不一致」根因）。
- **`failure_code` 只属于失败终态**（FAILED/TIMEOUT）；其他终态把原因写
  `terminal_reason`，绝不把「迁移原因」混进 `failure_code`。
- **无进展不算进展**：无状态迁移、无新远端状态、无可变列差异的重复轮询不写
  `updated_at`，否则 watchdog 永远看不到 stage stall。
- **终态可查**：审核卡与 Mini App 显示 任务ID `refetch-<source_review_id>-<epoch秒>`
  + 阶段中文标签 + 已等待秒数；`get_refetch_state` 在无 active attempt 时回落到
  `find_latest_by_chain`，终态同样可被查询（终态不是「查无此物」）。
- **候选历史**：`refetch_seen_candidates` 记录 generation / candidate_id / source /
  request_id / outcome / reason / decided_at / replaced_by——谁被拒、为什么、何时、
  被谁替代；`refetch_events` 是 attempt 的耐久时间线。运行历史与「当前状态」都不许只存在于内存。

### 重抓是一等持久 Job（30 秒心跳，§refetch-job）

- **作业模型**：attempt 即 job，进程内存只是缓存，重启后从库里恢复。新增列：
  `heartbeat_at`（**本机**轮询心跳，证明我们还在看）、`heartbeat_count`、
  `remote_heartbeat_at`/`remote_state_at`（**远端** liveness 时钟）、`next_poll_at`（退避）、
  `poll_failures`、`terminal_notified_at`（终态通知时钟）。`terminal_notified_at` 必须与
  `last_progress_notified_at` **分开**：两个时钟合成一个，就是生产上「进度播报之后终态静默」的根因
  （进度提醒吃掉了终态通知的一次性额度）。全部列在 `database/db_manager.py` 里以幂等
  additive migration 声明（可空/带默认值，历史行照常读），并做一次
  `terminal_notified_at = COALESCE(finished_at, ...)` 回填**仅限已通知过的行**，
  历史 `notify_count = 0` 的行永远不回填、不补发。
- **心跳循环**：`handlers/review.py:poll_refetch_jobs(bot, *, now=None, force=False)`。
  `main.py` 用 `job_queue.run_repeating(..., interval=REFETCH_POLL_INTERVAL_SECONDS(默认 30), first=5)`
  注册；300 秒的 `cleanup_runtime_data` 调的是**同一个**函数（`force=True`），
  绝不出现第二份 watchdog 实现；`monitor_refetch_progress` 只是它的转发别名。
  单条 attempt 出错只记录日志，绝不中断整轮（一条坏任务不能拖垮其余）。
- **心跳写路径**：`RefetchRepository.record_poll(...)` 只写心跳/远端时钟/退避/`poll_failures`，
  **绝不写 `state` 或 `updated_at`**——否则「无进展」会被自己的轮询刷新掩盖，stall 永远看不到。
  状态迁移仍然只能走 `apply_transition_on`（唯一写入口）。
- **远端访问只在端口里**：`telepost/application/pixivflow_jobs.py` 是唯一边界，暴露
  `submit(job_type, idempotency_key, *, correlation_id, params)` 与
  `get(job_id_or_key, *, job_type, params)`；心跳与状态机只依赖该端口，**不得**直接拼 HTTP 路径。
  Workflow Protocol v1 是默认通道（`POST /jobs`、`GET /jobs?idempotency_key=` 或
  `GET /jobs/{job_id}`，进入前先 `GET /capabilities` 协商协议版本与 `candidate_search`）；
  旧路由 `/internal/targets/{id}/refetch` 仍封装在端口内部，用
  `PIXIVFLOW_JOB_TRANSPORT=legacy` 回滚。切换只改这一个文件，状态机与心跳零改动。
  事件循环里绝不允许同步远端调用（一律 `asyncio.to_thread` + 可控超时）。
- **跨边界字段只用不透明关联**：`idempotency_key` / `correlation_id` / `job_id` / `labels`；
  `slotId` 只作诊断保存，**不得**把远端业务名词（slotId/disposition/「审核群重抓」…）当作
  状态判定输入——判定只看 status、心跳与时间戳、error.code。
- **liveness 规则：无进展且无心跳才算停摆**（「无进展且无心跳才算停摆」）。
  远端状态变化或远端新心跳都会重置停摆时钟；「我们还在轮询」**不算**远端进展。
- **重启恢复**：`main.py` 启动序列在 `/ready` 之前调用
  `handlers/review.py:recover_refetch_jobs(bot)`（一次性 sweep，进程内只跑一次，
  `_refetch_recovery_done` 守卫）：扫描非终态且心跳老于 2 个轮询周期的 attempt，
  读远端后把**已终态**的远端状态经状态机落库，并记一条
  `review.refetch_recovered_after_restart`（含扫描/收敛计数）。
  重启不得因为「本地很久没心跳」就把在途 attempt 判死：`heartbeat_at IS NULL`
  意味着「本进程还没轮询过」，只能去轮询，不能判失败。
- **永远有终态**：任何非终态 attempt 都在有限时间内收敛到终态（见
  §refetch-terminal-notify 的预算），不存在永久 running 的行。

## Refetch 终态可见性 与 watchdog 不变量（§refetch-terminal-notify）

- **已决审核的重抓终态必须可见**：cancelled（outcome 到达时源审核已被驳回/通过）与
  watchdog 的 `source_review_resolved` 分支都必须向审核群通知一次（“重抓已取消，当前稿件
  不变”），绝不能用无限“仍在处理中”掩盖 silent terminal。replay（已终态重复投递）不重复通知。
- **进度必须重复播报**：每 `REFETCH_PROGRESS_REMIND_MINUTES`（默认 2）分钟向审核群播报
  一次（阶段中文标签 + 已等待时长 + 任务ID），不是「每个 attempt 只提醒一次」。
- **终态必须恰好通知一次（idempotent by 任务ID）**：成功即审核卡变成替换稿；任何
  失败/超时都要在卡上显示原因 + 「可以再次重抓」 + 任务ID。一次性额度由
  `terminal_notified_at` 记录（与进度时钟分开）；直接发送失败时，通知文本进
  `submitter_notifications`（`kind='refetch_terminal'`，幂等键
  `refetch:<request_id>:terminal`）由 300 秒任务补发，并发一条
  `review.refetch_terminal_notify_undelivered` 审计。**发送失败绝不允许被吞掉。**
  历史行（`notify_count=0` 的 30 天前终态）永不补发、永不回填。
- **预算表（全部是环境变量开关，默认值如下）**：

  | 开关 | 默认 | 判定 |
  | --- | --- | --- |
  | `REFETCH_PROGRESS_REMIND_MINUTES` | 2 | 重复播报间隔 |
  | `REFETCH_QUEUED_TIMEOUT_MINUTES` | 30 | 远端一直 `pending`（从未被认领）→ `timeout(queued_too_long)` |
  | `REFETCH_STAGE_TIMEOUT_MINUTES` | 15 | **无远端状态变化且无新远端心跳**超过该时长 → `timeout(stalled_no_progress)` |
  | `REFETCH_STALE_TIMEOUT_MINUTES` | 20 | **本机**心跳过期（轮询器死了）→ `failed(watchdog_no_heartbeat)`；也是未受理 `requested` 的 admission 超时 |
  | `REFETCH_HARD_TIMEOUT_MINUTES` | 90 | 自 `created_at` 起的绝对上限 → `timeout(stalled_after_hard_timeout)` |
  | `REFETCH_WAKE_MINUTES` | 12 | 幂等唤醒间隔 |
  | `REFETCH_POLL_INTERVAL_SECONDS` | 30 | 心跳轮询间隔 |

- **停滞必须收敛，且 Liveness 优先**：远端仍在 working（或远端状态读不出来）但
  「无进展且无心跳」连续 `REFETCH_STAGE_TIMEOUT_MINUTES`（默认 15）分钟 →
  `timeout(stalled_no_progress)`；绝不能出现「无限 SEARCHING」。合法但缓慢的 2–20 分钟
  （历史上曾排队 10 小时）必须被**远端新心跳或状态迁移**保护：二者任一都重置停滞时钟，
  「我们还在轮询」**不算**远端进展。停摆时钟锚点是「最后一次远端进展」：
  `remote_state_at` → `updated_at` → admission/created。
- **预算优先级**：`pending` 的远端既受排队预算也受停摆预算约束，先到者判定；停摆阈值
  更短时（默认 15 < 30）先报 `stalled_no_progress`，排队时钟已过期才报
  `queued_too_long`。两者都是终态、都带 `terminal_reason`、都会通知审核人。
- **admission 超时只针对未被接受的 requested**：超过
  `REFETCH_STALE_TIMEOUT_MINUTES`（默认 20）分钟仍未被 PixivFlow 接受 →
  `timeout(admission_timeout)`；该分支在分支顺序中**先于**硬上限与 `watchdog_no_heartbeat`，
  且必须 `continue`，不得再落进硬上限分支（否则两个终态会互相覆盖 `failure_code`）。
- **`heartbeat_at IS NULL` 不是「心跳过期」**：它表示「本进程还没轮询过这条 attempt」
  （历史行或刚重启接管），必须去轮询，绝不能凭它判 `watchdog_no_heartbeat`。
- **watchdog 对同一 request UUID 可做幂等 wake**：机器不可达且超过
  `REFETCH_WAKE_MINUTES`（默认 12）分钟无进展、**且仍在硬上限内**时，用同一 request UUID
  再调 PixivFlow refetch（Fly Proxy 拉起机器，PixivFlow 恢复既有 manual slot）；
  不得创建第二条 attempt。已过 `REFETCH_HARD_TIMEOUT_MINUTES` 的 attempt 只终止、不唤醒。
- **硬性 SLA**：超过 `REFETCH_HARD_TIMEOUT_MINUTES`（默认 90）仍无法形成任何 terminal
  outcome 时，attempt 必须 `timeout(stalled_after_hard_timeout)` 并通知审核群——accepted
  重抓绝不永久 running。硬上限判定放在远端判定之后，避免掩盖远端给出的真实结论。
- **交付入口没有静默路径**：`telepost/application/review_queue.py:_reserve_replacement`
  的两处 `ValueError`（`refetch attempt is unknown or terminal` /
  `refetch attempt is cancelled or already terminal`）**HTTP 400 文案必须逐字不变**
  （PixivFlow 按字符串死信），但抛之前必须先向审核群通知「重抓未生效：原审核已结束
  （当前状态：…），替换作品已丢弃」并记 `review.refetch_dropped_replacement`
  （`error_class` = `refetch_attempt_obsolete` / `refetch_attempt_unknown`，request id 进
  `detail`）。丢弃是允许的，静默丢弃不允许。
- **只读自检**：`python -m telepost.observability.cli doctor`（`--bot/--all-bots/--json/--now`）
  校验 DB 完整性、卡住的 refetch（active >15 分钟 WARN / >30 分钟 CRIT）、
  active partial-index 不变量、孤儿审核、publishing 卡住、delivery outbox、近期 audit；
  并有 refetch 作业段（`refetch_jobs`），固定输出一行
  `Refetch: running: N stuck: N failed(last24h): N`：`running` = 非终态 attempt 数，
  `stuck` = 非终态但本机心跳超过 20 分钟（`CRIT`/exit 1，一眼可见「卡住」），
  `failed(last24h)` = 24 小时内到达 FAILED/TIMEOUT 的 attempt 数（按 `terminal_reason`/
  `failure_code` 归类）。同时计数「终态但 `terminal_reason` 为空」：
  **新代码路径**的行计入不变量（WARN），生产既有 12 行 legacy 历史行只单独计数
  （`legacy_missing_reason`，informational），不计入 CRIT，保证已部署环境仍 exit 0。
  全部 `mode=ro`，退出码 0/1/2，绝不打印令牌。

## Editorial Revision 与投稿者通知不变量（§editorial, §notify-submitter）

- Original submissions 是 immutable historical evidence：审核员编辑的是 Revision，
  绝不 mutate pending_reviews 原稿（含 media_json）——媒体排序/移除只生成发布子集，
  原始附件永远保留可审计。
- Review FSM 回答“能不能发布”；Editorial Revision 回答“发布哪个版本”。两者正交；
  只有 finalized 版本可以发布；发布使用 immutable Publication Snapshot
  （published_snapshot + published_source_revision_id），之后不能再改。
- Revision 编号单调且数据层唯一（UNIQUE(review_id, revision_number)）；并发编辑用
  version CAS（409 editorial_conflict）；stale generation（refetch 已替换链头）的
  revision 绝不可发布（409 editorial_stale）。
- **空 `review_chain_id` 的审核就是自己单行链的链头**：普通投稿（chat / Mini App / API）
  落库时 chain id 是空串，只有 refetch 替换稿才带 chain id（替换时两行同时写入）。
  所以空 chain id 必须直接视为 current head，绝不能拿合成 id 去查链——否则每条新投稿
  都会被误判为「已过时」而完全无法编辑（2.27.1 修复）。
- **Revision 编辑是 PATCH 语义**：payload 只改它携带的字段，其余字段保留 draft 的当前值
  （新建 draft 从投稿原稿复制）。未提到的字段绝不回退成原稿——那会静默丢掉上一次编辑；
  要还原某个字段就显式传空值。webapp 每次保存都发送完整可编辑状态。
- **投稿者发布通知由 Publication Success 触发，绝不由 Review approval 触发**
  （approval 只是中间审核事件）。适用于 DIRECT_PUBLISH、REVIEW_REQUIRED、
  EDITORIAL 三条路径统一 pipeline；idempotency key
  `publication:<message_id>:submitter-notification` 保证同一 publication 最多一条
  DM；投递失败只重试通知行，绝不回滚 publication。
- 匿名 human 投稿保留 ownership，仍可私聊通知；Service submission
  （submitter_user_id IS NULL）绝不把 actor/credential holder 当投稿者通知。
- 不改变 disposition 默认值：Chat 默认 DIRECT_PUBLISH，Mini App/API 默认
  REVIEW_REQUIRED。

## Manager 新投稿通知与多图 packing 不变量

- Human submission acceptance 与 submitter publication notification 是两个事件：
  REVIEW_REQUIRED 在审核稿/控制卡 durable 落库后通知 manager；DIRECT_PUBLISH 只在
  Publication Success 后通知。
- Manager 通知复用 durable notification outbox，按 logical submission 幂等；refetch
  generation、editorial revision 和重放不重复发送，manager 自投不发冗余提醒。
- 匿名 human 保留 ownership：manager 通知显示内部用户 ID（`tg://user` 链接）供封禁，
  但不展示 username / display name；service submission 不发 manager 新投稿提醒。
  允许展示时，username 或 display name 均链接到显式 submitter_user_id。
- Admin 手动兜底命令 `/ban_user` / `/ban_api` 与按钮写同一 moderation subject；
  用户命令会同步 legacy blacklist 表，确保所有入口一致拦截。
- 多图 publication 使用 capacity-first packing：root publication 先填满 Telegram
  media-group 容量，再把 overflow 按同一容量分批发到 replies/discussion；caption 只在
  root，canonical link/message_id 也始终指向 root。
- `chain` / `post` 的确定态部分失败必须 checkpoint 已确认消息并只续发余量；网络响应
  不确定时禁止盲目续发，保持 uncertain 直到人工核验。

## Media Capacity SSOT 不变量（§media-packing）

```text
Multi-media publication uses capacity-first packing.
```

- Telegram media-group 容量只定义一次：`telepost/domain/packing.py` 的
  `MEDIA_GROUP_CAPACITY`（env `MEDIA_GROUP_CAPACITY`，默认 10，clamp 到 Telegram 上限）。
  `handlers.publish.CHANNEL_ALBUM_SIZE`、`PublicationService` / `PublishCommand` /
  delivery gateway / planner 的默认值全部读取它；禁止在 handler 里散落 `10`
  （含 `handlers/preview_handlers._send_preview_media` 的预览相册：容量被下调时，
  审核预览必须与实际发布按同一容量分批，否则审核看到 10 张、发布只发 5 张）。
- `pack_media(ordered, capacity)` 是纯函数 SSOT：`root = first capacity`，
  overflow 按同一 capacity 分块。11 → root 10 + reply 1；21 → root 10 + reply 10 + reply 1。
- 一个 media group 在业务上是 ONE root publication，即使 Telegram 把它建模为多条
  Message；caption 只挂 root，canonical link/message_id 指向 root。

## Discussion 失败隔离不变量（§discussion-failure-isolation）

```text
A confirmed channel root must never be rolled back because a
linked-discussion follow-up failed, and must never be re-posted on retry.
```

- 一旦频道 root（cover）确认投递，它就是既定 `Publication`。linked-discussion
  的 overflow（anchor 等待 / 评论区相册）失败**绝不删除**已确认的 root，也**绝不整组重跑**
  （重跑会重复发 root）。cover 未确认时的失败才允许回滚整个 attempt 并重试一次。
- 确定态 overflow 失败：best-effort 删除评论区已确认的 overflow，保留 root，把 root
  作为发布结果返回；溢出缺失仅降级并被操作者告警日志标记。
- 不确定态 overflow（评论区相册响应丢失）：保留 root **且不盲删**可能已落地的 overflow；
  Publication 以 root 记成功，操作者需人工核验评论区。
- 回滚函数 `_do_rollback` / `_discussion_rollback` 只清理 rest/anchor，绝不触碰 cover。

## Schedule 失败可观测性与 Manual Recovery 不变量（§failure-observability, §manual-recovery）

```text
Schedule terminal failures preserve normalized durable reasons.
Recovery exhaustion is not itself the root cause.
Automatic retry exhaustion does not prohibit operator-initiated recovery.
Manual recovery retries failed targets only.
Relaxed recovery is a predefined occurrence-scoped server policy,
not arbitrary client-supplied tuning.
```

- PixivFlow 在每个 terminal cell 持久化 `terminal_reason_code` + 业务消息；outcome
  payload 原样携带。`build_schedule_outcome_text` 只展示一级原因（业务语言），
  绝不把 stack trace / 路径 / SQL / token 发到 Telegram。
- 失败的 schedule 终态消息为失败 target 提供 `[再试一次]`（normal）与
  `[放宽条件重试]`（relaxed）按钮；回调 `sched_recover|<target_id>|<mode>` 只提交
  服务器预定义 policy preset，客户端不传 acquisition 参数。
- Recovery 请求 durable + 幂等（同 callback key 收敛到同一 attempt；每个 target
  同时最多一个 active attempt，数据层 partial UNIQUE 保证）；等待 resource 容量时
  显示「已受理，系统会自动继续处理」，不显示队列内部细节。
- Recovery 只重跑失败 target；成功 target / 历史自动执行结果绝不重写。Recovery
  outcome 渲染为「已恢复（…）」而非 daily summary。

## Publication Presentation 不变量（§publication-presentation）

```text
Only explicit submitter identity may be presented as the submission author.
Actor, source, API credential holder, token alias, and service principal
must never be used as fallback submitter identity.
Anonymous hides public submitter presentation but does not erase human
ownership.
Service submissions with submitter_user_id = NULL have no human submitter.
Publication media actions must reflect the actual final published
attachment types. Document-only publications must not expose a
visual-media "view" action unless that action explicitly means
submission detail and is labeled accordingly.
```

- 展示语义的唯一权威在 `telepost/domain/presentation.py` + `build_caption`
  （utils/helper_functions.py）：入口（Chat / Mini App / API / PixivFlow）不
  决定最终频道语义，附件类型与显式 submitter 才决定。
- “点击查看”（剧透媒体提示）只对 photo / video / animation 出现；
  document / audio / 无附件绝不出现；mixed（photo+document）保留。
- **混合媒体不降维**：document 是素材，不是“media 为空时的替身”。
  归档 `file_ids` 必须按投递顺序保留 media + documents；禁止用
  `media if media else documents` 丢弃 document。`SubmissionText` 只承载
  multi-document publication 的投稿级 caption，排到最后一条纯文本消息，
  绝不计入 media/document 数量，也绝不伪装成附件。
- `投稿人` 只能来自 `submitter_user_id` / `submitter_username` /
  `submitter_display_name`；
  `user_id` / `username`（请求身份 / token alias）永不作投稿人展示。
- 匿名 human 隐藏公开投稿人，但 ownership 与发布成功私聊通知不变；
  service（submitter NULL）没有任何人类投稿人，也不发 human 通知。
- 内部 surface（审核卡/审核预览）可为无人属主投稿显示 `来源：API /
  PixivFlow`（真实 source/provenance）；公开 surface（频道 / Mini App 预览）
  永不显示来源行。
- 频道公开署名与 manager 新投稿消息是 intentional identity contexts：非匿名 human
  使用显式 submitter_user_id 的 `tg://user` link；匿名 human 在 manager 通知中只显示
  user ID（不显示 username / display name）供封禁。review/refetch/schedule/system
  状态消息不创建 user mention entity。

## Submission CTA 不变量（§submission-entrypoint）

```text
Channel publication navigation footer CTAs open the owning bot's
submission surfaces:
  READ_ONLINE     → Telegraph preview (novel_preview_url), when present
  BOT_SUBMIT      → https://t.me/<bot>?start=submit
  MINI_APP_SUBMIT → Direct Mini App when MINIAPP_SHORT_NAME is configured;
                    otherwise ?start=miniapp fallback (bot then opens a
                    private-chat Web App button). MINIAPP_SUBMIT_CTA must
                    also be enabled.

Each action appears EXACTLY ONCE in the footer.
```

- URL 构造唯一权威在 `telepost/domain/navigation.py`：`bot_submission_url`
  只接受 `https://t.me/<bot>` 形式并返回 `?start=submit`；`miniapp_submission_url`
  返回 `?startapp=submit`（或 Direct Mini App 的
  `https://t.me/<bot>/<short_name>?startapp=submit`）。
- `start=submit` / `startapp=submit` 只是导航意图：绝不放入 user id /
  username / token / session，也不被当成认证或授权；Mini App 身份仍只来自
  服务器校验的 Telegram initData。
- 频道 footer 是在 caption 内的文本导航行（不是单一 inline button），只承载
  投稿 CTA：`✉️ TG 投稿 | 📱 Mini App`；Mini App 未启用则只剩 `✉️ TG 投稿`。
  标签是固定展示契约，不接受 `CHANNEL_FOOTER_TEXT` 覆盖。小说预览成功时的
  `📖 在线阅读` 是主贴 root 的 inline keyboard URL 按钮（见 §online-reading），
  不再出现在 caption footer 文本行（避免与 TXT document 并列或重复）。
- `_publication_navigation()` 是加入 footer 的唯一位置，chat-DIRECT、API 直发、
  review 通过、editorial、PixivFlow/service 全部经 `channel_caption()` 汇聚；
  禁止各 handler 各自拼 CTA。
- `MINIAPP_SUBMIT_CTA` 未启用、链接缺失或非法时省略对应导航项；绝不产生
  `https://t.me/None...` 或坏链接。
- CTA 进入既有 caption 预算（`channel_caption` 预留 footer 宽度），不得让
  Publication 因加 footer 超出 Telegram 上限。

### 频道小说帖「在线阅读」= 主贴 root 的 inline 按钮（§online-reading）

- 形态：小说频道帖的「在线阅读」是**主贴 root 消息下方的 inline keyboard
  URL 按钮 `[ 📖 在线阅读 ]`**，按钮 `url` 指向 Telegraph 预览页
  （`novel_preview_url`）。它**不是** caption footer 超链接、**不是**尾随裸链
  TEXT 消息、**不是** Instant View / `LinkPreviewOptions` 预览卡片。
- 出现条件（三者同时满足，由 `presentation_policy.build_publication_presentation`
  决策，见 §online-reading-invariant）：
  1. surface = `CHANNEL_PUBLICATION`；
  2. 小说预览 SUCCEEDED（`PreviewState.SUCCEEDED`，预览页 URL 有效）；
  3. root 是单条可承载 `reply_markup` 的消息（普通小说 root 正是：单 TXT /
     单封面图 / 单 fallback card，`plan.batches[0].kind == SINGLE`）。
- 纯数据流动：`PublicationService` 把 `NavigationItem(READ_ONLINE)` 放进
  `DeliveryRequest.root_navigation`；`telepost/telegram/delivery/gateway.py`
  经 `PTBSender._navigation_markup()` 转为 PTB `InlineKeyboardMarkup`，仅挂在
  整段投递的**第一条**消息（root）。专辑（album）不能挂 `reply_markup`，
  `execute_plan` 自动丢弃；溢出 / discussion 回复绝不继承 root navigation
  （§root-only-navigation）。`root_navigation` 为空时 `_navigation_markup`
  返回 `None`，绝不发送空键盘。
- 旧适配层必须透传：`handlers/publish.py` 的 `_LegacyDeliveryPort.deliver`
  在转调 `deliver_items_to_chat` 时**必须**带上
  `root_navigation=request.root_navigation`。2.78.x 曾在此处丢字段，导致
  API 直投 / 审核通过路径发布的频道小说帖没有「在线阅读」按钮（会话内投稿路径
  单独显式传参，不受影响）。回归测试见
  `tests/test_legacy_delivery_port_navigation.py`。
- 审核 / 预览面（REVIEW / PREVIEW surface）保留 `📖 在线阅读` **footer 超链接**
  （便于审核者打开预览），但频道正式出版（CHANNEL_PUBLICATION）的 caption footer
  **不含** READ_ONLINE 超链接。`NOTIFICATION` surface 不挂任何 CTA。
- 最高不变量（§online-reading-invariant）：在线阅读的展示**绝不能改变
  Publication topology**。预览成功/失败只能影响 root `reply_markup`，绝不能影响
  item 数 / batch 数 / 相册打包 / caption 归属 / reply mode / discussion 路由 /
  ledger / idempotency / Publication outcome。2.76.0 曾为 Instant View 把单 TXT
  拆成 TXT + TEXT、在 `CHANNEL_ALBUM_REPLY=discussion` 下把 caption 搬进讨论串，
  本方案严格杜绝（caption 必须留在 root，绝不再产生第二条消息）。
- 旧 `READONLINE_LINK_PREVIEW` 开关、`readonline_bare_link_text`、
  `with_readonline_preview`、`SubmissionText.link_preview_url` 已删除；
  `LinkPreviewOptions` 的「在线阅读」专用分支已移除，其它合法用途不受影响。
- Telegram 主菜单契约：Mini App 配置可用时主菜单是 `MenuButtonWebApp`；
  `/commands` 仍全量可用但不再抢主菜单按钮。未配置 Mini App 时回退默认命令菜单。
- 热度契约：views/forwards 是 Telegram Bot API 不提供的字段，禁止在用户界面
  展示恒为 0 的假指标；当前唯一真实互动信号是 `message_reaction_count`，
  ingestion 必须可观测。

## Review 卡不携带公共投稿 CTA（§review-cta）

```text
Review control cards NEVER expose the public submission CTA (BOT_SUBMIT /
MINI_APP_SUBMIT / READ_ONLINE), even when the owning bot's submission
entrypoints are configured.
```

- 频道出版使用 caption/text 文本导航 footer（§submission-entrypoint），不再
  使用 `[✉️ 我要投稿]` 单一 inline URL button。
- 审核群控制卡与 superseded 旧卡**永不携带公共投稿 CTA**（`✉️ TG 投稿` /
  `📱 Mini App` / `startapp=submit`）：公共投稿 CTA 只属于最终频道出版。
  moderation 按钮（通过/拒绝/重抓/遮罩）与查看原链接按钮不受影响；后端 stale guard
  仍是权威。
- superseded 旧卡：所有 moderation 按钮移除（后端 stale guard 仍是权威）；也绝不
  追加公共投稿 CTA。

## Novel Cover 与 fallback card 不变量（§novel-cover）

```text
A novel's channel structure is: visual root + TXT document reply.
The root is the REAL cover when one exists, otherwise a TelePost-rendered
fallback card, otherwise (fallback disabled/failed) the TXT alone.
Inline body illustrations NEVER ship as channel media; they render on the
online reading page only.
The Telegram review group previews the SAME shape: the real cover leads the
submission and never becomes part of the review's staged media.
```

- **资源角色不靠猜**：PixivFlow 把封面发布为专用 `pixiv:<id>:novelcover`
  canonical asset（wire 仍是 `kind: image`）；正文插图保留
  `uploadedimage` / `pixivimage` 身份。禁止用 `asset[0]`、图片数量、
  MIME 或 URL 顺序猜测资源角色。
- **Pixiv 默认封面不是封面**：`novel-cover-master-default` 占位图在
  PixivFlow 归一化为 `cover_url: null`，绝不作为 Telegram media 发布。
- **TelePost 消费侧**：`novel_cover_asset_ids()` 只认显式 `:novelcover`
  后缀；旧 payload（无封面字段）保守降级为 fallback card / text-only root，
  绝不把正文第一张图当封面。
- **审核群预览与频道同形**：`novel_cover_preview_url()` 只从显式 `:novelcover`
  资产取 `source_url`（经媒体代理），作为 **URL 照片** 先发给审核群，再发 TXT
  document。该封面是 `staging_only` 展示项，绝不写入 `media_json`/`documents_json`
  （发布侧从 canonical asset 自建 root，不能重复计数）；`QueueCommand.media_assets`
  是唯一来源，畸形/缺失资产静默跳过，绝不因此让投稿失败。
- **Fallback card 是展示层职责**：`NOVEL_FALLBACK_CARD_ENABLED`（默认开）
  控制；Pillow 固定尺寸 RGB 渲染、临时文件发布后清理；渲染失败返回
  `None` → text-only root + TXT reply，绝不把 Publication 判失败。
- **投递永不为空**：预览过滤删掉所有插图后，TXT document 仍然是频道条目；
  过滤导致 items 为空时回退完整 plan（#130 类失败的硬防线）。
- 小说正文插图继续只渲染在 Telegraph 在线阅读页（§telepress-preview）；
  `:novelcover` 资产不得进入阅读页 manifest。

## Novel TXT Telegraph Preview（§telepress-preview）

```text
Telegraph novel preview is an optional Publication enrichment,
not a Publication success prerequisite.

The downloadable TXT document remains an authoritative Telegram
publication artifact even when a Telegraph preview exists.

Telegraph preview failure must never turn an otherwise successful TXT
Telegram publication into a failed Publication.

Novel preview content is derived from the immutable final Publication
Snapshot, not from mutable Submission or stale Review state.

Telegraph preview generation is publication-idempotent.
Publication retries reuse an existing successful preview instead of
creating duplicate Telegraph pages.

TelePress integration belongs behind a thin preview-provider adapter;
TelePost must not reimplement Telegraph rendering and pagination.
```

- 编排只发生在 `PublicationService`（review/API/editorial/refetch 共用）与 chat
  DIRECT_PUBLISH 分支；REVIEW_REQUIRED 的审核卡**绝不**提前创建真实 Telegraph 页
  ——预览绑定真实 Publication，不绑定审核稿。
- Eligibility 来自**最终 Publication Snapshot**（实际发布的 ordered items）：
  第一个 `.txt` document 才 eligible；photo-only、非 TXT document、以及被 Editorial
  移除的 TXT → `not_applicable`，绝不按 source/bot/target 判断。
- 内容来源 = final snapshot：title 用最终发布标题（Editorial 改名生效），正文读
  最终所选 TXT；绝不从 mutable original submission / stale revision 取内容。
- 幂等：`publication_previews` 以发布 idempotency key 为主键，
  `publication:<key>:novel-preview`；终态（succeeded/failed/timeout）复用，
  Telegram 投递重试**绝不**再次调用 TelePress；TelePress 自己的内容缓存不是
  本幂等的替代。rich 成功记录带 `title=rich-v2` 标记；旧 `rich` 或空标题的文本页
  在带图 manifest 可解析时会重生成一次。
- 小说内嵌图片是 preview-only：PixivFlow novel review 只把 TXT document 发频道，
  所有 `media_asset_refs` 图片只渲染在 Telegraph 阅读页。
- 跨仓依赖契约：`requirements.txt` 的 `telepress` pin 必须等于 deploy 仓库
  `docker/telepress.Dockerfile` 的 pin；`Version sync check` CI 强制校验。`/health`
  同时暴露 `telepress_version` 与 `telepress_rich_markdown`。
- 失败隔离：provider 异常/超时/无 Token/未安装库 → PreviewResult.failed /
  timeout / disabled；TXT document 照常投递，Publication 成功与否只由
  Telegram 投递决定。`NOVEL_PREVIEW_TIMEOUT_SECONDS` 是严格上界。
- Provider 是薄 port：`telepost/domain/novel_preview.py` 的
  `NovelPreviewPublisher`，实现 `TelePressNovelPreviewPublisher` 直接调用
  `from telepress import TelegraphPublisher`（正式 Python API，非 CLI）；
  TelePost 不重写 Telegraph node 生成/分页/API wrapper。
- 展示放置 SSOT 是 `telepost/domain/presentation_policy.py`
  （`build_publication_presentation`，FSM-C）：决定 READ_ONLINE 出现在 root 按钮
  （频道 CHANNEL_PUBLICATION）还是 footer 超链接（审核/预览 REVIEW/PREVIEW）。
  频道正式出版的 caption footer 不再渲染 READ_ONLINE 一行；TXT document 仍是
  权威下载 artifact（与按钮并列、不替换）。`查看发布内容` 仍指 Telegram channel post。
- privacy/attribution：Telegraph 页只含 title + TXT 正文，永不携带
  submitter/username/display name/internal id；anonymous 投稿在页内零身份泄漏。
- 预览 URL 属于 Publication（`publication_previews`），绝不写入 mutable
  Submission；refetch 仅 current head 的发布才生成预览，superseded 代不产生。
