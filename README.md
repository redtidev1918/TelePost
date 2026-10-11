# TelePost

**语言 / Language:** 中文 · [English](README.en.md)

让用户向你的 Telegram 频道投稿，在预览或审核群中检查内容，再发布到频道。

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest)
[![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Documentation](https://img.shields.io/badge/docs-redtidev1918.github.io-6366f1)](https://redtidev1918.github.io/TelePost/)

TelePost 自托管在你自己的服务器上。投稿来自 Telegram Bot、Mini App 或 HTTP API；
三个入口共用投稿与发布服务，是否需要审核由各自的来源策略决定。

```text
Telegram Bot ─────┐
Telegram Mini App ├──→ TelePost ──→ Telegram Channel
HTTP API ─────────┘
```

## 能做什么

| 场景 | 能力 |
| --- | --- |
| 接收投稿 | Bot、Mini App、HTTP API；支持图片、视频、音频和文件 |
| 审核与发布 | 审核队列、修改后发布、剧透控制、审核历史 |
| 阅读与检索 | 频道搜索、标签；TXT 小说可生成 Telegraph 在线阅读页 |
| 用户自助 | Mini App 浏览内容、投稿、查看自己的投稿 |
| 自动化集成 | Bearer Token、文件上传、幂等提交，避免重复投稿 |
| 自托管运行 | 多 Bot 独立配置与数据目录，SQLite 持久化，重启后恢复 |

投稿默认拦截可执行文件、常见脚本和压缩包；图片、视频及 TXT/PDF/MD 等可正常投稿。管理员可配置[文件类型黑名单](docs/CONFIGURATION.md)。这是类型限制，不进行病毒扫描。

## 快速开始

先准备 Bot Token、目标频道、你的数字 User ID，以及一个独立审核群。
把 Bot 加为频道管理员并授予发帖权限，再加入审核群。

不想安装 Python？[下载独立程序](https://redtidev1918.github.io/TelePost/#/download)。
Windows 在 PowerShell 中运行 `.\telepost-windows-x64.exe --setup`，补齐下方审核配置后，
运行 `.\telepost-windows-x64.exe`。升级时保留程序旁的 `config.ini` 和 `data` 文件夹。

使用 Python / pip：

1. 用 [@BotFather](https://t.me/BotFather) 创建 Bot，加入目标频道并授予发帖权限。
2. 安装 Python 3.10+。图片处理需要系统 libvips，安装方法见
   [系统依赖](docs/INSTALL.md#libvips-系统依赖)。
3. 在虚拟环境中安装并生成配置：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install telepost-bot
telepost --setup
```

Windows PowerShell 使用以下命令：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install telepost-bot
.\.venv\Scripts\telepost.exe --setup
```

配置向导填写 Bot Token、Channel ID、Owner ID。在生成的 `config.ini` 的 `[BOT]` 节中
补充 `REVIEW_CHAT_ID`（独立审核群 ID），再运行 `telepost`。API 与 Mini App 的审核开关
默认开启，三项向导配置尚不包含审核群。仅使用私聊直发时的配置见 [安装指南](docs/INSTALL.md#1-pip-安装推荐)。
Windows 保存配置时使用 UTF-8，启动命令为 `.\.venv\Scripts\telepost.exe`。

在已有的 `[BOT]` 节中修改或追加，群 ID 换成你自己的数字 ID：

```ini
LANGUAGE = zh
REVIEW_CHAT_ID = <你的审核群ID>
```

第一篇投稿：

1. 私聊 Bot，发送 `/start`，点击「开始投稿」或发送 `/submit`。
2. 上传图片、视频、音频或文件，完成后发送 `/done_media`。
3. 在预览中填写标签，按需修改标题、简介、链接、匿名和剧透设置。
4. 检查后点击发布按钮；开启聊天审核时，按钮显示「提交审核」。

随时发送 `/cancel` 取消，完整命令见 [投稿流程](docs/COMMANDS.md)。

Bot 私聊投稿默认直接发布，可配置为先审核；HTTP API 自动化投稿固定需要审核；
Mini App 默认需要审核，由独立的 `MINIAPP_REVIEW_REQUIRED` 控制。默认值与开关见
[配置参考](docs/CONFIGURATION.md)。

## 选择安装方式

| 方式 | 入口 | 适合 |
| --- | --- | --- |
| Python / pip | `python -m pip install telepost-bot` | 自管服务器与本地运行 |
| 独立程序 | [选择平台并下载](https://redtidev1918.github.io/TelePost/#/download) | 无需安装 Python |
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

示例使用单 Bot 地址；多 Bot 改用 `/api/botN/v1/submissions`。
响应表示已受理审核，人工批准后才发布。同一次请求重试时保留原 `idempotency_key`。

完整接口与字段见 [HTTP API](docs/API.md)。

## Mini App

Mini App 是 TelePost 的可选 Web 前端。

普通用户可以投稿、浏览公开内容和查看自己的投稿；reviewer / admin 可以使用审核和管理功能。

Mini App 与 Bot 共用同一套后端、权限和投稿状态。配置见 [Mini App](docs/MINIAPP.md)。

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
