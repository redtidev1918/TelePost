# TelePost documentation

TelePost accepts submissions from a Telegram Bot, Mini App, and HTTP API, then handles review
and channel publication. Choose a guide by task.

[中文](../README.md) · [Repository](https://github.com/redtidev1918/TelePost)

## Get started

See [Install and deploy](INSTALL.md) for installation, upgrades, and removal.
Install `telepost-bot` with Python 3.10+ and configure it with `telepost --setup`.
Standalone programs are available on the [download page](download.md).

| Task | Guide |
| --- | --- |
| Install, upgrade, or uninstall | [Install and deploy](INSTALL.md) |
| Submit or manage content in the bot | [Commands](COMMANDS.md) |
| Configure channels, review, and multiple bots | [Configuration](CONFIGURATION.md) |
| Integrate scripts and automation | [HTTP API](API.md) |

Private-chat submissions publish directly by default. Mini App submissions require review by
default and have an independent setting. Automated API submissions always require human review.
See [Review configuration](CONFIGURATION.md#review).

## Deployment and operations

These guides are currently in Chinese. Installation, configuration, commands, API, and downloads
have English versions; architecture notes and design records are maintained in Chinese.

| Task | Guide |
| --- | --- |
| Enable web submission and moderation | [Mini App](../MINIAPP.md) |
| Let an agent inspect submissions and suggest review decisions | [MCP review](../MCP_REVIEW.md) |
| Configure update delivery and a reverse proxy | [Webhook and Polling](../WEBHOOK_MODE.md) |
| Deploy on Fly.io | [Fly.io deployment](../FLYIO_DEPLOYMENT.md) |
| Back up, upgrade, roll back, or inspect runtime state | [Operations](../OPERATIONS.md) |
| Diagnose an unresponsive bot or failed publication | [Troubleshooting](../TROUBLESHOOTING.md) |
| Assess memory, uploads, and disk capacity | [Performance and capacity](../PERFORMANCE.md) |

TelePost stays running on Fly.io. Collectors such as PixivFlow use separate apps and volumes;
see the [deployment repository](https://github.com/redtidev1918/pixivflow-telepost-deploy)
for the combined topology.

## Development reference

| Topic | Guide (Chinese unless noted) |
| --- | --- |
| Local tests and CI | [Testing](../TESTING.md) |
| Application layers, delivery, and previews | [Runtime architecture](../internals/architecture.md) |
| Request identity and submission ownership | [Identity and provenance](../architecture/identity-and-provenance.md) |
| Chat state and persistence | [Submission flow](../internals/submission-flow.md) |
| Admission and admin permissions | [Admin control plane](../internals/admin-control-plane.md) |
| Deletion and history | [Moderation](../internals/moderation.md) |
| Submit the same content for review again | [Resubmit contract (English)](../RESUBMIT.md) |

Before contributing, read [AGENTS.md](https://github.com/redtidev1918/TelePost/blob/main/AGENTS.md)
and the [contribution guide](https://github.com/redtidev1918/TelePost/blob/main/CONTRIBUTING.md).
Report problems through [Issues](https://github.com/redtidev1918/TelePost/issues), with the version,
deployment method, reproduction steps, and redacted logs.
