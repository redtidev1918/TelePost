# TelePost Mini App (webapp)

Telegram Mini App frontend for TelePost: submission, personal submission
history, and the review/moderation console. Every mutation flows through the TelePost HTTP API, which
shares one domain / application / repository / state machine with the
Telegram Bot.

## Stack

- React 18 + TypeScript + Vite
- [@telegram-apps/sdk-react](https://www.npmjs.com/package/@telegram-apps/sdk-react)
  + [@telegram-apps/telegram-ui](https://www.npmjs.com/package/@telegram-apps/telegram-ui)
- [TanStack Query](https://tanstack.com/query) for server state
- [Uppy](https://uppy.io) for attachment selection, limits, removal, progress, and errors;
  TelePost submits one multipart request with a stable idempotency key
- Vitest + Testing Library for unit tests

## Information architecture (mobile first)

The bottom bar has **at most five items for every role** — reviewer and admin
surfaces are workspaces behind the 5th slot, never extra tabs:

| role       | bottom nav                          |
| ---------- | ----------------------------------- |
| submitter  | 首页 / 热门 / 投稿 / 我的 / 更多     |
| reviewer   | 首页 / 热门 / 投稿 / 我的 / 审核     |
| admin      | same five; 管理 lives in 更多        |

- `审核` is one workspace with two tabs (待处理 / 历史): `/review` and
  `/review/history` are two states of the same page.
- `更多` (`/more`) is the account + shortcuts hub and carries the admin entry.
- Deep routes belong to their section: `/mine/:id` → 我的, `/review/:id` → 审核,
  `/post/:id` → 热门, `/admin` → none (it is a secondary workspace).
- Level-1 pages use the bottom nav; every deeper page uses the Telegram
  BackButton only — no page renders its own back button.

Single source of truth: `src/lib/navigation.ts` (`navigationForSpace`,
`isNavItemActive`).

## UI kit

`src/components/ui/` holds the small presentation primitives every page shares
(PageHeader, PageSection, StatusBadge, ActionBar, Segmented, EmptyState,
MediaThumb) plus the design tokens in `src/index.css`. These wrap TelegramUI
components and use Telegram theme variables (`--tgui--*`) for light and dark mode.

- Media is lazy: `MediaThumb` only requests bytes when a card approaches the
  viewport, and always renders a placeholder on failure.
- Layout contract: the route content is the only scrolling region and reserves
  the measured bottom-nav height (`--app-tabbar-reserve`), so nothing is
  occluded and no device height is hard-coded.

## Development

```bash
npm ci
npm run dev        # http://localhost:3000/app/ — proxies /api to a local TelePost
```

Browser E2E (Playwright) runs against the dev server with a mocked `/api`:

```bash
npx playwright install --with-deps chromium
npx playwright test                 # functional + responsive + visual baselines
npx playwright test --update-snapshots   # only when a UI change is intended
```

A dev mock Telegram environment is injected automatically (`import.meta.env.DEV`).
Production reads raw launch `initData` from `@telegram-apps/sdk` and uses
`window.Telegram.WebApp` as a compatibility fallback. The server verifies it.

## Commands

```bash
npm run typecheck   # tsc --noEmit
npm run lint        # eslint src
npm test            # vitest run
npm run build       # vite build → dist/ (assets hash-cached)
```

## Security posture

- No bot token / long-lived API token ever enters the bundle. The app calls
  `POST /api/v1/miniapp/session` with Telegram `initData`; the server verifies
  it and returns a short-lived `ma_v1.*` session held in memory (§9-§12).
- Page visibility follows server-verified roles; server-side RBAC is the
  authority (§13-§14, §84).
- User content is rendered as plain text — never `dangerouslySetInnerHTML` (§52).
- External links are filtered to http/https in the form (§53).

## Deployment

The `dist/` output is served from the same domain as TelePost's API (e.g.
`https://telepost.example/app/`), removing CORS/cookie/session friction (§68-§69).
See the [Mini App guide](../docs/MINIAPP.md) for configuration and deployment,
and the [testing guide](../docs/TESTING.md) for backend and browser checks.
