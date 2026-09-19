import { apiFetch } from './client';

/**
 * Admin Control Plane API (§admin-api).
 *
 * Every call hits an admin-only endpoint; the server re-verifies the session's
 * roles on each request (`can_administer`) — the frontend guard only decides
 * what to render, never what is allowed (§45).
 */

/** Read-only operational snapshot from GET /admin/status. */
export interface AdminStatusSnapshot {
  version: { version: string; commit: string };
  queue: {
    pending: number;
    staging: number;
    failed: number;
    superseded: number;
    published: number;
    rejected: number;
  };
  refetch: {
    active: number;
    recent_failures: {
      id: string;
      source_review_id: number;
      state: string;
      failure_code: string | null;
      finished_at: string | null;
    }[];
  };
  submissions_24h: number;
  blacklist_size: number;
  policy: AdminPolicy;
  restart_managed: boolean;
}

/** Effective policy (deployment defaults + durable runtime overrides). */
export interface AdminPolicy {
  api_review_required: boolean;
  miniapp_review_required: boolean;
  chat_review_required: boolean;
  show_submitter: boolean;
  overrides: string[];
  review_chat_configured: boolean;
  channel_configured: boolean;
}

export interface RoleBinding {
  telegram_user_id: number;
  role: 'reviewer' | 'admin';
  created_by: string | null;
  created_at: string | null;
}

export interface RoleBindingMutationResult {
  telegram_user_id: number;
  role: string;
  changed: boolean;
}

export interface BlacklistEntry {
  user_id: number;
  reason: string | null;
  added_at?: string | null;
}

export interface BlacklistMutationResult {
  user_id: number;
  added?: boolean;
  removed?: boolean;
  reason?: string;
}

export function fetchAdminStatus(): Promise<AdminStatusSnapshot> {
  return apiFetch('/admin/status');
}

export function fetchAdminPolicy(): Promise<AdminPolicy> {
  return apiFetch('/admin/policy');
}

/**
 * Flip durable runtime toggles. The Mini App API deliberately exposes only
 * chat_review / show_submitter (+ api_review server-side); miniapp_review
 * stays Bot-side so the panel cannot lock itself out.
 */
export function patchAdminPolicy(
  changes: Partial<Record<'api_review' | 'chat_review' | 'show_submitter', 'on' | 'off'>>,
): Promise<AdminPolicy> {
  return apiFetch('/admin/policy', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(changes),
  });
}

export async function fetchRoleBindings(): Promise<RoleBinding[]> {
  const data = await apiFetch<{ items: RoleBinding[] }>('/admin/roles');
  return data.items;
}

export function addRoleBinding(
  telegramUserId: number,
  role: 'reviewer' | 'admin',
): Promise<RoleBindingMutationResult> {
  return apiFetch('/admin/roles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ telegram_user_id: telegramUserId, role }),
  });
}

export function removeRoleBinding(
  telegramUserId: number,
  role: string,
): Promise<RoleBindingMutationResult> {
  return apiFetch(`/admin/roles/${telegramUserId}/${encodeURIComponent(role)}`, {
    method: 'DELETE',
  });
}

export async function fetchBlacklist(): Promise<BlacklistEntry[]> {
  const data = await apiFetch<{ items: BlacklistEntry[] }>('/admin/blacklist');
  return data.items;
}

export function addBlacklistEntry(
  userId: number,
  reason: string,
): Promise<BlacklistMutationResult> {
  return apiFetch('/admin/blacklist', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, reason }),
  });
}

export function removeBlacklistEntry(userId: number): Promise<BlacklistMutationResult> {
  return apiFetch(`/admin/blacklist/${userId}`, { method: 'DELETE' });
}
