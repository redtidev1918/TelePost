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
| `/hot [count] [week]` | All-time hot list; `/hot 20` for TOP 20 |
| `/hotweek [count]` | This week's hot list (natural week, Mon 00:00 local) |
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
| `/schedule` | Manage scheduled tasks (see detailed section below) |
| `/ban_user <user id> [reason]` | Manually block a user (fallback if buttons fail; use the anonymous ID from the admin alert) |
| `/ban_api <token id> [reason]` | Manually disable an API token (fallback if buttons fail) |
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
| `/botconfig api_review on\|off` | API review toggle (kept for compatibility; API submissions always enter review) |
| `/botconfig miniapp_review on\|off` | Mini App review toggle |
| `/botconfig chat_review on\|off` | Chat review toggle |
| `/botconfig show_submitter on\|off` | Channel attribution toggle |
| `/botconfig reset` | Delete runtime overrides and fall back to the deployed configuration |

Process the pending review queue before switching channel or review group, or running `reset`.
A multi-bot supervisor restarts only the current bot; a single-bot deployment needs a manual
restart after the policy is written.


## `/schedule` (Admin command)

Admins can create recurring tasks so the bot automatically sends content (e.g. a weekly hot
list) at a set time.

### Interactive creation

Send `/schedule` (no args). The bot shows the task list or a create button. Tap "🔥 创建每周热榜":

1. Pick a weekday (Mon–Sun buttons)
2. Pick a time (presets 20:00 / 21:00 / 08:00 / 12:00, or send HH:MM directly)
3. Pick the TOP count (5 / 10 / 20)
4. Confirm the preview to create

### One-line command

```text
/schedule add weekly-hot <weekday> <HH:MM> <TOP N>
```

Example: `/schedule add weekly-hot sunday 20:00 10`

### Management subcommands

| Command | Description |
|---|---|
| `/schedule` | List all tasks (with run/disable/delete buttons) |
| `/schedule add weekly-hot <weekday> <HH:MM> <N>` | Create a weekly hot-list task |
| `/schedule enable <id>` | Enable a task |
| `/schedule disable <id>` | Disable a task |
| `/schedule run <id>` | Run the task immediately (manual trigger) |
| `/schedule preview <id>` | Preview the task output (no send) |
| `/schedule delete <id>` | Delete a task |

### Notes

- The target chat is the current bot's review group (`REVIEW_CHAT_ID`, falling back to
  `CHANNEL_ID` when unset)
- Tasks are persisted in SQLite; the bot restores them after a restart
- The same task will not send twice for the same time slot (idempotent)
- Timezone uses the `TZ` env var (default `Asia/Shanghai`)
- Manual runs do not affect the next scheduled occurrence
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


