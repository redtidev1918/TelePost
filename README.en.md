# TelePost

**Language / 语言:** [中文](https://github.com/redtidev1918/TelePost/blob/main/README.md) · English

TelePost is an open-source, self-hosted submission and moderation bot for Telegram channels.
It accepts submissions through private chat, a Mini App and an HTTP API. Users can upload, preview
and edit content; administrators can review pending submissions and publish them to a channel.

[![Release](https://img.shields.io/github/v/release/redtidev1918/TelePost)](https://github.com/redtidev1918/TelePost/releases/latest) [![PyPI](https://img.shields.io/pypi/v/telepost-bot.svg)](https://pypi.org/project/telepost-bot/) [![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/) [![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/redtidev1918/TelePost/blob/main/LICENSE) [![Documentation](https://img.shields.io/badge/docs-redtidev1918.github.io-6366f1)](https://redtidev1918.github.io/TelePost/#/en/)

[Download](https://github.com/redtidev1918/TelePost/blob/main/docs/en/download.md) · [Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md) · [Documentation](https://redtidev1918.github.io/TelePost/#/en/)

## What it does

| Feature | Description |
| --- | --- |
| Submissions | Photos, videos, GIFs, audio, documents and plain text; preview and edit before submitting, with anonymous and spoiler modes. |
| Review and publishing | Preview, revise, approve or reject submissions in a separate review group, with content masking; optionally connect an AI agent for review suggestions. |
| Mini App | Optional web interface: users browse, submit and track their work; admins handle the review queue. |
| Search and rankings | Keyword and tag search, popular content, personal submission history and statistics. |
| Scheduled posts | Commands such as `/schedule add weekly-hot` publish rankings on a schedule. |
| Automated submissions | An HTTP API for scripts and other programs, with protection against duplicate submissions on retries. |
| Restart and recovery | Submission and review state is persisted; pending reviews continue after a restart. |
| Multiple bots | Run several bots on one machine through a single entry point, each with its own channel, review group, language and data directory. |

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

The default setup requires a review group (`REVIEW_CHAT_ID`) separate from the publishing channel.
To review private-chat submissions too, set `CHAT_REVIEW_REQUIRED=true`; the Mini App is controlled
separately by `MINIAPP_REVIEW_REQUIRED`. More settings are in
[Configuration](https://github.com/redtidev1918/TelePost/blob/main/docs/en/CONFIGURATION.md#review).

## Download and install

Use a computer or server that can remain running and access Telegram. Bot-only use supports Polling without a domain. The Mini App requires Webhook mode and public HTTPS.

Choose one installation method:

| Method | Suitable for | Getting started |
| --- | --- | --- |
| Standalone program | Windows x64, Linux x64, macOS Apple Silicon; no Python needed | [Download the latest build](https://github.com/redtidev1918/TelePost/releases/latest) |
| pip | Python 3.10+ already installed | `python -m pip install telepost-bot` |
| Docker / Compose | Servers and long-running deployments | [Docker / Compose installation](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md#3-docker--compose) |

Run the standalone program with `--setup` to open the configuration wizard. For pip, run `telepost --setup`.
See the [Installation guide](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md) for full setup, upgrade and backup instructions.

## First run

1. **Create the bot**: Create it with [@BotFather](https://t.me/BotFather) and keep the token.
2. **Prepare the channel and review group**: Add the bot to the publishing channel as an
   administrator with permission to post, then add it to a **separate** private review group and
   check that it can send messages there. The channel ID is either `@username` or a numeric ID
   starting with `-100`; the owner ID is your own numeric Telegram user ID, not a group or channel
   ID.
3. **Complete the configuration and start**: The setup wizard collects only the token, the channel
   and the owner ID; the review group is added by hand. Open the generated `config.ini`, keep what
   is there, and add the review group under `[BOT]` (save the file as UTF-8):

   ```ini
   [BOT]
   LANGUAGE = en
   REVIEW_CHAT_ID = -1001234567890
   ```

   Replace the example ID with your review group's numeric ID. To review private-chat submissions
   too, add `CHAT_REVIEW_REQUIRED = true` under the same `[BOT]` section. Start commands:
   double-click the Windows standalone program, or run `./telepost-linux-x64` /
   `./telepost-macos-arm64` on Linux / macOS; `telepost` for pip. Docker users configure
   environment variables as described in the installation guide; no wizard or `config.ini` is required.
4. **Make a first submission**: Send `/start` to the bot, tap **📝 New submission** or send
   `/submit`, then upload photos, videos, GIFs, audio or files and send `/done_media` when done to
   reach the preview; for a text-only submission, `/skip_media` skips the upload. Fill in tags and
   text, adjust what you need, and tap **✅ Publish** (the button reads **✅ Submit for review**
   when chat review is on). Send `/cancel` at any point to cancel.

All commands: [Command reference](https://github.com/redtidev1918/TelePost/blob/main/docs/en/COMMANDS.md).

## Optional features

- [Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md) (Chinese): submit, track and review content inside Telegram. Requires an HTTPS address and a session secret.
- [HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/en/API.md): accept submissions from scripts and services, with idempotent submissions to prevent duplicates on retries.
- [AI-assisted review (MCP)](https://github.com/redtidev1918/TelePost/blob/main/docs/MCP_REVIEW.md) (Chinese): connect an external AI agent through MCP to inspect submissions and suggest decisions. Read-only mode is recommended; with write access enabled, the program does not enforce additional human confirmation.
- [Online reading](https://github.com/redtidev1918/TelePost/blob/main/docs/en/CONFIGURATION.md): optionally enable TelePress to generate Telegraph reading pages for TXT novels.

## Documentation

Documentation site: <https://redtidev1918.github.io/TelePost/#/en/>

- Install, upgrade, remove: [Install and deploy](https://github.com/redtidev1918/TelePost/blob/main/docs/en/INSTALL.md)
- Settings, review defaults, multiple bots: [Configuration](https://github.com/redtidev1918/TelePost/blob/main/docs/en/CONFIGURATION.md)
- Commands and required permissions: [Command reference](https://github.com/redtidev1918/TelePost/blob/main/docs/en/COMMANDS.md)
- Web submission and review UI: [Telegram Mini App](https://github.com/redtidev1918/TelePost/blob/main/docs/MINIAPP.md) (Chinese)
- Scripts and automated submissions: [HTTP API](https://github.com/redtidev1918/TelePost/blob/main/docs/en/API.md)
- Common runtime problems: [Troubleshooting](https://github.com/redtidev1918/TelePost/blob/main/docs/TROUBLESHOOTING.md) (Chinese)
- Local development, testing and contributing: [Contributing](https://github.com/redtidev1918/TelePost/blob/main/CONTRIBUTING.md) (Chinese)

Webhook deployment, Fly.io, operations and performance guides are on the
[documentation site](https://redtidev1918.github.io/TelePost/#/en/).

## Related projects

- [PixivFlow](https://github.com/redtidev1918/PixivFlow): find and collect Pixiv works matching your
  filters, then send them to TelePost for review and publishing.
- [TelePress](https://github.com/redtidev1918/TelePress): generate online reading pages for TXT novels; the library ships with TelePost, and online reading must be enabled separately.
- [pixivflow-telepost-deploy](https://github.com/redtidev1918/pixivflow-telepost-deploy): guides and
  tools for deploying PixivFlow and TelePost as separate services.

TelePost works independently; deploying PixivFlow or the full ecosystem is optional.

## License

[MIT License](https://github.com/redtidev1918/TelePost/blob/main/LICENSE)
