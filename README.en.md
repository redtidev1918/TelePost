# TelePost

**Language / 语言:** [中文](README.md) · English

Submissions, moderation, and publishing for Telegram channels.

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest)
[![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Documentation](https://img.shields.io/badge/docs-redtidev1918.github.io-6366f1)](https://redtidev1918.github.io/TelePost/)

TelePost is a self-hosted service for submissions, moderation, and publishing on Telegram channels.
People submit from the Telegram Bot or the Mini App, and other programs submit through the HTTP API;
every entry point ends up in the same submission, moderation, and publishing flow.

```text
Telegram Bot ─────┐
Telegram Mini App ├──→ TelePost ──→ Telegram Channel
HTTP API ─────────┘
```

## What it does

| Use case | Features |
| --- | --- |
| Accept submissions | Bot, Mini App, and HTTP API; images, video, audio, and files |
| Review and publish | Review queue, edit before publishing, spoilers, and review history |
| Read and search | Channel search and tags; TXT novels can generate Telegraph reading pages |
| User self-service | Browse content, submit, and view your own submissions in the Mini App |
| Integrate automation | Bearer tokens, file uploads, and idempotent submissions |
| Self-host | Isolated multi-bot configuration and storage, SQLite persistence, restart recovery |

## Quick start

1. Create a bot with [@BotFather](https://t.me/BotFather), add it to your channel, and grant it permission to post.
2. Install Python 3.10+. Image processing requires system libvips; see
   [system dependencies](docs/en/INSTALL.md#libvips-system-dependency).
3. Install and generate configuration in a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install telepost-bot
telepost --setup
```

On Windows PowerShell, use:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install telepost-bot
.\.venv\Scripts\telepost.exe --setup
```

The setup wizard asks for Bot Token, Channel ID, and Owner ID. Add `REVIEW_CHAT_ID` (a separate
review group's ID) under `[BOT]` in the generated `config.ini`, then run `telepost`.
API and Mini App review settings default to enabled; the three-field wizard does not configure
the review group. For private-chat-only use, see the [installation guide](docs/en/INSTALL.md#1-pip-install-recommended).
Save the configuration as UTF-8. On Windows, start with `.\.venv\Scripts\telepost.exe`.

Send `/start` → `/submit` → upload media → `/done_media`, check the preview,
then follow the submission prompts. See the [command guide](docs/en/COMMANDS.md).

Bot private-chat submissions publish directly by default and can be configured
for review. Automated HTTP API submissions always require review. Mini App submissions require
review by default, controlled independently by `MINIAPP_REVIEW_REQUIRED`.
Configure the review chat and reviewers before using moderation; see
[configuration](docs/en/CONFIGURATION.md).

## Choose an installation method

| Method | Entry point | Best for |
| --- | --- | --- |
| Python / pip | `python -m pip install telepost-bot` | Self-managed servers and local use |
| Standalone program | [Choose a platform and download](https://redtidev1918.github.io/TelePost/#/en/download) | No Python installation |
| Docker / Compose | `ghcr.io/redtidev1918/telepost:<version>` | Container deployments |
| Source | [Development and testing (Chinese)](docs/TESTING.md) | Code changes and contributions |

The PyPI package is `telepost-bot`; the command and Python import name are both `telepost`.
Pin a formal release version in production. Full install, upgrade, and uninstall instructions:
[Install and deployment](docs/en/INSTALL.md).

## HTTP API

As the Owner, generate an API token with `/gen_token <name>` in the bot, then submit content:

```bash
curl -X POST 'https://example.com/api/v1/submissions' \
  -H 'Authorization: Bearer tp_xxxx' \
  -F 'files=@image.jpg' \
  -F 'title=Example' \
  -F 'tags=illustration' \
  -F 'idempotency_key=example:123'
```

The example uses the single-bot path; use `/api/botN/v1/submissions` for multiple bots.
The response confirms admission to review; a human must approve it before publication.
Keep the same `idempotency_key` when retrying a request.

Full endpoints and fields: [HTTP API guide](docs/en/API.md).

## Mini App

The Mini App is an optional web front end for TelePost.

Regular users can submit, browse public content, and view their own submissions; reviewers and
admins get the moderation and admin features.

The Mini App shares the same backend, permissions, and submission state as the Bot. Setup:
[Mini App (Chinese)](docs/MINIAPP.md).

## Multi-bot & deployment

`run.py` manages separate bot processes, each with its own configuration and data directory.
The parent router exposes `/webhook/botN` and `/api/botN/v1/*`; child ports are local only.

Deploy on a local server, a VPS, Docker / Compose, or Fly.io. Use Polling when there is no public
HTTPS endpoint; use Webhook when you need to receive Telegram webhooks.

The Fly.io reference configuration uses 512 MiB of memory, a persistent volume, and an always-on
service. Content collectors such as PixivFlow run separately and submit through the HTTP API.
Official releases and production deployment: [Operations (Chinese)](docs/OPERATIONS.md).

## Documentation

Full documentation site: [TelePost documentation](https://redtidev1918.github.io/TelePost/#/en/).

| What you want to do | Guide |
| --- | --- |
| Install, upgrade, uninstall | [Install and deployment](docs/en/INSTALL.md) |
| Configuration | [Configuration](docs/en/CONFIGURATION.md) |
| Telegram commands | [Command reference](docs/en/COMMANDS.md) |
| HTTP API | [API guide](docs/en/API.md) |
| Mini App | [Mini App](docs/MINIAPP.md)（中文） |
| Webhook / Polling | [Run modes](docs/WEBHOOK_MODE.md)（中文） |
| Fly.io | [Fly.io deployment](docs/FLYIO_DEPLOYMENT.md)（中文） |
| Operations | [Operations](docs/OPERATIONS.md)（中文） |
| Troubleshooting | [Troubleshooting](docs/TROUBLESHOOTING.md)（中文） |
| Development | [Testing guide](docs/TESTING.md)（中文） |
| Contributing | [Contribution guide (Chinese)](CONTRIBUTING.md) |

## Related projects

- [PixivFlow](https://github.com/redtidev1918/PixivFlow) — Pixiv downloading, filtering, and automated collection
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) — combined PixivFlow + TelePost deployment

Both are optional integrations, not runtime dependencies of TelePost.

## License

[MIT License](LICENSE)
