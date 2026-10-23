# TelePost

**语言 / Language:** 中文 · [English](https://github.com/redtidev1918/TelePost/blob/main/README.en.md)

TelePost 是开源、自托管的 Telegram 频道投稿与审核 Bot，支持私聊、Mini App 和 HTTP API 投稿。
用户可上传、预览和编辑内容，管理员可在审核群中处理待审稿件并发布到频道。

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest) [![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/) [![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/) [![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/redtidev1918/TelePost/blob/main/LICENSE) [![Documentation](https://img.shields.io/badge/docs-redtidev1918.github.io-6366f1)](https://redtidev1918.github.io/TelePost/)

[下载](https://github.com/redtidev1918/TelePost/blob/main/docs/download.md) · [安装与部署](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md) · [完整文档](https://redtidev1918.github.io/TelePost/)

**快速导航：** [审核规则](#如何工作) · [安装](#下载与安装) · [首次使用](#首次使用) · [可选功能](#可选功能)

## 主要功能

| 功能 | 说明 |
| --- | --- |
| 投稿 | 支持图片、视频、GIF、音频、文档和纯文字，提交前可预览、编辑，支持匿名与剧透。 |
| 审核与发布 | 在私有审核群查看预览、发布、拒绝或遮罩稿件；支持重抓的自动化稿件可更换候选，替换后仍需人工审核。 |
| Mini App | 可选网页界面，供用户浏览、投稿和查看进度，供审核员与管理员审核、编辑和管理。 |
| 搜索与热榜 | 关键词与标签搜索、热门内容、个人投稿查询和统计。 |
| 定时发布 | `/schedule add weekly-hot` 等命令按计划发布热榜内容。 |
| 自动化投稿 | 脚本或上游服务通过 HTTP API 投稿，使用 `idempotency_key` 避免重试产生重复审核或发布。 |
| 重启与恢复 | 待审记录保存在数据目录中，重启后可继续处理；已清理的审核预览可按需恢复。 |
| AI 辅助审核 | 可选通过 MCP 接入 Codex、Claude 等 AI Agent，查看待审稿件并提供审核建议。 |
| 多 Bot 与自托管 | 同一台机器可并行运行多个 Bot，各自使用独立的频道、审核群、语言和数据目录。 |

## 如何工作

<details>
<summary>查看 Bot 界面</summary>

![Bot 私聊主菜单](https://raw.githubusercontent.com/redtidev1918/TelePost/main/docs/assets/screenshots/bot-menu.jpg)

</details>

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

需要一台可持续运行且能访问 Telegram 的电脑或服务器。仅使用 Bot 时可选 Polling 模式，无需域名；Mini App 需要 Webhook 模式和公网 HTTPS。

选择一种安装方式：

| 方式 | 适用情况 | 入口 |
| --- | --- | --- |
| 独立程序 | 无需安装 Python | [下载 Windows、Linux 或 macOS 程序](https://github.com/redtidev1918/TelePost/releases/latest) |
| pip | 已有 Python 3.10+ | `python -m pip install telepost-bot` |
| Docker | 服务器部署 | [Docker / Compose 安装](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md#3-docker--compose) |

独立程序下载后加 `--setup` 运行配置向导；pip 安装后执行 `telepost --setup`。
完整安装、升级与备份步骤见[安装指南](https://github.com/redtidev1918/TelePost/blob/main/docs/INSTALL.md)。

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
   Docker 用户按安装指南配置环境变量，无需运行向导或编辑 `config.ini`。
4. **发起第一次投稿**：私聊 Bot 发送 `/start`，点击「📝 开始投稿」或发送 `/submit`；
   上传图片、视频、GIF、音频或文件，完成后发送 `/done_media` 进入预览；纯文字投稿用
   `/skip_media` 跳过上传。在预览中填写标签和文字内容，确认后点击「✅ 确认发布」
   （开启聊天审核后为「✅ 提交审核」）。发送 `/cancel` 可取消当前投稿。

全部命令见[命令参考](https://github.com/redtidev1918/TelePost/blob/main/docs/COMMANDS.md)。

## 可选功能

- [Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md)：在 Telegram 内完成网页投稿、进度查询和审核；需配置 HTTPS 地址及登录密钥。
- [HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/API.md)：供脚本和其他服务投稿，支持幂等键，避免重试产生重复稿件。
- [AI 辅助审核（MCP）](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md)：连接外部 AI Agent 查看稿件、提供建议。建议使用只读模式；开启写入权限后，程序不强制要求额外的人工确认。
- [在线阅读](https://github.com/redtidev1918/TelePost/blob/main/docs/CONFIGURATION.md)：可选启用 TelePress，为 TXT 小说生成 Telegraph 阅读页。

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
