# TelePost

**语言 / Language:** 中文 · [English](README.en.md)

让用户向你的 Telegram 频道投稿，在预览或审核群中检查内容，再发布到频道。

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest)
[![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Documentation](https://img.shields.io/badge/docs-redtidev1918.github.io-6366f1)](https://redtidev1918.github.io/TelePost/)

TelePost 运行在你自己的电脑或服务器上。用户可以私聊 Bot 或打开 Mini App 投稿，
自动化程序可以通过 HTTP API 提交。你可以直接发布聊天投稿，也可以交给审核员检查后发布。

```text
Telegram Bot ─────┐
Telegram Mini App ├──→ TelePost ──→ Telegram Channel
HTTP API ─────────┘
```

## 能做什么

| 场景 | 能力 |
| --- | --- |
| 接收投稿 | 私聊 Bot、Mini App 或 HTTP API 投稿，支持图片、视频、音频、文件和纯文字 |
| 投稿前预览 | 检查附件与文案，修改标题、简介、标签和链接，选择匿名或剧透 |
| 人工审核 | 在审核群或 Mini App 批准、拒绝投稿；Mini App 可修改文案、排序或移除附件后发布，保留审核与编辑历史 |
| 多图发布 | 按相册分批发送，可配置将超出首组的图片发到关联讨论群 |
| 小说阅读 | 发布 TXT 附件与封面；启用小说预览后可生成 Telegraph 阅读页和「在线阅读」按钮 |
| 搜索与标签 | 按关键词、标签和时间范围搜索，浏览标签云，翻页查看结果 |
| 热榜与统计 | 查看全站热榜、本周热榜和个人投稿统计；管理员可设置每周热榜定时发送 |
| 个人投稿记录 | 在 Bot 或 Mini App 查看自己的投稿；Mini App 展示处理状态与已发布内容 |
| 管理与权限 | 封禁用户、禁用 API Token；Mini App 管理角色、黑名单和运行策略 |
| 文件类型限制 | 默认拦截可执行文件、常见脚本和压缩包；[规则可配置](docs/CONFIGURATION.md)，按文件名和 MIME 过滤，不扫描病毒 |
| 自动化接入 | API Token 鉴权、文件上传、幂等提交；重试同一请求不会重复创建审核或发布 |
| PixivFlow 集成（可选） | 接收自动收集的作品；审核时可请求更换候选，新作品仍需审核 |
| 中英文界面 | Bot 与 Mini App 支持中文、英文；同机多个 Bot 可分别选择语言 |
| 自托管运行 | 独立程序、pip 或 Docker 部署，支持 Polling / Webhook；多 Bot 独立配置与数据目录，持久保存审核和幂等记录 |

## 选择安装方式

| 你的环境 | 安装方式 | 从这里开始 |
| --- | --- | --- |
| 本地电脑，不想安装 Python | 独立程序 | [选择平台并下载](https://redtidev1918.github.io/TelePost/#/download) |
| 已有 Python 环境 | pip | [快速开始](#快速开始) |
| 用 Docker 管理服务 | Docker / Compose | [容器部署](docs/INSTALL.md#3-docker--compose) |
| 需要修改代码 | 源码 | [开发与测试](docs/TESTING.md) |

生产环境请固定[正式发布版本](https://github.com/redtidev1918/TelePost/releases/latest)。
完整安装、升级和卸载步骤见[安装与部署](docs/INSTALL.md)。

## 快速开始

### 1. 准备 Bot、频道和审核群

| 信息 | 用途 |
| --- | --- |
| Bot Token | 在 [@BotFather](https://t.me/BotFather) 创建 Bot 后取得 |
| Channel ID | 接收发布内容的频道，可用 `@频道用户名` 或数字 ID |
| Owner ID | 你的个人数字 User ID，用于识别所有者；不是群或频道 ID |
| Review Chat ID | 独立审核群的数字 ID；不能与目标频道相同 |

把 Bot 加为频道管理员并授予发帖权限，再加入审核群，确保它能发送消息。
默认配置需要审核群。只使用私聊直发时，可按[安装指南](docs/INSTALL.md#1-pip-安装推荐)配置。

### 2. 安装并运行配置向导

**Windows 独立程序**：下载并解压到固定文件夹，在该文件夹打开 PowerShell：

```powershell
.\telepost-windows-x64.exe --setup
```

其他平台的独立程序命令见[安装指南](docs/INSTALL.md#2-release-单文件)。

**Python / pip**：需要 Python 3.10+ 和系统 libvips，先按[系统依赖](docs/INSTALL.md#libvips-系统依赖)安装 libvips。
Linux / macOS 在虚拟环境中安装：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install telepost-bot
telepost --setup
```

Windows PowerShell 使用：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install telepost-bot
.\.venv\Scripts\telepost.exe --setup
```

PyPI 包名是 `telepost-bot`，安装后的启动命令是 `telepost`。
向导会依次询问 Bot Token、Channel ID 和 Owner ID。

### 3. 补齐审核配置并启动

打开向导生成的 `config.ini`，保留已有配置，在 `[BOT]` 节中修改或追加以下两项。
把占位符换成你自己的审核群数字 ID，文件保存为 UTF-8：

```ini
LANGUAGE = zh
REVIEW_CHAT_ID = <你的审核群ID>
```

| 安装方式 | 启动命令 |
| --- | --- |
| Windows 独立程序 | `.\telepost-windows-x64.exe` |
| Windows pip | `.\.venv\Scripts\telepost.exe` |
| Linux / macOS pip | 激活虚拟环境后运行 `telepost` |

pip 安装后请始终从同一目录启动，配置和数据保存在该目录。
独立程序升级时保留程序旁的 `config.ini` 和 `data` 文件夹。
启动后，在 Telegram 私聊 Bot 发送 `/start`，确认它能回复。

### 4. 完成第一篇投稿

1. 私聊 Bot，发送 `/start`，点击「开始投稿」或发送 `/submit`。
2. 上传图片、视频、音频或文件，完成后发送 `/done_media`。
3. 在预览中填写标签，按需修改标题、简介、链接、匿名和剧透设置。
4. 检查后点击发布按钮；开启聊天审核时，按钮显示「提交审核」。

随时发送 `/cancel` 取消。完整命令见[投稿流程](docs/COMMANDS.md)。

## 默认审核规则

| 投稿入口 | 默认行为 | 如何调整 |
| --- | --- | --- |
| Bot 私聊 | 用户确认后直接发布 | 设置 `CHAT_REVIEW_REQUIRED=true` 后先审核 |
| Mini App | 提交审核，批准后发布 | 由 `MINIAPP_REVIEW_REQUIRED` 单独控制 |
| HTTP API | 提交审核，批准后发布 | 固定需要审核，不能关闭 |

需要聊天投稿也先审核时，在 `[BOT]` 中加入 `CHAT_REVIEW_REQUIRED = true`，然后重启。
其他配置见[配置参考](docs/CONFIGURATION.md)。

## 中文 Bot、英文 Bot

同一份程序支持两种语言，默认中文。单 Bot 在 `config.ini` 的 `[BOT]` 节中设置
`LANGUAGE = en`，或部署时设置环境变量 `BOT_LANGUAGE=en`。改完重启生效。

同时运行中文和英文 Bot 时，为两个 Bot 配置独立凭据、频道和审核群，再设置：

```dotenv
BOT1_LANGUAGE=zh
BOT2_LANGUAGE=en
```

把这两项加到部署平台的环境变量或 Compose 的 `.env` 中。欢迎、帮助、菜单、投稿和审核提示
使用所选语言，Mini App 默认跟随所属 Bot；用户的标题、标签和正文保留原文。
完整配置见 [Bot 语言与多 Bot](docs/CONFIGURATION.md#bot-语言与多-bot)。

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

示例使用单 Bot 地址；多 Bot 改用 `/api/botN/v1/submissions`。
响应表示已受理审核，人工批准后才发布。同一次请求重试时保留原 `idempotency_key`。

完整接口与字段见 [HTTP API](docs/API.md)。

## Mini App

Mini App 是 TelePost 的可选 Web 前端。

普通用户可以投稿、浏览公开内容和查看自己的投稿；reviewer / admin 可以使用审核和管理功能。

Mini App 与 Bot 共用同一套后端、权限和投稿状态。配置见 [Mini App](docs/MINIAPP.md)。

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
| 参与贡献 | [贡献指南](CONTRIBUTING.md) |

## 相关项目

- [PixivFlow](https://github.com/redtidev1918/PixivFlow) — Pixiv 下载、筛选和自动收集工具
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) — PixivFlow + TelePost 组合部署

均为可选集成，不是 TelePost 的运行依赖。

## 许可证

[MIT License](LICENSE)
