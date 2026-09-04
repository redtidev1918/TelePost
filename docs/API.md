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
| `idempotency_key` | 否 | 最长 240；只在审核模式防止重复入队 |
| `target_id` | 否 | 最长 120；审核模式标识自动化来源，供定向重抓 |

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

立即发布成功：

```json
{
  "ok": true,
  "data": {
    "status": "published",
    "message_id": 123,
    "link": "https://t.me/channel/123",
    "media_count": 1,
    "document_count": 1
  }
}
```

`API_REVIEW_REQUIRED=true` 时成功响应为 `201`，`status` 是 `pending_review`，并包含
`review_id` 和 `reused`。`reused=true` 表示命中了现有幂等记录；TelePost 会补齐缺失的
`target_id` 并在审核群发送复用提示。只有审核群上传和 SQLite 记录都成功后才返回 201；上游收到非 2xx 时应
保留任务并重试。

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
