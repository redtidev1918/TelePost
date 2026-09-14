import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { retrieveRawInitData } from '@telegram-apps/sdk';
import { AppRoot } from '@telegram-apps/telegram-ui';
import { AuthProvider, useAuth } from '../auth/AuthProvider';
import { ApiError } from '../api/client';
import { App } from '../app/App';

vi.mock('@telegram-apps/sdk', () => ({ retrieveRawInitData: vi.fn() }));

let lastAuth: ReturnType<typeof useAuth> | null = null;

function Probe() {
  lastAuth = useAuth();
  return <div>probe:{lastAuth?.status}</div>;
}

function setBridge(initData: string) {
  Object.defineProperty(window, 'Telegram', {
    value: {
      WebApp: {
        initData,
        initDataUnsafe: {},
        ready: vi.fn(),
        expand: vi.fn(),
        close: vi.fn(),
        themeParams: {},
        colorScheme: 'light',
        platform: 'test',
        version: '7.0',
        BackButton: { show: vi.fn(), hide: vi.fn(), onClick: vi.fn().mockReturnThis(), offClick: vi.fn().mockReturnThis() },
        HapticFeedback: { notificationOccurred: vi.fn(), impactOccurred: vi.fn() },
      },
    },
    configurable: true,
    writable: true,
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.mocked(retrieveRawInitData).mockReset();
  vi.mocked(retrieveRawInitData).mockReturnValue(undefined);
  lastAuth = null;
});

function renderProbe() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/']}>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('AuthProvider', () => {
  it('shows outside Telegram only when no launch data exists', async () => {
    delete window.Telegram;
    renderProbe();
    await waitFor(() => expect(lastAuth?.status).toBe('outside_telegram'));
  });

  it('bootstraps from SDK raw initData with no WebApp global', async () => {
    const raw = 'user=%7B%22id%22%3A42%7D&auth_date=1700000000&hash=x';
    delete window.Telegram;
    vi.mocked(retrieveRawInitData).mockReturnValue(raw);
    const client = await import('../api/client');
    expect(client.getLaunchInitData()).toBe(raw);
    const bootstrap = vi.spyOn(client, 'bootstrapSession').mockResolvedValue({
      token: 'ma_v1.test',
      expiresAt: Date.now() + 60_000,
    });
    vi.spyOn(client, 'apiFetch').mockResolvedValue({
      telegram_user_id: 42,
      name: 'alice',
      surface: 'mini_app',
      submissions_last_hour: 0,
      rate_limit_per_hour: 5,
      roles: ['submitter', 'reviewer'],
    });
    renderProbe();
    await waitFor(
      () => expect(lastAuth?.status).toBe('authenticated'),
      { timeout: 3000 },
    );
    expect(lastAuth?.isReviewer).toBe(true);
    expect(lastAuth?.user?.telegram_user_id).toBe(42);
    expect(bootstrap).toHaveBeenCalledWith(raw);
  });

  it('uses the legacy bridge only when SDK launch data is unavailable', async () => {
    setBridge('legacy-raw');
    const client = await import('../api/client');
    expect(client.getLaunchInitData()).toBe('legacy-raw');
  });

  it.each([
    [new ApiError(403, 'miniapp_disabled', 'disabled'), 'miniapp_disabled'],
    [new ApiError(401, 'invalid_init_data_signature', 'invalid'), 'invalid_init_data'],
    [new ApiError(401, 'init_data_expired', 'expired'), 'expired_init_data'],
    [new Error('offline'), 'server_unavailable'],
  ] as const)('classifies login failure %s as %s', async (error, expected) => {
    delete window.Telegram;
    vi.mocked(retrieveRawInitData).mockReturnValue('signed-raw');
    const client = await import('../api/client');
    vi.spyOn(client, 'bootstrapSession').mockRejectedValue(error);
    renderProbe();
    await waitFor(() => expect(lastAuth?.status).toBe(expected));
  });

  it('shows a login error rather than the outside-Telegram hint on server rejection', async () => {
    delete window.Telegram;
    vi.mocked(retrieveRawInitData).mockReturnValue('signed-raw');
    const client = await import('../api/client');
    vi.spyOn(client, 'bootstrapSession').mockRejectedValue(
      new ApiError(401, 'invalid_init_data_signature', 'invalid'),
    );
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <AppRoot><MemoryRouter initialEntries={['/']}><App /></MemoryRouter></AppRoot>
      </QueryClientProvider>,
    );
    expect(screen.queryByText('首页')).not.toBeInTheDocument();
    expect(await screen.findByText('小程序登录失败，请关闭后重新打开。')).toBeInTheDocument();
    expect(screen.queryByText('请在 Telegram 中打开 TelePost 小程序。')).not.toBeInTheDocument();
  });
});
