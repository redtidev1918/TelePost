import { describe, expect, it, vi, beforeEach } from 'vitest';
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AppRoot } from '@telegram-apps/telegram-ui';
import { AdminPage } from '../pages/Admin/AdminPage';
import * as admin from '../api/admin';
import { ApiError } from '../api/client';

function snapshot(overrides: Partial<admin.AdminStatusSnapshot> = {}): admin.AdminStatusSnapshot {
  return {
    version: { version: '2.47.0', commit: 'abc123def4567890' },
    queue: { pending: 3, staging: 1, failed: 0, superseded: 2, published: 40, rejected: 5 },
    refetch: { active: 1, recent_failures: [] },
    submissions_24h: 12,
    blacklist_size: 1,
    policy: {
      api_review_required: true,
      miniapp_review_required: true,
      chat_review_required: false,
      show_submitter: true,
      overrides: [],
      review_chat_configured: true,
      channel_configured: true,
    },
    restart_managed: true,
    ...overrides,
  };
}

function renderAdmin() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AppRoot>
        <MemoryRouter initialEntries={['/admin']}>
          <AdminPage />
        </MemoryRouter>
      </AppRoot>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(admin, 'fetchAdminStatus').mockResolvedValue(snapshot());
  vi.spyOn(admin, 'fetchRoleBindings').mockResolvedValue([
    { telegram_user_id: 42, role: 'reviewer', created_by: 'telegram_user:1', created_at: '2026-09-19T00:00:00Z' },
  ]);
  vi.spyOn(admin, 'fetchBlacklist').mockResolvedValue([
    { user_id: 7, reason: '刷屏', added_at: '2026-09-19T00:00:00Z' },
  ]);
});

afterEach(cleanup);

describe('AdminPage', () => {
  it('renders the operational snapshot', async () => {
    renderAdmin();
    const status = await screen.findByTestId('admin-status');
    expect(status.textContent).toContain('2.47.0');
    expect(status.textContent).toContain('abc123de');
    expect(status.textContent).toContain('3 待审');
    expect(status.textContent).toContain('近 24 小时 12 次投稿');
  });

  it('lists role bindings with grantor and exposes grant actions', async () => {
    renderAdmin();
    const items = await screen.findAllByTestId('admin-role-item');
    expect(items).toHaveLength(1);
    expect(items[0].textContent).toContain('用户 42');
    expect(items[0].textContent).toContain('reviewer');
    expect(items[0].textContent).toContain('telegram_user:1');
    expect(screen.getByTestId('admin-role-add-reviewer')).toBeTruthy();
    expect(screen.getByTestId('admin-role-add-admin')).toBeTruthy();
  });

  it('grants a role from the numeric input and refreshes the list', async () => {
    const addRole = vi.spyOn(admin, 'addRoleBinding').mockResolvedValue({
      telegram_user_id: 100,
      role: 'admin',
      changed: true,
    });
    renderAdmin();
    await screen.findAllByTestId('admin-role-item');

    const input = screen.getByPlaceholderText('Telegram 用户 ID（数字）') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '100' } });
    fireEvent.click(screen.getByTestId('admin-role-add-admin'));

    await waitFor(() => expect(addRole).toHaveBeenCalledWith(100, 'admin'));
    // Input clears after a successful grant.
    await waitFor(() => expect(input.value).toBe(''));
  });

  it('surfaces a server rejection message instead of failing silently', async () => {
    vi.spyOn(admin, 'addRoleBinding').mockRejectedValue(
      new ApiError(409, 'invalid_state', 'root 用户不能被绑定'),
    );
    renderAdmin();
    await screen.findAllByTestId('admin-role-item');

    const input = screen.getByPlaceholderText('Telegram 用户 ID（数字）') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '100' } });
    fireEvent.click(screen.getByTestId('admin-role-add-reviewer'));

    expect(await screen.findByText('root 用户不能被绑定')).toBeTruthy();
  });

  it('adds a blacklist entry with reason, then clears the form', async () => {
    const addEntry = vi.spyOn(admin, 'addBlacklistEntry').mockResolvedValue({
      user_id: 9,
      added: true,
      reason: '盗图',
    });
    renderAdmin();
    await screen.findByTestId('admin-blacklist-item');

    const idInput = screen.getByPlaceholderText('要拉黑的 Telegram 用户 ID') as HTMLInputElement;
    const reasonInput = screen.getByPlaceholderText('原因（可选）') as HTMLInputElement;
    fireEvent.change(idInput, { target: { value: '9' } });
    fireEvent.change(reasonInput, { target: { value: '盗图' } });
    fireEvent.click(screen.getByTestId('admin-blacklist-add-button'));

    await waitFor(() => expect(addEntry).toHaveBeenCalledWith(9, '盗图'));
    await waitFor(() => expect(idInput.value).toBe(''));
  });

  it('shows policy toggles reflecting the effective snapshot', async () => {
    renderAdmin();
    const policy = await screen.findByTestId('admin-policy');
    expect(policy.textContent).toContain('审核群复审');
    expect(policy.textContent).toContain('公开投稿人');
    // Restart semantics are surfaced, never hidden.
    expect(policy.textContent).toContain('自动重启');
  });

  it('reports load failure with the server message', async () => {
    vi.spyOn(admin, 'fetchAdminStatus').mockRejectedValue(
      new ApiError(403, 'permission_denied', '需要管理员权限'),
    );
    renderAdmin();
    expect(await screen.findByText(/需要管理员权限/)).toBeTruthy();
  });
});
