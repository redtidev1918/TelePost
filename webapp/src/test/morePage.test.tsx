import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AppRoot } from '@telegram-apps/telegram-ui';
import { MorePage } from '../pages/More/MorePage';

// vi.mock is hoisted, so the mutable auth state must be created with vi.hoisted.
const state = vi.hoisted(() => ({
  auth: {
    status: 'authenticated' as const,
    user: { telegram_user_id: 42, username: 'e2e', roles: ['submitter'] },
    bootstrap: async () => undefined,
    logout: () => undefined,
    isReviewer: false,
    isAdmin: false,
  },
}));

vi.mock('../auth/AuthProvider', () => ({
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAuth: () => state.auth,
}));

beforeEach(() => {
  state.auth = {
    status: 'authenticated',
    user: { telegram_user_id: 42, username: 'e2e', roles: ['submitter'] },
    bootstrap: async () => undefined,
    logout: () => undefined,
    isReviewer: false,
    isAdmin: false,
  };
  vi.restoreAllMocks();
});

function renderMore() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AppRoot appearance="light">
        <MemoryRouter initialEntries={['/more']}>
          <MorePage />
        </MemoryRouter>
      </AppRoot>
    </QueryClientProvider>,
  );
}

describe('MorePage（第 5 个 Tab 的落地页）', () => {
  it('普通用户看到账号与快捷入口，看不到管理入口', () => {
    renderMore();
    expect(screen.getByText('更多')).toBeTruthy();
    expect(screen.getByTestId('more-submit')).toBeTruthy();
    expect(screen.getByTestId('more-mine')).toBeTruthy();
    expect(screen.getByTestId('more-hot')).toBeTruthy();
    expect(screen.queryByTestId('more-admin')).toBeNull();
    expect(screen.queryByTestId('more-review')).toBeNull();
  });

  it('管理员在「更多」里拿到管理面板入口，而不是多一个底部 Tab', () => {
    state.auth = { ...state.auth, isAdmin: true, user: { telegram_user_id: 42, username: 'e2e', roles: ['admin'] } };
    renderMore();
    expect(screen.getByTestId('more-admin')).toBeTruthy();
  });

  it('审核员在「更多」里也能回到审核工作区', () => {
    state.auth = { ...state.auth, isReviewer: true, user: { telegram_user_id: 42, username: 'e2e', roles: ['reviewer'] } };
    renderMore();
    expect(screen.getByTestId('more-review')).toBeTruthy();
  });

  it('不暴露内部字段：账号区只显示用户名与角色', () => {
    renderMore();
    expect(document.body.textContent).toContain('@e2e');
    expect(document.body.textContent).not.toContain('telegram_user_id');
  });
});
