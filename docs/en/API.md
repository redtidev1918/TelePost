# HTTP API v1

External programs can publish files through TelePost, reuse Telegram `file_id`s, or send status notifications to the review chat.
Polling and Webhook modes expose the same API.

## Base path

| Deployment | Base path |
|---|---|
| Single bot | `/api/v1` |
| Multi-bot | `/api/botN/v1` |

A single bot always uses `/api/v1`, whether running from source, Docker, or Fly.io. `/api/botN/v1` is only used when
multi-bot variables such as `BOT1_TOKEN`, `BOT2_TOKEN` are configured.

## Token

Generate a token with the Telegram account configured as `OWNER_ID`:

```text
/gen_token pixivflow
```

The plaintext is shown only once. The server stores only the SHA-256 hash; use `/tokens` to list token ids and
`/revoke_token <id>` to revoke. Except for health checks, every request needs:

```http
Authorization: Bearer tp_xxxxxxxx
```

Never put tokens in URLs, logs, repositories, or plain config files; use Secrets when deploying.

## Health check

```http
GET /api/v1/health
```

No auth required. Returns the API version, bot version (the real release version), and the two review switches.

## Version diagnostics (read-only, no auth)

```http
GET /api/v1/version                  # single bot
GET /api/bot1/v1/version             # multi-bot parent router: bot1
GET /api/bot2/v1/version             # multi-bot parent router: bot2
```

Each botN subprocess reports **its own** bot identity and real release identity (from the same `release_info()` source as the parent router's
`/version` and `/health`), used for version-consistency acceptance in dual-bot production
(Bot Version Matrix) (sample response — always check the latest release for the version number):

```json
{
  "ok": true,
  "data": {
    "bot": "bot1",
    "service": "telepost",
    "version": "2.76.2",
    "commit": "…",
    "build_date": "…"
  }
}
```

## Schedule status (read-only, no auth)

```http
GET /api/v1/schedule/status          # single bot
GET /api/bot1/v1/schedule/status     # multi-bot parent router
```

Returns the time and status of each schedule's most recent terminal notification, so a status page or channel pin can query it anytime
without waiting for the next publish slot to know "did anything update":

```json
{
  "ok": true,
  "data": {
    "schedules": [
      {"schedule_id": "bot1-daily", "last_sent_at": 1789415370.33,
       "last_sent_at_iso": "2026-09-14T19:49:30+00:00", "last_status": "partial"}
    ]
  }
}
```

In multi-bot deployments, a publicly readable plain-text status page lives at `<host>/status` (aggregated by the parent router from the bot
sub-services) — good for pinning/bookmarking by operators.

## Identity & quota

```http
GET /api/bot1/v1/me
Authorization: Bearer tp_xxxx
```

Returns the token's ownership, the last hour's usage, and `SUBMIT_LIMIT_PER_HOUR`.

## File submission

```bash
curl -X POST 'https://example.com/api/bot1/v1/submissions' \
  -H 'Authorization: Bearer tp_xxxx' \
  -F 'files=@cover.jpg' \
  -F 'files=@novel.txt' \
  -F 'tags=Pixiv,推荐' \
  -F 'title=标题' \
  -F 'note=简介' \
  -F 'link=https://example.com/source' \
  -F 'anonymous=true' \
  -F 'spoiler=false' \
  -F 'idempotency_key=source:123' \
  -F 'target_id=daily-pixiv'
```

`Content-Type` must be `multipart/form-data`.

| Field | Required | Constraints |
|---|---|---|
| `files` | yes | Repeatable; at most 100 files, 50 MiB each, 500 MiB total |
| `tags` | yes | Comma-separated, at most `ALLOWED_TAGS` (default 30) |
| `title` | no | Up to 100 chars |
| `note` | no | Up to 600 chars; accepts real newlines and literal `\\n` |
| `link` | no | Must start with `http://` or `https://` |
| `anonymous` | no | `true`, `1`, `yes` are truthy |
| `spoiler` | no | Same as above |
| `idempotency_key` | strongly recommended | Up to 240; prevents duplicate queueing in review mode and duplicate posting in direct mode (safe retry on ACK loss) |
| `target_id` | no | Up to 120; identifies the automation source in review mode, for targeted refetch |
| `work_type` | no | `illustration` / `novel` / empty; work type, participates in same-work dedup |
| `work_id` | no | Up to 32; source-neutral work identifier (canonical), participates in same-work dedup; wins when both fields are sent |
| `pixiv_id` | no | Deprecated: compatibility alias of `work_id`, kept for legacy callers |
| `source_label` | no | Up to 80; human-readable source label (e.g. `PixivFlow · 每日推荐`). Shown on the review control card; TelePost does not parse its meaning; hidden by default |
| `source_ref` | no | Up to 160; machine-readable, stable source reference (e.g. upstream job/execution id). For archival/troubleshooting only; TelePost does not interpret its structure |
| `scheduled_at` | no | Up to 40; scheduled time (ISO-8601). For source display/troubleshooting only; TelePost does not schedule based on it |

> `source_label` / `source_ref` / `scheduled_at` are **generic, optional, bounded** source fields that any API
> client may send; behavior is completely unchanged when they are absent (backward compatible). TelePost neither knows nor relies on any upstream scheduling/
> Slot state machine. Fields are treated as single-line plain text (control characters stripped), appear only on plain-text review control cards, and never enter
> the HTML channel body.

Uploads stream to `data/api_uploads/<request>` at 64 KiB chunks and are cleaned up on both normal returns and errors; directories
left behind by abnormal interruptions are swept in the background. The parent router also streams the forwarding and never reads a 500 MiB request fully into memory.

## `file_id` submission

When you already have Telegram `file_id`s obtained by the same bot, you can publish with zero transfer:

```bash
curl -X POST 'https://example.com/api/bot1/v1/submissions' \
  -H 'Authorization: Bearer tp_xxxx' \
  -H 'Content-Type: application/json' \
  -d '{
    "media": [
      {"type": "photo", "file_id": "AAA"},
      {"type": "video", "file_id": "BBB"}
    ],
    "documents": [{"file_id": "CCC", "filename": "novel.txt"}],
    "tags": "Pixiv,推荐",
    "title": "标题",
    "note": "简介",
    "link": "https://example.com/source",
    "anonymous": true,
    "spoiler": false,
    "idempotency_key": "source:123",
    "target_id": "daily-pixiv"
  }'
```

`media[].type` only accepts `photo`, `video`, `animation`, `audio`; `documents[]` must have
`file_id`. At least one item across both groups, at most 100 total. The JSON path currently allows empty tags, but callers should still provide
tags to stay consistent with chat submissions and multipart behavior. `file_id`s are bound to the bot and cannot be used across bots.


### Delivered-asset contract (optional `media_assets`, Step 10)

`file_id` submissions also support an optional `media_assets` array: TelePost only stores the "minimal delivery contract"
(`asset_id` + `kind` + `source_url` + optional `mime_type`) and does not parse or store other fields of upstream domain
objects.

```bash
curl -X POST 'https://example.com/api/bot1/v1/submissions' \
  -H 'Authorization: Bearer tp_xxxx' \
  -H 'Content-Type: application/json' \
  -d '{
    "media": [{"type": "photo", "file_id": "AAA"}],
    "media_assets": [
      {"asset_id": "pixiv-001", "kind": "image",
       "source_url": "https://i.pximg.net/001.jpg", "mime_type": "image/jpeg"}
    ],
    "tags": "Pixiv"
  }'
```

- Each item must be an object with non-empty `asset_id`; `kind` only supports `image`; `source_url` must be
  `http(s)`; duplicate `asset_id`s are rejected.
- Both JSON and multipart paths are supported: multipart clients serialize the same array as a JSON string in the
  `media_assets` field; local `files` uploads remain the primary media.
- `media_assets` is persisted by `review_chain_id` into `media_asset_refs` and can be read back via
  `GET /api/v1/reviews/{id}`; an empty array is equivalent to omitting it.
- The field is backward compatible: when omitted, behavior is exactly as before, and local `file_id`s still follow the original
  `media_json`/`documents_json` path.
- **Novel covers (`<work>:novelcover`) also appear in the review-chat preview**: for novels with a real cover, the review-chat preview has the same
  shape as the channel publication — the cover photo is sent first (taken from the asset's `source_url`, via the media proxy), then the TXT document.
  This cover is a **one-off message** for the review preview: it is not written into `media_json`/`documents_json`, and the publish side
  still builds the channel root from the canonical asset, so the media counts in the review record do not change (a novel stays `media=0`,
  `documents=1`). Missing assets or invalid fields are silently skipped and never affect submission success. See
  the "Novel covers are equally visible in the review chat" section of [Configuration](CONFIGURATION.md).

### Delivery plan (read-only, Step 11)

`GET /api/v1/reviews/{id}/delivery-plan` returns the plan for "how to get the media to
Telegram" for this review item; TelePost decides the strategy itself and does not pass upstream domain judgments through to clients.

```json
{
  "ok": true,
  "data": {
    "review_id": 7,
    "strategy": "mixed",
    "media_assets": [],
    "entries": [
      {"index": 0, "kind": "photo", "strategy": "telegram_file_id",
       "asset_id": "pixiv-001", "file_id": "AAA", "mime_type": "image/jpeg"},
      {"index": 1, "kind": "photo", "strategy": "remote_url",
       "asset_id": "pixiv-002", "source_url": "https://proxy.example/pixiv/2.jpg"}
    ]
  }
}
```

- `strategy`: `telegram_file_id` (all have local file_ids), `remote_url` (all via URL),
  `mixed`, `empty`.
- With a local `file_id`, `telegram_file_id` is always preferred (zero retransfer); with only a canonical
  `source_url`, it falls back to `remote_url`.
- The endpoint is read-only and does not change delivery behavior; actual publishing is still decided by the existing PublicationService.

### TelegramMediaCache (Step 12, reusing the same row)

The delivery cache adds no separate table: the same `media_asset_refs` row carries the
`file_id` / `file_unique_id` after confirmed delivery (bot-scoped; each bot has its own SQLite database).

- `GET /api/v1/reviews/{id}` returns `file_id` / `file_unique_id` in `media_assets[]`
  (empty string until delivered).
- `GET /api/v1/reviews/{id}/delivery-plan` adopts `telegram_file_id` directly when the cache already has a
  `file_id`, even when `media_json` is empty (reusing Telegram zero-retransfer material).
- The record action is invoked by the delivery chain after confirmed delivery; API callers never fill these two fields manually.

## Review-chat notification

```bash
curl -X POST 'https://example.com/api/bot1/v1/notifications' \
  -H 'Authorization: Bearer tp_xxxx' \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "本次没有符合条件的候选",
    "idempotency_key": "pixivflow:no-match:2026-09-04"
  }'
```

`REVIEW_CHAT_ID` must be configured. `text` is limited to 2000 chars; a duplicate
`idempotency_key` under the same Telegram user returns `duplicate`. A failed send releases the placeholder, allowing safe outbox retry.

## Responses

### Direct mode (only Mini App with `MINIAPP_REVIEW_REQUIRED=false`; API tokens always enter review)

First successful publish (HTTP 200):

```json
{
  "ok": true,
  "data": {
    "status": "published",
    "reused": false,
    "message_id": 123,
    "link": "https://t.me/channel/123",
    "media_count": 1,
    "document_count": 1
  }
}
```

If the upstream times out or disconnects before receiving the response, it should **retry as-is with the same `idempotency_key`**. This does not produce
a second channel message; the response is HTTP 200 with:

```json
{
  "ok": true,
  "data": {
    "status": "published",
    "reused": true,
    "reuse_reason": "idempotent_replay",
    "matched_idempotency_key": "source:123",
    "message_id": 123
  }
}
```

`reuse_reason` has two semantics; the upstream should treat both as success and **must not republish**:

| reuse_reason | Meaning |
|---|---|
| `idempotent_replay` | A retry with the same `idempotency_key` (typical: ACK lost). The returned `message_id` is the one from the first publish; there is only one message in the channel. |
| `duplicate_existing` | A different `idempotency_key` (new slot/trigger), but the same work (target + work_type + work_id, formerly pixiv_id) was already published within the dedup window by **another intent**. No new message is created; `matched_idempotency_key` points to the earlier one. |

### Review mode (fixed for API tokens; Mini App with `MINIAPP_REVIEW_REQUIRED=true`)

The success response is `201`, `status` is `pending_review`, and it includes `review_id` and `reused`.
With `reused=true` it also carries `reuse_reason` (`idempotent_replay` / `duplicate_existing`)
and `matched_idempotency_key`; TelePost fills in the missing `target_id` and sends a reuse notice to the review chat.
201 is returned only after both the review-chat upload and the SQLite record succeed; on a non-2xx response the upstream should keep the task and retry with the same
`idempotency_key`.

When the same `idempotency_key` hits a pending, failed, or within-7-days published record, media is not re-uploaded and no
review record is created, but a notice is sent to the current review chat referencing the original review id and showing its status. So every
successful delivery has visible feedback while duplicates are still avoided.

Errors are uniform:

```json
{"ok": false, "error": {"code": "invalid_token", "message": "…"}}
```

| HTTP | Common codes |
|---|---|
| 400 | `invalid_content_type`, `invalid_multipart`, `invalid_json`, `invalid_media`, `invalid_media_asset`, `missing_files`, `missing_media`, `too_many_files`, `invalid_tags`, `invalid_link` |
| 401 | `invalid_token` |
| 409 | `review_chat_not_configured` |
| 413 | `file_too_large`, `request_too_large` |
| 429 | `rate_limited` |
| 502 | `notification_failed` |
| 503 | `notification_state_failed`, and `retryable_failure` for submission delivery (see below) |

### Submission business ACK

Submission endpoints report the formal business status in `data.business_status`:

| business_status | HTTP | Meaning |
|---|---|---|
| `accepted` | 201 | This publish succeeded (or entered the review queue) |
| `idempotent_replay` | 200 | The same `idempotency_key` has already completed; returns the first result |
| `duplicate_existing` | 200 | Another key already published the same work within the 7-day window |
| `retryable_failure` | 503 | Network/timeout: whether Telegram received it is unknown; the server never auto-resends; check `GET /api/v1/deliveries/lookup` first, then decide |
| `permanent_failure` | 400 | Deterministic rejection (invalid params/media etc.); resending as-is is pointless |

On timeout/network failure, `ok=false` and `data` looks like
`{"business_status":"retryable_failure","reason":"…"}`. The full contract is in the repository spec
[api/openapi.yaml](../../api/openapi.yaml).

## Review management API (MCP/internal tools)

Review management endpoints are only available to a dedicated review token or the owner token; regular submission tokens have read-only access.
The MCP sidecar should set `TELEPOST_MCP_REVIEW_TOKEN`, and can use
`TELEPOST_REVIEW_API_MODE=readonly` to forbid HTTP writes by **API token / MCP** (a safety net for unattended automation).
**Human review** inside the Mini App (reviewer/admin approving/rejecting/refetching via a signed session)
is not restricted by this switch — those are explicit human actions, still governed by RBAC. Full configuration: [MCP review](../MCP_REVIEW.md) (Chinese).

- `GET /api/v1/reviews`: pending-review summary list, supports `limit`, `cursor`
- `GET /api/v1/reviews/history`: review history (terminal states: published/rejected/failed/expired/superseded),
  ordered by decision time descending, `updated_at` keyset pagination (`limit`, `cursor`); shares the same
  `ReviewService` and persisted review state as the pending queue; reviewer/admin only (the Mini App "review history" page uses the same route)
- `GET /api/v1/reviews/{id}`: full text metadata and media index
- `GET /api/v1/reviews/{id}/media/{index}?variant=preview`: restricted image preview
- `GET /api/v1/reviews/{id}/delivery-plan`: read-only delivery plan (Step 11: TelePost decides the media-source strategy)
- `GET /api/v1/reviews/policy`: admin-maintained Markdown review rules
- `POST /api/v1/reviews/{id}/approve`: approve and publish (requires explicit human confirmation)
- `POST /api/v1/reviews/{id}/reject`: reject, with an optional bounded `reason`
- `PATCH /api/v1/reviews/{id}/spoiler`: set spoiler before publishing

These endpoints call the same `ReviewService` as the Telegram review buttons, using the same conditional claim,
publish-failure fallback, and idempotent state transitions; see the OpenAPI fragment in [`openapi.yaml`](../openapi.yaml).

## Review states

```text
pending ──approve──▶ publishing ──▶ published ──delete──▶ deleted
   ├─reject───────────────────────▶ rejected
   ├─publish failed───────────────▶ failed
   └─timeout──────────────────────▶ expired
```

Approve/reject use conditional updates for atomic claiming, so repeated clicks never publish twice. Admins can toggle spoiler in the review chat;
Pixiv-sourced review items with `target_id` can also trigger a target-level refetch. TelePost sends the request to the standalone PixivFlow's authenticated manual entry; after the button returns "submitting", a review-chat notification confirms whether it was admitted. Admission does not mean download or submission succeeded — when a new item arrives it enters the review queue again.

## Publication layout

Channel publication and review preview share the same layout: image/video albums → GIF/audio → document group; each group has at most 10
items, and the caption only appears on the first message. Local images larger than 10 MiB are compressed first, and only sent as documents on failure; album failures
degrade to per-item sends. The production channel uses `discussion`: the channel root keeps the first image group + first file group (images
first, files after); overflow images/files go to the image/file threads of the linked discussion group respectively. For novel submissions only the TXT
document goes to the channel; inline body images are rendered only on the Telegraph read-online page.
