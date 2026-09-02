# TelePost

Telegram 频道投稿机器人：媒体/文档投稿、全文搜索、本地热榜、标签体系。

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org)
[![Docker](https://img.shields.io/badge/Docker-Supported-blue.svg)](https://www.docker.com)

## 功能

- 媒体/文档投稿：引导式流程，发布前可预览和修改，支持匿名选项
- 全文搜索：关键词、#标签、文件名、时间范围筛选
- 本地热榜：按数据库中已有统计生成排行榜；Bot API 不支持无副作用回读任意频道帖统计
- 标签云
- 管理：黑名单、批量删帖（软删除，保留历史）
- 多 bot：一台机器承载多个频道的投稿 bot，数据相互隔离
- 网络自适应：`RUN_MODE=AUTO` 在有公网 HTTPS Webhook 地址时使用推送，否则自动回退轮询；两种模式都保留健康检查与投稿 API
- 可选审核队列：可分别控制 API 投稿、Telegram `/submit` 投稿是否进入私有审核群，由管理员点击通过/拒绝
- Owner-only `/botconfig`：从 Telegram 修改当前 Bot 的频道、审核群、审核与署名策略，持久化后只重载该 Bot
- 可选 PixivFlow 联合调度：同一 Fly Machine 中用一个 Node 进程运行多条 Pixiv 下载计划，缓存投递到 Bot1/Bot2，配置可通过 SSH 原子热更新

## 快速开始

```bash
git clone https://github.com/redtidev1918/TelePost.git
cd TelePost
./quickstart.sh
```

必需配置两项，写入 `config.ini` 或环境变量均可：

| 配置 | 环境变量 | 说明 |
|---|---|---|
| 机器人 Token | `TOKEN` | 从 [@BotFather](https://t.me/BotFather) 获取；兼容 `BOT_TOKEN` 别名 |
| 频道 ID | `CHANNEL_ID` | `@yourchannel` 或 `-100xxxxxxxxxx`，机器人需为频道管理员 |

其余配置见 [docs/CONFIGURATION.md](docs/CONFIGURATION.md)。默认 AUTO 模式：配置公网 HTTPS Webhook 地址时使用推送，否则使用 Polling，见 [docs/WEBHOOK_MODE.md](docs/WEBHOOK_MODE.md)。

## 常用命令

| 命令 | 说明 |
|---|---|
| `/submit` | 开始投稿 |
| `/search <关键词>` | 全文搜索，支持 `#标签`、`-t week` 时间过滤 |
| `/hot` | 热门排行榜 |
| `/myposts`、`/mystats` | 我的投稿、我的统计 |
| `/help` | 完整帮助 |
| `/botconfig` | Owner-only 运行配置面板 |

管理员命令（`/blacklist_add`、`/delete_posts`、`/rebuild_index` 等）见 [docs/COMMANDS.md](docs/COMMANDS.md)。

自动化投稿：提供 token 鉴权的 HTTP API，详见 [docs/API.md](docs/API.md)。

## 部署

| 方式 | 文档 |
|---|---|
| 联合部署套件 | [redtidev1918/pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) — PixivFlow + TelePost 一套 Compose 启动，支持国内/海外、有/无公网 IP 任意场景 |
| quickstart.sh（快速体验） | [docs/INSTALL.md](docs/INSTALL.md) |
| install.sh + systemd（VPS） | [docs/INSTALL.md](docs/INSTALL.md) |
| Docker / Compose | [docs/INSTALL.md](docs/INSTALL.md) |
| Fly.io | [docs/FLYIO_DEPLOYMENT.md](docs/FLYIO_DEPLOYMENT.md) |
| Fly.io 512 MiB：PixivFlow + 双 Bot | [docs/OPERATIONS.md#fly-512-mibpixivflow--telepost-双-bot](docs/OPERATIONS.md#fly-512-mibpixivflow--telepost-双-bot) |
| PythonAnywhere | [docs/PYTHONANYWHERE_DEPLOYMENT.md](docs/PYTHONANYWHERE_DEPLOYMENT.md) |

日常操作：

```bash
./start.sh                  # 启动
./restart.sh                # 重启
./update.sh                 # 更新
curl localhost:8080/health  # 健康检查
```

日志、备份、索引维护见 [docs/OPERATIONS.md](docs/OPERATIONS.md)，故障排查见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。

## 性能

256MB 内存可运行单 Bot；双 Bot + PixivFlow 的 512 MiB 档必须关闭搜索、下载并发设为 1，并错开计划，见 [docs/PERFORMANCE.md](docs/PERFORMANCE.md)。

## 开发

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest -q --no-cov
```

贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，测试说明见 [docs/TESTING.md](docs/TESTING.md)，删帖机制设计见 [docs/internals/moderation.md](docs/internals/moderation.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | 部署方式与首次启动清单 |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | 配置项与优先级 |
| [docs/COMMANDS.md](docs/COMMANDS.md) | 命令参考 |
| [docs/API.md](docs/API.md) | HTTP API（自动化投稿） | 开发者 |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | 运维手册 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 故障排查 |
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | 性能与内存调优 |
| [docs/WEBHOOK_MODE.md](docs/WEBHOOK_MODE.md) | Polling 与 Webhook 模式 |
| [docs/FLYIO_DEPLOYMENT.md](docs/FLYIO_DEPLOYMENT.md) | Fly.io 部署 |
| [docs/PYTHONANYWHERE_DEPLOYMENT.md](docs/PYTHONANYWHERE_DEPLOYMENT.md) | PythonAnywhere 部署 |
| [docs/TESTING.md](docs/TESTING.md) | 测试指南 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献指南 |
| [docs/internals/moderation.md](docs/internals/moderation.md) | 删帖机制设计 |
| [CHANGELOG.md](CHANGELOG.md) | 版本历史 |

## 致谢

本项目依赖以下开源项目：

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) —— Bot 框架
- [Whoosh](https://github.com/mchaput/whoosh-search) —— 全文检索
- [jieba](https://github.com/fxsjy/jieba) —— 中文分词（可选）
- [aiohttp](https://github.com/aio-libs/aiohttp) —— Webhook 服务器与 HTTP 客户端
- [APScheduler](https://github.com/agronholm/apscheduler) —— 定时任务
- [aiosqlite](https://github.com/omnilib/aiosqlite) —— 异步 SQLite
- [psutil](https://github.com/giampaolo/psutil) —— 进程与内存监控

生产依赖见 [requirements.txt](requirements.txt)，测试工具见
[requirements-dev.txt](requirements-dev.txt)。

## 反馈与许可

- 问题反馈请开 [Issue](https://github.com/redtidev1918/TelePost/issues)
- 基于 [MIT License](LICENSE) 开源
