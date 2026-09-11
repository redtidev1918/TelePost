# TelePost

**Language / 语言:** [中文](README.md) · English

A Telegram channel submission bot with chat submissions, a review queue, full-text search,
multi-bot support and an HTTP API.

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest)
[![Docs](https://img.shields.io/badge/Docs-documentation_site-6366f1?style=flat-square)](https://redtidev1918.github.io/TelePost/)

## What it does

- Upload, preview, edit, toggle anonymity/spoiler and publish entirely inside Telegram
- Independently control whether chat submissions and HTTP API submissions enter a private review group
- Search channel history, tags, your own submissions and a local hot list
- Run multiple mutually isolated bots from a single supervisor
- Accept external automated submissions through a Bearer-token API
- Let an AI agent read pending submissions and media safely (and suggest decisions) via the optional MCP sidecar
- Switch between Polling, Webhook and `AUTO` modes
- Run as an always-on service on Fly.io, receiving submissions over webhook instantly
- Enforce a resource budget before image decoding; high-risk originals degrade safely to a preview or document
- Keep review staging in a recoverable state and repair missing control messages on startup

## Quickest start

Download the single-file binary for your platform from the
[latest release](https://github.com/redtidev1918/TelePost/releases/latest); the first run
opens a configuration wizard:

```bash
chmod +x telepost-linux-x64
./telepost-linux-x64
```

Run from source:

```bash
git clone https://github.com/redtidev1918/TelePost.git
cd TelePost
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python run.py --setup
./.venv/bin/python run.py
```

Minimum configuration:

| Setting | Notes |
|---|---|
| `TOKEN` | From [@BotFather](https://t.me/BotFather) |
| `CHANNEL_ID` | `@channel` or `-100…`; the bot must be allowed to post |
| `OWNER_ID` | Recommended; enables sensitive admin commands and API token generation |

Environment variables take precedence over `config.ini`. See the
[configuration reference](docs/CONFIGURATION.md) for everything, and
[install & deploy](docs/INSTALL.md) for deployment options.

## Runtime modes

| Scenario | Recommended mode |
|---|---|
| Local, no public HTTPS | `RUN_MODE=POLLING` |
| Public HTTPS available | `RUN_MODE=WEBHOOK` |
| Let it decide | `RUN_MODE=AUTO` (default) |

Both Webhook and Polling expose `/live` (process alive), `/ready` (database, bot and review
service available), `/health` and `/api/v1/*`. Multi-bot entry points are always
`/api/botN/v1/*` and `/webhook/botN`; see [Webhook and Polling](docs/WEBHOOK_MODE.md).

## Fly.io with PixivFlow

Two apps on Fly.io:

```text
PixivFlow 256 MiB, stopped by default, woken on demand (executor)
        │ Flycast/Fly Proxy HTTP
        ▼
TelePost 512 MiB, always-on, two bots (business plane)
```

TelePost must stay resident: it holds the channel publishing credentials and the review
queue, so a stopped machine cannot answer submissions in time. The cost saving lives on the
PixivFlow executor side — stopped by default, woken on demand, exiting when the batch is
done. Do not colocate the two on one machine: they would share memory, lifecycle and failure
domain. Full steps: [Fly.io deployment](docs/FLYIO_DEPLOYMENT.md).

## Common entry points

- Users: `/submit`, `/search`, `/hot`, `/myposts`, `/mystats`
- Owner: `/botconfig`, `/gen_token`, `/delete_posts`
- Health check: `curl http://127.0.0.1:8080/health`
- Version tracing: `python run.py --version` (release, commit SHA and build date)
- Tests: `./.venv/bin/python -m pytest -q --no-cov -o log_cli=false`

All commands are in the [command reference](docs/COMMANDS.md); automation is covered by the
[HTTP API](docs/API.md).

## Documentation

| Document | Purpose |
|---|---|
| [Download](docs/download.md) | Single-file builds for Windows / macOS / Linux |
| [English docs index](docs/en/README.md) | English entry point for the documentation site |
| [Install & deploy](docs/INSTALL.md) | Single file, source, Docker, Fly.io |
| [Configuration](docs/CONFIGURATION.md) | Environment variables, `config.ini`, multi-bot |
| [Commands](docs/COMMANDS.md) | User, admin and owner commands |
| [HTTP API](docs/API.md) | Tokens, submissions, notifications and errors |
| [MCP review](docs/MCP_REVIEW.md) | AI-assisted review, media preview, read-only mode |
| [Webhook and Polling](docs/WEBHOOK_MODE.md) | Mode selection, routing and security |
| [Operations](docs/OPERATIONS.md) | Backups, upgrades, monitoring and releases |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Unresponsive bot, OOM, submissions, search |
| [Performance](docs/PERFORMANCE.md) | Resource tiers and capacity limits |
| [Testing](docs/TESTING.md) | Local and CI verification |
| [Contributing](CONTRIBUTING.md) | Development and commit conventions |
| [Changelog](CHANGELOG.md) | Released changes |

Internal design: [submission state machine](docs/internals/submission-flow.md) and
[soft deletion](docs/internals/moderation.md).

## License

[MIT License](LICENSE). Please file issues on
[GitHub Issues](https://github.com/redtidev1918/TelePost/issues).
