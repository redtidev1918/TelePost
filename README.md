# TelePost

**语言 / Language:** 中文 · [English](README.en.md)

Telegram 频道投稿、审核与发布平台。

[文档](https://redtidev1918.github.io/TelePost/) ·
[Releases](https://github.com/redtidev1918/TelePost/releases)

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

TelePost 为 Telegram 频道提供投稿、审核、搜索和发布能力。用户可以通过 Bot 或 Mini App 投稿，外部程序可以通过 HTTP API 投递内容。

## 功能

- Telegram Bot 投稿、预览、编辑和发布
- Mini App 投稿、内容浏览和审核
- 审核队列、编辑后发布、剧透等审核功能
- 频道历史搜索、标签和投稿记录
- HTTP API、Bearer Token 和幂等提交
- 多 Bot 隔离运行
- Polling、Webhook 和自动运行模式
- SQLite 持久化，支持长期运行和重启恢复

## 工作方式

```text
Telegram Bot ─────┐
Telegram Mini App ├──→ TelePost ──→ Telegram Channel
HTTP API ─────────┘
```

不同入口共用同一套投稿、审核和发布流程。

审核策略可以分别配置。Telegram Chat 投稿、Mini App 投稿和 HTTP API 投稿不要求使用相同的审核设置。

## 快速开始

### 运行 Release

从 [Releases](https://github.com/redtidev1918/TelePost/releases) 下载对应平台版本。

首次运行后配置：

- Bot Token
- 目标频道 ID
- Owner ID

然后向 Bot 发送：

```text
/start
```

使用：

```text
/submit
```

开始投稿。

Linux 示例：

```bash
chmod +x telepost-linux-x64
./telepost-linux-x64
```

### Docker

```bash
docker compose up -d
```

详细配置见[安装与部署](docs/INSTALL.md)。

## HTTP API

TelePost 提供 HTTP API 用于脚本和自动化服务。

先在 Bot 中使用 `/gen_token` 创建 API Token，然后提交内容：

```bash
curl -X POST 'https://example.com/api/v1/submissions' \
  -H 'Authorization: Bearer tp_xxxx' \
  -F 'files=@image.jpg' \
  -F 'tags=illustration' \
  -F 'title=Example' \
  -F 'idempotency_key=example:123'
```

完整接口和字段说明见 [HTTP API](docs/API.md)。

TelePost 不依赖特定的上游程序。RSS、爬虫、CI、自有脚本，以及 PixivFlow 等工具都可以通过 HTTP API 接入。

## Mini App

Mini App 是 TelePost 的可选前端。

普通用户可以使用 Mini App 投稿和查看自己的投稿；审核员和管理员可以使用对应的审核和管理功能。

Mini App 与 Bot 共用 TelePost 后端和业务流程，不维护独立的数据或审核系统。

配置和部署方式见 [Mini App](docs/MINIAPP.md)。

## 多 Bot

TelePost 支持在一个进程中运行多个相互隔离的 Bot。

每个 Bot 使用独立的配置、数据目录和 Telegram 入口。

## 部署

TelePost 可以运行在：

- 本地服务器
- VPS
- Docker
- Fly.io
- 其他支持 Python 或容器的环境

没有公网 HTTPS 时可以使用 Polling。

需要接收 Telegram Webhook 时使用 Webhook 模式。

详见：

- [安装与部署](docs/INSTALL.md)
- [配置参考](docs/CONFIGURATION.md)
- [Webhook](docs/WEBHOOK_MODE.md)
- [Fly.io 部署](docs/FLYIO_DEPLOYMENT.md)

## 相关项目

[PixivFlow](https://github.com/redtidev1918/PixivFlow) 是独立的 Pixiv 内容下载与处理工具，可以通过 TelePost HTTP API 投递内容。

[pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) 提供 PixivFlow 与 TelePost 的组合部署和工作流配置。

这些项目不是 TelePost 的运行依赖。

## 文档

| 内容            | 文档                          |
| ---------------- | ----------------------------- |
| 安装和升级       | [安装与部署](docs/INSTALL.md)     |
| 配置             | [配置参考](docs/CONFIGURATION.md) |
| Telegram 命令    | [命令参考](docs/COMMANDS.md)      |
| HTTP API       | [API 文档](docs/API.md)          |
| Mini App       | [Mini App](docs/MINIAPP.md)      |
| Webhook / Polling | [运行模式](docs/WEBHOOK_MODE.md)  |
| 运维              | [运维手册](docs/OPERATIONS.md)    |
| 故障排查           | [故障排查](docs/TROUBLESHOOTING.md) |
| 开发贡献           | [贡献指南](CONTRIBUTING.md)        |

## 许可证

[MIT License](LICENSE)