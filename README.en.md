# TelePost

**Language / 语言:** [中文](README.md) · English

> **Submissions, moderation, and publishing for Telegram channels.**

📚 [Documentation](https://redtidev1918.github.io/TelePost/)

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest)
[![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Documentation](https://img.shields.io/badge/docs-redtidev1918.github.io-6366f1)](https://redtidev1918.github.io/TelePost/)

TelePost is a self-hosted service for submissions, moderation, and publishing on Telegram channels.
People submit from the Telegram Bot or the Mini App, and other programs submit through the HTTP API —
every entry point ends up in the same submission, moderation, and publishing flow.

```text
Telegram Bot ─────┐
Telegram Mini App ├──→ TelePost ──→ Telegram Channel
HTTP API ─────────┘
```

## Features

- Bot submissions: images, video, audio, and files
- Review queue: review, edit-before-publish, spoiler control, review history
- Mini App: submit, browse content, view your own submissions, moderate, and manage
- HTTP API: file upload, Bearer tokens, idempotent submissions
- Channel search: past content, tags, and submission records
- Novels / long-form text: TXT uploads can produce a Telegraph reading page
- Multi-bot: several isolated bots in a single process
- SQLite: persistent storage that survives restarts

## Quick start

```bash
pip install telepost-bot
telepost --setup
telepost
```

Needs Python 3.10+; image handling requires the system-level libvips library.

Before you start: create a bot with [@BotFather](https://t.me/BotFather), add it to your target
channel, and grant it permission to post.

The first `telepost` run opens the setup wizard for the **Bot Token**, **Channel ID**, and
**Owner ID**; after that, `telepost` starts with the saved configuration. Once it is running, send
`/start` to the bot and use `/submit` for your first submission.

| Name | Value |
| --- | --- |
| PyPI project | `telepost-bot` |
| CLI command | `telepost` |
| Python import | `telepost` |
| Python | 3.10+ |

## Installation

| Method | Usage | Fits |
| --- | --- | --- |
| PyPI | `pip install telepost-bot` | Regular installs, servers, scripts |
| Release | [Download](https://github.com/redtidev1918/TelePost/releases) | No Python installation needed |
| Docker | `ghcr.io/redtidev1918/telepost:<version>` | Container deployments |
| Source | `git clone` | Development, self-managed hosts |

In production, pin a release version; do not use `latest`. Full install, upgrade, and uninstall
steps: [Install and deployment](docs/en/INSTALL.md).

## HTTP API

Generate a token with `/gen_token` in the bot, then submit content:

```bash
curl -X POST 'https://example.com/api/v1/submissions' \
  -H 'Authorization: Bearer tp_xxxx' \
  -F 'files=@image.jpg' \
  -F 'title=Example' \
  -F 'tags=illustration' \
  -F 'idempotency_key=example:123'
```

Works well for RSS, scrapers, CI, scheduled jobs, custom scripts, and tools such as PixivFlow.

Full endpoints and fields: [HTTP API guide](docs/en/API.md).

## Mini App

The Mini App is an optional web front end for TelePost.

Regular users can submit, browse public content, and view their own submissions; reviewers and
admins get the moderation and admin features.

The Mini App shares the same backend, permissions, and submission state as the Bot. Setup:
[Mini App](docs/MINIAPP.md)（中文）.

## Multi-bot & deployment

TelePost can run several isolated bots in one process, each with its own configuration, data
directory, and Telegram entry point.

Deploy on a local server, a VPS, Docker / Compose, or Fly.io. Use Polling when there is no public
HTTPS endpoint; use Webhook when you need to receive Telegram webhooks.

[Install and deployment](docs/en/INSTALL.md) · [Configuration](docs/en/CONFIGURATION.md) ·
[Run modes](docs/WEBHOOK_MODE.md)（中文） · [Fly.io](docs/FLYIO_DEPLOYMENT.md)（中文）

Official releases and production deployment: [Operations](docs/OPERATIONS.md)（中文）.

## Documentation

Full documentation site: **https://redtidev1918.github.io/TelePost/**

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

## Related projects

- [PixivFlow](https://github.com/redtidev1918/PixivFlow) — Pixiv downloading, filtering, and automated collection
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) — combined PixivFlow + TelePost deployment

Both are optional integrations, not runtime dependencies of TelePost.

## License

[MIT License](LICENSE)
