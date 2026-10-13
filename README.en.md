# TelePost

**Language / 语言:** [中文](https://github.com/redtidev1918/TelePost/blob/main/README.md) · English

TelePost is an open-source, self-hosted submission and moderation bot for Telegram channels.
Readers submit work through the bot's private chat, the Mini App or the HTTP API; submissions that
need review are viewed, edited and decided by the admins in the review group.

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest) [![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/) [![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/) [![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/redtidev1918/TelePost/blob/main/LICENSE) [![Documentation](https://img.shields.io/badge/docs-redtidev1918.github.io-6366f1)](https://redtidev1918.github.io/TelePost/#/en/)

[Download](https://github.com/redtidev1918/TelePost/blob/main/docs/en/download.md) · [Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md) · [Documentation](https://redtidev1918.github.io/TelePost/#/en/)

## Screenshots

![Bot private-chat main menu](https://raw.githubusercontent.com/redtidev1918/TelePost/main/docs/assets/screenshots/bot-menu.jpg)

The bot's main menu: submit work, follow your own submissions, search the channel, or browse
rankings and tags.

<!-- Real screenshots still to add, in priority order: (1) submission preview and editing; (2) moderation actions in the review group; (3) the Telegram Mini App. -->

## What it does

| Feature | Description |
| --- | --- |
| Submissions | Photos, videos, GIFs, audio, documents and plain text; preview and edit before submitting, with anonymous and spoiler modes. |
| Review and publishing | View previews and publish, reject or spoiler submissions in a private review group; refetch-capable automated submissions can swap candidates, and the replacement still needs human review. |
| Mini App | Optional web interface: users browse, submit and track their own work; reviewers and admins review, edit and manage. |
| Search and rankings | `/search` by keyword (with `#tag` and time-range filters), `/tags` for the tag cloud, `/hot` and `/hotweek` for rankings, `/myposts` and `/mystats` for your own submissions and statistics. |
| Scheduled posts | Commands such as `/schedule add weekly-hot` publish rankings on a schedule. |
| Automated submissions | Scripts or upstream services submit through the HTTP API; an `idempotency_key` keeps retries from duplicating reviews or publications. |
| AI-assisted review | Optionally connect AI agents such as Codex or Claude through MCP to read pending submissions and offer review suggestions. |
| Multiple bots, self-hosted | Run several bots on one machine, each with its own channel, review group, language and data directory. |

Default review rules per entry point: [How it works](#how-it-works). Integrations and deployment:
[More capabilities](#more-capabilities).

## How it works

```text
Reader submits ──▶ Preview and edit ──▶ Review (per entry point) ──▶ Published to the channel
```

A reader uploads content in the bot's private chat or the Mini App, then confirms tags, title, note,
link and the anonymous and spoiler settings in the preview before submitting. Whether it goes
through review depends on the entry point:

| Entry point | Default |
| --- | --- |
| Bot private chat | Published after the reader confirms; `CHAT_REVIEW_REQUIRED=true` requires review |
| Mini App | Review by default; controlled separately by `MINIAPP_REVIEW_REQUIRED` |
| HTTP API | Always submitted for review; a human must approve |

Automated submissions sent through the HTTP API (including PixivFlow and other upstream services)
must be approved by a human. `API_REVIEW_REQUIRED` is kept for compatibility and cannot turn review
off for the API. The default setup uses a review group, so `REVIEW_CHAT_ID` is required and must
not be the same as the channel.
Full defaults, moderation behaviour and retention are in
[Configuration](https://github.com/redtidev1918/TelePost/blob/main/docs/en/CONFIGURATION.md#review).

## Download and install

Pick the option that matches your environment, then continue with [First run](#first-run).

**Standalone program (no Python)** — Take the file for your platform from the
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

**pip (Python 3.10+ already installed)**:

```bash
python -m pip install telepost-bot
telepost --setup
```

The PyPI package is `telepost-bot` and the command it installs is `telepost`. Configuration and data
stay in the current working directory, so always start from the same one. Installing system
[libvips](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md#libvips-system-dependency)
is recommended: without it everything still works, but streaming downscale of very large images is
unavailable.

**Docker (servers or long-running deployments)** — Download or clone the repository, create a
`.env` in its root (`TOKEN`, `CHANNEL_ID`, `OWNER_ID`, `REVIEW_CHAT_ID` and a pinned
`TELEPOST_VERSION`), then start:

```bash
docker compose pull
docker compose up -d
```

Upgrades, removal, system dependencies and every setting: [Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md).
Receiving modes and their public HTTPS requirements: [More capabilities](#more-capabilities).

## First run

1. **Create the bot** — Create it with [@BotFather](https://t.me/BotFather) and keep the token.
2. **Prepare the channel and review group** — Add the bot to the publishing channel as an
   administrator with permission to post, then add it to a **separate** private review group and
   check that it can send messages there. The channel ID is either `@username` or a numeric ID
   starting with `-100`; the owner ID is your own numeric Telegram user ID, not a group or channel
   ID.
3. **Complete the configuration and start** — The setup wizards of the standalone program and the
   pip package ask only for the token, the channel and the owner ID. Open the generated
   `config.ini`, keep what is there, add the review group under `[BOT]` (save the file as UTF-8),
   then start:

   ```ini
   [BOT]
   LANGUAGE = en
   REVIEW_CHAT_ID = -1001234567890
   ```

   Replace the example ID with your review group ID. Start commands: double-click the Windows
   standalone program, or run `./telepost-linux-x64` / `./telepost-macos-arm64` on Linux / macOS;
   `telepost` for pip. Docker users configure the `.env` above — no wizard, no `config.ini`.
4. **Make a first submission** — Send `/start` to the bot, tap **📝 New submission** or send
   `/submit`, then upload photos, videos, GIFs, audio or files and send `/done_media` when done to
   reach the preview; for a text-only submission, `/skip_media` skips the upload. Fill in tags and
   text, adjust what you need, and tap **✅ Publish** (the button reads **✅ Submit for review**
   when chat review is on). Send `/cancel` at any point to cancel.

All commands: [Command reference](https://github.com/redtidev1918/TelePost/blob/main/docs/en/COMMANDS.md).

## Mini App (optional)

The Mini App is a web interface that ships with the same program: readers browse published content,
submit and follow their own work on a phone; reviewers and admins handle the review queue, edit text
and attachment order, and manage roles and the blacklist.

Enabling it requires a public HTTPS address and `MINIAPP_SESSION_SECRET` set in the environment.
The web frontend is served by the program's own HTTP service and is currently mounted only in
Webhook mode. The private-chat entry needs `MINIAPP_ENABLED` on and `MINIAPP_PUBLIC_URL` configured
(it can be derived from `WEBHOOK_URL`); without the secret, the entry shows but login fails. To
open it through `?startapp=` links, also configure a Main Mini App in BotFather.

Identity comes from Telegram and is verified server-side from `initData`. Opening the URL in a
normal browser is not a login, and it grants no review or admin rights. Setup steps:
[Telegram Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md) (Chinese).

## AI-assisted review (MCP, optional)

MCP is the protocol AI agents use to call external tools. TelePost can connect to agents such as
Codex or Claude through [MCP](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md)
(Chinese): they read pending submissions, suggest decisions based on your channel's review policy,
and perform moderation actions only when you explicitly ask and their permissions allow it.

TelePost bundles no language model and never calls an AI to moderate on its own. Keeping both MCP
and the review API read-only is recommended, leaving the agent advisory only; once write access is
enabled, get human confirmation before concrete actions — the program does not enforce this step.
Tool list, tokens and connection setup:
[MCP review](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md) (Chinese).

## More capabilities

- **HTTP API** — for scripts and other services to send submissions, sharing the bot's review and
  publishing pipeline; see the example below.
- **TXT novels and online reading** — TXT is published as a document; TelePress can optionally build
  a Telegraph reading page, and a failure never affects the publication.
- **Webhook and Polling** — with Polling the bot pulls messages from Telegram and needs no domain
  or public HTTPS; with Webhook, Telegram pushes updates to the bot and a public HTTPS address is
  required. The Mini App also requires public HTTPS.
- **File type filtering** — executables, common scripts and archives are blocked by default in all
  three submission entry points.
- **Server deployment** — Docker images and a Fly.io setup; backups and runtime state are covered in
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

You need a machine that stays on. Polling mode with the bot's private chat and a review group
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
<summary>Why doesn't the Mini App show up?</summary>

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
<summary>Where are submission files stored?</summary>

By default under `data/` (for a pip install, in the working directory where the command starts):
the submission database `submissions.db`, session state `persistence.pickle`, runtime policy
`runtime-policy.json`, the search index `search_index` and API temporary uploads `api_uploads/`;
multiple bots use `data/botN/`. Media itself stays on Telegram's side and the database only stores
references. Back up and keep the whole `data/` directory when upgrading or moving; backup method:
[Operations](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md#持久数据) (Chinese).

</details>

<details>
<summary>Will MCP approve content for me?</summary>

Whether it can depends on permissions: `TELEPOST_MCP_REVIEW_MODE=readonly` does not register the
moderation write tools, and `TELEPOST_REVIEW_API_MODE=readonly` rejects writes server-side. Keeping
both layers read-only is recommended; for the human-confirmation expectation after enabling writes,
see [AI-assisted review](#ai-assisted-review-mcp-optional). Full configuration:
[MCP review](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md) (Chinese).

</details>

## Documentation

Documentation site: <https://redtidev1918.github.io/TelePost/#/en/>

- Install, upgrade, remove — [Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md)
- Commands and required permissions — [Command reference](https://github.com/redtidev1918/TelePost/blob/main/docs/en/COMMANDS.md)
- Settings, review defaults, multiple bots — [Configuration](https://github.com/redtidev1918/TelePost/blob/main/docs/en/CONFIGURATION.md)
- Web submission and review UI — [Telegram Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md) (Chinese)
- Scripts and automated submissions — [HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/en/API.md)
- AI agents in review — [MCP review](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md) (Chinese)
- Receiving modes and reverse proxies — [Webhook and Polling](https://github.com/redtidev1918/TelePost/blob/main/docs/WEBHOOK_MODE.md) (Chinese)
- Deploying to Fly.io — [Fly.io deployment](https://github.com/redtidev1918/TelePost/blob/main/docs/FLYIO_DEPLOYMENT.md) (Chinese)
- Backups, upgrades, runtime state — [Operations](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md) (Chinese)
- Unresponsive bot, failed upload or publish — [Troubleshooting](https://github.com/redtidev1918/TelePost/blob/main/docs/TROUBLESHOOTING.md) (Chinese)
- Memory and capacity — [Performance](https://github.com/redtidev1918/TelePost/blob/main/docs/PERFORMANCE.md) (Chinese)
- Development and testing — [Testing guide](https://github.com/redtidev1918/TelePost/blob/main/docs/TESTING.md) (Chinese), [Contributing](https://github.com/redtidev1918/TelePost/blob/main/CONTRIBUTING.md) (Chinese)

## Related projects

- [PixivFlow](https://github.com/redtidev1918/PixivFlow) — Pixiv downloading, filtering and
  automated collection; it can submit work to TelePost through the HTTP API.
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy) — a
  combined PixivFlow + TelePost deployment.

Both are optional integrations, not runtime dependencies of TelePost.

## License

[MIT License](https://github.com/redtidev1918/TelePost/blob/main/LICENSE)
