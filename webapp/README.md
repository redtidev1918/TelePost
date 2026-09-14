# TelePost Mini App (webapp)

Telegram Mini App frontend for TelePost: submission, personal submission
history, and the review/moderation console. It is a **presentation adapter**
only — every mutation flows through the TelePost canonical HTTP API, which
shares one domain / application / repository / state machine with the
Telegram Bot.

## Stack

- React 18 + TypeScript + Vite
- [@telegram-apps/sdk-react](https://www.npmjs.com/package/@telegram-apps/sdk-react)
  + [@telegram-apps/telegram-ui](https://www.npmjs.com/package/@telegram-apps/telegram-ui)
- [TanStack Query](https://tanstack.com/query) for server state
- [Uppy](https://uppy.io) for upload UX (progress / retry / cancel)
- Vitest + Testing Library for unit tests

## Development

```bash
npm install
npm run dev        # http://localhost:3000/app/ — proxies /api to a local TelePost
```

A dev mock Telegram environment is injected automatically (`import.meta.env.DEV`).
In production the SDK reads Telegram's raw signed launch data; the legacy
`window.Telegram.WebApp.initData` bridge is only a compatibility fallback.

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
See `docs/` in the repository root for the full deployment walkthrough.
