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
| `NOTIFY_OWNER` | `true` | 发布完成后是否私聊 Owner |
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
| `WEBHOOK_SECRET_TOKEN` | 随机生成 | Telegram Webhook 请求校验令牌 |
| `HEALTH_PORT` | `8080` | Polling 单 Bot 的健康/API 端口 |
| `API_ENABLED` | `true` | 是否挂载 `/api/v1/*` |
| `ROUTER_TIMEOUT_SECONDS` | `300` | 多 Bot 父路由的上游总超时 |
| `UPLOAD_SESSION_MAX_AGE_SECONDS` | `3600` | 强制中断后遗留上传目录的清理年龄 |
| `TELEPOST_IMAGE_DECODE_BUDGET_MB` | `64` | 允许图片压缩路径使用的估算峰值预算；超出时禁止 full decode |

`AUTO` 只有在 `WEBHOOK_URL` 是公网 HTTPS 地址时才选择 Webhook；自动选择的 Webhook
注册失败会回退 Polling。强制 `WEBHOOK` 失败则退出。

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
| `API_REVIEW_REQUIRED` | `false` | HTTP API 投稿进入审核群 |
| `CHAT_REVIEW_REQUIRED` | `false` | Telegram 聊天投稿进入审核群 |
| `REVIEW_CHAT_ID` | 空 | 任一审核开关启用时必填，且不能等于频道 |
| `REVIEW_ALBUM_SIZE` | `5` | 审核预览每组 1–10 个 |
| `REVIEW_PREVIEW_INTERVAL_SECONDS` | `0.75` | 预览组之间的节流间隔 |
| `REVIEW_PREVIEW_TIMEOUT_SECONDS` | `120` | 单次审核预览 Telegram I/O 超时 |
| `TELEGRAM_SEND_TIMEOUT_SECONDS` | `REVIEW_PREVIEW_TIMEOUT_SECONDS` | 频道发布 Telegram I/O 超时；大相册建议保持 120 秒 |
| `CHANNEL_ALBUM_REPLY` | `chain` | 多图展示：`chain` 在频道逐级回复；`post` 在频道都回复主贴；`discussion` 频道只发首图主贴，其余图片发到关联讨论组的该帖评论串（Webhook 模式） |
| `DISCUSSION_FORWARD_TIMEOUT_SECONDS` | `10` | `discussion` 模式等待频道帖自动转发到讨论组的超时；超时则删除频道主贴并判为发布失败，最小 1 秒 |
| `REVIEW_PREVIEW_THREAD` | `1` | 后续预览和控制消息回复上一条 |
| `PENDING_REVIEW_RETENTION_DAYS` | `0` | 待审过期天数；`0` 永久保留 |
| `PENDING_REVIEW_CLEANUP_BATCH_SIZE` | `100` | 每轮最多过期 1–200 条 |
| `REVIEW_RETENTION_DAYS` | `30` | 已决审核和 API 通知幂等记录保留天数 |
| `API_MAX_FILES` | `50` | HTTP API 单次投稿文件数上限；多页/超大作品可调大（如 100），父路由只限总字节不数文件 |

Telegram 只保证 Bot 可删除 48 小时内消息；需要自动清理审核群时通常把待审保留设为 1 天。

`TELEPOST_IMAGE_DECODE_BUDGET_MB` 衡量的是解码后的像素工作集，不是压缩文件大小。
无需转换且符合 Telegram photo 限制的文件会直接流式发送，不调用 Pillow；需要转换的文件只有
在估算峰值不超过预算时才生成临时 JPEG。超预算时，审核群显示调用方提供的 preview，并把
immutable original 暂存为最终发布用 document；没有 preview 时直接使用 document fallback。

### 已部署实例调整（Fly.io）

已跑起来的部署改这两项**不用改代码/重建镜像**，直接改环境变量后重启生效：

```bash
# 多相册投稿改为「都回复主贴」（不再逐级嵌套成链）
fly secrets set -a <app> CHANNEL_ALBUM_REPLY=post

# 单次投稿文件数上限从 50 放宽到 100（支持超大图集整本投）
fly secrets set -a <app> API_MAX_FILES=100
```

- `CHANNEL_ALBUM_REPLY` 生效样例：30 张图发布到频道 → 第 1 组（10 张）是主贴，第 2、3 组都回复主贴。
- 2.10.43 起，相册降级为单张发送时也保持所选层级：`chain` 逐条回复上一条，`post` 都回复主贴（或调用方指定的锚点）。网络超时仍不自动重发，需先确认频道中是否已送达。
- `post` 指同一频道内的消息回复，不会把后续图片移到关联讨论群的评论区；它不改变发送目标。
- `discussion` 才是评论区展示：Bot 必须在频道的关联讨论组中且可发消息。
- `discussion` 仅在 **Webhook 模式**可用——自动转发事件要在进入 PTB 更新队列前捕获；Polling 模式拿不到，多图发布会在 `DISCUSSION_FORWARD_TIMEOUT_SECONDS` 超时后回滚（删除频道主贴）并判失败。配错时启动日志会有告警。
- 讨论串建立失败（未关联讨论组 / Bot 不在讨论组 / 转发超时）时，已落地的频道封面主贴、讨论组锚点、已发相册会**完整回滚删除**，不留半成品；确定态失败会**自动重试一次**。首贴发送"响应丢失"时会反查自动转发自愈。仅评论相册"发了没成功"这类无法判断是否重复的情况不自动重试，审核群提示人工核对评论串后再点重试。
- 审核发布若进程中途崩溃，记录会卡在 `publishing`；超过 `PUBLISHING_STALE_SECONDS`（默认 300）秒后点「重试发布」会自动解锁重发。
- `API_MAX_FILES` 放宽的是 HTTP API 投稿入口（PixivFlow 等）；单个 Telegram 相册仍 ≤10，发布侧自动分批。
- 设置会触发应用重启；生产现网（telesubmit-multi-bot）已启用 `post` + `100`。

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
模式、文件类型、限频、审核、数据库、搜索、健康端口、超时、运行模式和 Webhook
Secret。默认数据目录为 `data/botN/`，父路由固定提供：

- `/webhook/botN`
- `/api/botN/v1/*`

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
Fly.io 省钱部署应把 PixivFlow 拆到独立常驻 Machine，TelePost 保持自动休眠
（拆分时 ffmpeg 由 PixivFlow 自己的镜像提供，TelePost 镜像无需安装）。

## `config.ini`

完整模板是仓库根目录的 [`config.ini.example`](https://github.com/redtidev1918/TelePost/blob/main/config.ini.example)。常用映射：

- `[BOT]`：核心配置、运行模式与审核
- `[WEBHOOK]`：`URL`、`PORT`、`PATH`、`SECRET_TOKEN`
- `[SEARCH]`：`INDEX_DIR`、`ENABLED`、`ANALYZER`、`HIGHLIGHT`
- `[DB]`：`CACHE_SIZE_KB`

并非所有高级环境变量都有 INI 映射；部署平台优先使用环境变量/Secrets。
