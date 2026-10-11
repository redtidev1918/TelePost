# TelePost

**Language / 语言:** [中文](https://github.com/redtidev1918/TelePost/blob/main/README.md) · English

TelePost is an open-source, self-hosted submission and moderation bot for Telegram channels.
Readers submit work through the bot's private chat or the Mini App; you check, edit and decide
what gets published.

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
| Easy submissions | Send photos, videos, GIFs, audio, documents or plain text from the bot's private chat or the Mini App; the preview fixes tags, title, note and link, and switches on anonymous or spoiler mode. |
| Review and publishing | The submission and its preview go to your private review group with **✅ Publish to channel / ❌ Reject / 🔇 Spoiler** buttons; automated submissions can **🔄 Refetch** a different candidate, which still needs a human decision. |
| Mini App | Optional web interface: browse published content, submit and follow your own work on a phone; reviewers and admins handle the review queue, edit text and attachment order, and manage roles and the blacklist. |
| Search and rankings | `/search` by keyword (with `#tag` and time-range filters), `/tags` for the tag cloud, `/hot` and `/hotweek` for rankings, `/myposts` and `/mystats` for your own submissions and statistics. |
| Scheduled posts | Commands such as `/schedule add weekly-hot` publish rankings on a schedule. |
| Automated submissions | An owner generates a token with `/gen_token` and programs submit through the HTTP API; an `idempotency_key` makes retries safe. |
| AI-assisted review | An optional MCP sidecar lets agents such as Codex or Claude read pending submissions and suggest decisions; no language model is bundled and a human always decides. |
| Multiple bots, self-hosted | Run several bots on one machine, each with its own channel, review group, language and data directory. |

Default review rules per entry point: [How it works](#how-it-works). Integrations and deployment:
[More capabilities](#more-capabilities).

## How it works

```text
Reader submits ──▶ Preview and edit ──▶ Review (per entry point) ──▶ Published to the channel
```

A reader uploads content in the bot's private chat or the Mini App, then confirms tags, title, note,
link, anonymous and spoiler in the preview. Whether it goes through review depends on the entry
point:

| Entry point | Default |
| --- | --- |
| Bot private chat | Published after the reader confirms; `CHAT_REVIEW_REQUIRED=true` requires review |
| Mini App | Submitted for review, controlled by `MINIAPP_REVIEW_REQUIRED` |
| HTTP API | Always submitted for review; a human must approve |

Automated submissions (including PixivFlow and other upstream services) also need human approval:
`API_REVIEW_REQUIRED` is kept for compatibility and cannot turn review off for the API. The default
setup uses a review group, so `REVIEW_CHAT_ID` is required and must not be the same as the channel.
Full defaults, moderation behaviour and retention are in
[Configuration](https://github.com/redtidev1918/TelePost/blob/main/docs/en/CONFIGURATION.md#review).

## Download and install

Pick the path that matches your setup.

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

**Docker (servers or long-running deployments)** — Create a `.env` in the repository root
(`TOKEN`, `CHANNEL_ID`, `OWNER_ID`, `REVIEW_CHAT_ID` and a pinned `TELEPOST_VERSION`), then start:

```bash
docker compose pull
docker compose up -d
```

Upgrades, removal, system dependencies and every setting: [Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md).
A plain Polling bot needs no domain or public HTTPS; only Webhook mode and the Mini App do.

## First run

1. **Create the bot** — Create it with [@BotFather](https://t.me/BotFather) and keep the token.
2. **Prepare the channel and review group** — Add the bot to the publishing channel as an
   administrator with permission to post, then add it to a **separate** private review group and
   check that it can send messages there. The channel ID is either `@username` or a numeric ID
   starting with `-100`; the owner ID is your own numeric Telegram user ID, not a group or channel
   ID.
3. **Add the review group and start** — The setup wizard asks only for the token, the channel and
   the owner ID. Open the generated `config.ini`, keep what is there, add the review group under
   `[BOT]` (save the file as UTF-8), then start:

   ```ini
   [BOT]
   LANGUAGE = en
   REVIEW_CHAT_ID = -1001234567890
   ```

   Replace the example ID with your review group ID. Start commands: run the standalone binary,
   `telepost` for pip, or `docker compose up -d` for Docker.
4. **Make a first submission** — Send `/start` to the bot, tap **📝 New submission** or send
   `/submit`, then upload photos, videos, GIFs, audio or files. For text only, send `/skip_media`
   to go straight to the preview and `/done_media` when you are done. Add tags, adjust what you
   need, and tap **✅ Publish** (the button reads **✅ Submit for review** when chat review is on).
   Send `/cancel` at any point to cancel.

All commands: [Command reference](https://github.com/redtidev1918/TelePost/blob/main/docs/en/COMMANDS.md).

## Mini App (optional)

The Mini App is a web interface that ships with the same program: readers browse published content,
submit and follow their own work on a phone; reviewers and admins handle the review queue, edit text
and attachment order, and manage roles and the blacklist.

It does not appear just because the bot is running. It needs a public HTTPS address, a
`MINIAPP_SESSION_SECRET` and a Telegram-side entry; the frontend is served by the program's own HTTP
service, so it is only mounted in Webhook mode. If any of that is missing, the entry button simply
does not show up.

Identity comes from Telegram and is verified server-side from `initData`. Opening the URL in a
normal browser is not a login, and it grants no review or admin rights. Setup steps:
[Telegram Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md) (Chinese).

## AI-assisted review (MCP, optional)

TelePost can connect to MCP-capable agents such as Codex or Claude through
[MCP](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md) (Chinese): the agent
reads pending submissions, suggests decisions based on your channel's review policy, and performs
moderation actions only when you explicitly ask and its permissions allow it.

TelePost bundles no language model and never moderates anything on its own — a human always decides.
Keeping both the MCP layer and the review API read-only is the recommended setup. Tool list, tokens
and connection setup: [MCP review](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md) (Chinese).

## More capabilities

- **HTTP API** — the same review and publishing pipeline as the bot; multi-bot deployments use
  `/api/botN/v1/submissions`. Fields and limits:
  [HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/en/API.md).
- **Multiple bots** — run several bots on one machine, each with its own channel, review group,
  language and data directory.
- **TXT novels and online reading** — TXT is published as a document; TelePress can optionally build
  a Telegraph reading page, and a failure never affects the publication.
- **Webhook and Polling** — Webhook when a public HTTPS address is available, Polling otherwise.
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

Multi-bot deployments use `/api/botN/v1/submissions` instead. API submissions always go through
review; full fields and limits are in
[HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/en/API.md).

</details>

## FAQ

<details>
<summary>Do I need a server and a domain?</summary>

No domain or public HTTPS is required: a machine that stays on, Polling mode, the bot's private chat
and a review group are enough. Only Webhook mode and the Mini App need a public HTTPS address. See
[Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md).

</details>

<details>
<summary>Why was my private-chat submission published directly?</summary>

Private-chat submissions are published after the reader confirms by default
(`CHAT_REVIEW_REQUIRED=false`). Set it to `true` to send them to the review group first. The Mini App
and the HTTP API are not affected by this switch and always go through review by default. See
[Configuration](https://github.com/redtidev1918/TelePost/blob/main/docs/en/CONFIGURATION.md#review).

</details>

<details>
<summary>Why doesn't the Mini App show up?</summary>

It is optional and needs a public HTTPS address, `MINIAPP_SESSION_SECRET`, Webhook mode and a
Telegram-side entry; without all of them the entry is not shown. See
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

Under the program's `data/` directory: the submission database `submissions.db`, session state
`persistence.pickle`, runtime policy `runtime-policy.json`, the search index `search_index` and API
temporary uploads `api_uploads/`; multiple bots use `data/botN/`. Media itself stays on Telegram's
side and the database only stores references. Keep the whole `data/` directory when upgrading or
moving. See [Operations](https://github.com/redtidev1918/TelePost/blob/main/docs/OPERATIONS.md) (Chinese).

</details>

<details>
<summary>Will MCP approve content for me?</summary>

No. TelePost bundles no language model, and MCP is optional: the moderation write tools are only
registered when the mode is not read-only, and they are limited by both the MCP and review API
switches. Human confirmation is the recommended workflow, not an enforced one; keeping both layers
read-only leaves the agent advisory only. See
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
