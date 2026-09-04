# Webhook 与 Polling

## 选择模式

| 模式 | 行为 | 适合 |
|---|---|---|
| `AUTO` | 有有效公网 HTTPS URL 时 Webhook，否则 Polling | 通用默认 |
| `POLLING` | Bot 主动向 Telegram 拉取更新 | 本地、无公网 HTTPS |
| `WEBHOOK` | Telegram POST 到 TelePost | Fly.io、PaaS、反向代理后的 VPS |

`AUTO` 选中 Webhook 后若监听或注册失败，会停止 Webhook 服务并回退 Polling；强制
`WEBHOOK` 失败则退出，让部署错误显性暴露。

## 配置

环境变量：

```env
RUN_MODE=WEBHOOK
WEBHOOK_URL=https://bot.example.com
WEBHOOK_PORT=8080
WEBHOOK_PATH=/webhook
WEBHOOK_SECRET_TOKEN=replace-with-random-secret
```

`config.ini`：

```ini
[BOT]
RUN_MODE = WEBHOOK

[WEBHOOK]
URL = https://bot.example.com
PORT = 8080
PATH = /webhook
SECRET_TOKEN = replace-with-random-secret
```

`WEBHOOK_URL` 是根地址；程序会追加路径。Telegram 要求公网 HTTPS 和有效证书。

## HTTP 路由

单 Bot 子服务：

- `POST /webhook`
- `GET /health`
- `/api/v1/*`

`run.py` 的多 Bot 父路由：

- `POST /webhook/botN`
- `GET /health`
- `/api/botN/v1/*`

多 Bot 子进程端口从 8081 开始，只有父路由对外。不要把 8081/8082 暴露到公网。

## Webhook 生命周期

- 启动时 TelePost 使用 Secret Token 调用 `setWebhook`，并保留 Telegram 已排队更新。
- 正常关机或 Fly auto-stop 时只关闭本地服务器，**不会删除 Webhook**。
- 下一次 Telegram POST 仍能到达 Fly Proxy 并唤醒 Machine。
- 改用 Polling 时，PTB 会处理 Webhook/Polling 切换；不要并行运行第二个相同 Token 实例。

这是 2.10.39 的关键行为。旧版本在关机时删除 Webhook，会让已停止的 Fly Machine
失去唯一唤醒来源。

## 反向代理

TelePost 监听 HTTP，由 Caddy/Nginx 终止 TLS。Nginx 最小示例：

```nginx
server {
    listen 443 ssl;
    server_name bot.example.com;

    ssl_certificate /path/fullchain.pem;
    ssl_certificate_key /path/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

父路由还承载最大 500 MiB 的 API 请求；反向代理的 body size 和 timeout 必须不低于
TelePost 的 API 限制。只使用 Telegram 聊天投稿时可保持较小上限。

## Secret Token

建议在生产环境显式设置稳定的 `WEBHOOK_SECRET_TOKEN`。未设置时程序每次启动生成一个
随机值并随 `setWebhook` 更新 Telegram；可运行，但不便于审计。不要在日志中输出它。

请求必须携带 Telegram 的 `X-Telegram-Bot-Api-Secret-Token`。不要用 URL 中的秘密值
代替 Header 校验。

## 验证

```bash
curl -fsS https://bot.example.com/health
curl -fsS https://bot.example.com/api/bot1/v1/health
curl -fsS 'https://api.telegram.org/bot<TOKEN>/getWebhookInfo'
```

检查：

- URL 精确匹配 `/webhook` 或 `/webhook/botN`
- `pending_update_count` 是否持续增长
- `last_error_date` 与 `last_error_message` 是否是当前错误
- 日志是否出现“Webhook 设置成功”和请求处理记录

部署或冷启动瞬间可能留下历史 502；若待处理数回到 0、后续消息成功，不代表仍在故障。

## 常见问题

| 症状 | 检查 |
|---|---|
| 404 | 路径是否遗漏 `/botN`，反代是否保留完整路径 |
| 401/403 | Secret Token 是否与 Telegram 当前配置一致 |
| 502 | Machine 是否仍在冷启动、内部端口是否就绪、健康检查宽限是否足够 |
| 完全无请求 | Webhook URL 是否为空/被删除，DNS/TLS 是否正常 |
| Polling conflict | 是否有另一个进程使用同一 Token |
| 待处理数增长 | 查看应用日志、Telegram 最近错误和处理超时 |

Fly.io 专项配置见 [FLYIO_DEPLOYMENT.md](FLYIO_DEPLOYMENT.md)，通用故障见
[TROUBLESHOOTING.md](TROUBLESHOOTING.md)。
