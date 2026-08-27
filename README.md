# TeleSubmit v2

> 功能强大的 Telegram 频道投稿机器人 —— 投稿、搜索、热度统计、标签体系，开箱即用

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot-blue.svg)](https://core.telegram.org/bots)
[![Docker](https://img.shields.io/badge/Docker-Supported-blue.svg)](https://www.docker.com)

---

## 它能做什么

- 📮 **投稿** —— 用户向机器人发送媒体/文档，经引导流程补充标签与简介后自动发布到频道
- 🔍 **搜索** —— 基于 Whoosh 的全文搜索，支持关键词、#标签、文件名与时间范围筛选
- 🔥 **热度统计** —— 自动采集浏览/转发数据，计算热度分并生成排行榜
- 🛡️ **管理** —— 黑名单、OWNER/Admin 分级、批量删帖（软删除，保留历史）
- 🏷️ **标签云** —— 频道内容自动打标，可视化浏览

## 快速开始

```bash
git clone https://github.com/zoidberg-xgd/TeleSubmit-v2.git
cd TeleSubmit-v2
./quickstart.sh   # 智能检测环境并引导完成配置与启动
```

最少只需两项配置（写入 `config.ini` 或设置环境变量均可）：

| 配置 | 环境变量 | 说明 |
|---|---|---|
| 机器人 Token | `TOKEN` | 从 [@BotFather](https://t.me/BotFather) 获取；兼容 `BOT_TOKEN` / `TELEGRAM_BOT_TOKEN` 别名 |
| 频道 ID | `CHANNEL_ID` | 形如 `@yourchannel` 或 `-100xxxxxxxxxx`，机器人需为频道管理员 |

> 完整配置项（含 `[SEARCH]`、`[DB]` 高级选项）见 **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**。

运行模式默认 Polling，零额外配置；服务器部署推荐切换 Webhook，见 **[docs/WEBHOOK_MODE.md](docs/WEBHOOK_MODE.md)**。

## 常用命令

| 命令 | 说明 |
|---|---|
| `/submit` | 开始投稿 |
| `/search <关键词>` | 全文搜索（支持 `#标签`、`-t week` 时间过滤） |
| `/hot` | 热门帖子排行榜 |
| `/myposts` / `/mystats` | 我的投稿 / 我的统计 |
| `/help` | 完整帮助 |

管理员：`/blacklist_add`、`/delete_posts`、`/rebuild_index` 等 —— 全部命令见 **[docs/COMMANDS.md](docs/COMMANDS.md)**。

## 部署方式

| 方式 | 适合 | 文档 |
|---|---|---|
| quickstart.sh | 快速体验 | [docs/INSTALL.md](docs/INSTALL.md) |
| install.sh + systemd | 生产 VPS | [docs/INSTALL.md](docs/INSTALL.md) |
| Docker / Compose | 容器环境 | [docs/INSTALL.md](docs/INSTALL.md) |
| Fly.io | 免运维 PaaS | [docs/FLYIO_DEPLOYMENT.md](docs/FLYIO_DEPLOYMENT.md) |
| PythonAnywhere | 低成本托管 | [docs/PYTHONANYWHERE_DEPLOYMENT.md](docs/PYTHONANYWHERE_DEPLOYMENT.md) |

## 运维速查

```bash
./start.sh                  # 启动
./restart.sh                # 重启
./update.sh                 # 更新到最新版
curl localhost:8080/health  # 健康检查（Polling 与 Webhook 模式均提供）
```

日志、备份、索引维护、定时任务说明 → **[docs/OPERATIONS.md](docs/OPERATIONS.md)**；常见故障排查 → **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)**。

## 性能

最低 256MB 内存即可运行（simple 分词模式约 80–120MB）。调优指南见 **[docs/PERFORMANCE.md](docs/PERFORMANCE.md)**。

## 开发

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m pytest -q --no-cov
```

贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，测试体系见 [docs/TESTING.md](docs/TESTING.md)，删帖/软删除内部设计见 [docs/internals/moderation.md](docs/internals/moderation.md)。

## 文档地图

| 文档 | 内容 | 读者 |
|---|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | 五种部署方式与首次启动清单 | 所有人 |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | 全部配置项与优先级 | 所有人 |
| [docs/COMMANDS.md](docs/COMMANDS.md) | 用户/管理员/流程内命令全集 | 所有人 |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | 日志、备份、索引、定时任务运维手册 | 管理员 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 症状→原因→处置 排查手册 | 管理员 |
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | 内存与搜索性能调优 | 管理员 |
| [docs/WEBHOOK_MODE.md](docs/WEBHOOK_MODE.md) | Polling vs Webhook 详解 | 管理员 |
| [docs/FLYIO_DEPLOYMENT.md](docs/FLYIO_DEPLOYMENT.md) | Fly.io 部署 | 管理员 |
| [docs/PYTHONANYWHERE_DEPLOYMENT.md](docs/PYTHONANYWHERE_DEPLOYMENT.md) | PythonAnywhere 部署 | 管理员 |
| [docs/TESTING.md](docs/TESTING.md) | 测试指南 | 贡献者 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献指南 | 贡献者 |
| [docs/internals/moderation.md](docs/internals/moderation.md) | 删帖与软删除设计 | 开发者 |
| [CHANGELOG.md](CHANGELOG.md) | 版本历史 | 所有人 |

## 支持 & 许可

- 问题反馈请开 [Issue](https://github.com/zoidberg-xgd/TeleSubmit-v2/issues)
- 本项目基于 [MIT License](LICENSE) 开源
