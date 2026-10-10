# TelePost 文档

TelePost 接收 Telegram Bot、Mini App 和 HTTP API 投稿，负责审核与频道发布。
先按任务选择指南；接口字段、配置默认值和内部设计各有独立参考。

[English](en/README.md) · [项目主页](https://github.com/redtidev1918/TelePost)

## 开始使用

首次安装见 [安装与部署](INSTALL.md)。Python 用户安装 `telepost-bot`，用
`telepost --setup` 配置后启动；无需 Python 的独立程序见 [下载](download.md)。

| 任务 | 指南 |
| --- | --- |
| 安装、升级或卸载 | [安装与部署](INSTALL.md) |
| 通过 Bot 投稿或管理频道 | [命令参考](COMMANDS.md) |
| 配置频道、审核群、多 Bot 和署名 | [配置参考](CONFIGURATION.md) |
| 启用网页投稿与审核 | [Telegram Mini App](MINIAPP.md) |
| 接入脚本或自动化服务 | [HTTP API](API.md) |
| 用 Agent 查看稿件、提供审核建议 | [MCP 投稿审核](MCP_REVIEW.md) |

Bot 私聊默认直接发布；Mini App 默认需要审核，可独立配置；自动化 API 投稿固定需要人工审核。
具体开关见 [审核配置](CONFIGURATION.md#审核)。

## 部署与运维

| 任务 | 指南 |
| --- | --- |
| 选择更新接收模式、配置反向代理 | [Webhook 与 Polling](WEBHOOK_MODE.md) |
| 使用 Fly.io | [Fly.io 部署](FLYIO_DEPLOYMENT.md) |
| 备份、升级、回退、检查运行状态 | [运维手册](OPERATIONS.md) |
| 处理无响应、上传失败、发布失败 | [故障排查](TROUBLESHOOTING.md) |
| 评估内存、上传和磁盘容量 | [性能与容量](PERFORMANCE.md) |

TelePost 在 Fly.io 上常驻运行。PixivFlow 等上游采集器使用独立 App 和卷；
组合部署以 [部署仓库](https://github.com/redtidev1918/pixivflow-telepost-deploy) 为准。

## 开发参考

| 主题 | 文档 |
| --- | --- |
| 本地测试与 CI | [测试指南](TESTING.md) |
| 应用分层、投递与预览 | [运行时架构](internals/architecture.md) |
| 请求身份与投稿归属 | [投稿归属契约](architecture/identity-and-provenance.md) |
| 私聊状态与持久化 | [聊天投稿状态机](internals/submission-flow.md) |
| 投稿处置与管理权限 | [管理控制面](internals/admin-control-plane.md) |
| 删帖与历史记录 | [删帖与软删除](internals/moderation.md) |
| 同一内容重新提交审核 | [重投契约](RESUBMIT.md) |

参与开发前阅读仓库 [AGENTS.md](https://github.com/redtidev1918/TelePost/blob/main/AGENTS.md)
和 [贡献指南](https://github.com/redtidev1918/TelePost/blob/main/CONTRIBUTING.md)。
设计记录 [私聊文案 RFC](private-chat-ux-rfc.md) 用于理解改动背景；
当前操作步骤以上述指南和代码为准。

遇到问题可提交 [Issue](https://github.com/redtidev1918/TelePost/issues)，附版本、部署方式、
复现步骤和脱敏日志。
