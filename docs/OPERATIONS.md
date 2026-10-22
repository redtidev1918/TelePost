# 运维手册

## 快速检查

```bash
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8080/live
curl -fsS http://127.0.0.1:8080/ready
curl -fsS http://127.0.0.1:8080/version    # 当前版本 + 提交号
curl -fsS http://127.0.0.1:8080/api/v1/health       # 单 Bot
curl -fsS http://127.0.0.1:8080/api/bot1/v1/health  # 多 Bot 父路由
```

`/live` 只证明 runtime 活着；`/ready` 还要求数据库迁移、Bot、投稿服务和审核恢复完成。
多 Bot 父路由的 `/health` 会汇总 Bot 序号、Python/Node RSS、系统可用内存、Volume、
API 临时上传、审核队列、PixivFlow cache 和 delivery outbox。单 Bot 子服务的
`/health` 只表示进程可用。`/version` 与 `/health` 都带 `version`/`commit`，
可以直接确认线上跑的是哪个发行版和哪次提交（Docker 镜像由发布流水线注入）。

多 Bot 父路由还提供人类可读状态页 `GET /status`（纯文本，无鉴权），汇总各 Bot
子服务的最近终态通知：`bot1-daily  last 2026-09-14 19:49 UTC · partial`。适合频道
置顶/收藏，用户不用等时点才知道那次更新是否发了。单 Bot 子服务继续用
`GET /api/v1/schedule/status` 返回同样的 JSON。

## 计划观察（schedule observability）

内容采集计划由 PixivFlow 执行，时点以部署仓库的生产配置为准。TelePost 收到的每条终态
（success/partial/failed）都通过 schedule outbox 投递到审核群。为避免「没等到作品
也没收到通知」的静默故障，TelePost 2.38.0 起增加：

- **前置告知**：空待发池时终态消息增加「前瞻：待发池为空，下一发布时点若仍无新作则
  无法按时更新」，运营提前知晓下一时点仍可能空。
- **Watchdog**：独立周期任务每 30 分钟检查一次，任何 schedule 超过
  `SCHEDULE_WATCHDOG_MAX_HOURS`（默认 `26`）没有新的终态通知，就直接向审核群发一条
  静默告警（按 UTC 日期幂等，每天最多一条），不依赖正常通知链路。
- **状态查询**：`GET /api/botN/v1/schedule/status` 与公共 `/status` 页可随时确认
  「最近一次更新是什么时候、什么状态」。
- **Telegram 命令**：`/status`（公开）在任意对话里查看最近计划终态；
  `/pin_status`（仅 OWNER）把状态消息发送并置顶到当前群/频道，作为「频道置顶区」
  的落地方式。

## 审计与可观测性

每条投稿的完整流水都落进本机 SQLite 表 `audit_events`（每个 Bot 一个库），
日志被轮转或进程重启后依然可查：

| 事件 | 含义 |
| --- | --- |
| `submission.received` / `submission.accepted` / `submission.duplicate` | 收到投稿、被接收、判定为重复（含复用的审核号与原因） |
| `review.created` / `review.preview_staged` / `review.control_created` / `review.pending` | 审核落库、预览与按钮发出、进入待审 |
| `review.approved` / `review.rejected` / `review.failed` | 审核决定或失败（带 `error_class`） |
| `publish.started` / `publish.completed` / `publish.duplicate_suppressed` | 发布开始、成功、重复抑制 |
| `review.reconciled` | 启动修复：补发控制消息、清理无按钮预览、需要人工介入 |
| `media.prepared` | 每张图的处理结论：尺寸、体积、解码预算、最终方式与原因 |

每行都带 `review_id`、`pixiv_id`、`target_id`、`idempotency_key`，
以及来自上游的 `execution_id`，可以把下载、投递、审核、发布串起来对账。

- 日志里每张图还会输出一行 `media decision {...}`，`decision` 取
  `photo_passthrough` / `safe_compress` / `use_preview` / `document_fallback`，
  `reason` 说明原因（体积超限、解码预算超限、尺寸超过 Telegram 照片上限等）。
- 失败会带类型化 `error_class`（如 `dependency_not_ready`、`network_timeout`、
  `telegram_send_failed`），「上游还没就绪」与「真正失败」区分记录。
- 令牌、密钥、请求头在落盘前统一脱敏。
- 保留期由 `AUDIT_RETENTION_DAYS`（默认 30 天）控制；仍在进行中的审核，
  其审计事件不会被清理。

查询某条审核的完整流水：

```bash
python -m telepost.observability.cli reviews inspect <review_id>
python -m telepost.observability.cli reviews inspect <review_id> --bot 2
```

## 启停与日志

| 环境 | 启动/更新 | 日志 |
|---|---|---|
| 源码 | `./.venv/bin/python run.py` | 前台输出或 `logs/` |
| 脚本安装 | `./start.sh`、`./restart.sh`、`./update.sh` | `logs/` |
| Compose | `docker compose up -d` | `docker compose logs -f telepost` |
| Fly.io | `flyctl machine start <id> -a <app>` | `flyctl logs -a <app>` |

同一个 Telegram Token 只能由一个运行实例消费。排障时不要同时启动源码、容器和旧
Machine。

## 持久数据

| 内容 | 默认位置 |
|---|---|
| SQLite | `data/submissions.db` 或 `data/botN/submissions.db` |
| 会话状态 | 数据库同目录的 `persistence.pickle` |
| `/botconfig` 策略 | 数据库同目录的 `runtime-policy.json` |
| 搜索索引 | `data/search_index` 或 `data/botN/search_index` |
| API 临时上传 | `data/api_uploads/` |

备份数据库时一并保留 `runtime-policy.json` 和 `persistence.pickle`。多 Bot 要备份
整个 `data/`。PixivFlow 的缓存、执行账本和 outbox 位于上游独立卷，按部署仓库的流程备份。

## 备份

SQLite 使用 WAL。停机备份最简单；在线备份使用 SQLite 自带 `.backup`：

```bash
mkdir -p backups
sqlite3 data/submissions.db ".backup 'backups/submissions.db'"
cp data/runtime-policy.json backups/ 2>/dev/null || true
cp data/persistence.pickle backups/ 2>/dev/null || true
```

多 Bot 对每个 `data/botN/submissions.db` 分别执行。若直接复制文件，先 checkpoint 并
同时保留 `-wal`、`-shm`。

Fly.io 每次部署前创建 snapshot：

```bash
flyctl volumes list --app <app>
flyctl volumes snapshots create <volume-id> --app <app>
flyctl volumes snapshots list <volume-id> --app <app>
```

snapshot 是回退保障，不替代异地备份。

## Fly.io 生命周期

TelePost 是用户可见的投稿入口，在 Fly.io 上保持常驻：`auto_stop_machines=false`、
`min_machines_running=1`。如果与 PixivFlow 等上游组合，让上游在独立应用中管理自己的调度、
资源和生命周期，不要把它们塞进 TelePost 容器。完整配置见
[FLYIO_DEPLOYMENT.md](FLYIO_DEPLOYMENT.md)。

## 安全升级与回退

通用模型见 [统一升级模型](#统一升级模型)。下面是各目标的具体动作。

### pip

```bash
pip install --upgrade telepost-bot
```

升级后重启进程，并核对 `/health` 的 `version`。

### Compose

1. 备份 `data/`。
2. 把镜像版本固定到新的 release version：设置 `TELEPOST_VERSION=<version>`，或直接改 `image`
   为 `ghcr.io/redtidev1918/telepost:<version>`（不要用 `latest`）。
3. `docker compose pull && docker compose up -d`。
4. 检查健康、版本、日志和一次测试投稿。

### Fly.io

```bash
flyctl volumes snapshots create <volume-id> --app <app>
flyctl machine update <machine-id> --app <app> \
  --image ghcr.io/redtidev1918/telepost:<version> --yes
```

随后验证：

```bash
flyctl machine status <machine-id> --app <app> --display-config
curl -fsS https://<app>.fly.dev/health
curl -fsS https://<app>.fly.dev/api/bot1/v1/health
curl -fsS https://<app>.fly.dev/api/bot2/v1/health
```

必须确认 Machine ID、Volume ID、资源和生命周期配置不变，镜像 digest 对应目标新版本
（`<version>` 是正式 release version，不是 `latest`）。两个 Bot 的 API 健康端点均应返回该版本。
正式发版不需要手工执行本段：流水线会用同一个 GHCR 镜像完成部署并校验，失败即判定发版失败
（见 [正式发布流程](#正式发布流程)）。升级前后的只读完整性检查均应为 `ok`。相册降级回复、
聊天遮罩和混合文件保存的回归测试见
`tests/test_publish_reply.py`、`tests/test_publish_regressions.py` 和 `tests/test_streaming_uploads.py`。
回退时更新为上一个 release version 的镜像即可；除非数据本身损坏，不要用旧 snapshot 覆盖较新的数据库。

## 数据库完整性

停机或只读连接下执行：

```bash
sqlite3 'file:data/submissions.db?mode=ro' 'PRAGMA quick_check;'
```

Fly 多 Bot 示例：

```bash
flyctl ssh console --app <app> --command \
  "python -c 'import sqlite3; [print(p, sqlite3.connect(\"file:\"+p+\"?mode=ro\", uri=True).execute(\"PRAGMA quick_check\").fetchone()[0]) for p in (\"/app/data/bot1/submissions.db\", \"/app/data/bot2/submissions.db\")]'"
```

发现非 `ok` 时先停止写入、复制 Volume/snapshot，再分析；不要先跑 VACUUM。

## 搜索索引

在安装了项目依赖且配置有效的环境运行：

```bash
python -m utils.index_manager status
python -m utils.index_manager sync
python -m utils.index_manager rebuild
python -m utils.index_manager optimize
```

优先 `sync`；Schema 变化或索引损坏时再 `rebuild`。也可以用 Admin 命令
`/index_stats`、`/sync_index`、`/rebuild_index`、`/optimize_index`。

仓库中的历史维护脚本不是统一 CLI，有些脚本不支持 `--help` 且会立即操作默认数据；
运行前必须先阅读源码并备份。生产维护优先使用上面的 SQLite 和索引命令。

## `/botconfig`

Owner 可以在 Telegram 中修改频道、审核群、审核开关和署名开关。策略原子写入当前
Bot 的数据目录；多 Bot supervisor 会只拉起当前子进程。Token、Owner、Admin 和
Webhook Secret 不能通过 Telegram 修改。

切换频道或审核群前，先批准/拒绝所有 pending 投稿，否则命令会拒绝执行。

## 审核队列与 outbox

- `PENDING_REVIEW_RETENTION_DAYS=0` 表示待审永久保留；部署模板显式设置 14 天，批次 20。
- `REVIEW_RETENTION_DAYS` 默认 30 天，清理已决审核和 API 通知幂等记录；审计由独立的 `AUDIT_RETENTION_DAYS`（默认 30 天）控制。
- outbox 数量、失败数、累计重试和最老年龄持续增长，说明 TelePost/API 链路异常。
- 不要直接删 outbox 引用的缓存文件；先恢复投递，让上游完成重试。
- PixivFlow 使用 `pixivflow outbox list` / `inspect <id>` 查看状态，使用
  `retry <id>` 或 `retry --dead` 正式重放 dead letter，使用 `cancel <id>` 取消尚未开始的
  intent；不要手改 SQLite `next_attempt_at`，也不要用 `run-once` 代替 outbox retry。
- replay 保留原 idempotency key。TelePost `/ready` 非 200 时 PixivFlow 只延后，不增加 attempt。

### 过期恢复与可追溯边界

新过期结论、`review.expired` 审计和有可信人类属主的私聊通知意图在同一事务提交。
`pending_reviews.expiry_progress` 保存剩余消息 ID、控制卡转换和失败类型；
`expiry_next_attempt_at` 的部分索引支持有界扫描。每个 Bot 的既有 300 秒维护任务继续
未完成步骤，失败退避至最多一天；关闭新增过期（设为 0）不会取消已提交的清理。
旧 `expired` 行不回填、不自动通知、不恢复审核。旧按钮仍受后端终态检查约束。

优先编辑原控制卡并去掉按钮；编辑/删除在确认落库前崩溃可重复执行。
原卡确实缺失或不可编辑时才补发。补发前写入 `fallback_started`；发送结果不确定时
不自动再次补发，进度中标记 `fallback_send_uncertain` 并记录错误日志，需人工核对群历史。
私聊复用现有通知 outbox；Telegram 成功但本地确认未提交的窗口仍可能重复私聊，
不承诺 exactly-once。API token 持有者不作为投稿者。

[Telegram deleteMessage](https://core.telegram.org/bots/api#deletemessage) 限制删除 48 小时内消息，
包括管理员 Bot。14 天媒体通常无法自动删除：保存受限的消息 ID，控制卡明确提示人工清理，
不会谎报“已清理”或无休止重试明确的删除限制。Bot 不主动删除过期文字卡；群自动删除设置、
管理员删除等仍可移除它，不保证永久存在。

已完成的过期记录按决定时间起 30 天清理（进度更新不延长业务时间）；未完成的记录受保护。
审计到期后亦会清理；之后可能只剩群内文字卡和既有私聊通知账本，无法保证完整永久追溯。
每个过期稿仅增加一个小 JSON、一个时间戳、一条审计及至多一个私聊意图，不复制媒体。
现有通知表没有自动清理策略，本次不扩展其生命周期；长期未解决的清理记录也会继续占用空间。

只读排查（以当前 Bot 的实际 DB 路径打开 SQLite `mode=ro`）：

```sql
SELECT id, decided_at, expiry_next_attempt_at, expiry_progress
FROM pending_reviews WHERE status='expired' AND expiry_progress IS NOT NULL;
SELECT review_id,state,attempts,last_error FROM submitter_notifications
WHERE kind='review_expired' AND state='pending';
```

迁移只加可空列和索引，旧程序可以读库；回滚会停止新恢复流程，且旧清理器不保护未完成过期。
因此回滚前应暂停过期维护并保留卷快照，核对未完成记录；不要删除新增列或通知意图。
复用现有 SQLite/outbox 和 PTB 编辑 API；依据 [SQLite 事务](https://www.sqlite.org/lang_transaction.html)
与 [幂等消费者模式](https://microservices.io/patterns/communication-style/idempotent-consumer.html)，
不新增队列依赖、进程或服务。测试使用临时 SQLite 与模拟 Telegram，不代表真实群删除验收。

## 审核卡异常恢复与回调反馈（§review-card-recovery）

审核回调失败时，唯一安全的用户可见反馈是 `answer_callback_query` 的提示或
`show_alert`；**绝不能用无键盘的 `edit_message_text` 覆盖控制卡**，否则审核人
失去全部操作按钮且无法重试。`handlers/callback_handlers.py` 的全局兜底只负责：
记录日志 → 弹出“操作失败，已记录；卡片与审核状态保持不变。”→ best-effort 重建
卡片 → 重新抛出异常。重新抛出是刻意的：只有异常到达 PTB error observer，
Durable Inbox 才会把该 update 记为失败并有限重试（`attempts<=5`）。

- **区分两类失败**（`telepost/telegram/callback_feedback.py`）：
  `is_ui_only_error`（消息未修改 / 消息已不存在 / 无权限编辑 / 回调过旧）只记日志、
  直接结束，不重试、不改写卡片；其余异常（网络、SQLite 锁、业务异常、未知错误）
  弹提示并继续抛出。
- **业务已成功、仅 UI 更新失败**不得重跑副作用：审核状态、发布记录、通知账本都
  已经落库，重放只会命中幂等/终态检查。重放卡片时按当前状态渲染只读版本。
- **重建卡片只读、不改状态**：`handlers/review.py:restore_review_card` 按
  `review_id` 读库，要求 `review_chat_id` + `control_message_id`，`pending` 状态
  委托 `refresh_refetch_card`（重抓进行中保留“重抓中”形态），终态由
  `review_keyboard.card_from_row` 渲染只读标题（已发布/已拒绝/已过期/已替代/发布中）
  并只保留 `🔗 查看原链接` 按钮。它永不改 `status`/`decided_*`，永不补发新卡，
  永不重新发布或收回已发布内容。
- **可审计的人工恢复**：`/restore_card <审核ID> [--dry-run]`（仅管理员）重建被破坏的
  卡片；成功/失败写 `review.card_restored` 审计（含状态与结果）。缺少
  `control_message_id` 时返回 `no_control_message` 并交给既有
  `reconcile_incomplete_reviews`，不在此处补发。
- **已知边界**：消息已被删除 → `message_gone`；群/消息不可编辑 → 记录原因、不重试。
  `restore_review_card` 是只读投影，不承诺恢复历史媒体（媒体见
  §review-media-ttl）或撤回已发出的通知。

只读排查：

```bash
python -m telepost.observability.cli reviews inspect <review_id> --bot N
python -m telepost.observability.cli doctor --all-bots
```

```sql
SELECT review_id,state,attempts,last_error,next_attempt_at FROM webhook_inbox
WHERE state <> 'done' ORDER BY update_id;
```

## Webhook Durable Inbox（§durable-inbox）

Webhook 模式下，Telegram Update 在返回 HTTP 200 之前先写入 `webhook_inbox`
表（独立连接 `synchronous=FULL`，落盘后才确认）。专用 worker 按 `update_id`
严格串行处理：只有最小的未完成 update 可被领取，退避中的头部行会阻塞后续行
（有界：最多 5 次尝试后退避总计约 15 分钟，之后转 `dead` 并解除阻塞）。
这样保证重放的旧 Update 绝不会在新一代投稿会话之后执行。

完成确认的时序：`process_update()` 返回 → 显式 `update_persistence()`（会话
状态同步落盘）→ 标记 done。PTB 会把处理器/持久化异常交给 error handler 后
正常返回，worker 注册了自己的 error handler 观察者（contextvar 限定作用域），
因此这类「被吞掉」的异常同样会阻止 done 标记并按退避重试——处理失败的日志
带 `phase=business|persistence|completion`，可定位失败窗口。

- 投稿会话已启用 `persistent=True`（PicklePersistence），重启后会话可继续；
  媒体追加按（user_id, update_id）跨投稿代去重（`submission_media_log`），
  重放不重复追加，用户重发同一图片（新 update_id）照常接收。
- 持久化配置统一使用 `main.build_persistence()`：保留 conversation、user_data
  和 chat_data；bot_data 只存进程内 worker、定时任务、就绪标志和投稿限流窗口，
  不参与持久化。将运行中的 Task 放进持久化范围会导致 deepcopy 抛 TypeError，
  阻止 Inbox 完成确认，后续审核按钮因此积压。
- 旧版本写入的 `persistence.pickle`（`store_data` 全开）可直接被新配置读取：
  conversation/user_data/chat_data 照常恢复，历史 bot_data 段不会被加载进
  Application，且在后续重写时原样保留在文件里（不删除、不迁移）。升级前
  备份该文件即可回滚。
- 审核按钮重放保护（同一 Callback 重复送达 vs 新点击产生新 callback id）：
  发布/拒绝由审核状态机 CAS 拒绝二次终态，发布幂等由 delivery 账本兜底；
  重抓按 `callback_query.id` 幂等（同一链同时最多一个活跃 attempt）；
  遮罩切换的翻转与 `review_spoiler_callbacks` 回调收据同事务提交，同一回调
  重放不二次翻转（收据随审核行一起被维护任务清理）；恢复预览由
  `begin_preview_regen` CAS 保护；封禁投稿人/禁用 API 按 subject
  `INSERT OR IGNORE` 幂等。
- 多 Bot 部署下每个 bot 进程持有自己的 SQLite、Inbox 和 `persistence.pickle`，
  互不共享；Inbox 日志带 `bot=<username>`，聚合日志可定位具体实例。
- 修复持久化故障后，pending 更新会在退避到期后继续处理；dead 不会自动重放。
  必须先核对审核/发布账本，再决定如何处理 dead 更新：它可能已经执行了业务动作，
  只是完成确认失败，不能把整批 dead 当作未执行请求直接重放。
- 重放边界是 at-least-once：崩溃后重放可能重复发送面向用户的确认消息，
  但不重复业务写入；不承诺 Telegram 外部副作用 exactly-once。
- 保留：done 7 天、dead 30 天，由 300 秒维护任务清理；pending/重试中的行
  绝不被过期清理删除。
- **Polling 模式不在本保证内**：PTB 的 get_updates offset 在入队后即前进，
  进程在「已确认但未处理」窗口崩溃同样可能丢失更新，本次未改动该路径，
  不要声称两种模式已统一保证。
- 只读排查（`mode=ro`）：
  `SELECT update_id,state,attempts,last_error FROM webhook_inbox ORDER BY update_id DESC LIMIT 20`；
  `dead` 行表示已确认接收但重试耗尽的更新，必须人工核对。
- `python -m telepost.observability.cli doctor` 固定输出
  `Inbox: pending: N processing: N dead: N｜最老待处理年龄`；dead>0 为 CRIT，
  pending 积压超过 1 小时为 WARN。

## 审核预览媒体 36 小时生命周期（§review-media-ttl）

审核预览媒体的删除窗口只有 Telegram 的 48 小时，而待审保留期是 14 天，所以预览媒体
在**实际发送后** `REVIEW_MEDIA_TTL_HOURS`（默认 36）小时就清理；控制卡和
`media_json`/`documents_json` 里的 `file_id` 都保留，管理员按需恢复。

```text
present ──TTL──▶ partial ──▶ cleared ──点「恢复预览」──▶ regenerating ──▶ present
   │               │            ▲                                        │
   └──restricted◀──┘            │ Telegram 拒绝删除（超 48h / 权限）      │
    （永久可见，需人工）          │                                        │
    regenerating ──记录消息 ID 前中断──▶ uncertain（永久可见，需人工核对）◀┘
```

- **锚点是发送时间**：`preview_sent_at` 在控制卡投递（`finalize_control`）成功时写入，
  不是落库时间；恢复的预览用自己新的发送时间重新计时。
- **提前清理 ≠ 稿件过期**：14 天保留期、`expire_pending()`/`finish_expiry()`、
  `review.expired` 审计和投稿者通知（PR #333 的机制）完全不变；已清理媒体的稿件照常过期。
- **只按需恢复**：没有任何「每天重发全部稿件」的自动逻辑。按钮走
  `begin_preview_regen` 的 CAS（`status='pending'` 且 `preview_state='cleared'`），
  双击/并发点击只有一次真正发送；已发布、已驳回、已过期、已替换的稿件全部拒绝。
- **不确定窗口**：CAS 先占位再发送，所以「Telegram 已发出、本地还没记录消息 ID」的崩溃
  无法自动区分。超过 `REVIEW_MEDIA_REGEN_TIMEOUT_MINUTES`（默认 5）分钟仍是
  `regenerating` 的行会被收敛成 `uncertain`（终端、可见、审计
  `review.preview_regen_uncertain`，doctor 计数），由人工在群里核对后手动删除重复媒体。
  不为此引入外部 API 的 exactly-once 框架。
- **有界重试**：临时性删除失败按 300 秒维护任务重试，超过
  `REVIEW_MEDIA_DELETE_DEADLINE_HOURS`（默认 47）小时仍失败就直接标记 `restricted`
  并记 `review.preview_delete_restricted`（`reason=delete_deadline_exceeded`），
  不再无休止重试，也不谎报「已清理」。

只读排查：

```bash
python -m telepost.observability.cli doctor --all-bots
```

`review_preview_lifecycle` 段按状态计数；`restricted`/`uncertain` 非零即 WARN
（需人工处理），不影响退出码。审计事件：`review.preview_cleared`、
`review.preview_regenerated`、`review.preview_delete_restricted`、
`review.preview_regen_uncertain`。

迁移只加可空列（`preview_state`、`preview_sent_at`、`preview_attempts`）和一个部分索引，
旧程序可读库；关闭 `REVIEW_MEDIA_TTL_HOURS=0` 只停止新清理，已 `restricted` 的媒体
仍需人工删除。

## 正式发布流程

TelePost 有两道不同的通道，不要混用：

| 通道 | 入口 | 结果 |
|---|---|---|
| 开发 | PR → merge 到 `main` | 只进代码，**不发版** |
| 正式发版 | release-please 开出的 **Release PR** → merge（人工闸门） | 触发完整发行链 |

**没有「push main 后自动部署」这条路径。** 只有 merge Release PR 才进入正式发布。

Release PR 由 release-please 维护：它按 Conventional Commits 累积变更、更新 `CHANGELOG.md` 与
`telepost/build_info.py` 的版本。合并后流水线依次完成测试、三平台并行构建与正式发布：

```text
开发：PR → main
正式发版：Release PR → merge
                ↓
            vX.Y.Z tag
                ↓
          ReleaseGraph
                ↓
   GitHub Release / GHCR image / PyPI telepost-bot / Docs
                ↓
        GHCR image → Fly.io 生产部署
                ↓
      health + version + bot1 / bot2 校验
```

- **自动发行物**：GitHub Release（三平台单文件 + 校验和 + SBOM）、GHCR 镜像
  `ghcr.io/redtidev1918/telepost:<version>`、PyPI `telepost-bot`、文档站。
- **自动生产部署**：使用与 release version 完全相同的 GHCR 镜像，不重新构建、不用 `latest`；
  部署后必须满足 `/health` 返回该 version、`bots` 含 1 和 2、`/ready` 的 `bot1`/`bot2` 均为 true。
- **任一关键阶段失败 = 这次发版失败**：GHCR 或 PyPI 不通过时 ReleaseGraph 报告 release 不完整，
  生产部署不会被触发；部署后达不到验收条件同样判定失败，不存在「发版显示成功、生产仍是旧版本」。
- 六处版本必须相等：Git tag / GitHub Release / `build_info.py` / PyPI / GHCR image tag / 生产 `/health`。

人工操作清单：

```bash
gh pr list --search "head:release-please"        # 找到 Release PR
gh pr view <pr>                                   # 核对 CHANGELOG 与版本号
gh pr merge <pr> --squash                         # 合并 = 放行发版
gh run list --workflow deploy-fly.yml --limit 3   # 生产部署结果
```

日常排障核对：

```bash
gh release view v<version>
docker buildx imagetools inspect ghcr.io/redtidev1918/telepost:<version>
curl -fsS https://<app>.fly.dev/health    # version 必须等于本次 release version
```

### 发版耗时与构建缓存

TelePost 使用固定提交的 ReleaseGraph 1.5.18：复用 Go 构建缓存，同一次运行只编译
一次发布引擎；测试成功后三平台产物并行构建，最终发布仍等待全部构建成功。业务仓库只声明
构建策略与调用版本，不维护第二套发布事务。

Docker 的网页阶段在 `$BUILDPLATFORM` 上构建，生成的静态资源供两个目标架构共用，
避免在 ARM QEMU 下重复运行 npm/Vite。版本、提交和构建时间 ARG 放在依赖安装之后，
每次发版只重写运行时版本文件，依赖与系统包仍可命中 BuildKit 缓存。
`.dockerignore` 排除 ReleaseGraph、release-please 工作区与嵌套 Git 目录，避免
发布工具进入应用镜像，或由发布工作区的变化使应用层缓存失效。

社区依据：[Docker ARG 缓存规则](https://docs.docker.com/reference/dockerfile/#impact-on-build-caching)、
[多平台构建](https://docs.docker.com/build/building/multi-platform/)、
[GitHub Actions 缓存](https://docs.docker.com/build/cache/backends/gha/)。复用现有 ReleaseGraph
与 Docker BuildKit，不关闭测试、版本校验、provenance 或 SBOM，也不增加付费构建服务。

`Release image cache check` CI 实际构建两次，仅改变版本、提交和时间；核对 apt、pip 与
网页构建层在第二次全部 CACHED，并启动第二个镜像检查真实版本与依赖，避免缓存错误
导致发布旧版本；CI 还模拟发布工具目录，确认它们没有进入镜像。完整发版速度仍受冷缓存、
基础镜像更新、GitHub API 和 registry 网络影响。

优化前参考：TelePost 2.81.2 的 [Release run 37582280930](https://github.com/redtidev1918/TelePost/actions/runs/37582280930)
约 21 分 16 秒；镜像构建 9 分 47 秒，post-release actions 3 分 31 秒。网页阶段的 ARM
npm 安装与构建分别约 140 / 162 秒。以后比较同类正式发布的 job/step 时长，分别记录
冷缓存与暖缓存；不要把 PR 检查时间或跳过已发布版本的空跑当成实际发版提速。

版本号一律由 release-please 写入 `telepost/build_info.py`：不手工改版本号、不手工打 tag、
也不要为「补某个资产」另行发布同版本。

## 统一升级模型

| 部署方式 | 升级动作 | 版本来源 |
|---|---|---|
| pip | `pip install --upgrade telepost-bot` | PyPI `telepost-bot` |
| Docker / Compose | 改为 `ghcr.io/redtidev1918/telepost:<version>` 后 `docker compose pull && docker compose up -d` | GHCR release tag |
| Fly.io | `ghcr.io/redtidev1918/telepost:<version>`（正式发版由流水线完成，见上一节） | GHCR release tag |
| Release 单文件 | 下载新 release 的单文件替换旧的 | GitHub Release |

规则：**生产不要使用 `latest`**；`<version>` 必须是正式 Release 的版本号。升级前备份 `data/`，
升级后核对 `/health` 的 `version` 与 `commit`。

## 热度统计

Telegram Bot API 不提供频道帖子的浏览量 / 转发量；这些字段显示为 0 是 API 边界，
不是故障。真实可用信号是频道 `message_reaction_count` 更新：用户对帖子增加或改变
reaction 后，Bot 把 `(message_id, total_count)` 写入 `message_reaction_counts`，
再聚合更新 `published_posts.reactions` 与 `heat_score`。

- Bot 必须是频道管理员，webhook `allowed_updates` 必须包含 `message_reaction_count`。
- 没有用户 reaction 时，计数表为空、热度为 0 是预期状态。
- 验证不要查看公开频道的浏览量，而是看 `message_reaction_counts` 是否出现真实更新。
- 每收到一次 Telegram reaction 更新，webhook 日志会出现
  `🔔 收到频道反应更新`；处理完成后出现 `Reaction projected`。
- 公共 `/health` 的 `reaction_ingest_by_bot` 聚合每个 Bot 子进程的
  `received_since_start`。该指标从进程启动起算，重启后归零；持久事实仍以
  数据库和日志为准。
- 常用排查命令：

```bash
fly logs -a <app> --no-tail | grep "Reaction ingest"
curl -fsS https://<app>.fly.dev/health | jq '.reaction_ingest_by_bot'
```

```bash
fly ssh console -a <app>
```

```python
import sqlite3
c = sqlite3.connect('file:/app/data/bot1/submissions.db?mode=ro', uri=True)
print(c.execute('SELECT COUNT(*) FROM message_reaction_counts').fetchone()[0])
```

- 若用户已加 reaction 但日志没有任何 `Reaction ingest`，说明 Telegram 没有推送
  或 webhook 入口失败；若有 ingest 但没有 `Reaction projected`，才查数据库投影。
