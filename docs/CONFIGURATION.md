# 配置参考

## 优先级

`/botconfig` 写入的运行时策略 > 环境变量 > `config.ini` > 内置默认值。

运行时策略只覆盖频道、审核群、两类审核开关和署名开关；`/botconfig reset` 删除覆盖。
敏感值始终通过环境变量、Secrets 或 `config.ini` 管理。

## 核心配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `TOKEN` | 必填 | Bot Token；兼容 `BOT_TOKEN`、`TELEGRAM_BOT_TOKEN` |
| `CHANNEL_ID` | 必填 | `@channel` 或 `-100…`；兼容 `CHANNEL` |
| `OWNER_ID` | 空 | 唯一所有者 ID；自动加入 `ADMIN_IDS` |
| `ADMIN_IDS` | 空 | 逗号分隔；只用于明确标为 Admin 的操作 |
| `BOT_MODE` | `MIXED` | `MEDIA`、`DOCUMENT` 或 `MIXED` |
| `ALLOWED_FILE_TYPES` | `*` | 文档扩展名或 MIME，逗号分隔 |
| `SHOW_SUBMITTER` | `true` | 频道是否显示投稿人 |
| `NOTIFY_OWNER` | `true` | 是否 durable 私聊 Owner：审核稿入队成功或直发成功后各按 logical submission 通知一次；refetch/editorial 不重复 |
| `CHANNEL_FOOTER_LINK` | 空 | **正式发布到频道**时，在 caption 最下方追加文本导航 footer（`✉️ TG 投稿` → `https://t.me/<bot>?start=submit`；Mini App 开启时再加 `📱 Mini App` → `?start=miniapp`；配置 `MINIAPP_SHORT_NAME` 后为 Direct Mini App `?startapp=submit`）。空 = 关闭。审核预览/排队**不**带 footer |
| `MINIAPP_SUBMIT_CTA` | `false` | 频道 footer 额外追加 Mini App 导航项；默认回退到 `?start=miniapp`，由 Bot 在私聊发送 Web App 按钮。未启用/链接缺失时省略，绝不生成坏链接 |
| `MINIAPP_SHORT_NAME` | 空 | BotFather Direct Mini App short name；配置后频道 footer 使用 `https://t.me/<bot>/<short_name>?startapp=submit` 直接打开应用 |
| `MINIAPP_PUBLIC_URL` | 空 | 私聊键盘 Web App URL；留空时从 `WEBHOOK_URL` 推导 `<公网根地址>/app/` |
| `MEDIA_PROXY_BASE_URL` | 空 | 公网媒体反代根地址；DeliveryPlanner 只重写 `MEDIA_PROXY_HOSTS` 内的精确主机。空时 remote URL 原样交给 Telegram |
| `MEDIA_PROXY_HOSTS` | 空 | 逗号分隔的精确主机 allowlist，例如 `i.pximg.net`；不配置主机时不重写任何 URL |
| `SUBMIT_LIMIT_PER_HOUR` | `10` | 每用户每小时投稿次数；`0` 关闭 |
| `ALLOWED_TAGS` | `30` | 单次最大标签数 |
| `TIMEOUT` | `300` | 数据库中过期上传数据的清理阈值（秒） |
| `SESSION_TIMEOUT` | `900` | 聊天投稿会话无操作超时（秒） |
| `TZ` | `Asia/Shanghai` | IANA 时区名；用于每日维护任务 |

## 运行模式与 HTTP

| 变量 | 默认 | 说明 |
|---|---|---|
| `RUN_MODE` | `AUTO` | `AUTO`、`POLLING` 或 `WEBHOOK` |
| `WEBHOOK_URL` | 空 | 公网 HTTPS 根地址，不含 `/webhook` |
| `WEBHOOK_PORT` | `8080` | HTTP 监听端口；多 Bot 父路由使用此端口 |
| `WEBHOOK_PATH` | `/webhook` | 单 Bot 回调路径；多 Bot 自动改为 `/webhook/botN` |
| `WEBHOOK_SECRET_TOKEN` | 首次生成并持久化 | Telegram Webhook 请求校验令牌；也可显式设置 |
| `HEALTH_PORT` | `8080` | Polling 单 Bot 的健康/API 端口 |
| `API_ENABLED` | `true` | 是否挂载 `/api/v1/*` |
| `ROUTER_TIMEOUT_SECONDS` | `300` | 多 Bot 父路由的上游总超时 |
| `UPLOAD_SESSION_MAX_AGE_SECONDS` | `3600` | 强制中断后遗留上传目录的清理年龄 |
| `TELEPOST_IMAGE_DECODE_BUDGET_MB` | `64` | 常规压缩路径的估算峰值预算；超出后仅 JPEG 仍可用降采样解码 |
| `TELEPOST_UNBOUNDED_DECODE_BUDGET_MB` | `192` | 没有 Image.draft 能力的格式（如 PNG）在压缩前允许的硬峰值；超限才回退文档 |

`AUTO` 只有在 `WEBHOOK_URL` 是公网 HTTPS 地址时才选择 Webhook；自动选择的 Webhook
注册失败会回退 Polling。强制 `WEBHOOK` 失败则退出。

## Telegram Mini App（可选增强，§152-§153）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MINIAPP_ENABLED` | `false` | 是否启用 Mini App surface。`false` 只关闭小程序入口，**不影响** Bot 与 HTTP API（上线安全开关） |
| `MINIAPP_SESSION_SECRET` | （无） | Mini App session 签名密钥（≥32 字符，独立随机 secret）。未配置时 `POST /miniapp/session` 直接 fail-closed |
| `MINIAPP_SESSION_TTL` | `1800` | Session 生命周期秒数（60–43200） |

完整架构、认证链路、构建与同域托管见 [`docs/MINIAPP.md`](MINIAPP.md)。

## 搜索与存储

| 变量 | 默认 | 说明 |
|---|---|---|
| `DB_PATH` | `data/submissions.db` | SQLite 路径 |
| `DB_CACHE_KB` | `4096` | SQLite page cache；低内存可设 `1024` |
| `SEARCH_ENABLED` | `true` | 是否建立并写入搜索索引 |
| `SEARCH_INDEX_DIR` | `data/search_index` | Whoosh 索引目录 |
| `SEARCH_ANALYZER` | `jieba` | `jieba`；未安装时回退 `simple` |
| `SEARCH_HIGHLIGHT` | `false` | 搜索结果高亮 |
| `RUNTIME_POLICY_PATH` | 数据库同目录 | `/botconfig` 的 JSON 文件 |

数据库使用 WAL。备份时执行 checkpoint，或同时复制 `.db`、`-wal` 和 `-shm`。

## 审核

| 变量 | 默认 | 说明 |
|---|---|---|
| `API_REVIEW_REQUIRED` | `true`（保留兼容） | 自动化 API（service token）投稿：**固定进入审核**，该开关不再关闭 API 的审核 |
| `MINIAPP_REVIEW_REQUIRED` | `true` | Mini App 真人投稿是否进入审核群（独立于 API 开关） |
| `CHAT_REVIEW_REQUIRED` | `false` | Telegram 聊天投稿进入审核群 |
| `REVIEW_CHAT_ID` | 空 | 任一审核开关启用时必填，且不能等于频道 |

**默认处置不变量（`SubmissionDisposition`）**：按**来源可信度**决定，而非入口形式：
API（自动化）固定进入审核；Mini App 由 `MINIAPP_REVIEW_REQUIRED` 独立控制（默认 true）；
原生 Telegram Chat 默认直接发布到频道（`CHAT_REVIEW_REQUIRED=false`）。
三者共享同一 domain/service（`QueueCommand → ReviewQueueService`）；每次路由都必须基于该来源
的处置，不得把 Mini App 与 API 绑定到同一开关。
| `REVIEW_ALBUM_SIZE` | `10` | 审核预览每组 1–10 个 |
| `REVIEW_PREVIEW_INTERVAL_SECONDS` | `0.75` | 预览组之间的节流间隔 |
| `REVIEW_PREVIEW_TIMEOUT_SECONDS` | `120` | 单次审核预览 Telegram I/O 超时 |
| `TELEGRAM_SEND_TIMEOUT_SECONDS` | `REVIEW_PREVIEW_TIMEOUT_SECONDS` | 频道发布 Telegram I/O 超时；大相册建议保持 120 秒 |
| `CHANNEL_ALBUM_REPLY` | code `chain` / 生产 deploy `discussion` | 多图展示：`chain` 在频道逐级回复；`post` 在频道都回复主贴；`discussion` 先填频道首组图片 + 首组文件，overflow 分别走图片/文件讨论串（Webhook 模式） |
| `DISCUSSION_FORWARD_TIMEOUT_SECONDS` | `10` | `discussion` 模式等待频道帖自动转发到讨论组的超时；root 已确认后超时会保留主贴、溢出等待人工核验，最小 1 秒 |
| `REVIEW_PREVIEW_THREAD` | `1` | 后续预览和控制消息回复上一条 |
| `PENDING_REVIEW_RETENTION_DAYS` | `0` | 待审过期天数；`0` 永久保留 |
| `NOVEL_PREVIEW_ENABLED` | `false` | **可选发布增强**：TXT 小说经 TelePress 发布到 Telegraph，「在线阅读」链接出现在频道 caption；**默认关闭**，开启后 TXT document 仍正常发送，Telegraph 失败/超时绝不导致投稿失败（§telepress-preview） |
| `NOVEL_PREVIEW_TIMEOUT_SECONDS` | `15` | 单次预览尝试的严格超时（秒）；Telegraph 不能无限拖住发布 |
| `NOVEL_PREVIEW_MAX_BYTES` | `4194304` | 读取 TXT 正文的大小上限（字节），超限则不生成预览 |
| `TELEGRAPH_ACCESS_TOKEN` | 空 | Telegraph 账户 access token（Secret，绝不写日志）；空 = 预览关闭 |
| `PENDING_REVIEW_CLEANUP_BATCH_SIZE` | `100` | 每轮最多过期 1–200 条 |
| `REVIEW_RETENTION_DAYS` | `30` | 已决审核和 API 通知幂等记录保留天数 |
| `SUPERSEDED_RETENTION_DAYS` | `30` | 被替换（重抓成功）的旧审核卡保留天数；到期后删除其 Telegram 预览/控制消息与记录，血缘（attempt/seen）保留；`0` 不清理 |
| `REFETCH_PROGRESS_REMIND_MINUTES` | `5` | 重抓受理后超过该分钟数仍无终态，向审核群最多提醒一次；`0` 关闭提醒 |
| `REFETCH_WAKE_MINUTES` | `12` | 远端机器不可达且超过该分钟数无进展时，watchdog 用同一 request UUID 幂等唤醒（不创建新 attempt） |
| `REFETCH_HARD_TIMEOUT_MINUTES` | `90` | 超过该分钟数仍无终态则 attempt 标 `failed(stalled_after_hard_timeout)` 并通知；`0` 关闭硬超时 |
| `REFETCH_STALE_TIMEOUT_MINUTES` | `45` | 无终态时开始核查 PixivFlow durable slot；未受理请求可判超时，已受理且仍在执行/投递的 attempt 不凭本地时间判失败；`0` 关闭核查 |
| `API_MAX_FILES` | `100` | HTTP API 单次投稿文件数上限；父路由只限总字节不数文件 |

Telegram 只保证 Bot 可删除 48 小时内消息；需要自动清理审核群时通常把待审保留设为 1 天。

`TELEPOST_IMAGE_DECODE_BUDGET_MB` 衡量的是解码后的像素工作集，不是压缩文件大小。
无需转换且符合 Telegram photo 限制的文件会直接流式发送，不调用 Pillow；常规需要转换的文件在
估算峰值不超过 64 MiB 时才生成临时 JPEG。PNG 这类没有降采样解码能力的格式可再用
192 MiB 硬峰值尝试一次压缩；超过这个硬峰值才把 immutable original 暂存为 document，或在没有
preview 时直接回退。

### 已部署实例调整（Fly.io）

已跑起来的部署改这两项**不用改代码/重建镜像**，直接改环境变量后重启生效：

```bash
# 多相册投稿改为「都回复主贴」（不再逐级嵌套成链）
fly secrets set -a <app> CHANNEL_ALBUM_REPLY=discussion

# 单次投稿文件数上限（代码默认已为 100；可继续按需调大）
fly secrets set -a <app> API_MAX_FILES=100
```

- `CHANNEL_ALBUM_REPLY=discussion` 生效样例：11 张图 + 4 个文件 → 频道发首组 10 图 + 全部 4 文件（文件相册跟随图片组），第 11 图进入图片讨论串；文件超过 10 个时，超出部分进入文件讨论串。
- 2.10.43 起，相册降级为单张发送时也保持所选层级：`chain` 逐条回复上一条，`post` 都回复主贴（或调用方指定的锚点）。网络超时仍不自动重发，需先确认频道中是否已送达。
- `post` 指同一频道内的消息回复，不会把后续图片移到关联讨论群的评论区；它不改变发送目标。
- `discussion` 才是评论区展示：频道 root 保留首组图片和首组文件（图片在前、文件在后），溢出图片/文件分别进入各自的讨论锚点串；Bot 必须在频道的关联讨论组中且可发消息。
- 普通 `chain` / `post` 多批发布遇到**确定失败**时，会把已确认的 Telegram 消息写入 delivery ledger；同一幂等键重试只续发剩余批次。响应状态不确定时该键会停止自动发送，必须先人工核对，避免重复主贴。
- `discussion` 仅在 **Webhook 模式**可用——自动转发事件要在进入 PTB 更新队列前捕获；Polling 模式拿不到自动转发，root 确认后只能保留频道主贴、不能投递溢出，不会自动重跑。配错时启动日志会有告警。
- 频道 root 未确认（发送失败 / 响应丢失且反查不到转发）时允许回滚并自动重试一次；root 一旦确认，linked-discussion overflow 失败**绝不删除或重跑 root**，只按可确定部分清理讨论区并保留主贴，需人工核验对应评论串。仅评论相册"发了没成功"这类无法判断是否重复的情况不自动重试。
- 审核发布若进程中途崩溃，记录会卡在 `publishing`；超过 `PUBLISHING_STALE_SECONDS`（默认 300）秒后点「重试发布」会自动解锁重发。
- `API_MAX_FILES` 放宽的是 HTTP API 投稿入口（PixivFlow 等）；单个 Telegram 相册仍 ≤10，发布侧自动分批。
- 设置会触发应用重启；生产现网（telesubmit-multi-bot）已启用 `discussion`，单个 Telegram 相册仍 ≤10。

## 编辑后发布（Editorial Revision，§editorial）

审核员在审核群里选「编辑后发布」时，可以先改稿再发：标题、简介、标签、链接、剧透与媒体顺序，或移除附件。

- **原始稿件不可变**：编辑只产生一份 Revision，投稿者原稿（含媒体清单）永远保留可审计；媒体排序或移除只影响这次发布的子集。
- **发布保存独立快照**：频道里发出去的是 Publication Snapshot，发布后不能再改，也不会反写原稿。
- **投稿者可以收到通知**：`SUBMITTER_PUBLISH_NOTIFY=with_changes` 时，发布通知会附带修改摘要（见本文末的「投稿者发布通知」）。
- **职责边界**：Review FSM 决定「能不能发布」，Editorial Revision 决定「发布哪个版本」，两者互不影响；被重抓替换掉的旧版本不能再发布。

实现不变量见 AGENTS.md（§editorial / §notify-submitter）。

## 多 Bot

存在 `BOT1_TOKEN` 时，`run.py` 进入多 Bot 模式，并连续读取
`BOT1_TOKEN`、`BOT2_TOKEN`……中间不能缺号。每个 Bot 至少配置：

```env
BOT1_TOKEN=...
BOT1_CHANNEL_ID=@channel_one
BOT1_OWNER_ID=123456789
BOT2_TOKEN=...
BOT2_CHANNEL_ID=@channel_two
BOT2_OWNER_ID=123456789
```

可用 `BOT{n}_` 覆盖 `run.py` 的 `OVERRIDABLE_KEYS`：Owner/Admin、显示与通知、Bot
模式、文件类型、限频、审核、数据库、搜索、健康端口、超时、运行模式、Webhook
Secret，以及频道 footer（`BOT{n}_CHANNEL_FOOTER_LINK` / `BOT{n}_MINIAPP_SUBMIT_CTA`）。
默认数据目录为 `data/botN/`，父路由固定提供：

- `/webhook/botN`
- `/api/botN/v1/*`

## PixivFlow 独立执行端与重抓

生产拆分部署时，TelePost 保持常驻，PixivFlow 平时停止（Fly `auto_start_machines` 会在收到
HTTPS 请求时自动唤醒）；审核群重抓使用以下配置：

| 变量 | 用途 |
|---|---|
| `PIXIVFLOW_REFETCH_BASE_URL` | PixivFlow 的 HTTPS 地址，例如 `https://pixivflow-scheduler.fly.dev` |
| `PIXIVFLOW_REFETCH_TOKEN` | 与 PixivFlow 端同名 Secret 一致的专用 Bearer；与定时触发令牌分离 |

### 重抓的语义：换一个候选

「重抓」= 为当前 **pending** 审核稿寻找一个**新的、该审核链尚未展示过**的候选作品，成功后用它
**替换**当前候选；不是重新下载同一个作品。流程：

```text
审核群点「重抓」 → TelePost 持久化一次 attempt（一链同时只允许一个活跃 attempt）
→ POST /internal/targets/{targetId}/refetch（携带 UUID requestId + 审核链 correlation）
→ Fly 代理唤醒已停止的 PixivFlow → 写入 durable manual Slot → 后台执行
→ 找到新候选：新稿预览和控制卡就绪后，同事务提交新稿、旧稿 superseded、attempt replaced
→ 没有新候选：PixivFlow 回报 no_alternative，当前稿件保持不变，之后可再次重抓
→ 真正失败：回报 failed，当前稿件保持不变
```

- 幂等：同一次按钮点击（同一 `callback_query.id`）的 webhook 重投复用同一个 requestId 与
  同一个 manual Slot，绝不产生第二次执行；只有**用户再次主动点击**才创建新一代 attempt。
- 一个审核链同一时刻只能有一个活跃重抓；处理中重复点击返回「正在重抓，请稍候」。
- 审批竞态安全：重抓运行期间若审核人已批准/拒绝，迟到的结果标记为 obsolete，
  不会覆盖审核结论、不会创建虚假的 superseded。
- 旧按钮安全：已被替换（superseded）或已结束的审核稿上的按钮被点击时直接拒绝，
  不会产生历史分叉。
- 审核链 lineage：`review_chain_id`（同一条审核线）、`generation`（0 = 原稿，
  每成功替换一次 +1）、`supersedes_review_id`（被替换的旧稿）。候选历史
  `refetch_seen_candidates` 以 (chain, work id) 唯一约束记录该链已展示过的作品。
- 旧稿（superseded）在保留 `SUPERSEDED_RETENTION_DAYS` 天后由定期维护删除群里的旧卡与
  记录（尝试删消息失败不阻断）；attempt 与候选历史永久保留作审计。
- 进度可感知：受理后 `REFETCH_PROGRESS_REMIND_MINUTES` 无终态会发「仍在处理中」提醒；
  `REFETCH_STALE_TIMEOUT_MINUTES` 仍无终态则判定 failed 并通知，用户可再次点击；
  成功替换、无候选、失败都各有明确群消息，不会看起来卡死。
- 不接受 `target_id` 为空、或未配置上面的两个变量；不要用 `PIXIVFLOW_ENABLED=true`
  尝试唤醒独立执行端（那是同容器兼容模式的开关，拆分拓扑不适用）。
- 内部 token 只在服务间 Bearer 请求头传递，绝不进群消息、日志或审计。

## PixivFlow 联合进程（兼容模式）

`PIXIVFLOW_ENABLED=true` 会让 TelePost supervisor 同时拉起 PixivFlow。相关变量：

| 变量 | 默认 |
|---|---|
| `PIXIVFLOW_CONFIG` | `/app/data/pixivflow/config.json` |
| `PIXIVFLOW_CONFIG_TEMPLATE` | 镜像内模板 |
| `PIXIVFLOW_COMMAND` | `pixivflow scheduler` |

该模式需要包含 Node/PixivFlow 的 `runtime-pixivflow` 镜像，并且必须常驻才能运行 Cron。
合一台镜像需带 `ffmpeg`：PixivFlow 处理 ugoira（Pixiv 动图）时会把帧 ZIP 转成循环 GIF，
运行时 spawn `python3` + `ffmpeg`；缺 ffmpeg 时动图只会以 ZIP + 帧 JSON 文档形式投递。
Fly.io 拆分部署由独立 PixivFlow Machine 按需唤醒；TelePost 常驻。此兼容模式不用于该拓扑。

## `config.ini`

完整模板是仓库根目录的 [`config.ini.example`](https://github.com/redtidev1918/TelePost/blob/main/config.ini.example)。常用映射：

- `[BOT]`：核心配置、运行模式与审核
- `[WEBHOOK]`：`URL`、`PORT`、`PATH`、`SECRET_TOKEN`
- `[SEARCH]`：`INDEX_DIR`、`ENABLED`、`ANALYZER`、`HIGHLIGHT`
- `[DB]`：`CACHE_SIZE_KB`

并非所有高级环境变量都有 INI 映射；部署平台优先使用环境变量/Secrets。

## 投稿者发布通知（§notify-submitter）

| 变量 | 默认 | 说明 |
|---|---|---|
| `SUBMITTER_PUBLISH_NOTIFY` | `off` | `off` 不通知；`published` 只通知“已发布”；`with_changes` 附带 editorial change summary |

- 触发点统一为 **Publication Success**（频道发布确认），不是 review approval。
- 覆盖 Chat 直发 / 审核原稿 / 审核编辑三条路径；匿名 human 仍私聊通知；Service
  投稿从不通知。
- 匿名 human 在管理员「投稿通知」中显示内部用户 ID（`tg://user` 链接）供封禁，
  不显示 username / display name；频道公开 caption 仍保持匿名。
- 幂等键 `publication:<message_id>:submitter-notification`；失败只重试通知，
  不回滚发布。
