# HTTP API（v1）

供外部项目/脚本向频道自动投稿的 HTTP 接口。与 Telegram 聊天投稿共用同一套
发布、记录、搜索索引与频控逻辑。

## 快速开始

1. 在 Telegram 里向 bot 发送：

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

3. 成功返回 `201`：

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

## 认证

所有 `/api/v1` 端点（除 health）都需要请求头：

```
Authorization: Bearer tp_xxxxxxxx
```

- token 由 `/gen_token` 生成，绑定生成者的 Telegram 用户身份（投稿记账按该身份）
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

创建并立即发布一次投稿（multipart/form-data）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `files` | file，可重复 | 是 | 1–10 个文件；图片/视频/GIF/音频按媒体发布，其余按文档发布 |
| `tags` | text | 是 | 逗号分隔，最多 30 个，发布时自动加 # 前缀 |
| `title` | text | 否 | ≤100 字符 |
| `note` | text | 否 | ≤600 字符 |
| `link` | text | 否 | http(s) 链接 |
| `anonymous` | text | 否 | `true` 时频道内不显示投稿人 |
| `spoiler` | text | 否 | `true` 时媒体加剧透遮罩 |

约束：单文件 ≤ 50MB；媒体（图片/视频/GIF）与文档混传时，文档作为媒体主贴的回复发出。

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
| 400 | `too_many_files` | 超过 10 个文件 |
| 400 | `file_too_large` | 单文件超过 50MB |
| 400 | `invalid_tags` / `invalid_link` | 字段校验失败 |
| 401 | `invalid_token` | token 缺失/错误/已吊销 |
| 413 | `file_too_large` | 同上 |
| 429 | `rate_limited` | 超过每小时投稿限额 |
| 502 | `publish_failed` | 频道发布失败（网络或 Telegram 侧错误） |

## 限额

- 每用户每小时 `SUBMIT_LIMIT_PER_HOUR` 次（默认 10，管理员可在配置中调整，0 关闭）
- 频道侧限制：单媒体组 ≤10 个文件；单文件 ≤50MB

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
