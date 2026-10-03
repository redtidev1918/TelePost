# 部署文档入口

本文件保留旧链接兼容，不再维护重复步骤。

| 场景 | 文档 |
|---|---|
| **安装与升级（推荐入口）** | [docs/INSTALL.md](docs/INSTALL.md) |
| **正式发版（含自动部署生产）** | [docs/OPERATIONS.md § 正式发布流程](docs/OPERATIONS.md#正式发布流程) |
| 统一升级模型（pip / Docker / Fly / 单文件） | [docs/OPERATIONS.md § 统一升级模型](docs/OPERATIONS.md#统一升级模型) |
| Fly.io 部署依据与验收 | [docs/FLYIO_DEPLOYMENT.md § 0](docs/FLYIO_DEPLOYMENT.md#0-正式发版路径唯一推荐) |
| 单文件、源码、Docker | [docs/INSTALL.md](docs/INSTALL.md) |
| Polling / Webhook | [docs/WEBHOOK_MODE.md](docs/WEBHOOK_MODE.md) |
| 配置项 | [docs/CONFIGURATION.md](docs/CONFIGURATION.md) |
| 日常运维与备份 | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| 故障处理 | [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) |

> **生产只由 Release 驱动。** 日常开发 PR merge 到 `main` 不会发版；只有合并 Release PR
> 才触发发行链：GitHub Release、GHCR 镜像、PyPI `telepost-bot`、文档，然后用同一个版本的镜像
> 部署 Fly 生产，并验证 `/health` 版本与 bot1/bot2 就绪。任一关键阶段失败即发版失败。
> 手工 `flyctl deploy` 不再是正常发布路径，只作为自动化不可用时的故障恢复手段。
> 详见 [运维手册 · 正式发布流程](docs/OPERATIONS.md#正式发布流程)。

PythonAnywhere 的旧 WSGI 适配不覆盖当前完整运行生命周期，**不受支持**，不要照旧教程部署到生产。
