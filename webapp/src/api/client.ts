import { tr } from "../lib/i18n";
/**
 * API client for the TelePost canonical HTTP API.
 *
 * Auth: the Mini App calls POST /api/v1/miniapp/session with Telegram
 * initData; the server returns a short-lived session token (ma_v1.*) kept in
 * memory only (§12). Every protected call attaches it via the session module
 * (never localStorage, never in the bundle).
 *
 * Multi-bot: the production router exposes per-bot API prefixes
 * (/api/botN/v1/*) and does not forward bare /api/v1/*. The Mini App is served
 * from one shared URL, so the bot is selected by a `?bot=botN` query param on
 * the launch URL (set by the menu-button script); it defaults to bot1 when
 * absent (single-bot deployments, dev).
 */
import { retrieveRawInitData } from '@telegram-apps/sdk';
import { setBotLanguage } from '../lib/i18n';

/** API base prefix for the bot this Mini App instance talks to. */
export function apiBase(): string {
  const bot = new URLSearchParams(window.location.search).get('bot') || 'bot1';
  return `/api/${bot}/v1`;
}

export type ApiErrorCode =
  | 'invalid_token'
  | 'miniapp_disabled'
  | 'missing_init_data'
  | 'invalid_init_data_signature'
  | 'init_data_expired'
  | 'invalid_init_data_format'
  | 'permission_denied'
  | 'not_found'
  | 'conflict'
  | 'invalid_state'
  | 'review_busy'
  | 'review_already_resolved'
  | 'session_expired'
  | 'blocked_file_type'
  | 'file_metadata_required'
  | 'unknown';

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  readonly body: unknown;

  constructor(status: number, code: ApiErrorCode, message: string, body?: unknown) {
    super(code === 'blocked_file_type'
      ? tr("此文件类型已被拦截。请改发图片、视频或 TXT/PDF/MD 文档。")
      : code === 'file_metadata_required'
        ? tr("请提供文档文件名，以便检查文件类型。")
        : message);
    this.status = status;
    this.code = code;
    this.body = body;
  }
}

/** Presentation-only profile of the verified Mini App user (§identity). */
export interface SessionUserProfile {
  telegram_user_id?: number;
  username?: string;
  display_name?: string;
}

interface SessionState {
  token: string;
  expiresAt: number;
  /** Verified Telegram user profile from the session endpoint (display only). */
  user?: SessionUserProfile;
}

let currentSession: SessionState | null = null;
let sessionPromise: Promise<SessionState> | null = null;

export function setSession(token: string, ttlSeconds: number, user?: SessionUserProfile): void {
  currentSession = { token, expiresAt: Date.now() + ttlSeconds * 1000, user };
}

export function clearSession(): void {
  currentSession = null;
}

export function hasSession(): boolean {
  return currentSession !== null && Date.now() < currentSession.expiresAt;
}

/** The verified Mini App user, for WebView display only (never an authority). */
export function getSessionUser(): SessionUserProfile | null {
  return currentSession?.user ?? null;
}

/** Establish a session from Telegram initData (server-verified, §9-§11). */
export async function bootstrapSession(
  initData: string,
): Promise<SessionState> {
  if (currentSession && Date.now() < currentSession.expiresAt) {
    return currentSession;
  }
  if (sessionPromise) {
    return sessionPromise;
  }
  sessionPromise = (async () => {
    const response = await fetch(`${apiBase()}/miniapp/session`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ initData }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const code = (body?.error?.code as string) || 'unknown';
      throw new ApiError(response.status, (code as ApiErrorCode) || 'unknown',
        body?.error?.message || tr("登录失败"), body);
    }
    const data = body?.data;
    await setBotLanguage(data?.bot_language);
    const user = (data?.user as SessionUserProfile | undefined) ?? undefined;
    setSession(data.token, data.expires_in || 1800, user);
    return currentSession!;
  })().finally(() => {
    sessionPromise = null;
  });
  return sessionPromise;
}

/**
 * Read the raw Telegram initData from the ONE canonical launch source: the
 * Telegram SDK (retrieveRawInitData()), which resolves launch parameters
 * from the WebView navigation context without depending on the injected
 * `window.Telegram.WebApp` bridge global.
 *
 * Priority (canonical first, bridge only as a compatibility fallback):
 *   1. @telegram-apps/sdk raw launch params (tgWebAppData query string)
 *   2. window.Telegram.WebApp.initData (legacy injected script)
 *
 * The SDK path is what makes production auth work even when
 * `window.Telegram` is absent (index.html does not load telegram-web-app.js).
 */
export function getLaunchInitData(): string {
  try {
    // Returns undefined / throws LaunchParamsRetrieveError outside Telegram.
    const raw = retrieveRawInitData();
    if (raw) {
      return raw;
    }
  } catch {
    // Not a Telegram launch context; fall through to the legacy bridge.
  }
  const tg = (window as unknown as { Telegram?: { WebApp?: { initData?: string } } })
    .Telegram?.WebApp;
  return tg?.initData || '';
}

/**
 * How long to wait for the Telegram WebView to inject its launch context
 * before we conclude the page was opened outside Telegram.
 *
 * Some Telegram clients (notably the bot's chat-menu-bar entry vs. a message
 * button) deliver the native bridge / launch params slightly after the app's
 * first synchronous read. A one-shot read that comes up empty must therefore
 * not immediately lock into "outside_telegram": we give the WebView a short,
 * bounded window to populate the launch data and only then fall back.
 */
export const LAUNCH_CONTEXT_WAIT_MS = 800;

/** Detect interval while waiting for the launch context to appear. */
const LAUNCH_CONTEXT_POLL_MS = 100;

/**
 * Resolve the launch initData, waiting (bounded) for an asynchronously
 * injected Telegram launch context. Returns the first non-empty value found
 * (SDK launch params preferred, native bridge as fallback), or '' once the
 * wait window elapses with no launch context at all — i.e. this is a plain
 * browser and the caller should show the "open in Telegram" notice.
 */
export async function waitForLaunchInitData(timeoutMs = LAUNCH_CONTEXT_WAIT_MS): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  // Poll so a bridge that lands just after the first synchronous read still
  // resolves. First check is free (no artificial delay added).
  let value = getLaunchInitData();
  while (!value && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, LAUNCH_CONTEXT_POLL_MS));
    value = getLaunchInitData();
  }
  return value;
}

async function authenticatedFetch(path: string, init?: RequestInit): Promise<Response> {
  if (!currentSession) {
    throw new ApiError(401, 'invalid_token', tr("未登录"));
  }
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string> | undefined),
    Authorization: `Bearer ${currentSession.token}`,
  };
  const response = await fetch(`${apiBase()}${path}`, { ...init, headers });
  if (response.status === 401 && currentSession) {
    // Session expired (natural TTL): clear so the caller can re-bootstrap
    // with the current Telegram initData (§108).
    clearSession();
    throw new ApiError(401, 'session_expired', tr("会话已过期，请重新打开小程序"));
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      ((body?.error?.code as string) || 'unknown') as ApiErrorCode,
      body?.error?.message || tr("请求失败 ({{p0}})", {p0: response.status}),
      body,
    );
  }
  return response;
}

/** Protected media uses the same session/error path; credentials never enter URLs. */
export async function apiBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  return (await authenticatedFetch(path, { signal })).blob();
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  const payload = (await response.json().catch(() => ({}))) as { ok?: boolean; data?: T };
  if (payload.ok === false) {
    const err = payload as unknown as { error?: { code?: string; message?: string } };
    throw new ApiError(
      400,
      (err.error?.code as ApiErrorCode) || 'unknown',
      err.error?.message || tr("请求失败"),
      payload,
    );
  }
  return payload.data as T;
}
