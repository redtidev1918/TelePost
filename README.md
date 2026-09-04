# TelePost

Telegram 频道投稿机器人，支持聊天投稿、审核队列、全文搜索、多 Bot 和 HTTP API。

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest)

## 能做什么

- 在 Telegram 内完成上传、预览、编辑、匿名/剧透切换与发布
- 分别控制聊天投稿和 HTTP API 投稿是否进入私有审核群
- 搜索频道历史、标签、个人投稿和本地热榜
- 用一个 supervisor 运行多个相互隔离的 Bot
- 通过 Bearer Token API 接收外部自动化投稿
- 在 Polling、Webhook 与 `AUTO` 模式间切换
- 在 Fly.io 保留 Webhook 后自动休眠，并由下一次请求唤醒

## 最快开始

从 [最新 Release](https://github.com/redtidev1918/TelePost/releases/latest) 下载当前平台的
单文件程序，首次运行会进入配置向导：

```bash
chmod +x telepost-linux-x64
./telepost-linux-x64
```

源码运行：

```bash
git clone https://github.com/redtidev1918/TelePost.git
cd TelePost
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python run.py --setup
./.venv/bin/python run.py
```

最少需要：

| 配置 | 说明 |
|---|---|
| `TOKEN` | 从 [@BotFather](https://t.me/BotFather) 获取 |
| `CHANNEL_ID` | `@channel` 或 `-100…`；Bot 必须有发帖权限 |
| `OWNER_ID` | 推荐设置；启用敏感管理命令和 API Token 生成 |

环境变量优先于 `config.ini`。完整配置见
[配置参考](docs/CONFIGURATION.md)，部署方式见[安装与部署](docs/INSTALL.md)。

## 运行方式

| 场景 | 推荐模式 |
|---|---|
| 本地、无公网 HTTPS | `RUN_MODE=POLLING` |
| 有公网 HTTPS | `RUN_MODE=WEBHOOK` |
| 希望自动选择 | `RUN_MODE=AUTO`（默认） |

Webhook 和 Polling 都提供 `/health` 与 `/api/v1/*`。多 Bot 入口固定为
`/api/botN/v1/*` 和 `/webhook/botN`，详见 [Webhook 与 Polling](docs/WEBHOOK_MODE.md)。

## Fly.io 与 PixivFlow

低成本推荐拓扑：

```text
PixivFlow 256 MiB，常驻调度
        │ Flycast/Fly Proxy HTTP
        ▼
TelePost 512 MiB，auto-stop + auto-start，双 Bot
```

PixivFlow 必须常驻才能按 Cron 执行；TelePost 只处理入站事件，可以自动休眠。不要把
两者塞进一台会自动休眠的 Machine，否则休眠期间没有进程能触发 Cron。完整步骤见
[Fly.io 部署](docs/FLYIO_DEPLOYMENT.md)。

## 常用入口

- 用户：`/submit`、`/search`、`/hot`、`/myposts`、`/mystats`
- Owner：`/botconfig`、`/gen_token`、`/delete_posts`
- 健康检查：`curl http://127.0.0.1:8080/health`
- 测试：`./.venv/bin/python -m pytest -q --no-cov -o log_cli=false`

全部命令见[命令参考](docs/COMMANDS.md)，自动化调用见 [HTTP API](docs/API.md)。

## 文档

| 文档 | 用途 |
|---|---|
| [安装与部署](docs/INSTALL.md) | 单文件、源码、Docker、Fly.io |
| [配置参考](docs/CONFIGURATION.md) | 环境变量、`config.ini`、多 Bot |
| [命令参考](docs/COMMANDS.md) | 用户、管理员和 Owner 命令 |
| [HTTP API](docs/API.md) | Token、投稿、通知与错误 |
| [Fly.io 部署](docs/FLYIO_DEPLOYMENT.md) | 自动休眠与拆分拓扑 |
| [Webhook 与 Polling](docs/WEBHOOK_MODE.md) | 模式选择、路由和安全 |
| [运维手册](docs/OPERATIONS.md) | 备份、升级、监控和发布 |
| [故障排查](docs/TROUBLESHOOTING.md) | 无响应、OOM、投稿和搜索问题 |
| [性能调优](docs/PERFORMANCE.md) | 资源档位与容量边界 |
| [测试指南](docs/TESTING.md) | 本地与 CI 验证 |
| [贡献指南](CONTRIBUTING.md) | 开发与提交约定 |
| [版本历史](CHANGELOG.md) | 已发布变更 |

内部设计见 [投稿状态机](docs/internals/submission-flow.md) 与
[软删除](docs/internals/moderation.md)。

## 许可

[MIT License](LICENSE)。问题请提交到
[GitHub Issues](https://github.com/redtidev1918/TelePost/issues)。
