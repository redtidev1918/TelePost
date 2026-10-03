# 部署文档入口

本文件保留旧链接兼容，不再维护重复步骤。

| 场景 | 文档 |
|---|---|
| **正式发版（含自动部署生产）** | [docs/FLYIO_DEPLOYMENT.md § 0](docs/FLYIO_DEPLOYMENT.md#0-正式发版路径唯一推荐) |
| 单文件、源码、Docker | [docs/INSTALL.md](docs/INSTALL.md) |
| Fly.io | [docs/FLYIO_DEPLOYMENT.md](docs/FLYIO_DEPLOYMENT.md) |
| Polling / Webhook | [docs/WEBHOOK_MODE.md](docs/WEBHOOK_MODE.md) |
| 配置项 | [docs/CONFIGURATION.md](docs/CONFIGURATION.md) |
| 日常运维与发布 | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| 故障处理 | [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) |

> **生产只由 Release 驱动。** 合并 Release PR 后，流水线会自动完成 GitHub Release、
> GHCR 镜像、PyPI、文档，然后把同一个版本的镜像部署到 Fly 生产，并验证
> `/health` 版本与 bot1/bot2 就绪。手工 `flyctl deploy` 不再是正常发布路径，
> 只作为自动化不可用时的故障恢复手段。

PythonAnywhere 的旧 WSGI 适配不覆盖当前完整运行生命周期，**不受支持**，不要照旧教程部署到生产。
