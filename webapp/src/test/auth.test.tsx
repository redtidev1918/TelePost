import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider, useAuth } from '../auth/AuthProvider';

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
  it('shows unauthorized when no initData is present (outside Telegram)', async () => {
    setBridge('');
    renderProbe();
    await waitFor(() => expect(lastAuth?.status).toBe('unauthorized'));
  });

  it('bootstraps a session when initData is present', async () => {
    setBridge('user=%7B%22id%22%3A42%7D&auth_date=1700000000&hash=x');
    const client = await import('../api/client');
    vi.spyOn(client, 'bootstrapSession').mockResolvedValue({
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
  });
});
