# TelePost 文档中心

> TelePost 是一个 Telegram 频道投稿机器人：聊天投稿、审核队列、全文搜索、多 Bot 与 HTTP API。这里汇聚它的全部文档。

## 🧭 按任务找文档

| 你想做什么 | 路线 |
| --- | --- |
| 第一次部署，跑起来 | [安装与部署](INSTALL.md) |
| 了解所有 Telegram 命令 | [命令参考](COMMANDS.md) |
| 精细配置（Token、频道、审核、多 Bot） | [配置参考](CONFIGURATION.md) |
| 部署到 Fly.io | [Fly.io 部署](FLYIO_DEPLOYMENT.md) |
| 部署到 PythonAnywhere | [PythonAnywhere 部署](PYTHONANYWHERE_DEPLOYMENT.md) |
| 理解 Webhook 与 Polling 的区别 | [Webhook 与 Polling](WEBHOOK_MODE.md) |
| 日常运维、升级、备份 | [运维手册](OPERATIONS.md) |
| 排查问题 | [故障排查](TROUBLESHOOTING.md) |
| 调内存 / 容量 | [性能与容量](PERFORMANCE.md) |
| 接外部自动化投稿（HTTP API） | [HTTP API](API.md) |
| 二次开发、改代码 | [投稿状态机](internals/submission-flow.md) · [删帖与软删除](internals/moderation.md) · [测试指南](TESTING.md) |

## 📚 全部文档

### 开始使用

| 文档 | 内容 |
| --- | --- |
| [INSTALL](INSTALL.md) | 各平台安装方式、首次配置、启动与验证 |
| [COMMANDS](COMMANDS.md) | 全部 Telegram 命令与用法 |

### 配置与部署

| 文档 | 内容 |
| --- | --- |
| [CONFIGURATION](CONFIGURATION.md) | 环境变量 / config.ini 逐项说明 |
| [FLYIO_DEPLOYMENT](FLYIO_DEPLOYMENT.md) | Fly.io 部署与自动休眠 |
| [PYTHONANYWHERE_DEPLOYMENT](PYTHONANYWHERE_DEPLOYMENT.md) | PythonAnywhere 部署状态 |
| [WEBHOOK_MODE](WEBHOOK_MODE.md) | Webhook 与 Polling 两种模式 |

### 运维

| 文档 | 内容 |
| --- | --- |
| [OPERATIONS](OPERATIONS.md) | 运维手册：更新、备份、监控 |
| [TROUBLESHOOTING](TROUBLESHOOTING.md) | 常见故障与排查步骤 |
| [PERFORMANCE](PERFORMANCE.md) | 内存 / 容量优化 |

### 开发者

| 文档 | 内容 |
| --- | --- |
| [API](API.md) | HTTP API v1 投稿接口参考 |
| [TESTING](TESTING.md) | 测试指南 |
| [submission-flow](internals/submission-flow.md) | 内部设计：聊天投稿状态机 |
| [moderation](internals/moderation.md) | 内部设计：删帖与软删除 |

## 🔗 其他入口

- 项目主页：<https://github.com/redtidev1918/TelePost>
- npm 无（Python 项目）；PyInstaller 单文件见 [Releases](https://github.com/redtidev1918/TelePost/releases)
- 问题反馈：[Issues](https://github.com/redtidev1918/TelePost/issues)
