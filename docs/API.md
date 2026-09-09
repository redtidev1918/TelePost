# HTTP API v1

外部程序可通过 TelePost 发布文件、复用 Telegram `file_id`，或向审核群发送状态通知。
Polling 与 Webhook 模式提供同一套 API。

## 地址

| 部署 | 根地址 |
|---|---|
| 单 Bot | `/api/v1` |
| 多 Bot | `/api/botN/v1` |

单 Bot 无论用源码、Docker 还是 Fly.io，路径都是 `/api/v1`。只有配置
`BOT1_TOKEN`、`BOT2_TOKEN` 等多 Bot 变量时，才使用 `/api/botN/v1`。

## Token

使用配置为 `OWNER_ID` 的 Telegram 账号生成 Token：

```text
/gen_token pixivflow
```

明文只显示一次。服务端只保存 SHA-256 哈希；用 `/tokens` 查看编号，
`/revoke_token <编号>` 吊销。除健康检查外，请求都需要：

```http
Authorization: Bearer tp_xxxxxxxx
```

不要把 Token 放进 URL、日志、仓库或普通配置文件；部署时使用 Secrets。

## 健康检查

```http
GET /api/v1/health
```

无需认证，返回 API 版本、Bot 版本和两类审核开关。

## 身份与限额

```http
GET /api/bot1/v1/me
Authorization: Bearer tp_xxxx
```

返回 Token 归属、最近一小时用量和 `SUBMIT_LIMIT_PER_HOUR`。

## 文件投稿

```bash
curl -X POST 'https://example.com/api/bot1/v1/submissions' \
  -H 'Authorization: Bearer tp_xxxx' \
  -F 'files=@cover.jpg' \
  -F 'files=@novel.txt' \
  -F 'tags=Pixiv,推荐' \
  -F 'title=标题' \
  -F 'note=简介' \
  -F 'link=https://example.com/source' \
  -F 'anonymous=true' \
  -F 'spoiler=false' \
  -F 'idempotency_key=source:123' \
  -F 'target_id=daily-pixiv'
```

`Content-Type` 必须是 `multipart/form-data`。

| 字段 | 必填 | 限制 |
|---|---|---|
| `files` | 是 | 可重复；最多 50 个，单个 50 MiB，合计 500 MiB |
| `tags` | 是 | 逗号分隔，最多 `ALLOWED_TAGS`（默认 30） |
| `title` | 否 | 最长 100 字符 |
| `note` | 否 | 最长 600 字符；接受真实换行和字面 `\\n` |
| `link` | 否 | 必须以 `http://` 或 `https://` 开头 |
| `anonymous` | 否 | `true`、`1`、`yes` 为真 |
| `spoiler` | 否 | 同上 |
| `idempotency_key` | 强烈建议 | 最长 240；审核模式防重复入队，直发模式防重复发帖（ACK 丢失重试安全） |
| `target_id` | 否 | 最长 120；审核模式标识自动化来源，供定向重抓 |
| `source_label` | 否 | 最长 80；人类可读来源标签（如 `PixivFlow · 每日推荐`）。审核控制卡上展示；TelePost 不解析其含义，缺省不显示 |
| `source_ref` | 否 | 最长 160；机器可读、稳定的来源引用（如上游 job/execution id）。仅存档/排查，TelePost 不解释其结构 |
| `scheduled_at` | 否 | 最长 40；计划时间（ISO-8601）。仅来源展示/排查，TelePost 不据此调度 |

> `source_label` / `source_ref` / `scheduled_at` 是**通用、可选、有界**的来源字段，任何 API
> 客户端都可发送；不传时行为完全不变（向后兼容）。TelePost 不知道也不依赖任何上游的调度/
> Slot 状态机。字段按单行纯文本处理（去除控制字符），只出现在纯文本审核控制卡，不进入
> HTML 频道正文。

上传按 64 KiB 流式写入 `data/api_uploads/<request>`，正常返回和错误都会清理；异常中断
遗留目录由后台清扫。父路由同样流式转发，不会把 500 MiB 请求整体读入内存。

## `file_id` 投稿

已有由同一个 Bot 获得的 Telegram `file_id` 时，可零传输发布：

```bash
curl -X POST 'https://example.com/api/bot1/v1/submissions' \
  -H 'Authorization: Bearer tp_xxxx' \
  -H 'Content-Type: application/json' \
  -d '{
    "media": [
      {"type": "photo", "file_id": "AAA"},
      {"type": "video", "file_id": "BBB"}
    ],
    "documents": [{"file_id": "CCC", "filename": "novel.txt"}],
    "tags": "Pixiv,推荐",
    "title": "标题",
    "note": "简介",
    "link": "https://example.com/source",
    "anonymous": true,
    "spoiler": false,
    "idempotency_key": "source:123",
    "target_id": "daily-pixiv"
  }'
```

`media[].type` 只接受 `photo`、`video`、`animation`、`audio`；`documents[]` 必须有
`file_id`。两组至少一项，总数最多 50。当前 JSON 路径允许空标签，但调用方仍应提供
标签，保持与聊天投稿和 multipart 行为一致。`file_id` 与 Bot 绑定，不能跨 Bot 使用。

## 审核群通知

```bash
curl -X POST 'https://example.com/api/bot1/v1/notifications' \
  -H 'Authorization: Bearer tp_xxxx' \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "本次没有符合条件的候选",
    "idempotency_key": "pixivflow:no-match:2026-09-04"
  }'
```

`REVIEW_CHAT_ID` 必须已配置。`text` 最长 2000 字符；同一 Telegram 用户下重复的
`idempotency_key` 返回 `duplicate`。发送失败会释放占位，允许 outbox 安全重试。

## 响应

### 直发模式（`API_REVIEW_REQUIRED=false`）

首次发布成功（HTTP 200）：

```json
{
  "ok": true,
  "data": {
    "status": "published",
    "reused": false,
    "message_id": 123,
    "link": "https://t.me/channel/123",
    "media_count": 1,
    "document_count": 1
  }
}
```

上游在收到响应前超时/断连时，应**用同一个 `idempotency_key` 原样重试**。此时不会产生
第二条频道消息，响应为 HTTP 200 且：

```json
{
  "ok": true,
  "data": {
    "status": "published",
    "reused": true,
    "reuse_reason": "idempotent_replay",
    "matched_idempotency_key": "source:123",
    "message_id": 123
  }
}
```

`reuse_reason` 有两种语义，上游都应按成功处理、**不得再次补发**：

| reuse_reason | 含义 |
|---|---|
| `idempotent_replay` | 同一个 `idempotency_key` 的重试（典型：ACK 丢失）。返回的 `message_id` 就是第一次发布的那条，频道里只有一条消息。 |
| `duplicate_existing` | `idempotency_key` 不同（新的 slot/触发），但同一作品（target+type+pixiv_id）在去重窗口内已由**另一次意图**发布过。不创建新消息，`matched_idempotency_key` 指向先发布的那条。 |

### 审核模式（`API_REVIEW_REQUIRED=true`）

成功响应为 `201`，`status` 是 `pending_review`，并包含 `review_id` 和 `reused`。
`reused=true` 时同样带 `reuse_reason`（`idempotent_replay` / `duplicate_existing`）
与 `matched_idempotency_key`；TelePost 会补齐缺失的 `target_id` 并在审核群发送复用提示。
只有审核群上传和 SQLite 记录都成功后才返回 201；上游收到非 2xx 时应保留任务并用同一
`idempotency_key` 重试。

同一 `idempotency_key` 命中待审核、失败或 7 天内已发布记录时，不重复上传媒体或
创建审核记录，但会在当前审核群发送一条提示，引用原审核编号并显示状态。因此每次
成功投递都有可见反馈，同时仍避免重复发布。

错误统一为：

```json
{"ok": false, "error": {"code": "invalid_token", "message": "…"}}
```

| HTTP | 常见 code |
|---|---|
| 400 | `invalid_content_type`、`invalid_multipart`、`invalid_json`、`invalid_media`、`missing_files`、`missing_media`、`too_many_files`、`invalid_tags`、`invalid_link` |
| 401 | `invalid_token` |
| 409 | `review_chat_not_configured` |
| 413 | `file_too_large`、`request_too_large` |
| 429 | `rate_limited` |
| 502 | `publish_failed`、`review_queue_failed`、`notification_failed` |
| 503 | `notification_state_failed` |

## 审核管理 API（MCP/内部工具）

审核管理端点仅供专用 review token 或 owner token 使用；普通投稿 token 只有读取权限。
MCP sidecar 推荐设置 `TELEPOST_MCP_REVIEW_TOKEN`，并可通过
`TELEPOST_REVIEW_API_MODE=readonly` 禁止 HTTP 写操作。完整配置见 [MCP 投稿审核](MCP_REVIEW.md)。

- `GET /api/v1/reviews`：待审核摘要列表，支持 `limit`、`cursor`
- `GET /api/v1/reviews/{id}`：完整文本元数据和媒体索引
- `GET /api/v1/reviews/{id}/media/{index}?variant=preview`：受限图片预览
- `GET /api/v1/reviews/policy`：管理员维护的 Markdown 审核规则
- `POST /api/v1/reviews/{id}/approve`：审核通过并发布（需人工明确确认）
- `POST /api/v1/reviews/{id}/reject`：拒绝，可附带有界 `reason`
- `PATCH /api/v1/reviews/{id}/spoiler`：发布前设置 spoiler

这些端点与 Telegram 审核按钮调用同一个 `ReviewService`，使用同一条条件 claim、
发布失败回退和幂等状态转换；OpenAPI 片段见 [`openapi.yaml`](openapi.yaml)。

## 审核状态

```text
pending ──批准──▶ publishing ──▶ published ──删帖──▶ deleted
   ├─拒绝────────────────────────▶ rejected
   ├─发布失败────────────────────▶ failed
   └─超时────────────────────────▶ expired
```

批准/拒绝使用条件更新原子抢占，重复点击不会重复发布。管理员可在审核群切换剧透；
Pixiv 来源且启用 PixivFlow 时还可触发目标级重抓。

## 发布布局

频道发布和审核预览共用同一布局：图片/视频相册 → GIF/音频 → 文档组；每组最多 10
个，后续组回复上一组，caption 只在首条。大于 10 MiB 的本地图片会先压缩，失败才改按
文档发送；相册失败会降级逐条发送。