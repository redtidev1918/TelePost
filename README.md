# TelePost

**语言 / Language:** 中文 · [English](https://github.com/redtidev1918/TelePost/blob/main/README.en.md)

TelePost 是一个开源、自托管的 Telegram 频道投稿与审核 Bot。用户通过 Bot 私聊、Mini App
或 HTTP API 投稿；需要审核的稿件由管理员在审核群里查看、修改并决定是否发布。

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest) [![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/) [![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/) [![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/redtidev1918/TelePost/blob/main/LICENSE) [![Documentation](https://img.shields.io/badge/docs-redtidev1918.github.io-6366f1)](https://redtidev1918.github.io/TelePost/)

[下载](https://github.com/redtidev1918/TelePost/blob/main/docs/download.md) · [安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md) · [完整文档](https://redtidev1918.github.io/TelePost/)

**从这里开始：** [了解审核规则](#如何工作) · [安装](#下载与安装) · [首次投稿](#首次使用) · [Mini App](#mini-app可选) · [常见问题](#常见问题)

## 产品界面

![Bot 私聊主菜单](https://raw.githubusercontent.com/redtidev1918/TelePost/main/docs/assets/screenshots/bot-menu.jpg)

Bot 私聊主菜单。用户在这里投稿、查看自己的投稿、搜索频道内容，或浏览热榜与标签云。

## 主要功能

| 功能 | 说明 |
| --- | --- |
| 投稿 | 支持图片、视频、GIF、音频、文档和纯文字，提交前可预览、编辑，支持匿名与剧透。 |
| 审核与发布 | 在私有审核群查看预览、发布、拒绝或遮罩稿件；支持重抓的自动化稿件可更换候选，替换后仍需人工审核。 |
| Mini App | 可选网页界面，供用户浏览、投稿和查看进度，供审核员与管理员审核、编辑和管理。 |
| 搜索与热榜 | `/search` 关键词检索（支持 `#标签` 和时间范围），`/tags` 标签云，`/hot`、`/hotweek` 热榜，`/myposts`、`/mystats` 个人投稿与统计。 |
| 定时发布 | `/schedule add weekly-hot` 等命令按计划发布热榜内容。 |
| 自动化投稿 | 脚本或上游服务通过 HTTP API 投稿，使用 `idempotency_key` 避免重试产生重复审核或发布。 |
| 持久化与恢复 | 审核记录和会话状态持久化；Webhook 更新落库后才确认接收，失败后有限重试。审核卡与已清理的预览可按需恢复。 |
| AI 辅助审核 | 可选通过 MCP 接入 Codex、Claude 等 AI Agent，查看待审稿件并提供审核建议。 |
| 多 Bot 与自托管 | 同一台机器可并行运行多个 Bot，各自使用独立的频道、审核群、语言和数据目录。 |

各入口的默认审核规则见[如何工作](#如何工作)，集成与部署细节见[更多能力](#更多能力)。

## 如何工作

```text
用户投稿 ──▶ 预览与编辑 ──▶ 按配置审核 ──▶ 发布到频道
```

用户在 Bot 私聊或 Mini App 上传内容，先在预览里确认标签、标题、简介、链接及匿名、剧透设置，
再提交。是否进入审核由投稿入口决定：

| 投稿入口 | 默认行为 |
| --- | --- |
| Bot 私聊 | 确认后直接发布；`CHAT_REVIEW_REQUIRED=true` 时先审核 |
| Mini App | 默认先审核；由 `MINIAPP_REVIEW_REQUIRED` 单独控制 |
| HTTP API | 固定先提交审核，必须人工批准 |

通过 HTTP API 提交的自动化稿件（含 PixivFlow 等上游服务）必须人工批准。
`API_REVIEW_REQUIRED` 只保留兼容，不能关闭 API 审核。默认配置会用到审核群，
因此 `REVIEW_CHAT_ID` 必填，且不能与发布频道相同；
完整默认值、审核行为与保留策略见[配置参考](https://github.com/redtidev1918/TelePost/blob/main/docs/CONFIGURATION.md#审核)。

## 下载与安装

准备一台可以长期运行、能连接 Telegram 的电脑或服务器，以及 Bot Token、发布频道和独立审核群。
仅使用 Bot 私聊时可选 Polling，无需域名；Mini App 需要 Webhook 与公网 HTTPS。
按运行环境选择一种安装方式，完成后继续[首次使用](#首次使用)。

**独立程序（不需要 Python）** —— 从[下载页](https://github.com/redtidev1918/TelePost/blob/main/docs/download.md)
或 [Releases](https://github.com/redtidev1918/TelePost/releases/latest) 取得对应平台的文件：
Windows x64 是 `telepost-windows-x64.exe`，Linux x64 是 `telepost-linux-x64`，
macOS（Apple Silicon）是 `telepost-macos-arm64`。放在一个固定文件夹里，运行配置向导：

```powershell
.\telepost-windows-x64.exe --setup
```

Linux / macOS 首次运行前先赋予执行权限：

```bash
chmod +x telepost-linux-x64
./telepost-linux-x64 --setup
```

macOS 将上述文件名换成 `telepost-macos-arm64`。

**pip（已有 Python 3.10+ 环境）**：

```bash
python -m pip install telepost-bot
telepost --setup
```

PyPI 分发包名是 `telepost-bot`，安装后的命令是 `telepost`；配置与数据保持在当前工作目录，
之后请始终从同一目录启动。建议安装系统 [libvips](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md#libvips-系统依赖)：
缺少它仍可启动和投稿，但超大图片无法流式缩放，会回退为文档发送。

**Docker（服务器或长期部署）** —— 下载或克隆仓库，在根目录创建 `.env`：

```dotenv
TOKEN=replace-with-your-bot-token
CHANNEL_ID=@your_channel
OWNER_ID=123456789
REVIEW_CHAT_ID=-1001234567890
TELEPOST_VERSION=2.x.y
```

把占位值换成自己的配置，并把 `2.x.y` 换成 [Releases](https://github.com/redtidev1918/TelePost/releases/latest)
中的正式版本号，然后启动：

```bash
docker compose pull
docker compose up -d
docker compose ps
```

Compose 默认不映射宿主机端口，数据持久化到 `./data`。额外环境变量必须在 Compose 的
`environment` 中传入，仅写入 `.env` 不会自动进入容器；Webhook、Mini App 和审核开关的配置见
[安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md#3-docker--compose)。

升级、卸载、系统依赖和全部配置项见[安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md)。
接收模式与公网 HTTPS 的要求见[更多能力](#更多能力)。

## 首次使用

1. **创建 Bot** —— 在 [@BotFather](https://t.me/BotFather) 创建 Bot 并取得 Token。
2. **准备频道和审核群** —— 把 Bot 加为发布频道的管理员并授予发帖权限；再把它加入一个
   **独立**的私有审核群，确认它能在群里发送消息。频道 ID 可以用 `@频道用户名` 或
   `-100` 开头的数字 ID；Owner ID 是你自己的数字 Telegram User ID，不是群或频道 ID。
3. **补齐配置并启动** —— 独立程序与 pip 的配置向导只询问 Token、频道和 Owner ID。打开它生成的
   `config.ini`，保留已有内容，在 `[BOT]` 节补上审核群（文件保存为 UTF-8），然后启动：

   ```ini
   [BOT]
   LANGUAGE = zh
   REVIEW_CHAT_ID = -1001234567890
   ```

   把示例 ID 换成你的审核群 ID。若希望私聊投稿也先审核，在同一 `[BOT]` 节追加
   `CHAT_REVIEW_REQUIRED = true`。Windows 独立程序可双击启动；Linux / macOS 运行对应的
   `./telepost-linux-x64` 或 `./telepost-macos-arm64`；pip 用 `telepost`。
   Docker 用户在前述 `.env` 中填写配置，无需运行向导或编辑 `config.ini`。
4. **发起第一次投稿** —— 私聊 Bot 发送 `/start`，点击「📝 开始投稿」或发送 `/submit`；
   上传图片、视频、GIF、音频或文件，完成后发送 `/done_media` 进入预览；纯文字投稿用
   `/skip_media` 跳过上传。在预览里填写标签和文字内容、按需修改，最后点击「✅ 确认发布」
   （开启聊天审核后是「✅ 提交审核」）。随时可以发送 `/cancel` 取消。

全部命令见[命令参考](https://github.com/redtidev1918/TelePost/blob/main/docs/COMMANDS.md)。

## Mini App（可选）

Mini App 是同一程序自带的网页界面：用户在手机上浏览公开内容、投稿并跟踪自己的投稿；
审核员和管理员可以处理审核队列、编辑文案与附件顺序、管理角色与黑名单。

启用需要公网 HTTPS 地址，并在环境变量中设置 `MINIAPP_SESSION_SECRET`。网页前端由程序
自己的 HTTP 服务提供，目前只在 Webhook 模式下挂载。私聊入口需启用 `MINIAPP_ENABLED`
并配置 `MINIAPP_PUBLIC_URL`（可从 `WEBHOOK_URL` 推导）；缺少密钥时，入口即使显示也无法登录。
如需通过 `?startapp=` 链接直接打开，还要在 BotFather 配置 Main Mini App。

身份来自 Telegram，并在服务端用 `initData` 校验。在普通浏览器里直接打开网址不等于登录，
也不会获得审核或管理权限。配置与部署步骤见
[Telegram Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md)。

## AI 辅助审核（MCP，可选）

MCP 是 AI Agent 调用外部工具的协议。TelePost 可通过 MCP 连接 Codex、Claude 等 AI Agent，
让它们查看待审核稿件、依据频道审核规则提出建议，
并在你明确授权、权限允许时执行审核操作。

TelePost 不内置大语言模型，也不会自行调用 AI 审核。推荐把 MCP 与审核 API 都设为只读，
只让 AI 提供建议；启用写入权限后，应在执行具体操作前取得人工确认，但程序不强制这一步。
工具列表、Token 与接入配置见
[MCP 投稿审核](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md)。

## 更多能力

- **HTTP API** —— 供脚本或其他服务发送投稿请求，与 Bot 共用审核与发布流程，示例见下方。
- **TXT 小说与在线阅读** —— TXT 照常作为文件发布；可选启用 TelePress 生成 Telegraph 阅读页，
  失败不影响发布。
- **Webhook 与 Polling** —— Polling 由 Bot 主动获取 Telegram 消息，无需域名或公网 HTTPS；
  Webhook 由 Telegram 将消息推送给 Bot，需要公网 HTTPS 地址。Mini App 同样需要公网 HTTPS。
- **文件类型过滤** —— 默认拦截可执行文件、常见脚本和压缩包，三个投稿入口都会检查。
- **服务器部署** —— 提供 Docker 镜像与 Fly.io 配置，备份与运行状态见
  [运维手册](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md)。

<details>
<summary>HTTP API 投稿示例</summary>

Owner 在 Bot 中用 `/gen_token <名称>` 生成 Token，明文只显示一次：

```bash
curl -X POST 'https://example.com/api/v1/submissions' \
  -H 'Authorization: Bearer tp_xxxx' \
  -F 'files=@image.jpg' \
  -F 'title=示例稿件' \
  -F 'tags=插画' \
  -F 'idempotency_key=source:123'
```

多 Bot 部署把地址换成 `/api/botN/v1/submissions`。审核规则见[如何工作](#如何工作)，完整字段与限制见
[HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/API.md)。

</details>

## 常见问题

<details>
<summary>需要服务器和域名吗？</summary>

需要一台可以长期运行的电脑或服务器。使用 Bot 私聊与审核群时，可选择 Polling，无需域名；
Webhook 和 Mini App 的公网要求见[更多能力](#更多能力)。
详见[安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md)。

</details>

<details>
<summary>私聊投稿为什么直接发布了？</summary>

Bot 私聊投稿默认在用户确认后直接发布。设置 `CHAT_REVIEW_REQUIRED=true` 可改为先审核，
不影响其他入口。各入口的默认行为见[如何工作](#如何工作)。

</details>

<details>
<summary>Mini App 为什么没有出现？</summary>

先检查 `MINIAPP_ENABLED` 和入口网址是否配置，再确认 Webhook 模式下网页可访问。
入口显示却无法登录时，检查环境变量中的 `MINIAPP_SESSION_SECRET`，并从 Telegram 内打开。
启用条件见 [Mini App](#mini-app可选)，排查步骤见
[Telegram Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md)。

</details>

<details>
<summary>Bot 为什么没有响应？</summary>

先确认同一个 Bot Token 只被一个实例使用，再看进程状态、最近日志和 `/health`；Webhook 模式
还要检查 `getWebhookInfo`。同时启动源码、容器或旧机器会造成 Polling 冲突。详见
[故障排查](https://github.com/redtidev1918/TelePost/blob/main/docs/TROUBLESHOOTING.md#bot-完全无响应)。

</details>

<details>
<summary>审核群里的预览消失了，稿件还在吗？</summary>

审核预览媒体默认在发送约 36 小时后清理，控制卡和审核记录仍保留；这不代表稿件已过期。
待审稿件可通过控制卡的「恢复预览」按需查看。卡片异常时，管理员可向对应 Bot 发送
`/restore_card <审核ID> --dry-run` 检查，再去掉 `--dry-run` 就地恢复；已删除的卡片不会重发。
保留策略和恢复限制见[运维手册](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md)。

</details>

<details>
<summary>投稿文件保存在哪里？</summary>

默认数据保存在 `data/`（pip 安装时位于启动命令的工作目录）：稿件库 `submissions.db`、
会话状态 `persistence.pickle`、运行策略 `runtime-policy.json`、搜索索引 `search_index`
和 API 临时上传 `api_uploads/`；
多 Bot 各自使用 `data/botN/`。媒体本身保存在 Telegram 侧，数据库只记录引用。升级或迁移时
备份并保留整个 `data/`，备份方法见[运维手册](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md#持久数据)。

</details>

<details>
<summary>MCP 会自动替我批准内容吗？</summary>

是否能执行批准取决于权限：`TELEPOST_MCP_REVIEW_MODE=readonly` 不注册审核写入工具，
`TELEPOST_REVIEW_API_MODE=readonly` 则在服务端拒绝写入。建议两层都设为只读；
启用写入后的人工确认要求见[AI 辅助审核](#ai-辅助审核mcp可选)。完整配置见
[MCP 投稿审核](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md)。

</details>

## 文档

完整文档站：<https://redtidev1918.github.io/TelePost/>

- 安装、升级与卸载 —— [安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md)
- 命令与所需权限 —— [命令参考](https://github.com/redtidev1918/TelePost/blob/main/docs/COMMANDS.md)
- 配置项、审核默认值与多 Bot —— [配置参考](https://github.com/redtidev1918/TelePost/blob/main/docs/CONFIGURATION.md)
- 网页投稿与审核界面 —— [Telegram Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md)
- 脚本与自动化投稿 —— [HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/API.md)
- AI Agent 参与审核 —— [MCP 投稿审核](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md)
- 接收模式与反向代理 —— [Webhook 与 Polling](https://github.com/redtidev1918/TelePost/blob/main/docs/WEBHOOK_MODE.md)
- 部署到 Fly.io —— [Fly.io 部署](https://github.com/redtidev1918/TelePost/blob/main/docs/FLYIO_DEPLOYMENT.md)
- 备份、升级与运行状态 —— [运维手册](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md)
- 排查无响应、上传或发布失败 —— [故障排查](https://github.com/redtidev1918/TelePost/blob/main/docs/TROUBLESHOOTING.md)
- 评估内存与容量 —— [性能与容量](https://github.com/redtidev1918/TelePost/blob/main/docs/PERFORMANCE.md)
- 开发与测试 —— [测试指南](https://github.com/redtidev1918/TelePost/blob/main/docs/TESTING.md)、[贡献指南](https://github.com/redtidev1918/TelePost/blob/main/CONTRIBUTING.md)

## 相关项目

- [PixivFlow](https://github.com/redtidev1918/PixivFlow) —— 负责内容发现、解析、候选筛选与调度，
  通过 HTTP API 向 TelePost 投稿；审核与 Telegram 发布由 TelePost 负责。
- [TelePress](https://github.com/redtidev1918/TelePress) —— 提供富媒体渲染与在线阅读能力；
  TelePost 可选启用其阅读页功能。
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) ——
  跨仓库部署编排与运维规范；PixivFlow 与 TelePost 作为独立服务运行。

TelePost 可以独立使用，无需部署 PixivFlow 或整套生态。TelePress 库随程序安装，在线阅读需另行配置。

## 许可证

[MIT License](https://github.com/redtidev1918/TelePost/blob/main/LICENSE)
