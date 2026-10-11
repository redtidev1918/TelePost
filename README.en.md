# TelePost

**Language / 语言:** [中文](https://github.com/redtidev1918/TelePost/blob/main/README.md) · English

TelePost is an open-source, self-hosted submission and moderation bot for Telegram channels.
Readers send work through a private chat with the bot or through the Mini App; you check and edit
it in a moderation group before it is published; automation submits through the HTTP API.

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest)
[![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/redtidev1918/TelePost/blob/main/LICENSE)
[![Documentation](https://img.shields.io/badge/docs-redtidev1918.github.io-6366f1)](https://redtidev1918.github.io/TelePost/#/en/)

[Download](https://github.com/redtidev1918/TelePost/blob/main/docs/en/download.md) · [Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md) · [Documentation](https://redtidev1918.github.io/TelePost/#/en/)

## Screenshots

![Bot private-chat main menu](https://raw.githubusercontent.com/redtidev1918/TelePost/main/docs/assets/screenshots/bot-menu.jpg)

The bot's main menu. Users can submit, see their own submissions, search the channel, and browse
rankings and tags.

<!-- Real screenshots still to add: (1) submission preview and editing; (2) moderation actions in the review group; (3) the Telegram Mini App. -->

## What it does

- **Submitting** — send photos, videos, GIFs, audio or files to the bot in a private chat, or send
  text only. Uploads end in a preview where the sender adds tags and can edit the title, note and
  source link, and turn anonymous or spoiler mode on.
- **Review and publishing** — with review enabled, submissions first go to your private moderation
  group with a preview and **✅ Publish to channel / ❌ Reject / 🔇 Spoiler** buttons. API
  submissions can also use **🔄 Refetch / replace** to ask the upstream service for a different
  candidate; the replacement is reviewed again.
- **Mini App (optional)** — the same program ships a web submission front end. Users can browse
  published content, submit and follow their own submissions on a phone; reviewers and admins get
  the review queue, editing of text and attachment order, role management and the blacklist.
  Enabling it takes a public HTTPS address, a `MINIAPP_SESSION_SECRET` and a Telegram-side entry;
  installing the bot alone does not give you the Mini App. See the
  [Mini App documentation](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md) (Chinese).
- **Search and tags** — `/search` searches by keyword with `#tag` and time-range filters, `/tags`
  opens the tag cloud.
- **Rankings and statistics** — `/hot` for the all-time ranking, `/hotweek` for this week,
  `/mystats` for personal statistics. `/schedule add weekly-hot` posts a ranking on a weekly
  schedule.
- **Automated submissions** — an owner generates an API token with `/gen_token`; programs then
  upload files over the HTTP API. An `idempotency_key` makes retrying a request safe: it never
  creates a second review or a second publication.
- **AI-assisted review (MCP)** — an optional sidecar lets an MCP-capable agent list pending
  submissions, read their details and media, follow your review policy and suggest decisions. It
  only performs a moderation action after you ask for that exact action.
- **Multiple bots and self-hosting** — several bots can run on one machine with their own
  channels, moderation groups, languages and data directories. Deploy as a standalone program,
  with pip, in Docker or on Fly.io, in Polling or Webhook mode. The review queue, idempotency
  records and submission sessions live in the local `data` directory and survive a restart.

## How it works

```text
User submits ──▶ Preview and edit ──▶ Review (optional) ──▶ Published to the channel
```

1. A user uploads content in the bot's private chat or in the Mini App, or sends text only.
2. `/done_media` opens the preview: add tags, and adjust title, note, link, anonymous and spoiler
   as needed.
3. The publishing button then either publishes directly or queues the submission for review,
   depending on the entry point.
4. Approved content is published to the channel; reviewers can reject, toggle the spoiler or block
   a submitter at any point.

The three entry points do not share one default:

| Entry point | Default | Configuration |
| --- | --- | --- |
| Bot private chat | Publish after the user confirms | Set `CHAT_REVIEW_REQUIRED=true` to require review |
| Mini App | Submit for review, publish after approval | Controlled separately by `MINIAPP_REVIEW_REQUIRED` |
| HTTP API | Always submitted for review, cannot be turned off | A human must approve it |

Automated submissions (including PixivFlow and other upstream services) always need human
approval; `API_REVIEW_REQUIRED` is kept for compatibility only and never lets the API skip review.
The default setup uses a moderation group, so `REVIEW_CHAT_ID` is required. For private-chat-only
use, set both `API_REVIEW_REQUIRED` and `MINIAPP_REVIEW_REQUIRED` to `false` explicitly and leave
`CHAT_REVIEW_REQUIRED=false`.

## Quick start

### 1. Prepare four things

| What | Where to get it |
| --- | --- |
| Bot token | Create a bot with [@BotFather](https://t.me/BotFather) |
| Channel ID | The destination channel, as `@username` or a numeric ID starting with `-100` |
| Owner ID | Your own numeric Telegram user ID; not a group or channel ID |
| Review Chat ID | A numeric ID of a **separate** private moderation group, not the channel |

Add the bot to the channel as an administrator with permission to post, then add it to the
moderation group and make sure it can send messages there.

### 2. Install

**Standalone program (no Python needed)** — take the file for your platform from the
[download page](https://github.com/redtidev1918/TelePost/blob/main/docs/en/download.md) or
[Releases](https://github.com/redtidev1918/TelePost/releases/latest) and put it in a folder you
will keep:

| Platform | File |
| --- | --- |
| Windows x64 | `telepost-windows-x64.exe` |
| Linux x64 | `telepost-linux-x64` |
| macOS (Apple Silicon) | `telepost-macos-arm64` |

On Windows, open PowerShell in that folder and run the setup wizard:

```powershell
.\telepost-windows-x64.exe --setup
```

On Linux / macOS, make it executable the first time (the example uses Linux; on macOS use
`telepost-macos-arm64` instead):

```bash
chmod +x telepost-linux-x64
./telepost-linux-x64 --setup
```

**pip (Python 3.10+ already installed)**:

```bash
python -m pip install telepost-bot
telepost --setup
```

The PyPI package is `telepost-bot`; the installed command is `telepost`. Installing the system
[libvips](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md#libvips-system-dependency) is recommended: without it everything
still works, but streaming downscale of very large images is unavailable.

**Docker** — create a `.env` in the repository root (`TOKEN`, `CHANNEL_ID`, `OWNER_ID`,
`REVIEW_CHAT_ID` and a pinned `TELEPOST_VERSION`), then start:

```bash
docker compose pull
docker compose up -d
```

Full commands, upgrades and removal for all three paths: [Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md).

### 3. Add the moderation group and start

The wizard asks only for the bot token, the channel and the owner ID. Open the generated
`config.ini`, keep what is there, and add the moderation group under `[BOT]`; save the file as
UTF-8:

```ini
[BOT]
LANGUAGE = en
REVIEW_CHAT_ID = -1001234567890
```

Replace the example ID with your moderation group ID, then start:

| Installation | Start command |
| --- | --- |
| Windows standalone | `.\telepost-windows-x64.exe` |
| Linux / macOS standalone | `./telepost-linux-x64` |
| pip | `telepost` |
| Docker | `docker compose up -d` |

With pip, configuration and data live in the current working directory, so always start from the
same one. When upgrading the standalone program, keep `config.ini` and the `data` folder next to
it. After starting, send `/start` to the bot in a private chat and check that it replies.

### 4. Make your first submission

1. Send `/start`, tap **📝 New submission**, or send `/submit`.
2. Upload photos, videos, GIFs, audio or files; for text only, send `/skip_media` to go straight to
   the preview. Send `/done_media` when you are done.
3. Add tags in the preview and adjust title, note, link, anonymous and spoiler as needed.
4. Tap **✅ Publish** (with chat review enabled the button says **✅ Submit for review**). Send
   `/cancel` at any point to cancel.

All commands: [Command reference](https://github.com/redtidev1918/TelePost/blob/main/docs/en/COMMANDS.md).

## AI-assisted review (MCP, optional)

TelePost bundles no language model and never moderates anything by itself. What it offers is an
optional [MCP](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md) sidecar: connect it to an MCP-capable agent such as Claude
Desktop or Codex, and the AI takes part in review while a human still decides.

Through MCP an agent can:

- list pending submissions with title, tags, source and media metadata;
- fetch image previews from a submission (video and GIF return the Telegram thumbnail; documents
  and audio return metadata only);
- read the channel review policy in `config/review_policy.md` and suggest a decision with
  confidence and reasons;
- approve, reject or set the spoiler once you ask for that exact action.

Set both layers to read-only to keep the agent advisory, and act yourself after checking its
suggestions:

```env
TELEPOST_MCP_REVIEW_MODE=readonly
TELEPOST_REVIEW_API_MODE=readonly
```

Installation, tokens and the stdio / HTTP setup: [MCP review](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md) (Chinese).

## More capabilities

- **HTTP API** — the same review and publishing pipeline as the bot. As the owner, generate a
  token with `/gen_token <name>`; the plaintext is shown once:

  ```bash
  curl -X POST 'https://example.com/api/v1/submissions' \
    -H 'Authorization: Bearer tp_xxxx' \
    -F 'files=@image.jpg' \
    -F 'title=Example' \
    -F 'tags=illustration' \
    -F 'idempotency_key=source:123'
  ```

  On a multi-bot deployment the path becomes `/api/botN/v1/submissions`. All fields and limits:
  [HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/en/API.md).

- **Novels with online reading** — a TXT submission is published to the channel as a document.
  With `NOVEL_PREVIEW_ENABLED` on and a `TELEGRAPH_ACCESS_TOKEN` configured, TelePost also builds
  a Telegraph reading page through TelePress and adds a **📖 Read online** button to the channel
  post; a failed or timed-out preview never affects the TXT publication.

- **File type filtering** — executables, common scripts and archives are blocked by default, and
  the check runs for private chats, the Mini App and the HTTP API alike. Normal images, audio,
  video plus TXT, PDF and Markdown documents are unaffected. The list is configurable; this is
  type filtering, not malware scanning.

- **Receiving mode** — `RUN_MODE=AUTO` uses Webhook when a public HTTPS address is configured and
  Polling otherwise. On a multi-bot deployment the webhook path is `/webhook/botN`; see
  [Webhook and Polling](https://github.com/redtidev1918/TelePost/blob/main/docs/WEBHOOK_MODE.md) (Chinese).

- **Health checks** — the service listens on port `8080` by default; `GET /health` reports status
  and `GET /ready` succeeds once the service is ready, waiting for every bot child process on a
  multi-bot deployment. For Fly.io, see [Fly.io deployment](https://github.com/redtidev1918/TelePost/blob/main/docs/FLYIO_DEPLOYMENT.md) (Chinese).

## Documentation

Documentation site: <https://redtidev1918.github.io/TelePost/#/en/>

| I want to… | Read |
| --- | --- |
| Install, upgrade or remove TelePost | [Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md) |
| Configure the channel, moderation group, language and multiple bots | [Configuration](https://github.com/redtidev1918/TelePost/blob/main/docs/en/CONFIGURATION.md) |
| Use the bot commands | [Command reference](https://github.com/redtidev1918/TelePost/blob/main/docs/en/COMMANDS.md) |
| Deploy the web submission and review UI | [Telegram Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md) (Chinese) |
| Connect a script or an automation service | [HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/en/API.md) |
| Let an AI agent take part in review | [MCP review](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md) (Chinese) |
| Choose a receiving mode and set up a reverse proxy | [Webhook and Polling](https://github.com/redtidev1918/TelePost/blob/main/docs/WEBHOOK_MODE.md) (Chinese) |
| Deploy to Fly.io | [Fly.io deployment](https://github.com/redtidev1918/TelePost/blob/main/docs/FLYIO_DEPLOYMENT.md) (Chinese) |
| Back up, upgrade and check the running service | [Operations](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md) (Chinese) |
| Fix an unresponsive bot, a failed upload or a failed publish | [Troubleshooting](https://github.com/redtidev1918/TelePost/blob/main/docs/TROUBLESHOOTING.md) (Chinese) |
| Understand memory and capacity | [Performance](https://github.com/redtidev1918/TelePost/blob/main/docs/PERFORMANCE.md) (Chinese) |
| Develop and test | [Testing guide](https://github.com/redtidev1918/TelePost/blob/main/docs/TESTING.md) (Chinese), [Contributing](https://github.com/redtidev1918/TelePost/blob/main/CONTRIBUTING.md) (Chinese) |

## Related projects

- [PixivFlow](https://github.com/redtidev1918/PixivFlow) — Pixiv downloading, filtering and
  automated collection
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) —
  combined PixivFlow + TelePost deployment

Both are optional integrations, not runtime dependencies of TelePost.

## License

[MIT License](https://github.com/redtidev1918/TelePost/blob/main/LICENSE)
