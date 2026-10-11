# TelePost

**语言 / Language:** 中文 · [English](https://github.com/redtidev1918/TelePost/blob/main/README.en.md)

TelePost 是一个开源、自托管的 Telegram 频道投稿与审核 Bot。用户可以向频道投稿，
管理员在审核群里查看、修改并决定是否发布。

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest) [![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/) [![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/) [![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/redtidev1918/TelePost/blob/main/LICENSE) [![Documentation](https://img.shields.io/badge/docs-redtidev1918.github.io-6366f1)](https://redtidev1918.github.io/TelePost/)

[下载](https://github.com/redtidev1918/TelePost/blob/main/docs/download.md) · [安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md) · [完整文档](https://redtidev1918.github.io/TelePost/)

## 产品界面

![Bot 私聊主菜单](https://raw.githubusercontent.com/redtidev1918/TelePost/main/docs/assets/screenshots/bot-menu.jpg)

Bot 私聊主菜单。用户在这里投稿、查看自己的投稿、搜索频道内容，或浏览热榜与标签云。

<!-- 待补充的真实截图（按重要性排序）：① 投稿预览与编辑；② 审核群的审核操作；③ Telegram Mini App。 -->

## 主要功能

| 功能 | 说明 |
| --- | --- |
| 便捷投稿 | 从 Bot 私聊或 Mini App 提交图片、视频、GIF、音频、文档或纯文字，提交前在预览里改标签、标题、简介和链接，可开启匿名或剧透。 |
| 审核与发布 | 稿件连同预览发到私有审核群，用「✅ 发布到频道 / ❌ 拒绝 / 🔇 遮罩」决定；自动化稿件可「🔄 重抓」换候选，替换后仍需人工审核。 |
| Mini App | 可选网页界面：手机浏览公开内容、投稿并跟踪自己的投稿；审核员与管理员处理审核队列、编辑文案与附件顺序、管理角色和黑名单。 |
| 搜索与热榜 | `/search` 关键词检索（支持 `#标签` 和时间范围），`/tags` 标签云，`/hot`、`/hotweek` 热榜，`/myposts`、`/mystats` 个人投稿与统计。 |
| 定时发布 | `/schedule add weekly-hot` 等命令按计划发布热榜内容。 |
| 自动化投稿 | Owner 用 `/gen_token` 生成 Token 后即可用 HTTP API 投稿，`idempotency_key` 保证重试不重复审核或发布。 |
| AI 辅助审核 | 可选接入 MCP，让 Codex、Claude 等 Agent 查看待审稿件并给出建议；不内置大语言模型，是否发布由人决定。 |
| 多 Bot 与自托管 | 同一台机器可并行运行多个 Bot，各自使用独立的频道、审核群、语言和数据目录。 |

各入口的默认审核规则见[如何工作](#如何工作)，集成与部署细节见[更多能力](#更多能力)。

## 如何工作

```text
用户投稿 ──▶ 预览与编辑 ──▶ 按配置审核 ──▶ 发布到频道
```

用户在 Bot 私聊或 Mini App 上传内容，先在预览里确认标签、标题、简介、链接、匿名和剧透，
再提交。是否进入审核由投稿入口决定：

| 投稿入口 | 默认行为 |
| --- | --- |
| Bot 私聊 | 确认后直接发布；`CHAT_REVIEW_REQUIRED=true` 时先审核 |
| Mini App | 先提交审核，由 `MINIAPP_REVIEW_REQUIRED` 单独控制 |
| HTTP API | 固定先提交审核，必须人工批准 |

自动化投稿（含 PixivFlow 等上游服务）同样要人工批准，`API_REVIEW_REQUIRED` 只保留兼容、
不能关闭 API 审核。默认配置会用到审核群，因此 `REVIEW_CHAT_ID` 必填，且不能与发布频道相同；
完整默认值、审核行为与保留策略见[配置参考](https://github.com/redtidev1918/TelePost/blob/main/docs/CONFIGURATION.md#审核)。

## 下载与安装

三条路径，按你的环境选一条。

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

**pip（已有 Python 3.10+ 环境）**：

```bash
python -m pip install telepost-bot
telepost --setup
```

PyPI 分发包名是 `telepost-bot`，安装后的命令是 `telepost`；配置与数据保持在当前工作目录，
之后请始终从同一目录启动。建议安装系统 [libvips](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md#libvips-系统依赖)：
缺少它也能正常投稿，只是超大图片的流式缩放不可用。

**Docker（服务器或长期部署）** —— 在仓库根目录准备 `.env`（`TOKEN`、`CHANNEL_ID`、
`OWNER_ID`、`REVIEW_CHAT_ID` 与固定的 `TELEPOST_VERSION`），然后启动：

```bash
docker compose pull
docker compose up -d
```

升级、卸载、系统依赖和全部配置项见[安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md)。
普通 Polling Bot 不需要域名或公网 HTTPS，只有 Webhook 接收模式和 Mini App 需要。

## 首次使用

1. **创建 Bot** —— 在 [@BotFather](https://t.me/BotFather) 创建 Bot 并取得 Token。
2. **准备频道和审核群** —— 把 Bot 加为发布频道的管理员并授予发帖权限；再把它加入一个
   **独立**的私有审核群，确认它能在群里发送消息。频道 ID 可以用 `@频道用户名` 或
   `-100` 开头的数字 ID；Owner ID 是你自己的数字 Telegram User ID，不是群或频道 ID。
3. **补上审核群再启动** —— 配置向导只询问 Token、频道和 Owner ID。打开它生成的
   `config.ini`，保留已有内容，在 `[BOT]` 节补上审核群（文件保存为 UTF-8），然后启动：

   ```ini
   [BOT]
   LANGUAGE = zh
   REVIEW_CHAT_ID = -1001234567890
   ```

   把示例 ID 换成你的审核群 ID。启动命令：独立程序直接双击或 `./telepost-*`，pip 用
   `telepost`，Docker 用 `docker compose up -d`。
4. **发起第一次投稿** —— 私聊 Bot 发送 `/start`，点击「📝 开始投稿」或发送 `/submit`；
   上传图片、视频、GIF、音频或文件，只发文字时用 `/skip_media` 直接进入预览，完成后发送
   `/done_media`。在预览里填写标签、按需修改，最后点击「✅ 确认发布」（开启聊天审核后是
   「✅ 提交审核」）。随时可以发送 `/cancel` 取消。

全部命令见[命令参考](https://github.com/redtidev1918/TelePost/blob/main/docs/COMMANDS.md)。

## Mini App（可选）

Mini App 是同一程序自带的网页界面：用户在手机上浏览公开内容、投稿并跟踪自己的投稿；
审核员和管理员可以处理审核队列、编辑文案与附件顺序、管理角色与黑名单。

它不会在装好 Bot 之后自动出现。启用需要公网 HTTPS 地址、`MINIAPP_SESSION_SECRET`，
以及 Telegram 侧的入口配置；网页前端由程序自己的 HTTP 服务提供，因此运行在 Webhook 模式下
才会挂载。缺少任一项时，私聊里的入口按钮不会显示。

身份来自 Telegram，并在服务端用 `initData` 校验。在普通浏览器里直接打开网址不等于登录，
也不会获得审核或管理权限。配置与部署步骤见
[Telegram Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md)。

## AI 辅助审核（MCP，可选）

TelePost 可以通过 [MCP](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md)
连接 Codex、Claude 等支持 MCP 的 AI Agent：查看待审核稿件、依据频道审核规则提出建议，
并在你明确授权、权限允许时执行审核操作。

TelePost 不内置大语言模型，也不会自动审核任何内容，是否发布始终由人决定。推荐把 MCP 与
审核 API 都设为只读，只让 AI 提供建议。工具列表、Token 与接入配置见
[MCP 投稿审核](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md)。

## 更多能力

- **HTTP API** —— 与 Bot 共用同一套审核与发布流程；多 Bot 部署使用
  `/api/botN/v1/submissions`。字段与限制见 [HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/API.md)。
- **多 Bot** —— 同一台机器并行运行多个 Bot，各自使用独立的频道、审核群、语言和数据目录。
- **TXT 小说与在线阅读** —— TXT 照常作为文件发布；可选启用 TelePress 生成 Telegraph 阅读页，
  失败不影响发布。
- **Webhook 与 Polling** —— 有公网 HTTPS 地址时使用 Webhook，否则使用 Polling。
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

多 Bot 部署把地址换成 `/api/botN/v1/submissions`。API 投稿固定进入审核，完整字段与限制见
[HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/API.md)。

</details>

## 常见问题

<details>
<summary>需要服务器和域名吗？</summary>

不需要域名或公网 HTTPS 也能用：在一台可以长期运行的电脑或服务器上以 Polling 模式运行，
配合 Bot 私聊与审核群即可。只有 Webhook 接收模式和 Mini App 需要公网 HTTPS 地址。
详见[安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md)。

</details>

<details>
<summary>私聊投稿为什么直接发布了？</summary>

Bot 私聊投稿默认在用户确认后直接发布（`CHAT_REVIEW_REQUIRED=false`）。把它设为 `true`，
私聊投稿就会先进入审核群。Mini App 与 HTTP API 不受这个开关影响，默认都要先审核。
详见[配置参考](https://github.com/redtidev1918/TelePost/blob/main/docs/CONFIGURATION.md#审核)。

</details>

<details>
<summary>Mini App 为什么没有出现？</summary>

Mini App 是可选组件：需要公网 HTTPS 地址、`MINIAPP_SESSION_SECRET`、Webhook 模式和
Telegram 侧的入口配置，缺少任一项时入口不会显示。详见
[Telegram Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md)。

</details>

<details>
<summary>Bot 为什么没有响应？</summary>

先确认同一个 Bot Token 只被一个实例使用，再看进程状态、最近日志和 `/health`；Webhook 模式
还要检查 `getWebhookInfo`。同时启动源码、容器或旧机器会造成 Polling 冲突。详见
[故障排查](https://github.com/redtidev1918/TelePost/blob/main/docs/TROUBLESHOOTING.md#bot-完全无响应)。

</details>

<details>
<summary>投稿文件保存在哪里？</summary>

数据都在程序目录下的 `data/`：稿件库 `submissions.db`、会话状态 `persistence.pickle`、
运行策略 `runtime-policy.json`、搜索索引 `search_index` 和 API 临时上传 `api_uploads/`；
多 Bot 各自使用 `data/botN/`。媒体本身保存在 Telegram 侧，数据库只记录引用。升级或迁移时
保留整个 `data/` 即可，详见[运维手册](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md#持久数据)。

</details>

<details>
<summary>MCP 会自动替我批准内容吗？</summary>

不会。TelePost 不内置大语言模型，MCP 是可选组件：审核写入工具只在非只读模式下注册，并同时
受 MCP 与审核 API 权限开关的限制。人工确认是推荐流程，不是强制机制；把两层都设为只读，
AI 就只能给出建议。详见
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

- [PixivFlow](https://github.com/redtidev1918/PixivFlow) —— Pixiv 下载、筛选与自动收集工具，
  可通过 HTTP API 把作品投稿到 TelePost。
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) ——
  PixivFlow + TelePost 的组合部署。

两者都是可选集成，不是运行 TelePost 的必需依赖。

## 许可证

[MIT License](https://github.com/redtidev1918/TelePost/blob/main/LICENSE)
