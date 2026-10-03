# TelePost

**Language / 语言:** [中文](README.md) · English

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/)

> Submission, moderation, and publishing for Telegram channels.

**Install:** `pip install telepost-bot` · **Run:** `telepost` · **Python import:** `telepost`

People can submit from the **Bot** or the **Mini App**, and external programs can submit through the **HTTP API**; every entry point shares the same flow.

## ✨ Features

- **Bot submissions** — preview, edit, publish; images, video, audio, and files
- **Mini App** — submit, browse content, moderate, and manage; the empty review queue links to recently processed items and review history
- **Review queue** — review, edit-before-publish, and spoiler moderation
- **Novel online reading** — TXT novels can optionally ship a Telegraph reading page; the channel root keeps the full title/summary/tags, and the read-online entrance is a single non-duplicated visual card; with TelePress 0.16.4, long-form pages target ~20,000 source characters for fewer reading interruptions
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

## 🚀 Start in 30 seconds

```bash
pip install telepost-bot
telepost --setup
telepost
```

The first `telepost` run starts the setup wizard for the **Bot Token**, **Channel ID** and
**Owner ID**; after that, running `telepost` starts with the saved configuration.

Two prerequisites: create (or reuse) a bot with [@BotFather](https://t.me/BotFather), then add it
to your target channel and grant it permission to post. Once it is running, send `/start` to the
bot and use `/submit` for your first submission.

| Name | Value |
| --- | --- |
| PyPI project | `telepost-bot` |
| CLI command | `telepost` |
| Python import | `telepost` |
| Python | 3.10+ |
| System dependency | `pyvips` needs OS-level libvips (macOS `brew install vips`, Debian/Ubuntu `sudo apt-get install libvips42`) |

## 📦 Install options

| Order | Method | Fits | Entry point |
| --- | --- | --- | --- |
| 1 | **PyPI (recommended)** | Anyone with a Python environment, scripting and automated deployment | `pip install telepost-bot` |
| 2 | Release single file | Users who do not want to install Python | Download the build for your platform from [Releases](https://github.com/redtidev1918/TelePost/releases) |
| 3 | Docker / Compose | Container deployments | `ghcr.io/redtidev1918/telepost:<version>` |
| 4 | Source | Development, self-managed VPS | `git clone`, then `python run.py` |

The release single file needs no Python environment: download it, then
`chmod +x telepost-linux-x64 && ./telepost-linux-x64`.

Upgrading is always `pip install --upgrade telepost-bot`; containers and Fly move to the new
release image `ghcr.io/redtidev1918/telepost:<version>` (never `latest` in production).
Versions always come from an official release. Full install, configuration, upgrade and uninstall
steps: [Install and deployment](docs/en/INSTALL.md).

## Releasing

Day-to-day development is **PR → `main`**. A formal release has a manual gate: **merge the Release
PR**. Everything after that runs serially in the pipeline, and any critical stage failing fails the
release outright:

```text
merge Release PR → vX.Y.Z → GitHub Release → GHCR image → PyPI telepost-bot
                                                          ↓
                              Fly.io production deploy ← verify /health version + bot1/bot2
```

A release can therefore never look successful while production still serves an older version. Both
reference pages below are Chinese-only: [docs/FLYIO_DEPLOYMENT.md § 0](docs/FLYIO_DEPLOYMENT.md#0-正式发版路径唯一推荐)（中文） and
[Operations · release flow](docs/OPERATIONS.md#正式发布流程)（中文）.

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

See the [HTTP API guide](docs/en/API.md) for the full endpoint and field reference.

TelePost does not depend on any specific upstream program. RSS, scrapers, CI, custom scripts, and tools such as PixivFlow can all integrate through the HTTP API.

## 📱 Mini App

The Mini App is an optional front end: regular users can submit and view their own submissions; moderators and admins use the corresponding moderation and admin features.

It reuses the TelePost backend and business flow and does not maintain a separate data or moderation system. See [Mini App](docs/MINIAPP.md)（中文） for setup.

## 🏢 Multi-bot & deployment

- **Multi-bot**: run multiple isolated bots in one process, each with its own configuration, data directory, and Telegram entry point.
- **Deployment**: local servers, VPS, Docker, Fly.io, or any environment that supports Python / containers.
- **Run modes**: use **Polling** when there is no public HTTPS endpoint; use **Webhook** when you need to receive Telegram Webhooks.

See [Install and deployment](docs/en/INSTALL.md) · [Configuration](docs/en/CONFIGURATION.md) ·
[Webhook / Polling](docs/WEBHOOK_MODE.md)（中文） · [Fly.io deployment](docs/FLYIO_DEPLOYMENT.md)（中文）.

## 📚 Documentation

English pages exist for install, commands, configuration and the HTTP API; the remaining guides are
still Chinese-only and are marked **（中文）**.

| What you want to do         | Guide                                     |
| ---------------------------- | ------------------------------------------ |
| Install and upgrade          | [Install and deployment](docs/en/INSTALL.md)   |
| Configuration                | [Configuration](docs/en/CONFIGURATION.md)       |
| Telegram commands            | [Command reference](docs/en/COMMANDS.md)        |
| HTTP API                     | [API guide](docs/en/API.md)                     |
| Mini App                     | [Mini App](docs/MINIAPP.md)（中文）             |
| Webhook / Polling            | [Run modes](docs/WEBHOOK_MODE.md)（中文）       |
| Official release flow        | [Operations](docs/OPERATIONS.md#正式发布流程)（中文） |
| Operations                   | [Operations](docs/OPERATIONS.md)（中文）        |
| Troubleshooting              | [Troubleshooting](docs/TROUBLESHOOTING.md)（中文） |
| Development and contributing | [Contributing](CONTRIBUTING.md)（中文）         |

## 🔗 Related projects

- [PixivFlow](https://github.com/redtidev1918/PixivFlow) — an independent Pixiv downloading and processing tool that can deliver content through the TelePost HTTP API.
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) — combined deployment and workflow configuration for PixivFlow and TelePost.

These are optional integrations, not runtime dependencies of TelePost.

## 📄 License

[MIT License](LICENSE)