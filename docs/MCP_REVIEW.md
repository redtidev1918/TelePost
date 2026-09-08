# AI Agent 投稿审核 MCP

MCP 只暴露少量审核语义工具。它不是第二套 TelePost Core，不直接访问 SQLite，也不直接拼 Telegram Bot API；Telegram 审核界面、HTTP 审核 API 和 MCP 都调用同一个 `ReviewService`。

## 1. 定位与架构

```text
Telegram 审核按钮 ─┐
                  ├─▶ ReviewService ─▶ pending_reviews claim / publish pipeline
Review HTTP API ──┤        ▲
                  │        │
MCP sidecar ──────┴────────┘ （只通过内部 HTTP API 调用 Service）
```

MCP 首期只围绕 Review：

- `list_pending_reviews`
- `get_review`
- `get_review_media`
- `approve_review`
- `reject_review`
- `set_review_spoiler`
- Resource：`telepost://review-policy`
- Prompt：`review_submission`

健康检查、投稿、搜索、统计、通知等通用能力继续使用现有 HTTP/OpenAPI，不手工包装成 MCP。

## 2. 为什么是 sidecar

官方 MCP Python SDK 2.x 要求 Python 3.10+，而 TelePost 主程序仍支持 Python 3.9+。因此 MCP 是可选 sidecar：

```text
TelePost:        Python 3.9+，主 requirements.txt，:8080
review-mcp:      Python 3.10+，requirements-mcp.txt，stdio 或 :8081
```

不要把 `requirements-mcp.txt` 加进主 Docker 镜像的必需依赖。

## 3. 安装与 stdio 配置

在 TelePost 仓库中创建独立环境：

```bash
python3.10 -m venv .venv-mcp
./.venv-mcp/bin/pip install -r requirements-mcp.txt
```

Claude Desktop / Codex / 其他本地 Agent 示例：

```json
{
  "mcpServers": {
    "telepost-review": {
      "command": "/absolute/path/TelePost/.venv-mcp/bin/python",
      "args": ["-m", "mcp_server.server"],
      "env": {
        "TELEPOST_REVIEW_API_URL": "http://127.0.0.1:8080/api/v1",
        "TELEPOST_MCP_REVIEW_TOKEN": "replace-with-long-random-secret",
        "TELEPOST_MCP_REVIEW_MODE": "readonly"
      }
    }
  }
}
```

TelePost API 进程使用同一个 token：

```env
TELEPOST_MCP_REVIEW_TOKEN=replace-with-long-random-secret
TELEPOST_REVIEW_API_MODE=readonly
```

多 bot 部署把 URL 指到具体 bot：

```text
Bot 1: https://<host>/api/bot1/v1
Bot 2: https://<host>/api/bot2/v1
```

本地 Agent 与 TelePost 同机时优先使用 stdio + `127.0.0.1` HTTP；token 放环境变量或 Secret，不写入仓库、URL、日志或截图。

## 4. Streamable HTTP（可选）

```bash
TELEPOST_REVIEW_API_URL=http://127.0.0.1:8080/api/v1 \
TELEPOST_MCP_REVIEW_TOKEN=... \
TELEPOST_MCP_TRANSPORT=streamable-http \
TELEPOST_MCP_HTTP_HOST=127.0.0.1 \
TELEPOST_MCP_HTTP_PORT=8081 \
./.venv-mcp/bin/python -m mcp_server.server
```

默认路径是 `http://127.0.0.1:8081/mcp`，以 stateless 模式运行。不要在没有反向代理认证/TLS 的情况下暴露到公网。

## 5. Token、权限和只读模式

TelePost 接受三类身份：

| 身份 | list/get/media/policy | approve/reject/spoiler |
|---|---:|---:|
| 专用 `TELEPOST_MCP_REVIEW_TOKEN` / `TELEPOST_REVIEW_TOKEN` | 可以 | `readwrite` 模式可以 |
| 绑定 `OWNER_ID` 的 TelePost API token | 可以 | `readwrite` 模式可以 |
| 普通 TelePost API token | 可以 | 不可以 |
| 无 token / 错误 token | 不可以 | 不可以 |

两层只读开关：

- `TELEPOST_MCP_REVIEW_MODE=readonly`：MCP 不注册写工具，Agent 无法发现或调用它们。
- `TELEPOST_REVIEW_API_MODE=readonly`：TelePost HTTP 层拒绝所有审核写操作，是服务端兜底。

让 AI 只给建议时，建议两层都设置为 `readonly`。需要人工通过 Agent 执行时，再改成 `readwrite`，并保持“用户明确确认具体动作”的工作流。

## 6. 查看待审核稿件和媒体

典型只读流程：

1. `list_pending_reviews(limit=20)` 获取轻量摘要。
2. `get_review(review_id=184)` 获取标题、备注、标签、来源、投稿人和媒体 metadata。
3. 读取 `telepost://review-policy`。
4. 对必要媒体调用 `get_review_media(review_id=184, index=0, variant="preview")`。

媒体只能通过 `review_id + index` 访问，不接受任意本地路径。服务端：

- 图片：默认长边 ≤1600 的 JPEG preview；thumbnail 长边 ≤400；`original` 返回原始图片（仍有 20 MiB 安全上限）。
- GIF/animation：第一版只返回 Telegram thumbnail 对应的静态 JPEG；无 thumbnail 时返回 `preview_unavailable`，不做完整 GIF pipeline。
- 视频：第一版只返回 Telegram 封面；无封面时返回 `preview_unavailable`，不引入 ffmpeg、不抽帧、不返回几十 MB 原视频。
- 文档/音频：第一版只通过 `get_review` 查看元数据，不返回内容。

## 7. 审核策略

管理员直接编辑：

```text
config/review_policy.md
```

也可以通过环境变量指定：

```env
TELEPOST_REVIEW_POLICY=/etc/telepost/review_policy.md
```

策略文件应写清频道主题、允许/禁止内容、广告引流、版权、媒体质量、标签、NSFW/spoiler、拒绝原因和边界情况。模板没有替频道制定价值判断；规则缺失时 Agent 应选择 `needs_human_review`。

## 8. Human-in-the-loop 推荐流程

推荐先使用 `review_submission` Prompt。Agent 应输出：

```json
{
  "decision": "approve",
  "confidence": 0.91,
  "reasons": ["内容符合频道主题", "图片清晰", "无明显广告引流"],
  "suggested_spoiler": true,
  "suggested_tags": [],
  "warnings": []
}
```

并声明：**未执行审核动作**。

只有当用户明确说“184 通过并开启 spoiler”这类具体指令后，Agent 才能调用：

```python
approve_review(review_id=184, spoiler=True)
```

“帮我看看”“筛出不合格的”“给建议”都不是写授权，不得调用 approve/reject/spoiler。

## 9. 内部 Review HTTP API

OpenAPI：[`openapi.yaml`](./openapi.yaml)。

- `GET /api/v1/reviews?limit=20&cursor=...`
- `GET /api/v1/reviews/{id}`
- `GET /api/v1/reviews/{id}/media/{index}?variant=preview`
- `GET /api/v1/reviews/policy`
- `POST /api/v1/reviews/{id}/approve`
- `POST /api/v1/reviews/{id}/reject`
- `PATCH /api/v1/reviews/{id}/spoiler`

错误统一为：

```json
{"ok": false, "error": {"code": "review_busy", "message": "..."}}
```

常见 code：`review_not_found`、`review_already_resolved`、`review_busy`、`permission_denied`、`media_not_found`、`preview_unavailable`、`publish_failed`。Tool 不返回 Python traceback。

## 10. 并发、幂等和失败

`approve` 使用现有条件更新抢占：

- `pending` / `failed` 可 claim 为 `publishing`。
- 新鲜的 `publishing` 拒绝第二个操作者，返回 `review_busy`。
- 超过 `PUBLISHING_STALE_SECONDS`（默认 300 秒）的僵尸 publishing 可重新 claim，用于发布进程崩溃后的重试。
- Telegram、HTTP、MCP 同时操作时只有一个 claim 能进入发布。
- 发布失败回到 `failed`，审核卡和 MCP/HTTP 都可重试。

## 11. 审计日志

写操作记录结构化日志：`source`、`review_id`、`action`、`actor`、`result`、时间戳由日志系统提供。拒绝原因经长度限制和控制字符清理，只进入日志，不新增数据库表。日志不记录 token、Authorization header 或完整媒体 payload。

## 12. 故障排查

- `invalid_token`：检查 MCP 与 TelePost 进程是否使用同一 token；重启后环境变量是否生效。
- `permission_denied`：检查 readonly 两层开关；普通投稿 token 不能审核写操作。
- `review_busy`：另一个管理员/Agent 正在发布；稍后刷新。
- `preview_unavailable`：视频/GIF 没有 Telegram 封面，或文档/音频第一版不提供内容预览。
- `publish_failed`：记录已回到 `failed`；先按提示检查频道，再由人工明确要求重试。
- MCP server 启动提示缺少 SDK：用 Python 3.10+ 的 `.venv-mcp` 安装 `requirements-mcp.txt`。
