# TelePost

**Language / 语言:** [中文](README.md) · English

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

> Submission, moderation, and publishing for Telegram channels.

People can submit from the **Bot** or the **Mini App**, and external programs can submit through the **HTTP API**; every entry point shares the same flow.

## ✨ Features

- **Bot submissions** — preview, edit, publish; images, video, audio, and files
- **Mini App** — submit, browse content, moderate, and manage; the empty review queue links to recently processed items and review history
- **Review queue** — review, edit-before-publish, and spoiler moderation
- **Novel online reading** — TXT novels can optionally ship a Telegraph reading page; the channel root keeps the full title/summary/tags, and the read-online entrance is a single non-duplicated visual card
- **Media proxy rewriting** — restricted image-host URLs can be rewritten through a self-hosted public reverse proxy before delivery
- **Channel search** — history search, tags, and submission records
- **HTTP API** — Bearer tokens and idempotent submissions for scripts and automation
- **Multi-bot isolation** — run multiple independent bots in one process
- **Flexible run modes** — Polling, Webhook, or automatic
- **SQLite persistence** — stable long-running use with restart recovery

## 🔁 How it works

```text
Telegram Bot ─────┐
Telegram Mini App ├──→ TelePost ──→ Telegram Channel
HTTP API ─────────┘
```

Submission, moderation, and publishing share one flow. Review policy is configurable per entry point (Chat / Mini App / HTTP API).

## 🚀 Quick start

**Get running in four steps:**

1. Create (or reuse) a bot with [@BotFather](https://t.me/BotFather)
2. Add the bot to your target channel and grant it permission to post
3. Download the build for your platform from the [Releases](https://github.com/redtidev1918/TelePost/releases) page
4. Run it once and follow the wizard to enter **Bot Token**, **Channel ID**, and **Owner ID**

First launch on Linux:

```bash
chmod +x telepost-linux-x64
./telepost-linux-x64
```

Once running, send `/start` to the bot, then use `/submit` for your first submission.

**Or use Docker:**

```bash
docker compose up -d
```

> Full install, configuration, and upgrade steps: [Install and deployment](docs/INSTALL.md).

## 🌐 HTTP API

For scripts and automation: generate a token with `/gen_token` in the bot, then submit content:

```bash
curl -X POST 'https://example.com/api/v1/submissions' \
  -H 'Authorization: Bearer tp_xxxx' \
  -F 'files=@image.jpg' \
  -F 'tags=illustration' \
  -F 'title=Example' \
  -F 'idempotency_key=example:123'
```

See the [HTTP API guide](docs/API.md) for the full endpoint and field reference.

TelePost does not depend on any specific upstream program. RSS, scrapers, CI, custom scripts, and tools such as PixivFlow can all integrate through the HTTP API.

## 📱 Mini App

The Mini App is an optional front end: regular users can submit and view their own submissions; moderators and admins use the corresponding moderation and admin features.

It reuses the TelePost backend and business flow and does not maintain a separate data or moderation system. See [Mini App](docs/MINIAPP.md) for setup.

## 🏢 Multi-bot & deployment

- **Multi-bot**: run multiple isolated bots in one process, each with its own configuration, data directory, and Telegram entry point.
- **Deployment**: local servers, VPS, Docker, Fly.io, or any environment that supports Python / containers.
- **Run modes**: use **Polling** when there is no public HTTPS endpoint; use **Webhook** when you need to receive Telegram Webhooks.

See [Install and deployment](docs/INSTALL.md) · [Configuration](docs/CONFIGURATION.md) · [Webhook / Polling](docs/WEBHOOK_MODE.md) · [Fly.io deployment](docs/FLYIO_DEPLOYMENT.md).

## 📚 Documentation

| What you want to do         | Guide                                     |
| ---------------------------- | ------------------------------------------ |
| Install and upgrade          | [Install and deployment](docs/INSTALL.md)   |
| Configuration                | [Configuration](docs/CONFIGURATION.md)       |
| Telegram commands            | [Command reference](docs/COMMANDS.md)        |
| HTTP API                     | [API guide](docs/API.md)                     |
| Mini App                     | [Mini App](docs/MINIAPP.md)                  |
| Webhook / Polling            | [Run modes](docs/WEBHOOK_MODE.md)            |
| Operations                   | [Operations](docs/OPERATIONS.md)             |
| Troubleshooting              | [Troubleshooting](docs/TROUBLESHOOTING.md)    |
| Development and contributing | [Contributing](CONTRIBUTING.md)             |

## 🔗 Related projects

- [PixivFlow](https://github.com/redtidev1918/PixivFlow) — an independent Pixiv downloading and processing tool that can deliver content through the TelePost HTTP API.
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) — combined deployment and workflow configuration for PixivFlow and TelePost.

These are optional integrations, not runtime dependencies of TelePost.

## 📄 License

[MIT License](LICENSE)