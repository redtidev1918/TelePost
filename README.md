# TelePost

**语言 / Language:** 中文 · [English](README.en.md)

> **Telegram 频道投稿、审核与发布服务。**

📚 [完整文档](https://redtidev1918.github.io/TelePost/)

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest)
[![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Documentation](https://img.shields.io/badge/docs-redtidev1918.github.io-6366f1)](https://redtidev1918.github.io/TelePost/)

TelePost 是一个自托管服务，用于 Telegram 频道的投稿、审核和发布。用户可以通过 Telegram Bot
或 Mini App 投稿，其他程序可以通过 HTTP API 提交内容；所有入口最终进入同一套投稿、审核和发布流程。

```text
Telegram Bot ─────┐
Telegram Mini App ├──→ TelePost ──→ Telegram Channel
HTTP API ─────────┘
```

## 功能

- Bot 投稿：图片、视频、音频和文件
- 审核队列：审核、修改后发布、剧透控制、审核历史
- Mini App：投稿、内容浏览、我的投稿、审核和管理
- HTTP API：文件上传、Bearer Token、幂等提交
- 频道搜索：历史内容、标签和投稿记录
- 小说 / 长文：TXT 可生成 Telegraph 在线阅读页
- 多 Bot：一个进程运行多个相互隔离的 Bot
- SQLite：持久化存储，重启后恢复

## 快速开始

```bash
pip install telepost-bot
telepost --setup
telepost
```

需要 Python 3.10+；图片处理依赖系统级 libvips。

开始前：用 [@BotFather](https://t.me/BotFather) 创建一个 Bot，把它加入目标频道并授予发帖权限。

首次运行 `telepost` 会进入配置向导，填入 **Bot Token**、**Channel ID**、**Owner ID**；
之后直接运行 `telepost` 即以前次配置启动。启动后向 Bot 发送 `/start`，再用 `/submit`
发布第一条投稿。

| 名称 | 值 |
| --- | --- |
| PyPI 项目名 | `telepost-bot` |
| 命令行 | `telepost` |
| Python 导入 | `telepost` |
| Python | 3.10+ |

## 安装

| 方式 | 用法 | 适合 |
| --- | --- | --- |
| PyPI | `pip install telepost-bot` | 普通安装、服务器、脚本 |
| Release | [下载](https://github.com/redtidev1918/TelePost/releases) | 不想安装 Python |
| Docker | `ghcr.io/redtidev1918/telepost:<version>` | 容器部署 |
| 源码 | `git clone` | 开发、自建环境 |

生产环境固定 Release 版本，不要使用 `latest`。完整安装、升级和卸载见
[安装与部署](docs/INSTALL.md)。

## HTTP API

先在 Bot 中用 `/gen_token` 生成 Token，再提交内容：

```bash
curl -X POST 'https://example.com/api/v1/submissions' \
  -H 'Authorization: Bearer tp_xxxx' \
  -F 'files=@image.jpg' \
  -F 'title=Example' \
  -F 'tags=illustration' \
  -F 'idempotency_key=example:123'
```

适合 RSS、爬虫、CI、定时任务、自有脚本以及 PixivFlow 等自动化工具。

完整接口与字段见 [HTTP API](docs/API.md)。

## Mini App

Mini App 是 TelePost 的可选 Web 前端。

普通用户可以投稿、浏览公开内容和查看自己的投稿；reviewer / admin 可以使用审核和管理功能。

Mini App 与 Bot 共用同一套后端、权限和投稿状态。配置见 [Mini App](docs/MINIAPP.md)。

## 多 Bot 与部署

TelePost 支持在一个进程中运行多个相互隔离的 Bot，各自拥有独立的配置、数据目录与 Telegram 入口。

可以部署在本地服务器、VPS、Docker / Compose 或 Fly.io。没有公网 HTTPS 时使用 Polling；
需要接收 Telegram Webhook 时使用 Webhook。

[安装与部署](docs/INSTALL.md) · [配置](docs/CONFIGURATION.md) ·
[运行模式](docs/WEBHOOK_MODE.md) · [Fly.io](docs/FLYIO_DEPLOYMENT.md)

正式发版和生产部署见 [运维手册](docs/OPERATIONS.md)。

## 文档

完整文档站：**https://redtidev1918.github.io/TelePost/**

| 内容 | 文档 |
| --- | --- |
| 安装、升级、卸载 | [安装与部署](docs/INSTALL.md) |
| 配置 | [配置参考](docs/CONFIGURATION.md) |
| Telegram 命令 | [命令参考](docs/COMMANDS.md) |
| HTTP API | [API](docs/API.md) |
| Mini App | [Mini App](docs/MINIAPP.md) |
| Webhook / Polling | [运行模式](docs/WEBHOOK_MODE.md) |
| Fly.io | [Fly.io 部署](docs/FLYIO_DEPLOYMENT.md) |
| 运维 | [运维手册](docs/OPERATIONS.md) |
| 故障排查 | [故障排查](docs/TROUBLESHOOTING.md) |
| 开发 | [测试指南](docs/TESTING.md) |

## 相关项目

- [PixivFlow](https://github.com/redtidev1918/PixivFlow) — Pixiv 下载、筛选和自动收集工具
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) — PixivFlow + TelePost 组合部署

均为可选集成，不是 TelePost 的运行依赖。

## 许可证

[MIT License](LICENSE)
