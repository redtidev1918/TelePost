# TelePost

> 功能强大的 Telegram 频道投稿机器人 —— 投稿、搜索、热度统计、标签体系，开箱即用

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot-blue.svg)](https://core.telegram.org/bots)
[![Docker](https://img.shields.io/badge/Docker-Supported-blue.svg)](https://www.docker.com)

---

## 它能做什么

- 📮 **投稿** —— 用户向机器人发送媒体/文档，经引导流程补充标签与简介后自动发布到频道；支持 🕵️ 匿名投稿与发布前预览/一键修改
- 🔍 **搜索** —— 基于 Whoosh 的全文搜索，支持关键词、#标签、文件名与时间范围筛选
- 🔥 **热度统计** —— 自动采集浏览/转发数据，计算热度分并生成排行榜
- 🛡️ **管理** —— 黑名单、OWNER/Admin 分级、批量删帖（软删除，保留历史）
- 🏷️ **标签云** —— 频道内容自动打标，可视化浏览
- 🧩 **多 bot 单机部署** —— 一个容器承载任意数量的频道 bot（BOT1_TOKEN/BOT2_TOKEN/…），数据互相隔离，配合 auto_stop 最大化省钱

## 快速开始

```bash
git clone https://github.com/redtidev1918/TelePost.git
cd TelePost
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

## 致谢

TelePost 站在前人的肩膀上，感谢这些优秀的开源项目与作者：

| 项目 | 贡献 |
|---|---|
| [zoidberg-xgd/TeleSubmit-v2](https://github.com/zoidberg-xgd/TeleSubmit-v2) | **上游项目** —— 本项目基于其设计与代码积累演进而来（频道监听、部署脚本体系等） |
| [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) | Telegram Bot 开发框架（Apache-2.0），本项目的骨架 |
| [Whoosh](https://github.com/mchaput/whoosh-search) | 纯 Python 全文搜索引擎，支撑 /search 与标签检索 |
| [jieba](https://github.com/fxsjy/jieba) | 中文分词（可选启用，显著提升中文搜索质量） |
| [aiohttp](https://github.com/aio-libs/aiohttp) | 异步 HTTP：webhook 服务器、健康检查与多 bot 路由 |
| [APScheduler](https://github.com/agronholm/apscheduler) | 定时任务（统计更新、过期清理、已删帖检查） |
| [aiosqlite](https://github.com/omnilib/aiosqlite) | 异步 SQLite 访问 |
| [psutil](https://github.com/giampaolo/psutil) | 进程/内存监控（/health 自报容量） |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | 环境变量加载 |
| [Fly.io](https://fly.io) | 一键全球化部署的托管平台 |

排名不分先后。若你的项目被本仓库引用而未列名，请提 Issue 告知。

## 支持 & 许可

- 问题反馈请开 [Issue](https://github.com/redtidev1918/TelePost/issues)
- 本项目基于 [MIT License](LICENSE) 开源
