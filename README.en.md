# TelePost

**Language / 语言:** [中文](https://github.com/redtidev1918/TelePost/blob/main/README.md) · English

TelePost is an open-source, self-hosted submission and moderation bot for Telegram channels.
It accepts submissions through private chat, a Mini App and an HTTP API. Users can upload, preview
and edit content; administrators can review pending submissions and publish them to a channel.

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest) [![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/) [![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/) [![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/redtidev1918/TelePost/blob/main/LICENSE) [![Documentation](https://img.shields.io/badge/docs-redtidev1918.github.io-6366f1)](https://redtidev1918.github.io/TelePost/#/en/)

[Download](https://github.com/redtidev1918/TelePost/blob/main/docs/en/download.md) · [Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md) · [Documentation](https://redtidev1918.github.io/TelePost/#/en/)

**Quick navigation:** [Review rules](#how-it-works) · [Install](#download-and-install) · [First run](#first-run) · [Optional features](#optional-features)

## What it does

| Feature | Description |
| --- | --- |
| Submissions | Photos, videos, GIFs, audio, documents and plain text; preview and edit before submitting, with anonymous and spoiler modes. |
| Review and publishing | View previews and publish, reject or spoiler submissions in a private review group; refetch-capable automated submissions can swap candidates, and the replacement still needs human review. |
| Mini App | Optional web interface: users browse, submit and track their own work; reviewers and admins review, edit and manage. |
| Search and rankings | Keyword and tag search, popular content, personal submission history and statistics. |
| Scheduled posts | Commands such as `/schedule add weekly-hot` publish rankings on a schedule. |
| Automated submissions | Scripts or upstream services submit through the HTTP API; an `idempotency_key` keeps retries from duplicating reviews or publications. |
| Restart and recovery | Keep the data directory to continue handling pending submissions after a restart. Cleared review previews can be restored when needed. |
| AI-assisted review | Optionally connect AI agents such as Codex or Claude through MCP to read pending submissions and offer review suggestions. |
| Multiple bots, self-hosted | Run several bots on one machine, each with its own channel, review group, language and data directory. |

## How it works

<details>
<summary>View the bot interface</summary>

![Bot private-chat main menu](https://raw.githubusercontent.com/redtidev1918/TelePost/main/docs/assets/screenshots/bot-menu.jpg)

</details>

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

Use a computer or server that can remain running and access Telegram. Bot-only use supports Polling without a domain. The Mini App requires Webhook mode and public HTTPS.

Choose one installation method:

| Method | Suitable for | Getting started |
| --- | --- | --- |
| Standalone program | No Python installation required | [Download for Windows, Linux or macOS](https://github.com/redtidev1918/TelePost/releases/latest) |
| pip | Python 3.10+ already installed | `python -m pip install telepost-bot` |
| Docker | Server deployments | [Docker / Compose installation](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md) |

Run the standalone program with `--setup` to open the configuration wizard. For pip, run `telepost --setup`.
See the [Installation guide](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md) for full setup, upgrade and backup instructions.

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
   `telepost` for pip. Docker users configure environment variables as described in the installation guide; no wizard or `config.ini` is required.
4. **Make a first submission**: Send `/start` to the bot, tap **📝 New submission** or send
   `/submit`, then upload photos, videos, GIFs, audio or files and send `/done_media` when done to
   reach the preview; for a text-only submission, `/skip_media` skips the upload. Fill in tags and
   text, adjust what you need, and tap **✅ Publish** (the button reads **✅ Submit for review**
   when chat review is on). Send `/cancel` at any point to cancel.

All commands: [Command reference](https://github.com/redtidev1918/TelePost/blob/main/docs/en/COMMANDS.md).

## Optional features

- [Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md) (Chinese): submit, track and review content inside Telegram. Requires an HTTPS address and a session secret.
- [HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/en/API.md): accept submissions from scripts and services, with idempotency keys to prevent duplicates on retries.
- [AI-assisted review (MCP)](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md) (Chinese): connect an external AI agent to inspect submissions and suggest decisions. Read-only mode is recommended; with write access enabled, the program does not enforce additional human confirmation.
- [Online reading](https://github.com/redtidev1918/TelePost/blob/main/docs/en/CONFIGURATION.md): optionally enable TelePress to generate Telegraph reading pages for TXT novels.

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
