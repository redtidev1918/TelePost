# TelePost

**Language / 语言:** [中文](README.md) · English

Submission, moderation, and publishing for Telegram channels.

[Docs](https://redtidev1918.github.io/TelePost/) ·
[Releases](https://github.com/redtidev1918/TelePost/releases)

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

TelePost provides submission, moderation, search, and publishing for a Telegram channel. People can
submit through the Bot or the Mini App, and external programs can submit through the HTTP API.

## Features

- Submit, preview, edit, and publish through a Telegram Bot
- Submit and browse content through the Mini App
- Review queue, edit-before-publish, and spoiler moderation
- Channel history search, tags, and submission records
- HTTP API, Bearer tokens, and idempotent submissions
- Multiple isolated bots
- Polling, Webhook, and automatic run mode
- SQLite persistence for long-running use and restart recovery

## How it works

```text
Telegram Bot ─────┐
Telegram Mini App ├──→ TelePost ──→ Telegram Channel
HTTP API ─────────┘
```

Different entry points share the same submission, moderation, and publishing flow.

Review policy is configurable per entry point. Telegram Chat, Mini App, and HTTP API submissions do not
have to use the same moderation settings.

## Quick start

### Run a release

Download the build for your platform from the [Releases](https://github.com/redtidev1918/TelePost/releases) page.

On first run, configure:

- Bot Token
- Target channel ID
- Owner ID

Then send the Bot:

```text
/start
```

And submit with:

```text
/submit
```

Linux example:

```bash
chmod +x telepost-linux-x64
./telepost-linux-x64
```

### Docker

```bash
docker compose up -d
```

See [Install and deployment](docs/INSTALL.md).
## HTTP API

TelePost exposes an HTTP API for scripts and automation.

Create a token with `/gen_token` in the Bot, then submit content:

```bash
curl -X POST 'https://example.com/api/v1/submissions' \
  -H 'Authorization: Bearer tp_xxxx' \
  -F 'files=@image.jpg' \
  -F 'tags=illustration' \
  -F 'title=Example' \
  -F 'idempotency_key=example:123'
```

See the [HTTP API guide](docs/API.md) for the full endpoint and field reference.

TelePost does not depend on any specific upstream program. RSS, scrapers, CI, custom scripts, and
tools such as PixivFlow can all integrate through the HTTP API.

## Mini App

The Mini App is an optional front end for TelePost.

Regular users can submit content and view their own submissions; moderators and admins use the
corresponding moderation and admin features.

The Mini App reuses the TelePost backend and business flow and does not maintain a separate data or
moderation system.

See [Mini App](docs/MINIAPP.md) for setup and deployment.

## Multi-bot

TelePost can run multiple isolated bots in one process.

Each bot uses its own configuration, data directory, and Telegram entry point.

## Deployment

TelePost can run on:

- Local servers
- VPS
- Docker
- Fly.io
- Other environments that support Python or containers

Use Polling when there is no public HTTPS endpoint.

Use Webhook mode when you need to receive Telegram Webhooks.

See:

- [Install and deployment](docs/INSTALL.md)
- [Configuration](docs/CONFIGURATION.md)
- [Webhook / Polling](docs/WEBHOOK_MODE.md)
- [Fly.io deployment](docs/FLYIO_DEPLOYMENT.md)

## Related projects

[PixivFlow](https://github.com/redtidev1918/PixivFlow) is an independent Pixiv downloading and processing
tool that can deliver content through the TelePost HTTP API.

[pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) provides combined
deployment and workflow configuration for PixivFlow and TelePost. Workflow collaboration between the two
is described by Workflow Protocol v1, documented in that repository.

These projects are not runtime dependencies of TelePost.

## Documentation

| What you want to do        | Guide                                    |
| --------------------------- | ---------------------------------------- |
| Install and upgrade         | [Install and deployment](docs/INSTALL.md) |
| Configuration               | [Configuration](docs/CONFIGURATION.md)     |
| Telegram commands           | [Command reference](docs/COMMANDS.md)      |
| HTTP API                    | [API guide](docs/API.md)                   |
| Mini App                    | [Mini App](docs/MINIAPP.md)                |
| Webhook / Polling           | [Run modes](docs/WEBHOOK_MODE.md)          |
| Operations                  | [Operations](docs/OPERATIONS.md)           |
| Troubleshooting             | [Troubleshooting](docs/TROUBLESHOOTING.md)  |
| Development and contributing | [Contributing](CONTRIBUTING.md)           |

## License

[MIT License](LICENSE)
