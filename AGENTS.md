# AGENTS.md —— 本仓库是「业务平面」

这份文件写给任何进入本仓库的智能体或工程师。先读
`pixivflow-telepost-deploy/docs/reference/deployment-contract.md`，它是三仓库职责契约的唯一权威描述；
本文件只回答「什么该做、什么绝对不该做」。

## 一句话

TelePost 决定 **「投稿如何审核与发布到 Telegram」**：Telegram 更新的接收与路由、会话式私聊投稿、
HTTP 投稿接口、幂等键、审核队列、批准/驳回、发布与发布恢复。

- 它是**唯一持有 Telegram 凭据**（`BOTn_TOKEN` / `BOTn_CHANNEL_ID` / `BOTn_OWNER_ID`）的服务，
  也是**唯一能发布到频道**的服务。
- 它**不决定要抓哪些 Pixiv 作品**——那是 PixivFlow。本仓库里没有 Pixiv refresh token，
  也不应该出现调度 cron / occurrence / 槽位。

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
3. **投稿处置必须显式建模（`SubmissionDisposition`）。** 原生 Telegram Chat 投稿默认
   `DIRECT_PUBLISH`（直接发布到频道），`CHAT_REVIEW_REQUIRED=true` 才进入审核队列；HTTP API
   （Mini App / 服务，含 PixivFlow 自动稿）默认 `REVIEW_REQUIRED`（生产
   `API_REVIEW_REQUIRED=true`）——自动投稿必须进入审核队列、**人工批准后**才发布，不接受自动批准。
   两个入口默认值可以不同，但共享同一 domain/service；重构任一入口**不得**静默改变另一入口的默认处置。
4. **不要删掉 `force_https = false`。** 独立 PixivFlow App 通过 Flycast 私网以明文 HTTP 调用投稿
   接口，`force_https = true` 会把它 301 到 HTTPS 并直接打断投递（Flycast/6PN 本身在 WireGuard 上加密）。
5. **不要在别处注册/删除 webhook。** webhook 的负责人只有本仓库的 `webhook_server.py`。
6. **不要在日志、响应或报告里打印任何令牌**（Bot token、投稿 token、webhook secret）。
   只输出「已配置 / 缺失 / 就绪」。
7. **不要在 256MB 上跑。** 512MB 是实测规格；降内存前必须先做真实内存验收（见 `fly.toml` 注释）。

## 改动前必须保持的行为

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
  只调用独立 PixivFlow 的受认证 `POST /internal/targets/{id}/refetch`。
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

## Refetch 终态可见性 与 watchdog 不变量（§refetch-terminal-notify）

- **已决审核的重抓终态必须可见**：obsolete（outcome 到达时源审核已被驳回/通过）与
  watchdog 的 `source_review_resolved` 分支都必须向审核群通知一次（“重抓已取消，当前稿件
  不变”），绝不能用无限“仍在处理中”掩盖 silent terminal。replay（已终态重复投递）不重复通知。
- **watchdog 对同一 request UUID 可做幂等 wake**：机器不可达且超过
  `REFETCH_WAKE_MINUTES` 无进展时，用同一 request UUID 再调 PixivFlow refetch
  （Fly Proxy 拉起机器，PixivFlow 恢复既有 manual slot）；不得创建第二条 attempt。
- **硬性 SLA**：超过 `REFETCH_HARD_TIMEOUT_MINUTES` 仍无法形成任何 terminal outcome
  时，attempt 必须 `failed(stalled_after_hard_timeout)` 并通知审核群——accepted 重抓
  绝不永久 running。

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
- `投稿人` 只能来自 `submitter_user_id` / `submitter_username`；
  `user_id` / `username`（请求身份 / token alias）永不作投稿人展示。
- 匿名 human 隐藏公开投稿人，但 ownership 与发布成功私聊通知不变；
  service（submitter NULL）没有任何人类投稿人，也不发 human 通知。
- 内部 surface（审核卡/审核预览）可为无人属主投稿显示 `来源：API /
  PixivFlow`（真实 source/provenance）；公开 surface（频道 / Mini App 预览）
  永不显示来源行。
