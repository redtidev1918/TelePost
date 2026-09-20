# TelePost

**Language / 语言:** [中文](README.md) · English

**A submission, moderation, and automated publishing platform for Telegram channels.**

📖 Full documentation: <https://redtidev1918.github.io/TelePost/>

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Docs](https://img.shields.io/badge/Docs-documentation-6366f1?style=flat-square)](https://redtidev1918.github.io/TelePost/)

People can submit through Telegram chat or a Mini App, moderators can review and manage content,
and external programs can send submissions through the HTTP API. Every entry point shares the same
submission, review, search, publishing, and status workflow.
TelePost runs on its own; PixivFlow, Fly.io, the Mini App, and multi-bot mode are all optional.

## Where it fits

| Use case | Flow | Good for |
| --- | --- | --- |
| Community channel | Member → Bot / Mini App → direct publish or review → channel | Community submissions, calls for work, UGC channels |
| Automated content channel | PixivFlow or another tool → HTTP API → review → channel | Automated collection with human oversight |
| Custom automation | RSS / scraper / CI / your script → HTTP API → channel | Using Telegram as the publishing end of an existing workflow |

```mermaid
flowchart LR
    Chat["People / Telegram Chat"] --> TP["TelePost"]
    Mini["Mini App"] --> TP
    Auto["Automation / HTTP API"] --> TP
    TP --> Ch["Telegram Channel"]
```

Chat, Mini App, and API are not separate systems: they all enter the same TelePost workflow. The Mini App
is an optional richer interface, and moderation is a configurable policy. Native chat submissions publish
directly by default; set `CHAT_REVIEW_REQUIRED=true` to send them to the review queue.

## Start in 30 seconds

1. Create a bot with [@BotFather](https://t.me/BotFather), add it to the target channel, and grant permission to post.
2. Download the single-file program for your platform from the [latest release](https://github.com/redtidev1918/TelePost/releases/latest).
3. Run it once and enter the Bot Token, channel ID, and the recommended Owner ID in the setup wizard.
4. Send `/start` to the bot, then use `/submit` for your first submission.

Linux example:

```bash
chmod +x telepost-linux-x64
./telepost-linux-x64
```

Docker and source installs are also available. See [Install and deployment](docs/INSTALL.md) for downloads,
platform-specific setup, and upgrades.

## Core capabilities

- **Submission and publishing:** images, video, audio, and files, with preview, editing, tags, anonymity, and spoilers.
- **Source-trust-based moderation:** native Chat submissions publish directly by default (opt-in review via `CHAT_REVIEW_REQUIRED`); API/automation submissions always enter the review queue; Mini App review is controlled independently by `MINIAPP_REVIEW_REQUIRED`. Reviewers can edit before publishing; the original submission stays untouched — see [Editorial revision](docs/CONFIGURATION.md).
- **Mini App:** contributors submit and track their own posts; moderators work through the review queue and details.
- **HTTP API:** Bearer tokens, idempotency keys, uploads, and Telegram `file_id` reuse for scripts and services.
- **Channel management:** search channel history, tags, personal submissions, and a local hot list, plus admin commands.
- **Multi-bot:** run multiple isolated bots under one supervisor with separate configuration and data directories.
- **Runtime choices:** Polling, Webhook, or automatic selection, without tying deployment to one cloud provider.

## Telegram Mini App

```text
Contributor: Telegram → Mini App → Submit / My submissions
Moderator:   Telegram → Mini App → Review queue / Review details
```

The Mini App reuses the Bot's identity, submission, and moderation workflow; it is not a second backend.
Disabling it does not affect chat submissions or the HTTP API. The current Mini App submission UI expects
the review workflow, so it is enabled by `MINIAPP_REVIEW_REQUIRED=true` (independent of the API flag). See the [Mini App guide](docs/MINIAPP.md)
for setup, same-origin hosting, and security requirements.

## HTTP API and automation

External programs can use TelePost as a Telegram submission, moderation, and publishing backend. First,
the Owner runs `/gen_token <name>` in the Bot, then sends content:

```bash
curl -X POST 'https://example.com/api/v1/submissions' \
  -H 'Authorization: Bearer tp_xxxx' \
  -F 'files=@image.jpg' \
  -F 'tags=illustration,featured' \
  -F 'title=Example' \
  -F 'idempotency_key=my-source:123'
```

See the [HTTP API guide](docs/API.md) for multi-bot paths, moderation policy, response semantics, and all fields.

### Working with other tools

TelePost runs on its own and can accept submissions from any upstream system that can call an HTTP API:

```mermaid
flowchart LR
    PixivFlow["PixivFlow"] --> TelePost["TelePost"]
    RSS["RSS / scrapers"] --> TelePost
    CI["Scripts / CI"] --> TelePost
    TelePost --> TG["Telegram"]
```

[PixivFlow](https://github.com/redtidev1918/PixivFlow) is an independent Pixiv downloading, filtering, and
collection tool. It can send results to TelePost, save them locally, or deliver them elsewhere.
**TelePost does not depend on PixivFlow.**

The optional [MCP sidecar](docs/MCP_REVIEW.md) lets an AI agent read pending submissions and suggest decisions;
a human still explicitly confirms publishing.

## Running and deployment

TelePost can run locally, on a VPS, with Docker, on Fly.io, or anywhere else that can run Python or containers:

| Environment | Suggested entry point |
| --- | --- |
| Local or no public HTTPS | `RUN_MODE=POLLING` |
| VPS / container platform | Polling, or Webhook behind public HTTPS |
| Fly.io | Pinned image, persistent volume, and Webhook |

A single bot is the simplest starting point. Mini App, multi-bot, Webhook, and PixivFlow integration are all
optional. See [Install and deployment](docs/INSTALL.md) for general setup and
[Fly.io deployment](docs/FLYIO_DEPLOYMENT.md) for platform-specific details.

## Built for long-running use

- Submission and publishing use idempotent semantics, so retries do not silently duplicate content.
- Review state, sessions, and runtime policy persist and recover across restarts.
- `/live`, `/ready`, and `/health` provide layered health checks.
- Image processing enforces a resource budget and safely falls back to previews or documents.

## Documentation

| What you want to do | Guide |
| --- | --- |
| Download and get started | [Download](docs/en/download.md) · [Install and deployment](docs/INSTALL.md) |
| Configure a bot, review, or multi-bot | [Configuration](docs/CONFIGURATION.md) |
| Browse Telegram commands | [Command reference](docs/COMMANDS.md) |
| Connect automation | [HTTP API](docs/API.md) |
| Enable the Mini App | [Mini App](docs/MINIAPP.md) |
| Configure Webhook or Fly.io | [Webhook and Polling](docs/WEBHOOK_MODE.md) · [Fly.io deployment](docs/FLYIO_DEPLOYMENT.md) |
| Back up, upgrade, or troubleshoot | [Operations](docs/OPERATIONS.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) |
| Contribute | [Contributing](CONTRIBUTING.md) · [Full documentation index](docs/en/README.md) |

Detailed guides are currently written mainly in Chinese; commands, paths, and configuration names are identical.

## Related projects

- [PixivFlow](https://github.com/redtidev1918/PixivFlow): Pixiv downloading, filtering, scheduling, and HTTP delivery.
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy): deployment and operations toolkit for combining PixivFlow and TelePost across Docker, VPS, and cloud environments.

## Acknowledgements

TelePost builds on:

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot): the bot framework behind both Polling and Webhook modes.
- [aiohttp](https://github.com/aio-libs/aiohttp): the server for Webhook, Polling, and the Mini App API.
- [aiosqlite](https://github.com/omnilib/aiosqlite) · [Whoosh](https://github.com/mchaput/whoosh) · [jieba](https://github.com/fxsjy/jieba): storage and full-text search (CJK tokenization is optional).
- [Pillow](https://github.com/python-pillow/Pillow) · [psutil](https://github.com/giampaolo/psutil): compressing oversized originals and runtime memory analysis.
- [init-data-py](https://github.com/nimaxin/init-data-py): Mini App `initData` validation.
- Mini App front end: [React](https://react.dev) · [@telegram-apps/sdk](https://github.com/telegram-mini-apps-dev/telegram-apps) · [Telegram UI](https://github.com/telegram-mini-apps-dev/TelegramUI) · [Uppy](https://uppy.io).
- [tg_searcher](https://github.com/SharzyL/tg_searcher) (MIT): the original implementation our full-text search was integrated from.
- [TelePress](https://github.com/redtidev1918/TelePress): the optional Telegraph preview for TXT novels; rendering and pagination stay in TelePress.
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk): the optional AI review sidecar.

Interfaces and conventions: [Telegram Bot API](https://core.telegram.org/bots/api) · [Telegram Mini Apps](https://core.telegram.org/bots/webapps) · [Telegraph API](https://telegra.ph/api) · [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · [Semantic Versioning](https://semver.org/).

## Contributing and license

Please report problems in [GitHub Issues](https://github.com/redtidev1918/TelePost/issues) and see
[CONTRIBUTING.md](CONTRIBUTING.md) for code contributions. TelePost is available under the [MIT License](LICENSE).
