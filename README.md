# TelePost

**语言 / Language:** 中文 · [English](https://github.com/redtidev1918/TelePost/blob/main/README.en.md)

TelePost 是一个开源、自托管的 Telegram 频道投稿与审核机器人。读者通过 Bot 私聊或
Mini App 投稿，你在审核群里检查、修改并决定是否发布；自动化程序可以通过 HTTP API 提交内容。

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest)
[![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/redtidev1918/TelePost/blob/main/LICENSE)
[![Documentation](https://img.shields.io/badge/docs-redtidev1918.github.io-6366f1)](https://redtidev1918.github.io/TelePost/)

[下载](https://github.com/redtidev1918/TelePost/blob/main/docs/download.md) · [安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md) · [完整文档](https://redtidev1918.github.io/TelePost/)

## 界面

![Bot 私聊主菜单](https://raw.githubusercontent.com/redtidev1918/TelePost/main/docs/assets/screenshots/bot-menu.jpg)

Bot 私聊主菜单。用户可以投稿、查看自己的投稿、搜索频道内容、浏览热榜与标签云。

<!-- 待补充真实截图：① 投稿预览与编辑；② 审核群的审核操作；③ Telegram Mini App。 -->

## 核心功能

- **投稿** —— 私聊 Bot 发送图片、视频、GIF、音频或文件，也可以只发文字。上传完成后先进入
  投稿预览：填写标签，按需修改标题、简介和来源链接，打开匿名或剧透。
- **审核与发布** —— 打开审核开关后，投稿会先发到你的私有审核群，附预览和
  「✅ 发布到频道 / ❌ 拒绝 / 🔇 遮罩」按钮；自动化投稿还可以点「🔄 重抓/换一张」请求上游
  重新搜索候选作品，替换后的稿件仍需人工审核。
- **Mini App（可选）** —— 同一个程序自带网页投稿界面。用户可以在手机上浏览公开内容、投稿、
  查看自己的投稿；审核员和管理员在其中处理审核队列、编辑文案与附件顺序、管理角色和黑名单。
  启用它需要一个公网 HTTPS 地址、`MINIAPP_SESSION_SECRET`，以及 Telegram 侧的入口配置；
  只安装 Bot 不会自动得到 Mini App，具体步骤见 [Mini App 文档](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md)。
- **搜索与标签** —— `/search` 按关键词搜索，支持 `#标签` 和时间范围；`/tags` 浏览标签云。
- **热榜与统计** —— `/hot` 查看全部时间热榜，`/hotweek` 查看本周热榜，`/mystats` 查看个人投稿
  统计；`/schedule add weekly-hot` 可以让 Bot 每周定时发布热榜。
- **自动化投稿** —— Owner 用 `/gen_token` 生成 API Token，程序即可通过 HTTP API 上传文件投稿。
  `idempotency_key` 保证重试同一个请求不会重复创建审核或重复发布。
- **AI 辅助审核（MCP）** —— 可选插件，让支持 MCP 的 AI Agent 列出待审稿件、查看详情与媒体、
  读取你的审核规则并给出建议；只有在你明确要求某个具体动作后，它才会代为执行审核操作。
- **多 Bot 与自托管** —— 同一台机器可以并行运行多个 Bot，各自使用独立的频道、审核群、语言和
  数据目录。支持独立程序、pip、Docker 与 Fly.io 部署，以及 Polling 和 Webhook 两种接收模式。
  审核队列、幂等记录和投稿会话都保存在本机的 `data` 目录里，重启后继续有效。

## 如何工作

```text
用户投稿 ──▶ 预览与编辑 ──▶ 审核（可选）──▶ 发布到频道
```

1. 用户在 Bot 私聊或 Mini App 中上传内容，或只发送文字。
2. 发送 `/done_media` 进入预览：填写标签，按需修改标题、简介、链接、匿名和剧透。
3. 点击发布按钮后，按投稿入口决定是否先进入审核。
4. 审核通过的内容发布到频道；审核群里可以拒绝、切换遮罩或封禁投稿人。

三个入口的默认处置并不相同：

| 投稿入口 | 默认行为 | 如何调整 |
| --- | --- | --- |
| Bot 私聊 | 用户确认后直接发布 | 设置 `CHAT_REVIEW_REQUIRED=true` 后先审核 |
| Mini App | 先提交审核，批准后发布 | 由 `MINIAPP_REVIEW_REQUIRED` 单独控制 |
| HTTP API | 固定先提交审核，不能关闭 | 必须人工批准后才发布 |

自动化投稿（包括 PixivFlow 等上游服务）始终需要人工批准；`API_REVIEW_REQUIRED` 只保留兼容，
不会让 API 绕过审核。默认配置会用到审核群，因此 `REVIEW_CHAT_ID` 是必填项。只使用私聊直发时，
可以把 `API_REVIEW_REQUIRED` 和 `MINIAPP_REVIEW_REQUIRED` 都显式设为 `false`，并保持
`CHAT_REVIEW_REQUIRED=false`。

## 快速开始

### 1. 准备四样东西

| 需要 | 从哪里得到 |
| --- | --- |
| Bot Token | 在 [@BotFather](https://t.me/BotFather) 创建 Bot 后取得 |
| Channel ID | 接收发布的频道，可用 `@频道用户名` 或 `-100` 开头的数字 ID |
| Owner ID | 你自己的 Telegram 数字 User ID，用于识别所有者，不是群或频道 ID |
| Review Chat ID | 一个**独立**私有审核群的数字 ID，不能与发布频道相同 |

把 Bot 加为频道管理员并授予发帖权限，再加入审核群，确认它能在群里发送消息。

### 2. 安装

**独立程序（不需要 Python）** —— 从 [下载页](https://github.com/redtidev1918/TelePost/blob/main/docs/download.md) 或
[Releases](https://github.com/redtidev1918/TelePost/releases/latest) 取得对应平台的文件，
放到一个固定的文件夹：

| 平台 | 文件 |
| --- | --- |
| Windows x64 | `telepost-windows-x64.exe` |
| Linux x64 | `telepost-linux-x64` |
| macOS（Apple Silicon） | `telepost-macos-arm64` |

Windows 在该文件夹打开 PowerShell，运行配置向导：

```powershell
.\telepost-windows-x64.exe --setup
```

Linux / macOS 首次运行前先赋予执行权限（下例为 Linux，macOS 换成 `telepost-macos-arm64`）：

```bash
chmod +x telepost-linux-x64
./telepost-linux-x64 --setup
```

**pip（已有 Python 3.10+）**：

```bash
python -m pip install telepost-bot
telepost --setup
```

PyPI 分发包名是 `telepost-bot`，安装后的命令是 `telepost`。建议同时安装系统
[libvips](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md#libvips-系统依赖)；缺少它也能正常投稿，只是超大图片的流式缩放不可用。

**Docker** —— 在仓库根目录准备 `.env`（`TOKEN`、`CHANNEL_ID`、`OWNER_ID`、`REVIEW_CHAT_ID`
和固定的 `TELEPOST_VERSION`），然后启动：

```bash
docker compose pull
docker compose up -d
```

三种方式的完整命令、升级和卸载见[安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md)。

### 3. 补上审核群再启动

配置向导只询问 Bot Token、频道和 Owner ID。打开它生成的 `config.ini`，保留已有内容，
在 `[BOT]` 节补上审核群，文件保存为 UTF-8：

```ini
[BOT]
LANGUAGE = zh
REVIEW_CHAT_ID = -1001234567890
```

把示例 ID 换成你的审核群 ID，然后启动：

| 安装方式 | 启动命令 |
| --- | --- |
| Windows 独立程序 | `.\telepost-windows-x64.exe` |
| Linux / macOS 独立程序 | `./telepost-linux-x64` |
| pip | `telepost` |
| Docker | `docker compose up -d` |

pip 安装时，配置和数据保存在当前工作目录，之后请始终从同一目录启动。升级独立程序时保留程序旁
的 `config.ini` 和 `data` 文件夹。启动后在 Telegram 私聊 Bot 发送 `/start`，确认它能回复。

### 4. 完成第一次投稿

1. 私聊 Bot 发送 `/start`，点击「📝 开始投稿」或发送 `/submit`。
2. 上传图片、视频、GIF、音频或文件；只发文字时发送 `/skip_media` 直接进入预览，完成后发送
   `/done_media`。
3. 在预览里填写标签，按需修改标题、简介、链接、匿名和剧透。
4. 点击「✅ 确认发布」；打开聊天审核时按钮显示「✅ 提交审核」。随时发送 `/cancel` 取消。

完整命令见[命令参考](https://github.com/redtidev1918/TelePost/blob/main/docs/COMMANDS.md)。

## AI 辅助审核（MCP，可选）

TelePost 不内置大语言模型，也不会自动审核任何内容。它提供的是一个可选的
[MCP](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md) 插件（sidecar）：把它接入 Claude Desktop、Codex 等支持 MCP 的
Agent，让 AI 参与审核，但由人做决定。

接入后 Agent 可以：

- 列出待审核稿件，查看标题、标签、来源和媒体元数据；
- 取回稿件里的图片预览（视频与 GIF 返回 Telegram 封面，文档和音频只给元数据）；
- 读取 `config/review_policy.md` 中的频道审核规则，按规则给出建议、置信度和理由；
- 在你明确要求某个具体动作后，批准、拒绝或设置遮罩。

建议把 MCP 和审核 API 都显式设为只读，让 AI 只给建议、由你确认后再操作：

```env
TELEPOST_MCP_REVIEW_MODE=readonly
TELEPOST_REVIEW_API_MODE=readonly
```

安装方式、Token 与 stdio / HTTP 接入配置见 [MCP 投稿审核](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md)。

## 更多能力

- **HTTP API** —— 与 Bot 共用同一套审核和发布流程。Owner 在 Bot 中用 `/gen_token <名称>` 生成
  Token，明文只显示一次：

  ```bash
  curl -X POST 'https://example.com/api/v1/submissions' \
    -H 'Authorization: Bearer tp_xxxx' \
    -F 'files=@image.jpg' \
    -F 'title=示例稿件' \
    -F 'tags=插画' \
    -F 'idempotency_key=source:123'
  ```

  多 Bot 部署把地址换成 `/api/botN/v1/submissions`。完整字段与限制见 [HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/API.md)。

- **小说在线阅读** —— TXT 投稿会作为文件正常发送到频道。开启 `NOVEL_PREVIEW_ENABLED` 并配置
  `TELEGRAPH_ACCESS_TOKEN` 后，TelePost 会通过 TelePress 生成 Telegraph 阅读页，并在频道帖上
  提供「📖 在线阅读」按钮；生成失败或超时不会影响 TXT 的发布。

- **文件类型过滤** —— 默认拦截可执行文件、常见脚本和压缩包，私聊、Mini App 与 HTTP API 都会
  检查；正常图片、音视频和 TXT、PDF、Markdown 等文档不受影响。拦截名单可以自定义，这只是
  类型过滤，不是病毒扫描。

- **接收模式** —— `RUN_MODE=AUTO` 会在有公网 HTTPS 地址时使用 Webhook，否则使用 Polling。
  多 Bot 的 Webhook 路径是 `/webhook/botN`，详见 [Webhook 与 Polling](https://github.com/redtidev1918/TelePost/blob/main/docs/WEBHOOK_MODE.md)。

- **健康检查** —— 默认监听 `8080` 端口，`GET /health` 返回运行状态，`GET /ready` 在服务就绪后
  返回成功；多 Bot 部署会等所有 Bot 子进程就绪。Fly.io 部署见
  [Fly.io 部署](https://github.com/redtidev1918/TelePost/blob/main/docs/FLYIO_DEPLOYMENT.md)。

## 文档

完整文档站：<https://redtidev1918.github.io/TelePost/>

| 我想…… | 看这里 |
| --- | --- |
| 安装、升级、卸载 | [安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md) |
| 配置频道、审核群、语言与多 Bot | [配置参考](https://github.com/redtidev1918/TelePost/blob/main/docs/CONFIGURATION.md) |
| 在 Bot 里使用命令 | [命令参考](https://github.com/redtidev1918/TelePost/blob/main/docs/COMMANDS.md) |
| 部署网页投稿与审核界面 | [Telegram Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md) |
| 接入脚本或自动化服务 | [HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/API.md) |
| 让 AI Agent 参与审核 | [MCP 投稿审核](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md) |
| 选择接收模式、配置反向代理 | [Webhook 与 Polling](https://github.com/redtidev1918/TelePost/blob/main/docs/WEBHOOK_MODE.md) |
| 部署到 Fly.io | [Fly.io 部署](https://github.com/redtidev1918/TelePost/blob/main/docs/FLYIO_DEPLOYMENT.md) |
| 备份、升级、查看运行状态 | [运维手册](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md) |
| 排查无响应、上传或发布失败 | [故障排查](https://github.com/redtidev1918/TelePost/blob/main/docs/TROUBLESHOOTING.md) |
| 评估内存与容量 | [性能与容量](https://github.com/redtidev1918/TelePost/blob/main/docs/PERFORMANCE.md) |
| 参与开发与测试 | [测试指南](https://github.com/redtidev1918/TelePost/blob/main/docs/TESTING.md)、[贡献指南](https://github.com/redtidev1918/TelePost/blob/main/CONTRIBUTING.md) |

## 相关项目

- [PixivFlow](https://github.com/redtidev1918/PixivFlow) —— Pixiv 下载、筛选与自动收集工具
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) ——
  PixivFlow + TelePost 组合部署

两者都是可选集成，不是 TelePost 的运行依赖。

## 许可证

[MIT License](https://github.com/redtidev1918/TelePost/blob/main/LICENSE)
