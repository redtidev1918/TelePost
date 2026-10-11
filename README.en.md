# TelePost

**Language / 语言:** [中文](README.md) · English

Submissions, moderation, and publishing for Telegram channels.

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest)
[![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Documentation](https://img.shields.io/badge/docs-redtidev1918.github.io-6366f1)](https://redtidev1918.github.io/TelePost/)

TelePost runs on your own computer or server. People submit through bot private chats or the Mini App;
programs submit through the HTTP API. You can publish chat submissions directly or have reviewers approve them first.

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

The default file denylist blocks executables, common scripts and archives. It does not block common formats
such as images, videos and TXT/PDF/MD documents. Administrators can change the [file type denylist](docs/en/CONFIGURATION.md).
It checks filenames and MIME types, not file contents for malware.

## Choose an installation method

| Your environment | Method | Start here |
| --- | --- | --- |
| Local computer, without Python | Standalone program | [Choose a platform and download](https://redtidev1918.github.io/TelePost/#/en/download) |
| Existing Python environment | pip | [Quick start](#quick-start) |
| Services managed with Docker | Docker / Compose | [Container deployment](docs/en/INSTALL.md#3-docker--compose) |
| Code changes | Source | [Development and testing (Chinese)](docs/TESTING.md) |

Pin a [formal release version](https://github.com/redtidev1918/TelePost/releases/latest) in production.
See [Install and deployment](docs/en/INSTALL.md) for installation, upgrades and removal.

## Quick start

### 1. Prepare a bot, channel and review group

| Setting | What to use |
| --- | --- |
| Bot Token | Create a bot with [@BotFather](https://t.me/BotFather) to get its token |
| Channel ID | The destination channel's `@username` or numeric ID |
| Owner ID | Your personal numeric user ID, not a group or channel ID |
| Review Chat ID | A separate review group's numeric ID; it must differ from the channel |

Add the bot as a channel administrator with permission to post. Add it to the review group and allow it to send messages.
The default configuration needs a review group. For private-chat-only use, see the [installation guide](docs/en/INSTALL.md#1-pip-install-recommended).

### 2. Install and run setup

**Windows standalone program**: download and extract it to a folder you will keep, then open PowerShell in that folder:

```powershell
.\telepost-windows-x64.exe --setup
```

See the [installation guide](docs/en/INSTALL.md) for standalone commands on other platforms.

**Python / pip**: install Python 3.10+ and [system libvips](docs/en/INSTALL.md#libvips-system-dependency) first.
On Linux / macOS, install in a virtual environment:

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

The PyPI package is `telepost-bot`; the installed command is `telepost`.
The wizard asks for Bot Token, Channel ID and Owner ID.

### 3. Configure review and start the bot

Open the generated `config.ini`, keep the existing settings, and set or add these values under `[BOT]`.
Replace the placeholder with your review group's numeric ID and save the file as UTF-8:

```ini
LANGUAGE = en
REVIEW_CHAT_ID = <your-review-group-ID>
```

| Installation | Start command |
| --- | --- |
| Windows standalone | `.\telepost-windows-x64.exe` |
| Windows pip | `.\.venv\Scripts\telepost.exe` |
| Linux / macOS pip | Activate the virtual environment, then run `telepost` |

For pip installs, always start from the same directory: configuration and data are stored there.
When upgrading the standalone program, keep `config.ini` and `data` beside it.
After starting, send `/start` in a private chat with the bot and check that it replies.

### 4. Send your first submission

1. Chat privately with the bot, send `/start`, then tap “New submission” or send `/submit`.
2. Upload images, videos, audio or files, then send `/done_media`.
3. Add tags in the preview. Edit the title, note, link, anonymous mode or spoiler settings as needed.
4. Check and tap “Publish”; with chat review enabled, the button says “Submit for review”.

Send `/cancel` at any step to cancel. See the [command guide](docs/en/COMMANDS.md).

## Review defaults

| Entry point | Default behavior | Configuration |
| --- | --- | --- |
| Bot private chat | Publish after the user confirms | Set `CHAT_REVIEW_REQUIRED=true` to require review |
| Mini App | Submit for review, then publish after approval | Controlled separately by `MINIAPP_REVIEW_REQUIRED` |
| HTTP API | Submit for review, then publish after approval | Review is always required |

To review chat submissions too, add `CHAT_REVIEW_REQUIRED = true` under `[BOT]` and restart.
See [configuration](docs/en/CONFIGURATION.md) for the remaining settings.

## Chinese bots and English bots

One program supports both languages, with Chinese as the default. For a single bot, set
`LANGUAGE = en` under `[BOT]` in `config.ini`, or set `BOT_LANGUAGE=en` in your deployment
environment. Restart to apply the change.

To run a Chinese bot and an English bot together, configure their separate credentials,
channels and review groups, then set:

```dotenv
BOT1_LANGUAGE=zh
BOT2_LANGUAGE=en
```

Add these settings to your deployment environment or Compose `.env`. Welcome messages, help,
menus, submission prompts and review actions use the selected language. The Mini App follows
its bot by default; submitted titles, tags and content keep their original language.
See [Bot language and multiple bots](docs/en/CONFIGURATION.md#bot-language-and-multiple-bots).

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

## Documentation

Full documentation site: [TelePost documentation](https://redtidev1918.github.io/TelePost/#/en/).

| What you want to do | Guide |
| --- | --- |
| Install, upgrade, uninstall | [Install and deployment](docs/en/INSTALL.md) |
| Configuration | [Configuration](docs/en/CONFIGURATION.md) |
| Telegram commands | [Command reference](docs/en/COMMANDS.md) |
| HTTP API | [API guide](docs/en/API.md) |
| Mini App | [Mini App (Chinese)](docs/MINIAPP.md) |
| Webhook / Polling | [Run modes (Chinese)](docs/WEBHOOK_MODE.md) |
| Fly.io | [Fly.io deployment (Chinese)](docs/FLYIO_DEPLOYMENT.md) |
| Operations | [Operations (Chinese)](docs/OPERATIONS.md) |
| Troubleshooting | [Troubleshooting (Chinese)](docs/TROUBLESHOOTING.md) |
| Development | [Testing guide (Chinese)](docs/TESTING.md) |
| Contributing | [Contribution guide (Chinese)](CONTRIBUTING.md) |

## Related projects

- [PixivFlow](https://github.com/redtidev1918/PixivFlow) — Pixiv downloading, filtering, and automated collection
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) — combined PixivFlow + TelePost deployment

Both are optional integrations, not runtime dependencies of TelePost.

## License

[MIT License](LICENSE)
