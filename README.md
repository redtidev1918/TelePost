# TelePost

**语言 / Language:** 中文 · [English](README.en.md)

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/)

> Telegram 频道投稿、审核与发布平台。

**安装：** `pip install telepost-bot` · **启动：** `telepost` · **Python 导入：** `telepost`

用户可以从 **Bot** 或 **Mini App** 投稿，外部程序可以通过 **HTTP API** 投递内容；不同入口共用同一套流程。

## ✨ 功能

- **Bot 投稿** — 预览、编辑、发布，支持图片、视频、音频与文件
- **Mini App** — 投稿、浏览内容、审核与后台管理；审核队列空态附「最近处理」与审核历史入口
- **审核队列** — 审核、编辑后发布、剧透等
- **小说在线阅读** — TXT 小说可选生成 Telegraph 在线阅读页；频道主贴保留完整标题/简介/tags，在线阅读入口独立且不重复；搭配 TelePress 0.16.4 后，长文单页目标约 20,000 字符，阅读中断更少
- **媒体代理重写** — 受限图床链接可经自建公网反代重写后投递
- **频道搜索** — 历史搜索、标签、投稿记录
- **HTTP API** — Bearer Token、幂等提交，方便脚本与自动化
- **多 Bot 隔离** — 一个进程运行多个互不干扰的 Bot
- **灵活运行** — Polling / Webhook / 自动模式
- **SQLite 持久化** — 长期稳定运行，重启可恢复

## 🔁 工作方式

```text
Telegram Bot ─────┐
Telegram Mini App ├──→ TelePost ──→ Telegram Channel
HTTP API ─────────┘
```

投稿、审核和发布共用同一套流程；审核策略可按入口（Chat / Mini App / HTTP API）分别配置。

## 🚀 30 秒开始

```bash
pip install telepost-bot
telepost --setup
telepost
```

首次运行 `telepost` 会进入配置向导，按提示填入 **Bot Token**、**频道 ID**、**Owner ID**；
之后直接运行 `telepost` 即以前次配置启动。

开始前只需要两件事：用 [@BotFather](https://t.me/BotFather) 创建（或复用）一个 Bot，
把它加入目标频道并授予发帖权限。启动后向 Bot 发送 `/start`，再用 `/submit` 发布第一条投稿。

| 名称 | 值 |
| --- | --- |
| PyPI 项目名 | `telepost-bot` |
| 命令行 | `telepost` |
| Python 导入 | `telepost` |
| Python | 3.10+ |
| 系统依赖 | `pyvips` 需要操作系统 libvips（macOS `brew install vips`，Debian/Ubuntu `sudo apt-get install libvips42`） |

## 📦 安装方式

| 顺序 | 方式 | 适合 | 入口 |
| --- | --- | --- | --- |
| 1 | **PyPI（推荐）** | 有 Python 环境的用户、脚本化与自动化部署 | `pip install telepost-bot` |
| 2 | Release 单文件 | 不想安装 Python 的用户 | [Releases](https://github.com/redtidev1918/TelePost/releases) 下载对应平台可执行文件 |
| 3 | Docker / Compose | 容器化部署 | `ghcr.io/redtidev1918/telepost:<version>` |
| 4 | 源码 | 开发、自建 VPS | `git clone` 后 `python run.py` |

Release 单文件不需要 Python 环境：下载后 `chmod +x telepost-linux-x64 && ./telepost-linux-x64`。

升级统一为 `pip install --upgrade telepost-bot`；容器与 Fly 则切换到新的 release 版本镜像
`ghcr.io/redtidev1918/telepost:<version>`（生产环境不要使用 `latest`）。版本号一律取自正式
Release。完整安装、配置、升级与卸载见 [安装与部署](docs/INSTALL.md)。

## 🔖 发版

日常开发是 **PR → `main`**；正式发版有人工闸门——**merge Release PR**。之后的每一步都由流水线
串行完成，任一关键阶段失败都会让这次发版明确失败：

```text
merge Release PR → vX.Y.Z → GitHub Release → GHCR 镜像 → PyPI telepost-bot
                                                          ↓
                              Fly 生产部署 ← 校验 /health 版本与 bot1/bot2 就绪
```

不会出现「发版显示成功、生产仍是旧版本」。详见
[docs/FLYIO_DEPLOYMENT.md § 0](docs/FLYIO_DEPLOYMENT.md#0-正式发版路径唯一推荐) 与
[运维手册 · 正式发布流程](docs/OPERATIONS.md#正式发布流程)。

## 🌐 HTTP API

方便脚本与自动化：先在 Bot 中用 `/gen_token` 生成 Token，再提交内容：

```bash
curl -X POST 'https://example.com/api/v1/submissions' \
  -H 'Authorization: Bearer tp_xxxx' \
  -F 'files=@image.jpg' \
  -F 'tags=illustration' \
  -F 'title=Example' \
  -F 'idempotency_key=example:123'
```

完整接口与字段见 [HTTP API](docs/API.md)。

TelePost 不依赖特定上游程序。RSS、爬虫、CI、自有脚本，以及 PixivFlow 等工具都能通过 HTTP API 接入。

## 📱 Mini App

Mini App 是可选前端：普通用户投稿、查看自己的投稿；审核员和管理员使用对应的审核与管理功能。

它与 Bot 共用 TelePost 后端和业务流程，不维护独立的数据或审核系统。配置见 [Mini App](docs/MINIAPP.md)。

## 🏢 多 Bot 与部署

- **多 Bot**：一个进程运行多个相互隔离的 Bot，各自拥有独立的配置、数据目录与 Telegram 入口。
- **部署环境**：本地服务器、VPS、Docker、Fly.io 或任何支持 Python / 容器的环境。
- **运行模式**：没有公网 HTTPS 时用 **Polling**；需要接收 Webhook 时用 **Webhook** 模式。

详见 [安装与部署](docs/INSTALL.md) · [配置参考](docs/CONFIGURATION.md) · [Webhook](docs/WEBHOOK_MODE.md) · [Fly.io 部署](docs/FLYIO_DEPLOYMENT.md)。

## 📚 文档

| 内容            | 文档                               |
| ---------------- | ---------------------------------- |
| 安装和升级       | [安装与部署](docs/INSTALL.md)          |
| 配置             | [配置参考](docs/CONFIGURATION.md)      |
| Telegram 命令    | [命令参考](docs/COMMANDS.md)           |
| HTTP API         | [API 文档](docs/API.md)               |
| Mini App         | [Mini App](docs/MINIAPP.md)           |
| Webhook / Polling | [运行模式](docs/WEBHOOK_MODE.md)      |
| 运维             | [运维手册](docs/OPERATIONS.md)         |
| 故障排查          | [故障排查](docs/TROUBLESHOOTING.md)     |
| 开发贡献          | [贡献指南](CONTRIBUTING.md)            |

## 🔗 相关项目

- [PixivFlow](https://github.com/redtidev1918/PixivFlow) —— 独立的 Pixiv 内容下载与处理工具，可通过 TelePost HTTP API 投递内容。
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) —— PixivFlow 与 TelePost 的组合部署与工作流配置。

以上均为可选集成，不是 TelePost 的运行依赖。

## 📄 许可证

[MIT License](LICENSE)