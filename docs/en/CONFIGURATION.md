# Configuration Reference

## Precedence

Runtime policy written by `/botconfig` > environment variables > `config.ini` > built-in defaults.

Runtime policy only overrides the channel, review chat, the two review switches, and the attribution switch; `/botconfig reset` removes the overrides.
Sensitive values are always managed via environment variables, Secrets, or `config.ini`.

## Core configuration

| Variable | Default | Description |
|---|---|---|
| `TOKEN` | required | Bot token; also accepts `BOT_TOKEN`, `TELEGRAM_BOT_TOKEN` |
| `CHANNEL_ID` | required | `@channel` or `-100…`; also accepts `CHANNEL` |
| `OWNER_ID` | empty | The single owner ID; automatically added to `ADMIN_IDS` |
| `ADMIN_IDS` | empty | Comma-separated; only used for operations explicitly marked Admin |
| `BOT_MODE` | `MIXED` | `MEDIA`, `DOCUMENT`, or `MIXED` |
| `ALLOWED_FILE_TYPES` | `*` | Document extensions or MIME types, comma-separated |
| `SHOW_SUBMITTER` | `true` | Whether the channel shows the submitter |
| `NOTIFY_OWNER` | `true` | Whether to durably DM the owner: notified once per logical submission after a review item is queued or a direct post succeeds; refetch/editorial do not re-notify |
| `CHANNEL_FOOTER_LINK` | empty | When **formally publishing to the channel**, appends a text navigation footer at the bottom of the caption (`✉️ TG 投稿` → `https://t.me/<bot>?start=submit`; with Mini App enabled also `📱 Mini App` → `?start=miniapp`; with `MINIAPP_SHORT_NAME` configured, Direct Mini App `?startapp=submit`). Empty = off. Review previews/queued items do **not** carry the footer |
| `MINIAPP_SUBMIT_CTA` | `false` | Adds a Mini App navigation item to the channel footer; falls back to `?start=miniapp` by default, with the bot sending a Web App button in private chat. Omitted when not enabled or the link is missing — never generates a broken link |
| `MINIAPP_SHORT_NAME` | empty | BotFather Direct Mini App short name; once configured the channel footer uses `https://t.me/<bot>/<short_name>?startapp=submit` to open the app directly |
| `MINIAPP_PUBLIC_URL` | empty | Entry URL for the private-chat menu button and inline Web App button; when empty, derived from `WEBHOOK_URL` as `<public root>/app/` |
| `MEDIA_PROXY_BASE_URL` | empty | Public media reverse-proxy root; DeliveryPlanner only rewrites exact hosts listed in `MEDIA_PROXY_HOSTS`. When empty, remote URLs are handed to Telegram as-is |
| `MEDIA_PROXY_HOSTS` | empty | Comma-separated exact-host allowlist, e.g. `i.pximg.net`; without hosts no URL is rewritten |
| `SUBMIT_LIMIT_PER_HOUR` | `10` | Submissions per user per hour; `0` disables |
| `ALLOWED_TAGS` | `30` | Maximum tags per submission |
| `TIMEOUT` | `300` | Cleanup threshold (seconds) for stale upload data in the database |
| `SESSION_TIMEOUT` | `900` | Idle timeout (seconds) for chat submission sessions |
| `TZ` | `Asia/Shanghai` | IANA timezone name; used by daily maintenance tasks |

## Run mode & HTTP

| Variable | Default | Description |
|---|---|---|
| `RUN_MODE` | `AUTO` | `AUTO`, `POLLING`, or `WEBHOOK` |
| `WEBHOOK_URL` | empty | Public HTTPS root address, without `/webhook` |
| `WEBHOOK_PORT` | `8080` | HTTP listen port; the multi-bot parent router uses this port |
| `WEBHOOK_PATH` | `/webhook` | Single-bot callback path; multi-bot automatically uses `/webhook/botN` |
| `WEBHOOK_SECRET_TOKEN` | generated and persisted on first run | Validation token for Telegram webhook requests; can also be set explicitly |
| `HEALTH_PORT` | `8080` | Health/API port for single-bot polling |
| `API_ENABLED` | `true` | Whether to mount `/api/v1/*` |
| `ROUTER_TIMEOUT_SECONDS` | `300` | Upstream total timeout of the multi-bot parent router |
| `UPLOAD_SESSION_MAX_AGE_SECONDS` | `3600` | Cleanup age for upload directories left behind by forced interruptions |
| `TELEPOST_IMAGE_DECODE_BUDGET_MB` | `64` | Estimated peak budget for the regular compression path; beyond it only JPEG can still be decoded with downsampling |
| `TELEPOST_UNBOUNDED_DECODE_BUDGET_MB` | auto | Hard peak allowed before compression for formats without Image.draft capability (e.g. PNG); when unset, derived from the container memory limit (64–192 MiB); an explicit value overrides the default |

`AUTO` only selects Webhook when `WEBHOOK_URL` is a public HTTPS address; if the automatically selected Webhook
registration fails it falls back to Polling. Forced `WEBHOOK` exits on failure.

## Telegram Mini App (optional enhancement, §152-§153)

| Variable | Default | Description |
|---|---|---|
| `MINIAPP_ENABLED` | `true` | Whether to enable the Mini App surface. Enabled by default, sharing the same "enabled by default, disabled only explicitly" contract as the Mini App session auth switch (`telepost/miniapp/auth.py`); the mini-app entry is closed only when explicitly set to `false`/`0`/`no`/`off`, and this does **not** affect the Bot or the HTTP API (launch safety switch) |
| `MINIAPP_CONTENT_ENABLED` | `true` | Mini App public content browsing (hot list / post detail / published media proxy). Disabling only affects the content API (`/api/v1/posts/*`), not submission or review |
| `MINIAPP_SESSION_SECRET` | (none) | Mini App session signing key (≥32 chars, independent random secret). When unset, `POST /miniapp/session` is fail-closed |
| `MINIAPP_SESSION_TTL` | `1800` | Session lifetime in seconds (60–43200) |

For the full architecture, auth chain, build, and same-origin hosting see [`docs/MINIAPP.md`](../MINIAPP.md) (Chinese).

## Search & storage

| Variable | Default | Description |
|---|---|---|
| `DB_PATH` | `data/submissions.db` | SQLite path |
| `DB_CACHE_KB` | `4096` | SQLite page cache; `1024` for low memory |
| `SEARCH_ENABLED` | `true` | Whether to build and write the search index. When off, `/search`, the "🔍 搜索" menu button and the inline search buttons reply "search is not enabled on this bot"; "my posts / tag cloud / hot posts" read the database and stay available |
| `SEARCH_INDEX_DIR` | `data/search_index` | Whoosh index directory |
| `SEARCH_ANALYZER` | `jieba` | `jieba`; falls back to `simple` when not installed |
| `SEARCH_HIGHLIGHT` | `false` | Search result highlighting |
| `RUNTIME_POLICY_PATH` | same directory as the database | JSON file for `/botconfig` |

The database uses WAL. When backing up, run a checkpoint, or copy `.db`, `-wal`, and `-shm` together.

## Review

| Variable | Default | Description |
|---|---|---|
| `API_REVIEW_REQUIRED` | `true` (kept for compatibility) | Automation API (service token) submissions: **always enter review**; this switch no longer disables review for the API |
| `MINIAPP_REVIEW_REQUIRED` | `true` | Whether Mini App human submissions enter the review chat (independent of the API switch) |
| `CHAT_REVIEW_REQUIRED` | `false` | Telegram chat submissions enter the review chat |
| `REVIEW_CHAT_ID` | empty | Required when any review switch is enabled, and must not equal the channel |

**Default disposition invariant (`SubmissionDisposition`)**: decided by **source trust level**, not entry form:
API (automation) always enters review; Mini App is independently controlled by `MINIAPP_REVIEW_REQUIRED` (default true);
native Telegram Chat publishes directly to the channel by default (`CHAT_REVIEW_REQUIRED=false`).
All three share the same domain/service (`QueueCommand → ReviewQueueService`); every routing decision must be based on
the source's disposition — Mini App and API must never be bound to the same switch.
| `REVIEW_ALBUM_SIZE` | `10` | 1–10 items per review preview group |
| `REVIEW_PREVIEW_INTERVAL_SECONDS` | `0.75` | Throttle interval between preview groups |
| `REVIEW_PREVIEW_TIMEOUT_SECONDS` | `120` | Telegram I/O timeout for a single review preview |
| `TELEGRAM_SEND_TIMEOUT_SECONDS` | `REVIEW_PREVIEW_TIMEOUT_SECONDS` | Telegram I/O timeout for channel publishing; keep 120s for large albums |
| `CHANNEL_ALBUM_REPLY` | code `chain` / production deploy `discussion` | Multi-image display: `chain` replies level by level in the channel; `post` replies to the root post in the channel; `discussion` fills the channel with the first image group + first file group, with overflow going to the image/file discussion threads respectively (Webhook mode) |
| `DISCUSSION_FORWARD_TIMEOUT_SECONDS` | `10` | In `discussion` mode, the timeout for waiting for the channel post to be auto-forwarded to the discussion group; on timeout after the root is confirmed, the root post is kept and overflow waits for manual verification; minimum 1 second |
| `REVIEW_PREVIEW_THREAD` | `1` | Subsequent previews and control messages reply to the previous one |
| `PENDING_REVIEW_RETENTION_DAYS` | `0` | Pending-review expiry in days; `0` keeps forever |
| `NOVEL_PREVIEW_ENABLED` | `false` | **Optional publishing enhancement**: TXT novels are published to Telegraph via TelePress, with a "read online" entry as an inline button on the channel root post (§online-reading); **off by default** — when enabled, the TXT document is still sent normally, and Telegraph failure/timeout never fails the submission (§telepress-preview). Current releases depend on TelePress 0.17.2; the page uses the current publishing Bot's Telegram username as native Telegraph author metadata (for example `@example_bot → https://t.me/example_bot`), and omits it when unavailable |
| `NOVEL_FALLBACK_CARD_ENABLED` | `true` | For novels without a real cover, Pillow generates a lightweight fallback card as the channel root (§novel-cover); when disabled or generation fails, degrades to text-only root + TXT reply, never affecting publish success |
| `NOVEL_PREVIEW_TIMEOUT_SECONDS` | `15` | Strict timeout (seconds) for a single preview attempt; Telegraph must not hold publishing indefinitely |
| `NOVEL_PREVIEW_MAX_BYTES` | `4194304` | Size cap (bytes) for reading the TXT body; beyond it no preview is generated |
| `TELEGRAPH_ACCESS_TOKEN` | empty | Telegraph account access token (Secret, never logged); empty = preview off |
| `PENDING_REVIEW_CLEANUP_BATCH_SIZE` | `100` | At most 1–200 items expired per round |
| `REVIEW_RETENTION_DAYS` | `30` | Retention days for decided reviews and API notification idempotency records |
| `SUPERSEDED_RETENTION_DAYS` | `30` | Retention days for old review cards superseded (successful refetch); on expiry their Telegram preview/control messages and records are deleted, while lineage (attempt/seen) is kept; `0` disables cleanup |
| `REFETCH_PROGRESS_REMIND_MINUTES` | `2` | If no terminal state this many minutes after a refetch is admitted, remind the review chat at most once (the reminder rewrites the control card to "refetching" with the elapsed wait); `0` disables reminders |
| `REFETCH_WAKE_MINUTES` | `12` | When the remote machine is unreachable and no progress for this many minutes, the watchdog idempotently wakes it with the same request UUID (no new attempt is created) |
| `REFETCH_HARD_TIMEOUT_MINUTES` | `90` | Beyond this many minutes without a terminal state, the attempt is marked `failed(stalled_after_hard_timeout)` and notified; `0` disables the hard timeout |
| `REFETCH_STALE_TIMEOUT_MINUTES` | `20` | Without a terminal state, start checking the PixivFlow durable slot; unadmitted requests may be judged timed out, but admitted attempts still executing/delivering are not failed on local time alone; `0` disables the check |
| `API_MAX_FILES` | `100` | File count cap per HTTP API submission; the parent router limits total bytes only, not file count |

Telegram only guarantees a bot can delete messages within 48 hours; when automatic cleanup of the review chat is needed, pending retention is usually set to 1 day.

### Novel covers are equally visible in the review chat (§novel-cover)

For novels with a real cover (`pixiv:<id>:novelcover` asset), the review-chat preview and the channel publication have the **same shape**:
the cover photo is sent first (remote URL provided by PixivFlow, via the media proxy), then the TXT document. The cover is a
`staging_only` display item, never written into review records (`media_json`/`documents_json`); at publish time
the channel root is still built from the canonical asset, so it is never double-counted. Missing or malformed assets are silently skipped
and never fail the submission; without a real cover the review chat shows only the TXT, and the channel falls back to the fallback card.

`TELEPOST_IMAGE_DECODE_BUDGET_MB` measures the decoded pixel working set, not the compressed file size.
Files that need no conversion and fit Telegram photo limits are streamed directly without invoking Pillow; regular files needing conversion only
get a temporary JPEG when the estimated peak stays within 64 MiB. Formats without downsample-decoding capability such as PNG may retry
compression once with a capacity-aware hard peak (derived from the container memory limit by default, range 64–192 MiB; can also be
explicitly overridden via `TELEPOST_UNBOUNDED_DECODE_BUDGET_MB`); only beyond this hard peak is the immutable original staged as a document,
or dropped entirely when there is no preview.

### Adjusting a deployed instance (Fly.io)

For an already-running deployment, changing these two requires **no code change / image rebuild** — set environment variables and restart:

```bash
# Switch multi-album submissions to "all reply to the root post" (no more nested chain)
fly secrets set -a <app> CHANNEL_ALBUM_REPLY=discussion

# File count cap per submission (code default is already 100; raise as needed)
fly secrets set -a <app> API_MAX_FILES=100
```

- `CHANNEL_ALBUM_REPLY=discussion` in action: 11 images + 4 files → the channel gets the first group of 10 images + all 4 files (the file album follows the image group), the 11th image goes to the image discussion thread; with more than 10 files, the overflow goes to the file discussion thread.
- Since 2.10.43, when an album degrades to single-message sends it still keeps the chosen hierarchy: `chain` replies to the previous message, `post` replies to the root post (or a caller-specified anchor). Network timeouts are still not auto-resent — confirm whether the channel already received them first.
- `post` means replying within the same channel; it does not move subsequent images into the linked discussion group's comment section, and does not change the send target.
- `discussion` is the comment-section display: the channel root keeps the first image group and first file group (images first, files after); overflow images/files go to their respective discussion anchor threads; the bot must be in the channel's linked discussion group with permission to post.
- When a regular `chain` / `post` multi-batch publish hits a **deterministic failure**, confirmed Telegram messages are written to the delivery ledger; retrying with the same idempotency key only continues the remaining batches. When the response status is uncertain, the key stops automatic sending and must be manually verified first, to avoid duplicate root posts.
- `discussion` is only available in **Webhook mode** — auto-forward events must be captured before entering the PTB update queue; Polling mode cannot receive auto-forwards, so after root confirmation it can only keep the channel root post and cannot deliver overflow, and it does not auto-rerun. Misconfiguration triggers a startup log warning.
- When the channel root is unconfirmed (send failure / response lost and no forward found on recheck), one rollback and automatic retry is allowed; once the root is confirmed, linked-discussion overflow failure **never deletes or reruns the root** — only the determinable part of the discussion area is cleaned up, the root post is kept, and the corresponding comment thread needs manual verification. Cases like "comment album sent but not confirmed", where duplication cannot be judged, are not auto-retried.
- If the process crashes mid-review-publish, the record stays in `publishing`; after `PUBLISHING_STALE_SECONDS` (default 300) seconds, clicking "retry publish" automatically unlocks and resends.
- `API_MAX_FILES` relaxes the HTTP API submission entry (PixivFlow etc.); a single Telegram album is still ≤10, and the publish side batches automatically.
- Changing secrets triggers an app restart; the production deployment (telesubmit-multi-bot) has `discussion` enabled, and a single Telegram album is still ≤10.

## Editorial Revision (§editorial)

When a reviewer chooses "edit then publish" in the review chat, they can edit before publishing: title, description, tags, links, spoiler, and media order, or remove attachments.

- **Original submissions are immutable**: editing only produces a Revision; the submitter's original (including the media list) is always kept auditable; media reordering or removal only affects the subset published this time.
- **Publishing saves an independent snapshot**: what goes out to the channel is a Publication Snapshot; it cannot be changed after publishing and never writes back to the original.
- **The submitter can be notified**: with `SUBMITTER_PUBLISH_NOTIFY=with_changes`, the publish notification includes a change summary (see "Submitter publish notification" at the end of this document).
- **Separation of duties**: the Review FSM decides "whether it can be published", Editorial Revision decides "which version to publish" — they do not affect each other; old versions superseded by a refetch can no longer be published.

Implementation invariants are in AGENTS.md (§editorial / §notify-submitter).

## Multi-bot

When `BOT1_TOKEN` exists, `run.py` enters multi-bot mode and reads
`BOT1_TOKEN`, `BOT2_TOKEN`, … consecutively — gaps are not allowed. Each bot needs at least:

```env
BOT1_TOKEN=...
BOT1_CHANNEL_ID=@channel_one
BOT1_OWNER_ID=123456789
BOT2_TOKEN=...
BOT2_CHANNEL_ID=@channel_two
BOT2_OWNER_ID=123456789
```

`BOT{n}_` can override `run.py`'s `OVERRIDABLE_KEYS`: Owner/Admin, display and notification, bot
mode, file types, rate limits, review, database, search, health port, timeouts, run mode, webhook
secret, and the channel footer (`BOT{n}_CHANNEL_FOOTER_LINK` / `BOT{n}_MINIAPP_SUBMIT_CTA`).
The default data directory is `data/botN/`; the parent router always provides:

- `/webhook/botN`
- `/api/botN/v1/*`

## PixivFlow standalone executor & refetch

In the production split deployment, TelePost stays resident while PixivFlow is normally stopped (Fly `auto_start_machines`
wakes it on incoming HTTPS requests); review-chat refetch uses the following configuration:

| Variable | Purpose |
|---|---|
| `PIXIVFLOW_REFETCH_BASE_URL` | PixivFlow's HTTPS address, e.g. `https://pixivflow-scheduler.fly.dev` |
| `PIXIVFLOW_REFETCH_TOKEN` | Dedicated Bearer matching the same-named Secret on the PixivFlow side; separate from the scheduled-trigger token |
| `PIXIVFLOW_JOB_TRANSPORT` | Job channel: `protocol` (default, Workflow Protocol v1) or `legacy` (rollback, see below) |
| `TELEPOST_API_BASE_URL` | TelePost's public address (all bot processes share the same domain), e.g. `http://telepost:8080`. Only effective under the `protocol` channel and when this variable **is configured**: when TelePost submits a job as the protocol consumer, it declares `callback_url = {base}/api/bot{N}/v1/jobs/events` on the Task (`N` is the subprocess `TELEPOST_BOT_INDEX`, default `1`), so PixivFlow's outbox can push events to this end's event entry `POST /api/botN/v1/jobs/events`. When unset, no `callback_url` is carried (the Task payload stays byte-for-byte stable) |

`PIXIVFLOW_JOB_TRANSPORT` is read in exactly one place, `telepost/application/pixivflow_jobs.py`, defaulting to
`protocol`: submission goes through `POST /jobs` (`job_type=candidate_search`, `params.target_id`, with
requestId as `idempotency_key`), reads go through `GET /jobs?idempotency_key=` (falling back to
`GET /jobs/{job_id}` when needed), and `GET /capabilities` negotiates versions and job types first; if the server does not declare
protocol version `1` or `candidate_search`, it fails directly without silent fallback. Setting it to `legacy` uses the old
`POST /internal/targets/{targetId}/refetch`, byte-for-byte identical to pre-migration behavior, for rollback.

### Refetch semantics: swap the candidate

"Refetch" = find a **new candidate work that this review chain has not shown yet** for the current **pending** review item, and on success
**replace** the current candidate with it; it is not re-downloading the same work. Flow:

```text
Review chat clicks "refetch" → TelePost persists one attempt (only one active attempt per chain at a time)
→ POST /jobs (default channel; with `PIXIVFLOW_JOB_TRANSPORT=legacy` still
  POST /internal/targets/{targetId}/refetch, carrying UUID requestId + review-chain correlation)
→ Fly proxy wakes the stopped PixivFlow → writes a durable manual Slot → executes in background
→ New candidate found: once the new preview and control card are ready, the new item, old item superseded,
  and attempt replaced are committed in the same transaction
→ No new candidate: PixivFlow reports no_alternative; the current item stays unchanged; refetch can be retried later
→ Real failure: reports failed; the current item stays unchanged
```

- Idempotency: webhook redelivery of the same button click (same `callback_query.id`) reuses the same requestId and
  the same manual Slot, never producing a second execution; only **another deliberate user click** creates a new-generation attempt.
- Only one active refetch per review chain at a time; repeated clicks while processing get "refetching, please wait".
- Approval-race safe: if a reviewer has already approved/rejected while the refetch runs, the late result is marked obsolete —
  it does not overwrite the review verdict and does not create a phantom superseded.
- Old-button safe: clicks on buttons of superseded or finished review items are rejected outright, producing no historical fork.
- Review-chain lineage: `review_chain_id` (the same review line), `generation` (0 = original,
  +1 per successful replacement), `supersedes_review_id` (the superseded old item). Candidate history
  `refetch_seen_candidates` records works already shown by the chain under a (chain, work id) unique constraint.
- Superseded items are deleted after `SUPERSEDED_RETENTION_DAYS` days by periodic maintenance, removing the old cards and
  records in the chat (failure to delete a message does not block); attempts and candidate history are kept forever for audit.
- Progress-aware: after clicking refetch the control card immediately switches to "refetching" (publish/reject/mask hidden, refetch and view-original kept);
  if no terminal state beyond `REFETCH_PROGRESS_REMIND_MINUTES` after admission, a "still processing" reminder is sent and the elapsed
  wait is updated on the same card; if `REFETCH_STALE_TIMEOUT_MINUTES` passes with no terminal state it is judged failed and notified, and the user may click again;
  successful replacement, no candidate, and failure each get a clear chat message and return a normal actionable card — nothing looks stuck.
  The source review is **not** rejected early because of a refetch click (that is a terminal verdict and would make its own replacement `obsolete`).
- Empty `target_id` is rejected, as is running without the two variables above; do not use `PIXIVFLOW_ENABLED=true`
  to try to wake the standalone executor (that is the same-container compatibility mode switch, not applicable to the split topology).
- Internal tokens travel only in service-to-service Bearer headers — never in chat messages, logs, or audit.

## PixivFlow co-process (compatibility mode)

`PIXIVFLOW_ENABLED=true` makes the TelePost supervisor also launch PixivFlow. Related variables:

| Variable | Default |
|---|---|
| `PIXIVFLOW_CONFIG` | `/app/data/pixivflow/config.json` |
| `PIXIVFLOW_CONFIG_TEMPLATE` | in-image template |
| `PIXIVFLOW_COMMAND` | `pixivflow scheduler` |

This mode needs the `runtime-pixivflow` image containing Node/PixivFlow, and must stay resident to run Cron.
The all-in-one image needs `ffmpeg`: when PixivFlow processes ugoira (Pixiv animations) it converts frame ZIPs into looping GIFs,
spawning `python3` + `ffmpeg` at runtime; without ffmpeg, animations are delivered only as ZIP + frame JSON documents.
The Fly.io split deployment wakes an independent PixivFlow Machine on demand; TelePost stays resident. This compatibility mode is not used in that topology.

## `config.ini`

The full template is [`config.ini.example`](https://github.com/redtidev1918/TelePost/blob/main/config.ini.example) at the repository root. Common mappings:

- `[BOT]`: core configuration, run mode, and review
- `[WEBHOOK]`: `URL`, `PORT`, `PATH`, `SECRET_TOKEN`
- `[SEARCH]`: `INDEX_DIR`, `ENABLED`, `ANALYZER`, `HIGHLIGHT`
- `[DB]`: `CACHE_SIZE_KB`

Not every advanced environment variable has an INI mapping; deployment platforms should prefer environment variables/Secrets.

## Submitter publish notification (§notify-submitter)

| Variable | Default | Description |
|---|---|---|
| `SUBMITTER_PUBLISH_NOTIFY` | `off` | `off` no notification; `published` only notifies "published"; `with_changes` includes the editorial change summary |

- The trigger is uniformly **Publication Success** (channel publish confirmed), not review approval.
- Covers all three paths: chat direct publish / reviewed original / reviewed edit; anonymous humans are still DMed; service
  submissions are never notified.
- Anonymous humans are shown with their internal user ID (`tg://user` link) in the admin "submission notification" for banning;
  username / display name are not shown; the public channel caption stays anonymous.
- Idempotency key `publication:<message_id>:submitter-notification`; on failure only the notification is retried,
  the publication is not rolled back.

## Health self-check `telepost doctor` (read-only)

```bash
python -m telepost.observability.cli doctor                 # default database (same as reviews inspect)
python -m telepost.observability.cli doctor --bot 1 --bot 2 # specific bots, repeatable
python -m telepost.observability.cli doctor --all-bots      # scan data/bot*/submissions.db
python -m telepost.observability.cli doctor --json          # a single JSON object
python -m telepost.observability.cli doctor --now 1800000000 # fixed reference clock (for tests)
```

Database path resolution is exactly the same as `reviews inspect` and `run.build_bot_env`:
`BOTn_DB_PATH` → `data/botn/submissions.db`, otherwise `DB_PATH` → `config.settings.DB_PATH`.

**Read-only contract**: all connections are `file:...?mode=ro`; this command **does not write to any database**,
runs no migrations, makes no repairs, starts no background loops; output never contains any token (bot token / submission token /
webhook secret / PixivFlow secret); audit events are only counted with latest timestamps — payloads are never printed.

Exit codes and output:

| Exit code | Meaning | Final human-readable line |
|---|---|---|
| `0` | All OK or only WARN | `HEALTHY` / `DEGRADED` |
| `1` | At least one CRIT | `FAILED` |
| `2` | Cannot verify (database file missing/unreadable/`PRAGMA integrity_check` failed) | `FAILED` |

`--json` outputs `{"status", "checks":[{"code","level","message","details"?}], "databases", "generated_at"}`;
`details` is guaranteed JSON-serializable and secret-free.

Checks (degrade to `SKIP` when a table/column is missing, never crash):

| code | Level | Description |
|---|---|---|
| `db_integrity` | CRIT/OK | `PRAGMA integrity_check` per database; anything other than `ok` means cannot verify (exit code 2) |
| `refetch_stuck` | CRIT/WARN/OK | Active attempts in `refetch_attempts` (**normalized via `telepost/domain/refetch_state.py` first**, compatible with legacy `requested`/`admitted`); based on `created_at` (`started_at` when missing): > 30 minutes CRIT, > 15 minutes WARN, listing `request_id`/`state`/`source_review_id`/minutes waited per row |
| `refetch_active_invariant` | CRIT/OK | The partial UNIQUE index `idx_refetch_one_active` must exist; any `review_chain_id` with ≥2 active attempts at once is CRIT |
| `review_queue_orphans` | CRIT/WARN/OK | `pending_reviews.status='pending'` with empty/NULL `control_message_id`: > 15 minutes WARN, > 60 minutes CRIT (lists at most 5 ids) |
| `review_queue_publishing` | CRIT/OK | `status='publishing'` older than `PUBLISHING_STALE_SECONDS` (reusing the constant from `services.review_service`, definition unchanged) is CRIT — meaning the cleanup task did not reclaim it |
| `review_queue_counts` | WARN/OK | Counts grouped by `status` plus `oldest_pending_age_seconds`; oldest pending > 7 days is WARN |
| `delivery_outbox` | WARN/OK/SKIP | Delivery-ledger total attempts, unconfirmed count, oldest **unconfirmed** age; unconfirmed > 0 or oldest unconfirmed > 30 minutes is WARN; missing table is SKIP. `delivery_ledger` is the idempotency ledger of "confirmed publications" — historical rows keep aging and do not indicate a health problem, so age only looks at unconfirmed rows like `partial`/`uncertain`/`failed`/`error` (`FAILED_LEDGER_STATUSES`) |
| `audit_events_recent` | WARN/OK/SKIP | Count of `audit_events` in the last 24 hours and the age of the latest `review.refetch_*` event; if an active attempt exists while the latest refetch event has not updated for > 2 hours, WARN |

Implementation lives in `telepost/observability/doctor.py` (pure function `run_doctor(*, db_paths, now)`);
the CLI is only a thin wrapper; the diagnostic logic does not depend on the web app (does not import `run.py` / aiohttp).
