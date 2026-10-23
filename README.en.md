# TelePost

**Language / 语言:** [中文](https://github.com/redtidev1918/TelePost/blob/main/README.md) · English

TelePost is an open-source, self-hosted submission and moderation bot for Telegram channels.
It accepts submissions through private chat, a Mini App and an HTTP API. Users can upload, preview
and edit content; administrators can review pending submissions and publish them to a channel.

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest) [![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/) [![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/) [![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/redtidev1918/TelePost/blob/main/LICENSE) [![Documentation](https://img.shields.io/badge/docs-redtidev1918.github.io-6366f1)](https://redtidev1918.github.io/TelePost/#/en/)

[Download](https://github.com/redtidev1918/TelePost/blob/main/docs/en/download.md) · [Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md) · [Documentation](https://redtidev1918.github.io/TelePost/#/en/)

**Quick navigation:** [Review rules](#how-it-works) · [Install](#download-and-install) · [First submission](#first-run) · [Mini App](#mini-app-optional) · [FAQ](#faq)

## Screenshots

![Bot private-chat main menu](https://raw.githubusercontent.com/redtidev1918/TelePost/main/docs/assets/screenshots/bot-menu.jpg)

The private-chat menu provides access to submissions, submission history, channel search, rankings and tags.

## What it does

| Feature | Description |
| --- | --- |
| Submissions | Photos, videos, GIFs, audio, documents and plain text; preview and edit before submitting, with anonymous and spoiler modes. |
| Review and publishing | View previews and publish, reject or spoiler submissions in a private review group; refetch-capable automated submissions can swap candidates, and the replacement still needs human review. |
| Mini App | Optional web interface: users browse, submit and track their own work; reviewers and admins review, edit and manage. |
| Search and rankings | `/search` by keyword (with `#tag` and time-range filters), `/tags` for the tag cloud, `/hot` and `/hotweek` for rankings, `/myposts` and `/mystats` for your own submissions and statistics. |
| Scheduled posts | Commands such as `/schedule add weekly-hot` publish rankings on a schedule. |
| Automated submissions | Scripts or upstream services submit through the HTTP API; an `idempotency_key` keeps retries from duplicating reviews or publications. |
| Restart and recovery | Keep the data directory to continue handling pending submissions after a restart. Cleared review previews can be restored when needed. |
| AI-assisted review | Optionally connect AI agents such as Codex or Claude through MCP to read pending submissions and offer review suggestions. |
| Multiple bots, self-hosted | Run several bots on one machine, each with its own channel, review group, language and data directory. |

## How it works

A reader uploads content in the bot's private chat or the Mini App, then confirms tags, title, note,
link and the anonymous and spoiler settings in the preview before submitting. Whether it goes
through review depends on the entry point:

| Entry point | Default |
| --- | --- |
| Bot private chat | Published after the reader confirms; `CHAT_REVIEW_REQUIRED=true` requires review |
| Mini App | Review by default; controlled separately by `MINIAPP_REVIEW_REQUIRED` |
| HTTP API | Always submitted for review; a human must approve |

To review every submission, enable `CHAT_REVIEW_REQUIRED` and keep `MINIAPP_REVIEW_REQUIRED=true`.
The default setup requires a review group (`REVIEW_CHAT_ID`) separate from the publishing channel.
HTTP API submissions always need review, even with `API_REVIEW_REQUIRED=false`. More settings are in
[Configuration](https://github.com/redtidev1918/TelePost/blob/main/docs/en/CONFIGURATION.md#review).

## Download and install

Installation requires an always-on computer or server with access to Telegram, a bot token, a publishing channel and a
separate review group. Private-chat use can run in Polling mode without a domain; the Mini App
requires Webhook mode and public HTTPS. If the bot and IDs are not ready, complete the first two
steps of [First run](#first-run) before installing. Select an installation method below, then finish the setup.

**Standalone program (no Python)**: Download the file for the target platform from the
[download page](https://github.com/redtidev1918/TelePost/blob/main/docs/en/download.md) or
[Releases](https://github.com/redtidev1918/TelePost/releases/latest):
`telepost-windows-x64.exe` for Windows x64, `telepost-linux-x64` for Linux x64, and
`telepost-macos-arm64` for macOS (Apple Silicon). Keep it in one folder and run the setup wizard:

```powershell
.\telepost-windows-x64.exe --setup
```

On Linux / macOS, make it executable first:

```bash
chmod +x telepost-linux-x64
./telepost-linux-x64 --setup
```

On macOS, replace the filename with `telepost-macos-arm64`.

**pip (Python 3.10+ already installed)**:

```bash
python -m pip install telepost-bot
telepost --setup
```

The PyPI package is `telepost-bot` and the command it installs is `telepost`. Configuration and data
stay in the current working directory, so always start from the same one. Installing system
[libvips](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md#libvips-system-dependency)
is recommended: the bot can start and accept submissions without it, but very large images are
sent as files.

**Docker (servers or long-running deployments)**: Download or clone the repository and create
a `.env` in its root:

```dotenv
TOKEN=replace-with-your-bot-token
CHANNEL_ID=@your_channel
OWNER_ID=123456789
REVIEW_CHAT_ID=-1001234567890
```

Replace all four values with actual settings; the example token and IDs cannot be used as provided.
For an initial trial, no version setting is required: Compose pulls the `latest` image by default.
Run these commands from the directory containing `docker-compose.yml`:

```bash
docker compose pull
docker compose up -d
docker compose ps
```

After startup, `docker compose ps` should show the container running and, once the health check
completes, `healthy`. Send `/start` to the bot in private chat to verify that the main menu opens.

For long-term deployments, add `TELEPOST_VERSION` to `.env` with a published version selected from
[Releases](https://github.com/redtidev1918/TelePost/releases/latest), without the `v` prefix.
This variable selects the Docker image version; it is not a bot setting and is unnecessary for
standalone or pip installations. Pinning a version controls upgrades and rollbacks.
See [Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md) for upgrade steps.

Compose saves data in `./data` and exposes no HTTP ports by default. To add settings beyond the
example above, also pass the corresponding variables through Compose's `environment`.
For Webhook, Mini App and review settings, see
[Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md).

Upgrades, removal, system dependencies and every setting: [Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md).
Receiving modes and their public HTTPS requirements: [More capabilities](#more-capabilities).

## First run

1. **Create the bot**: Create it with [@BotFather](https://t.me/BotFather) and keep the token.
2. **Prepare the channel and review group**: Add the bot to the publishing channel as an
   administrator with permission to post, then add it to a **separate** private review group and
   check that it can send messages there. The channel ID is either `@username` or a numeric ID
   starting with `-100`; the owner ID is your own numeric Telegram user ID, not a group or channel
   ID.
3. **Complete the configuration and start**: The setup wizards of the standalone program and the
   pip package ask only for the token, the channel and the owner ID. Open the generated
   `config.ini`, keep what is there, add the review group under `[BOT]` (save the file as UTF-8),
   then start:

   ```ini
   [BOT]
   LANGUAGE = en
   REVIEW_CHAT_ID = -1001234567890
   ```

   Replace the example ID with your review group ID. To review private-chat submissions too, add
   `CHAT_REVIEW_REQUIRED = true` under the same `[BOT]` section. Start commands: double-click the Windows
   standalone program, or run `./telepost-linux-x64` / `./telepost-macos-arm64` on Linux / macOS;
   `telepost` for pip. Docker users configure the `.env` above without running the wizard or editing `config.ini`.
4. **Make a first submission**: Send `/start` to the bot, tap **📝 New submission** or send
   `/submit`, then upload photos, videos, GIFs, audio or files and send `/done_media` when done to
   reach the preview; for a text-only submission, `/skip_media` skips the upload. Fill in tags and
   text, adjust what you need, and tap **✅ Publish** (the button reads **✅ Submit for review**
   when chat review is on). Send `/cancel` at any point to cancel.

All commands: [Command reference](https://github.com/redtidev1918/TelePost/blob/main/docs/en/COMMANDS.md).

## Mini App (optional)

The Mini App is the bot's built-in web interface: readers browse published content,
submit and check their submission status on a phone; reviewers and admins handle the review queue, edit text
and attachment order, and manage roles and the blacklist.

Enable Webhook mode and prepare a public HTTPS address. Set `MINIAPP_ENABLED`, the entry URL
`MINIAPP_PUBLIC_URL` and the login secret `MINIAPP_SESSION_SECRET`, then open the app inside Telegram.
Opening the URL in a normal browser does not log you in. Setup steps and BotFather settings:
[Telegram Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md) (Chinese).

## AI-assisted review (MCP, optional)

MCP is a protocol for AI agents to call external tools. TelePost provides MCP tools for external
agents such as Codex and Claude to inspect pending submissions, suggest review decisions and,
with write access, perform moderation actions.

The AI agent requires separate configuration. Read-only access for both MCP and the review API is
recommended, leaving publication decisions to administrators. With write access enabled, the program
does not enforce additional human confirmation.
Tool list, tokens and connection setup:
[MCP review](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md) (Chinese).

## More capabilities

- **HTTP API**: for scripts and other services to send submissions, sharing the bot's review and
  publishing pipeline; see the example below.
- **TXT novels and online reading**: TXT is published as a document; TelePress can optionally build
  a Telegraph reading page, and a failure never affects the publication.
- **Webhook and Polling**: with Polling the bot pulls messages from Telegram and needs no domain
  or public HTTPS; with Webhook, Telegram pushes updates to the bot and a public HTTPS address is
  required. The Mini App also requires public HTTPS.
- **File type filtering**: executables, common scripts and archives are blocked by default in all
  three submission entry points.
- **Server deployment**: Docker images and a Fly.io setup; backups and runtime state are covered in
  [Operations](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md) (Chinese).

<details>
<summary>HTTP API submission example</summary>

An owner generates a token in the bot with `/gen_token <name>`; the plaintext is shown only once:

```bash
curl -X POST 'https://example.com/api/v1/submissions' \
  -H 'Authorization: Bearer tp_xxxx' \
  -F 'files=@image.jpg' \
  -F 'title=Example post' \
  -F 'tags=illustration' \
  -F 'idempotency_key=source:123'
```

Multi-bot deployments use `/api/botN/v1/submissions` instead. Review rules: [How it works](#how-it-works);
full fields and limits are in [HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/en/API.md).

</details>

## FAQ

<details>
<summary>Do I need a server and a domain?</summary>

A computer or server must remain running. Polling mode with the bot's private chat and a review group
needs no domain; the public HTTPS requirements of Webhook and the Mini App are in
[More capabilities](#more-capabilities). See
[Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md).

</details>

<details>
<summary>Why was my private-chat submission published directly?</summary>

Private-chat submissions are published after the reader confirms by default. Set
`CHAT_REVIEW_REQUIRED=true` to send them to the review group first; other entry points are not
affected. Default behaviour per entry point: [How it works](#how-it-works).

</details>

<details>
<summary>Why is the Mini App entry unavailable?</summary>

First check that `MINIAPP_ENABLED` is on and the entry URL is configured, then that the page is
reachable in Webhook mode. If the entry shows but login fails, check `MINIAPP_SESSION_SECRET` in
the environment and open the app from inside Telegram. Enabling conditions:
[Mini App](#mini-app-optional); troubleshooting steps:
[Telegram Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md) (Chinese).

</details>

<details>
<summary>Why does the bot not respond?</summary>

Check that only one instance uses the bot token, then look at the process, the recent logs and
`/health`; in Webhook mode also check `getWebhookInfo`. Running the source, a container and an old
machine at the same time causes a Polling conflict. See
[Troubleshooting](https://github.com/redtidev1918/TelePost/blob/main/docs/TROUBLESHOOTING.md#bot-完全无响应) (Chinese).

</details>

<details>
<summary>Are submissions retained after preview cleanup?</summary>

Review preview media is cleared about 36 hours after sending by default; the control card and review
record remain. This does not expire the submission. Tap **Restore preview** on a pending review's
card to view it again. If the card's buttons are unavailable, administrators can follow the recovery steps in
[Operations](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md) (Chinese).

</details>

<details>
<summary>Where are submission files stored?</summary>

Submission records, runtime policies and conversation state are saved in `data/` by default. With pip,
this directory is relative to the command's working directory. Multiple bots use `data/botN/`.
Uploaded media stays on Telegram; `data/` is not a full copy of those files.
Back up and keep the whole `data/` directory when upgrading or moving. Backup steps:
[Operations](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md#持久数据) (Chinese).

</details>

<details>
<summary>Can AI agents approve submissions through MCP?</summary>

In read-only mode, the AI can view submissions and offer suggestions. Set
`TELEPOST_MCP_REVIEW_MODE=readonly` and `TELEPOST_REVIEW_API_MODE=readonly` to prevent it from
performing moderation actions. Before enabling write access, read
[MCP review](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md) (Chinese).

</details>

## Documentation

Documentation site: <https://redtidev1918.github.io/TelePost/#/en/>

Use the links below for installation and configuration. Before modifying the repository, developers
and AI agents should read [AGENTS.md](https://github.com/redtidev1918/TelePost/blob/main/AGENTS.md)
and its cross-repository requirements, then follow
[Contributing](https://github.com/redtidev1918/TelePost/blob/main/CONTRIBUTING.md) for development setup and validation (Chinese).

- Install, upgrade, remove: [Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md)
- Commands and required permissions: [Command reference](https://github.com/redtidev1918/TelePost/blob/main/docs/en/COMMANDS.md)
- Settings, review defaults, multiple bots: [Configuration](https://github.com/redtidev1918/TelePost/blob/main/docs/en/CONFIGURATION.md)
- Web submission and review UI: [Telegram Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md) (Chinese)
- Scripts and automated submissions: [HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/en/API.md)
- AI agents in review: [MCP review](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md) (Chinese)
- Receiving modes and reverse proxies: [Webhook and Polling](https://github.com/redtidev1918/TelePost/blob/main/docs/WEBHOOK_MODE.md) (Chinese)
- Deploying to Fly.io: [Fly.io deployment](https://github.com/redtidev1918/TelePost/blob/main/docs/FLYIO_DEPLOYMENT.md) (Chinese)
- Backups, upgrades, runtime state: [Operations](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md) (Chinese)
- Unresponsive bot, failed upload or publish: [Troubleshooting](https://github.com/redtidev1918/TelePost/blob/main/docs/TROUBLESHOOTING.md) (Chinese)
- Memory and capacity: [Performance](https://github.com/redtidev1918/TelePost/blob/main/docs/PERFORMANCE.md) (Chinese)
- Development and testing: [Testing guide](https://github.com/redtidev1918/TelePost/blob/main/docs/TESTING.md) (Chinese), [Contributing](https://github.com/redtidev1918/TelePost/blob/main/CONTRIBUTING.md) (Chinese)

## Related projects

- [PixivFlow](https://github.com/redtidev1918/PixivFlow): find and collect Pixiv works matching your
  filters, then send them to TelePost for review and publishing.
- [TelePress](https://github.com/redtidev1918/TelePress): turn novels and other content into online reading pages.
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy): guides and
  tools for deploying PixivFlow and TelePost as separate services.

TelePost works independently; deploying PixivFlow or the full ecosystem is optional. The TelePress
library ships with the program, while online reading requires configuration.

## License

[MIT License](https://github.com/redtidev1918/TelePost/blob/main/LICENSE)
