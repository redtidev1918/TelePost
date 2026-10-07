# TelePost

**语言 / Language:** 中文 · [English](README.en.md)

> **Telegram 频道投稿、审核与发布服务。**

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

## 从哪里开始

[安装与部署](docs/INSTALL.md) · [配置](docs/CONFIGURATION.md) ·
[命令](docs/COMMANDS.md) · [故障排查](docs/TROUBLESHOOTING.md) ·
[完整文档](https://redtidev1918.github.io/TelePost/)

## 能做什么

| 场景 | 能力 |
| --- | --- |
| 接收投稿 | Bot、Mini App、HTTP API；支持图片、视频、音频和文件 |
| 审核与发布 | 审核队列、修改后发布、剧透控制、审核历史 |
| 阅读与检索 | 频道搜索、标签；TXT 小说可生成 Telegraph 在线阅读页 |
| 用户自助 | Mini App 浏览内容、投稿、查看自己的投稿 |
| 自动化集成 | Bearer Token、文件上传、幂等提交，避免重复投稿 |
| 自托管运行 | 多 Bot 独立配置与数据目录，SQLite 持久化，重启后恢复 |

## 快速开始

1. 用 [@BotFather](https://t.me/BotFather) 创建 Bot，加入目标频道并授予发帖权限。
2. 安装 Python 3.10+。图片处理需要系统 libvips，安装方法见
   [系统依赖](docs/INSTALL.md#libvips-系统依赖)。
3. 在虚拟环境中安装并启动：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install telepost-bot
telepost --setup
telepost
```

Windows PowerShell 使用 `.venv\Scripts\Activate.ps1` 激活环境。
配置向导需要 **Bot Token、Channel ID、Owner ID**；再次运行 `telepost` 会使用保存的配置。

启动后向 Bot 发送 `/start` → `/submit` → 上传媒体 → `/done_media`，
在预览中检查内容，再按提示提交。命令详情见 [投稿流程](docs/COMMANDS.md)。

**审核规则：** Bot 私聊投稿默认直接发布，可配置为先审核；HTTP API 投稿固定需要审核；
Mini App 使用独立审核开关。需要审核时，先配置审核群与审核人，参见
[配置参考](docs/CONFIGURATION.md)。

## 选择安装方式

| 方式 | 入口 | 适合 |
| --- | --- | --- |
| Python / pip | `python -m pip install telepost-bot` | 自管服务器与本地运行 |
| 独立程序 | [选择平台并下载](https://redtidev1918.github.io/TelePost/download.html) | 无需安装 Python |
| Docker / Compose | `ghcr.io/redtidev1918/telepost:<version>` | 容器部署 |
| 源码 | [开发与测试](docs/TESTING.md) | 修改代码或参与开发 |

PyPI 包名为 `telepost-bot`，启动命令和 Python 导入名均为 `telepost`。
生产环境固定正式 Release 版本；完整安装、升级、卸载步骤以
[安装与部署](docs/INSTALL.md) 为准。

## HTTP API

Owner 在 Bot 中用 `/gen_token <名称>` 生成 API Token，再提交内容：

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

完整文档站：[TelePost 文档](https://redtidev1918.github.io/TelePost/)。

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
