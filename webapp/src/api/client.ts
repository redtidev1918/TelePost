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

/** API base prefix for the bot this Mini App instance talks to. */
let selectedBot = 'bot1';
export function apiBase(): string {
  const bot = new URLSearchParams(window.location.search).get('bot');
  if (bot && /^bot[1-9]\d*$/.test(bot)) selectedBot = bot;
  return `/api/${selectedBot}/v1`;
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
  | 'unknown';

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  readonly body: unknown;

  constructor(status: number, code: ApiErrorCode, message: string, body?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.body = body;
  }
}

interface SessionState {
  token: string;
  expiresAt: number;
}

let currentSession: SessionState | null = null;
let sessionPromise: Promise<SessionState> | null = null;

export function setSession(token: string, ttlSeconds: number): void {
  currentSession = { token, expiresAt: Date.now() + ttlSeconds * 1000 };
}

export function clearSession(): void {
  currentSession = null;
}

export function hasSession(): boolean {
  return currentSession !== null && Date.now() < currentSession.expiresAt;
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
        body?.error?.message || '登录失败', body);
    }
    const data = body?.data;
    setSession(data.token, data.expires_in || 1800);
    return currentSession!;
  })().finally(() => {
    sessionPromise = null;
  });
  return sessionPromise;
}

/** Read raw signed initData from the SDK, then the legacy WebApp bridge. */
export function getLaunchInitData(): string {
  try {
    const raw = retrieveRawInitData();
    if (raw) return raw;
  } catch {
    // No SDK launch params: the legacy bridge may still be available.
  }
  const tg = (window as unknown as { Telegram?: { WebApp?: { initData?: string } } })
    .Telegram?.WebApp;
  return tg?.initData || '';
}

export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  if (!currentSession) {
    throw new ApiError(401, 'invalid_token', '未登录');
  }
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string> | undefined),
    Authorization: `Bearer ${currentSession.token}`,
  };
  let response = await fetch(`${apiBase()}${path}`, { ...init, headers });
  if (response.status === 401 && currentSession) {
    clearSession();
    const initData = getLaunchInitData();
    if (!initData) throw new ApiError(401, 'session_expired', '会话已过期，请重新打开小程序');
    await bootstrapSession(initData);
    headers.Authorization = `Bearer ${currentSession!.token}`;
    response = await fetch(`${apiBase()}${path}`, { ...init, headers });
    if (response.status === 401) {
      clearSession();
      throw new ApiError(401, 'session_expired', '会话已过期，请重新打开小程序');
    }
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      ((body?.error?.code as string) || 'unknown') as ApiErrorCode,
      body?.error?.message || `请求失败 (${response.status})`,
      body,
    );
  }
  const payload = (await response.json().catch(() => ({}))) as { ok?: boolean; data?: T };
  if (payload.ok === false) {
    const err = payload as unknown as { error?: { code?: string; message?: string } };
    throw new ApiError(
      400,
      (err.error?.code as ApiErrorCode) || 'unknown',
      err.error?.message || '请求失败',
      payload,
    );
  }
  return payload.data as T;
}
