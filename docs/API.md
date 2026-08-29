# HTTP API（v1）

供外部项目/脚本向频道自动投稿的 HTTP 接口。与 Telegram 聊天投稿共用同一套
发布、记录、搜索索引与频控逻辑。

## 快速开始

1. 使用配置为 `OWNER_ID` 的 Telegram 账号向 bot 发送：

   ```
   /gen_token 我的自动化脚本
   ```

   bot 会返回一个 `tp_` 开头的 token（仅显示一次，请妥善保存）。

2. 用该 token 调用投稿接口：

   ```bash
   curl -X POST "https://<你的域名>/api/bot1/v1/submissions" \
     -H "Authorization: Bearer tp_你的token" \
     -F "files=@图片1.jpg" \
     -F "files=@图片2.jpg" \
     -F "tags=测试, API" \
     -F "title=标题（可选）" \
     -F "note=简介（可选）" \
     -F "link=https://example.com（可选）"
   ```

3. 成功返回 `201`。默认立即发布：

   ```json
   {
     "ok": true,
     "data": {
       "status": "published",
       "message_id": 123,
       "link": "https://t.me/yourchannel/123",
       "media_count": 2,
       "document_count": 0
     }
   }
   ```

   开启 `API_REVIEW_REQUIRED=true` 时，改为进入审核队列：

   ```json
   {
     "ok": true,
     "data": {
       "status": "pending_review",
       "review_id": 42,
       "media_count": 2,
       "document_count": 0
     }
   }
   ```

## 认证

Polling 与 Webhook 模式都会启动相同的 HTTP API。多 Bot 部署通过父路由使用
`/api/botN/v1/*`，因此切换 Telegram 更新模式时 PixivFlow 不需要修改投稿地址。
父路由和 Telegram 上传均采用 64 KiB 分块流式传输；单文件仍限制 50 MiB、单次
最多 10 个，临时文件会在所有成功或失败返回路径统一清理。

所有 `/api/v1` 端点（除 health）都需要请求头：

```
Authorization: Bearer tp_xxxxxxxx
```

- token 只能由 Bot 所有者（`OWNER_ID`）通过 `/gen_token` 生成，并绑定其 Telegram 用户身份
- 服务端只存 SHA-256 哈希，明文丢失只能重新生成
- `/tokens` 查看自己名下的 token，`/revoke_token <编号>` 吊销

## 多 bot 寻址

多 bot 部署（见 [CONFIGURATION.md](CONFIGURATION.md)「多 bot 模式」）时，每个 bot
有独立的 API 前缀：

| 公网路径 | 转发到 |
|---|---|
| `/api/bot1/v1/...` | bot1 子进程 |
| `/api/bot2/v1/...` | bot2 子进程 |

单 bot 部署同样可以使用 `/api/bot1` 前缀（run.py 对单 bot 也生效）。

## 端点

### POST /api/v1/submissions

创建一次投稿（multipart/form-data）。默认立即发布；开启 API 审核时先进入私有审核群。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `files` | file，可重复 | 是 | 1–50 个文件；图片/视频/GIF/音频按媒体发布，其余按文档发布 |
| `tags` | text | 是 | 逗号分隔，最多 30 个，发布时自动加 # 前缀 |
| `title` | text | 否 | ≤100 字符 |
| `note` | text | 否 | ≤600 字符 |
| `link` | text | 否 | http(s) 链接 |
| `anonymous` | text | 否 | `true` 时频道内不显示投稿人 |
| `spoiler` | text | 否 | `true` 时媒体加剧透遮罩 |
| `idempotency_key` | text | 否 | 审核模式下的防重键，建议传稳定的来源 ID，最长 240 字符 |

约束：单文件 ≤50MB，单次文件累计 ≤500MB；媒体（图片/视频/GIF）与文档混传时，文档作为媒体主贴的回复发出。

### GET /api/v1/me

返回当前 token 的身份与频用量：

```json
{
  "ok": true,
  "data": {
    "telegram_user_id": 5073758941,
    "name": "我的自动化脚本",
    "submissions_last_hour": 2,
    "rate_limit_per_hour": 10
  }
}
```

### GET /api/v1/health

无需认证。返回服务与版本信息，可用于探活。

## file_id 直投（JSON body）

如果素材已经在 Telegram 服务器上（你持有它的 file_id，且该 file_id 是**由本 bot**获取的——例如素材来自本 bot 监听的频道），可以用 JSON body 直投，文件传输量为零。

```http
POST /api/bot1/v1/submissions
Content-Type: application/json
Authorization: Bearer tp_xxxx

{
  "media": [
    {"type": "photo", "file_id": "AAA"},
    {"type": "video", "file_id": "BBB"}
  ],
  "documents": [{"file_id": "CCC", "filename": "archive.zip"}],
  "tags": "测试",
  "title": "标题（可选）",
  "anonymous": false,
  "idempotency_key": "source:123"
}
```

注意：
- file_id 与 bot 绑定：必须是**同一个 bot**获取的 file_id，跨 bot 不可用
- 适合素材源自本 bot 监听的频道/会话的场景；外部网站下载的文件请走 multipart 上传
- 响应与 multipart 形态完全一致

## 错误格式

非 2xx 响应统一为：

```json
{
  "ok": false,
  "error": { "code": "invalid_tags", "message": "标签格式错误（必填，最多30个，逗号分隔）" }
}
```

| HTTP | code | 场景 |
|---|---|---|
| 400 | `invalid_content_type` | 未使用 multipart/form-data |
| 400 | `missing_files` | 没有提供文件 |
| 400 | `too_many_files` | 超过 50 个文件 |
| 400 | `invalid_tags` / `invalid_link` | 字段校验失败 |
| 401 | `invalid_token` | token 缺失/错误/已吊销 |
| 413 | `file_too_large` | 单文件超过 50MB |
| 413 | `request_too_large` | 单次文件累计超过 500MB |
| 429 | `rate_limited` | 超过每小时投稿限额 |
| 502 | `publish_failed` | 频道发布失败（网络或 Telegram 侧错误） |
| 502 | `review_queue_failed` | 审核群上传或审核记录持久化失败 |

## 投稿审核来源选择

API 与 Telegram 聊天投稿使用两个独立开关，部署者可以按来源选择是否审核：

| API_REVIEW_REQUIRED | CHAT_REVIEW_REQUIRED | 行为 |
|---|---|---|
| `false` | `false` | 两种投稿都直接发布（默认，兼容旧行为） |
| `true` | `false` | 仅 API 投稿进入审核群 |
| `false` | `true` | 仅 Telegram `/submit` 投稿进入审核群 |
| `true` | `true` | 两种投稿都进入审核群 |

```env
API_REVIEW_REQUIRED=true
CHAT_REVIEW_REQUIRED=false
REVIEW_CHAT_ID=-1001234567890
```

1. 创建私有 Telegram 审核群，将 Bot 加入群组，并取得 `-100...` 形式的 ID。
2. 开启对应来源后，素材会先发到该群；SQLite 只保存 Telegram `file_id`、投稿字段、来源和审核状态。
3. `OWNER_ID` 与 `ADMIN_IDS` 中的用户可点击「发布到频道」或「拒绝」。批准后复用 file_id，不二次上传原文件。
4. 审批状态通过条件更新原子抢占，多人点击或重复点击不会重复发布。
5. Telegram 聊天投稿人在提交后会看到“已进入审核队列”，通过或拒绝后会收到 Bot 私聊通知。

多 bot 模式可逐 bot 配置：

```env
BOT1_API_REVIEW_REQUIRED=true
BOT1_CHAT_REVIEW_REQUIRED=false
BOT1_REVIEW_CHAT_ID=-1001234567890
BOT2_API_REVIEW_REQUIRED=true
BOT2_CHAT_REVIEW_REQUIRED=true
BOT2_REVIEW_CHAT_ID=-1001234567890
```

PixivFlow 的 multipart target 建议加入幂等键：

```json
{
  "fields": {
    "tags": ["Pixiv", "{{tag}}"],
    "title": "{{title}}",
    "note": "Pixiv ID: {{pixivId}}",
    "link": "https://www.pixiv.net/artworks/{{pixivId}}",
    "anonymous": true,
    "idempotency_key": "pixiv:{{pixivId}}:{{tag}}"
  },
  "success": { "statuses": [201], "jsonPath": "ok", "equals": true }
}
```

TelePost 只在审核群上传成功且 SQLite 记录已建立后才返回 `201`。因此 PixivFlow 收到成功响应后可安全清理本地 cache；失败时应保留 outbox 并重试。

## 限额

- 每用户每小时 `SUBMIT_LIMIT_PER_HOUR` 次（默认 10，管理员可在配置中调整，0 关闭）
- API 入站：最多 50 个文件，单文件 ≤50MB，累计 ≤500MB
- 频道侧：单媒体组 ≤10 个文件；TelePost 会自动分组发送

## Python 调用示例

```python
import requests

BASE = "https://your-domain.fly.dev/api/bot1/v1"
HEADERS = {"Authorization": "Bearer tp_xxxx"}

files = [("files", open("cover.jpg", "rb")), ("files", open("video.mp4", "rb"))]
data = {"tags": "公告, 更新", "title": "新版本发布", "note": "详见正文"}

resp = requests.post(f"{BASE}/submissions", headers=HEADERS, files=files, data=data)
resp.raise_for_status()
print(resp.json()["data"]["link"])
```

## 运维

- 关闭 API：设置环境变量 `API_ENABLED=false` 后重启
- 撤销所有 token：数据库 `UPDATE api_tokens SET revoked=1`
- API 投稿与聊天投稿共用频控与审计日志
