# Command reference

**Language / 语言:** [中文](/COMMANDS.md) · English

## Permissions

- **User**: any user who has not been blocked.
- **Admin**: the IDs in `ADMIN_IDS`; `OWNER_ID` is included automatically.
- **Owner**: `OWNER_ID` only. Sensitive operations — tokens, runtime configuration, bulk
  deletion and the block list — are not widened to Admin.

## User commands

| Command | Description |
|---|---|
| `/start` | Welcome message and menu |
| `/submit` | Start a submission |
| `/cancel` | Cancel the current submission; outside a session it explains there is none in progress |
| `/search <keyword>` | Search; supports `#tag`, `-t day\|week\|month`, `-n <count>` |
| `/tags` | Tag cloud |
| `/hot [count] [range]` | Local hot list; at most 50 |
| `/myposts [count]` | Your submissions |
| `/mystats` | Statistics for your submissions |
| `/settings` | Show the current public configuration |
| `/help` | Help |

## Owner commands

| Command | Description |
|---|---|
| `/debug` | Diagnostics for the current bot and runtime configuration |
| `/searchuser <user id>` | Look up a specific user's submissions |
| `/delete_posts <id or range...>` | Bulk soft delete, at most 50 per call |
| `/blacklist` | Block-list panel |
| `/blacklist_add <user id> [reason]` | Add to the block list |
| `/blacklist_remove <user id>` | Remove from the block list |
| `/blacklist_list` | Show the block list |
| `/gen_token <name>` | Generate an API token; the plaintext is shown only once |

`/tokens` and `/revoke_token <number>` only let the current user inspect or revoke their own
tokens; new tokens can still only be created by the Owner through `/gen_token`.

## Admin commands

| Command | Description |
|---|---|
| `/rebuild_index` | Clear and rebuild the search index |
| `/sync_index` | Incrementally sync the database and the index |
| `/index_stats` | Show the database/index difference |
| `/optimize_index` | Merge Whoosh index segments |

The approve, reject, spoiler-toggle and Pixiv-refetch buttons in the review group also require
Admin.

## `/botconfig` (Owner only)

| Command | Description |
|---|---|
| `/botconfig` | Show the panel |
| `/botconfig channel @channel or -100ID` | Change the submission channel |
| `/botconfig review here` | Make the current group the review group |
| `/botconfig review -100ID` | Set the review group by ID |
| `/botconfig api_review on\|off` | API review toggle |
| `/botconfig chat_review on\|off` | Chat review toggle |
| `/botconfig show_submitter on\|off` | Channel attribution toggle |
| `/botconfig reset` | Delete runtime overrides and fall back to the deployed configuration |

Process the pending review queue before switching channel or review group, or running `reset`.
A multi-bot supervisor restarts only the current bot; a single-bot deployment needs a manual
restart after the policy is written.

## Submission flow commands

| Command | Stage | Description |
|---|---|---|
| `/done_media` | Upload | Finish uploading and open the preview |
| `/skip_media` | Upload | Open the preview without uploading files |

The preview page lets you edit tags, title, note and link, add media, toggle anonymity and the
spoiler, then publish or cancel. Tags are required; `MIXED` accepts both media and documents by
default.

## Notes

- `/hot` reads the local database. The Telegram Bot API offers no side-effect-free way to read
  live view counts from arbitrary channel posts.
- `/search` and `/hot` support pagination; `/myposts` outputs one message per item.
- The block list intercepts both submissions and button interaction.

> Pages marked **（中文）** are currently Chinese-only. Their English versions are being added
> incrementally.
