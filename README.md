# TelePost

**语言 / Language:** 中文 · [English](https://github.com/redtidev1918/TelePost/blob/main/README.en.md)

TelePost 是开源、自托管的 Telegram 频道投稿与审核 Bot，支持私聊、Mini App 和 HTTP API 投稿。
用户可上传、预览和编辑内容，管理员可在审核群中处理待审稿件并发布到频道。

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest) [![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/) [![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/) [![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/redtidev1918/TelePost/blob/main/LICENSE) [![Documentation](https://img.shields.io/badge/docs-redtidev1918.github.io-6366f1)](https://redtidev1918.github.io/TelePost/)

[下载](https://github.com/redtidev1918/TelePost/blob/main/docs/download.md) · [安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md) · [完整文档](https://redtidev1918.github.io/TelePost/)

**快速导航：** [了解审核规则](#如何工作) · [安装](#下载与安装) · [首次投稿](#首次使用) · [Mini App](#mini-app可选) · [常见问题](#常见问题)

## 产品界面

![Bot 私聊主菜单](https://raw.githubusercontent.com/redtidev1918/TelePost/main/docs/assets/screenshots/bot-menu.jpg)

Bot 私聊主菜单，提供投稿、个人投稿查询、频道搜索、热榜和标签云入口。

## 主要功能

| 功能 | 说明 |
| --- | --- |
| 投稿 | 支持图片、视频、GIF、音频、文档和纯文字，提交前可预览、编辑，支持匿名与剧透。 |
| 审核与发布 | 在私有审核群查看预览、发布、拒绝或遮罩稿件；支持重抓的自动化稿件可更换候选，替换后仍需人工审核。 |
| Mini App | 可选网页界面，供用户浏览、投稿和查看进度，供审核员与管理员审核、编辑和管理。 |
| 搜索与热榜 | `/search` 关键词检索（支持 `#标签` 和时间范围），`/tags` 标签云，`/hot`、`/hotweek` 热榜，`/myposts`、`/mystats` 个人投稿与统计。 |
| 定时发布 | `/schedule add weekly-hot` 等命令按计划发布热榜内容。 |
| 自动化投稿 | 脚本或上游服务通过 HTTP API 投稿，使用 `idempotency_key` 避免重试产生重复审核或发布。 |
| 重启与恢复 | 待审记录保存在数据目录中，重启后可继续处理；已清理的审核预览可按需恢复。 |
| AI 辅助审核 | 可选通过 MCP 接入 Codex、Claude 等 AI Agent，查看待审稿件并提供审核建议。 |
| 多 Bot 与自托管 | 同一台机器可并行运行多个 Bot，各自使用独立的频道、审核群、语言和数据目录。 |

## 如何工作

用户在 Bot 私聊或 Mini App 上传内容，在预览中确认标签、标题、简介、链接及匿名、剧透设置后提交。
是否进入审核由投稿入口决定：

| 投稿入口 | 默认行为 |
| --- | --- |
| Bot 私聊 | 确认后直接发布；`CHAT_REVIEW_REQUIRED=true` 时先审核 |
| Mini App | 默认先审核；由 `MINIAPP_REVIEW_REQUIRED` 单独控制 |
| HTTP API | 固定先提交审核，必须人工批准 |

如需所有投稿先审核，设置 `CHAT_REVIEW_REQUIRED=true`，并保持 `MINIAPP_REVIEW_REQUIRED=true`。
默认配置需要填写审核群 `REVIEW_CHAT_ID`，且不能与发布频道相同。
HTTP API 投稿始终需要审核，`API_REVIEW_REQUIRED=false` 不会关闭 API 审核。
更多设置见[配置参考](https://github.com/redtidev1918/TelePost/blob/main/docs/CONFIGURATION.md#审核)。

## 下载与安装

准备一台可以长期运行、能连接 Telegram 的电脑或服务器，以及 Bot Token、发布频道和独立审核群。
尚未创建 Bot 或取得相关 ID 时，先完成[首次使用](#首次使用)的前两步。
仅使用 Bot 私聊时可选 Polling，无需域名；Mini App 需要 Webhook 与公网 HTTPS。
按运行环境选择一种安装方式，完成后继续[首次使用](#首次使用)。

**独立程序（不需要 Python）**：从[下载页](https://github.com/redtidev1918/TelePost/blob/main/docs/download.md)
或 [Releases](https://github.com/redtidev1918/TelePost/releases/latest) 取得对应平台的文件：
Windows x64 是 `telepost-windows-x64.exe`，Linux x64 是 `telepost-linux-x64`，
macOS（Apple Silicon）是 `telepost-macos-arm64`。将程序保存在固定目录中，运行配置向导：

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
缺少它仍可启动和投稿，但超大图片会作为文件发送。

**Docker（服务器或长期部署）**：下载或克隆仓库，在根目录创建 `.env`：

```dotenv
TOKEN=replace-with-your-bot-token
CHANNEL_ID=@your_channel
OWNER_ID=123456789
REVIEW_CHAT_ID=-1001234567890
```

以上四项均需替换为实际值，示例 Token 和 ID 不可直接使用。首次试用无需指定版本，
Compose 默认拉取 `latest` 镜像。在 `docker-compose.yml` 所在目录执行：

```bash
docker compose pull
docker compose up -d
docker compose ps
```

启动后，`docker compose ps` 应显示容器运行，并在健康检查完成后显示 `healthy`。
随后私聊 Bot 发送 `/start`，确认能够打开主菜单。

长期部署应在 `.env` 中追加 `TELEPOST_VERSION`，值为
[Releases](https://github.com/redtidev1918/TelePost/releases/latest) 中选定的正式版本号（不带 `v` 前缀）。
该变量仅用于选择 Docker 镜像版本，不属于 Bot 配置；独立程序与 pip 安装无需设置。
固定版本可控制升级和回滚，升级步骤见[安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md)。

Compose 将数据保存在 `./data`，默认不映射宿主机 HTTP 端口。添加示例之外的配置时，
还需在 Compose 的 `environment` 中传入对应变量；Webhook、Mini App 和审核开关的配置见
[安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md#3-docker--compose)。

升级、卸载、系统依赖和全部配置项见[安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md)。
接收模式与公网 HTTPS 的要求见[更多能力](#更多能力)。

## 首次使用

1. **创建 Bot**：在 [@BotFather](https://t.me/BotFather) 创建 Bot 并取得 Token。
2. **准备频道和审核群**：将 Bot 设为发布频道的管理员并授予发帖权限，再加入一个
   **独立**的私有审核群，确认 Bot 有发送消息的权限。频道 ID 支持 `@频道用户名` 或
   `-100` 开头的数字 ID；Owner ID 应填写 Bot 所有者的数字 Telegram User ID，不得填写群或频道 ID。
3. **补齐配置并启动**：独立程序与 pip 的配置向导仅配置 Token、频道和 Owner ID。打开生成的
   `config.ini`，保留已有内容，在 `[BOT]` 节补充审核群配置（保存为 UTF-8）：

   ```ini
   [BOT]
   LANGUAGE = zh
   REVIEW_CHAT_ID = -1001234567890
   ```

   将示例 ID 替换为实际审核群 ID。如需私聊投稿先审核，在同一 `[BOT]` 节追加
   `CHAT_REVIEW_REQUIRED = true`。Windows 独立程序可双击启动；Linux / macOS 运行对应的
   `./telepost-linux-x64` 或 `./telepost-macos-arm64`；pip 用 `telepost`。
   Docker 用户在前述 `.env` 中填写配置，无需运行向导或编辑 `config.ini`。
4. **发起第一次投稿**：私聊 Bot 发送 `/start`，点击「📝 开始投稿」或发送 `/submit`；
   上传图片、视频、GIF、音频或文件，完成后发送 `/done_media` 进入预览；纯文字投稿用
   `/skip_media` 跳过上传。在预览中填写标签和文字内容，确认后点击「✅ 确认发布」
   （开启聊天审核后为「✅ 提交审核」）。发送 `/cancel` 可取消当前投稿。

全部命令见[命令参考](https://github.com/redtidev1918/TelePost/blob/main/docs/COMMANDS.md)。

## Mini App（可选）

Mini App 是内置网页界面，支持浏览公开内容、投稿和查询处理进度；
审核员和管理员可以处理审核队列、编辑文案与附件顺序、管理角色与黑名单。

启用时需要 Webhook 模式和公网 HTTPS 地址。设置 `MINIAPP_ENABLED`、入口地址
`MINIAPP_PUBLIC_URL` 和登录密钥 `MINIAPP_SESSION_SECRET` 后，从 Telegram 内打开使用。
普通浏览器直接打开网址无法登录。配置步骤及 BotFather 设置见
[Telegram Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md)。

## AI 辅助审核（MCP，可选）

MCP 是 AI Agent 调用外部工具的协议。TelePost 提供 MCP 工具，供 Codex、Claude 等外部 AI Agent
查看待审稿件、提供审核建议，并在具备写入权限时执行审核操作。

AI Agent 需单独配置。建议将 MCP 与审核 API 均设为只读，由管理员决定是否发布。
开启写入权限后，程序不强制要求额外的人工确认。
工具列表、Token 与接入配置见
[MCP 投稿审核](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md)。

## 更多能力

- **HTTP API**：供脚本或其他服务发送投稿请求，与 Bot 共用审核与发布流程，示例见下方。
- **TXT 小说与在线阅读**：TXT 照常作为文件发布；可选启用 TelePress 生成 Telegraph 阅读页，
  失败不影响发布。
- **Webhook 与 Polling**：Polling 由 Bot 主动获取 Telegram 消息，无需域名或公网 HTTPS；
  Webhook 由 Telegram 将消息推送给 Bot，需要公网 HTTPS 地址。Mini App 同样需要公网 HTTPS。
- **文件类型过滤**：默认拦截可执行文件、常见脚本和压缩包，三个投稿入口都会检查。
- **服务器部署**：提供 Docker 镜像与 Fly.io 配置，备份与运行状态见
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

先确认同一个 Bot Token 仅由一个实例使用，再检查进程状态、近期日志和 `/health`；Webhook 模式
还要检查 `getWebhookInfo`。同时启动源码、容器或旧机器会造成 Polling 冲突。详见
[故障排查](https://github.com/redtidev1918/TelePost/blob/main/docs/TROUBLESHOOTING.md#bot-完全无响应)。

</details>

<details>
<summary>审核预览清理后，稿件是否保留？</summary>

审核预览媒体默认在发送约 36 小时后清理，控制卡和审核记录仍保留；这不代表稿件已过期。
待审稿件可点击卡片上的「恢复预览」重新查看。若卡片按钮异常，管理员可按
[运维手册](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md)中的步骤恢复卡片。

</details>

<details>
<summary>投稿文件保存在哪里？</summary>

投稿记录、运行策略和会话状态默认保存在 `data/`；pip 安装时，该目录位于启动命令的工作目录中。
多个 Bot 分别使用 `data/botN/`。上传的媒体保存在 Telegram，`data/` 不是媒体文件的完整副本。升级或迁移时
备份并保留整个 `data/`，备份方法见[运维手册](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md#持久数据)。

</details>

<details>
<summary>MCP 是否允许 AI 批准稿件？</summary>

只读模式下，AI 只能查看和提出建议。设置 `TELEPOST_MCP_REVIEW_MODE=readonly`
和 `TELEPOST_REVIEW_API_MODE=readonly` 可禁止它执行审核操作。开启写入权限前，请先阅读
[MCP 投稿审核](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md)。

</details>

## 文档

完整文档站：<https://redtidev1918.github.io/TelePost/>

安装与配置说明见下方文档。开发者和 AI Agent 修改仓库前，应先阅读
[AGENTS.md](https://github.com/redtidev1918/TelePost/blob/main/AGENTS.md) 及其列出的跨仓库规范，
再按[贡献指南](https://github.com/redtidev1918/TelePost/blob/main/CONTRIBUTING.md)配置开发环境并验证改动。

- 安装、升级与卸载：[安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md)
- 命令与所需权限：[命令参考](https://github.com/redtidev1918/TelePost/blob/main/docs/COMMANDS.md)
- 配置项、审核默认值与多 Bot：[配置参考](https://github.com/redtidev1918/TelePost/blob/main/docs/CONFIGURATION.md)
- 网页投稿与审核界面：[Telegram Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md)
- 脚本与自动化投稿：[HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/API.md)
- AI Agent 参与审核：[MCP 投稿审核](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md)
- 接收模式与反向代理：[Webhook 与 Polling](https://github.com/redtidev1918/TelePost/blob/main/docs/WEBHOOK_MODE.md)
- 部署到 Fly.io：[Fly.io 部署](https://github.com/redtidev1918/TelePost/blob/main/docs/FLYIO_DEPLOYMENT.md)
- 备份、升级与运行状态：[运维手册](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md)
- 排查无响应、上传或发布失败：[故障排查](https://github.com/redtidev1918/TelePost/blob/main/docs/TROUBLESHOOTING.md)
- 评估内存与容量：[性能与容量](https://github.com/redtidev1918/TelePost/blob/main/docs/PERFORMANCE.md)
- 开发与测试：[测试指南](https://github.com/redtidev1918/TelePost/blob/main/docs/TESTING.md)、[贡献指南](https://github.com/redtidev1918/TelePost/blob/main/CONTRIBUTING.md)

## 相关项目

- [PixivFlow](https://github.com/redtidev1918/PixivFlow)：按条件检索和收集 Pixiv 作品，提交至 TelePost 审核发布。
- [TelePress](https://github.com/redtidev1918/TelePress)：为小说等内容生成在线阅读页面。
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy)：在独立服务中部署 PixivFlow 与 TelePost 的指南和工具。

TelePost 可以独立使用，无需部署 PixivFlow 或整套生态。TelePress 库随程序安装，在线阅读需另行配置。

## 许可证

[MIT License](https://github.com/redtidev1918/TelePost/blob/main/LICENSE)
