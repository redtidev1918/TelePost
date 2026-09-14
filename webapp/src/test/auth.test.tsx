import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { mockTelegramEnv } from '@telegram-apps/sdk';
import { render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider, useAuth } from '../auth/AuthProvider';
import { ApiError } from '../api/client';
import * as client from '../api/client';

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

/**
 * Inject SDK-only launch params: the P0-B scenario where
 * @telegram-apps/sdk resolves a TMA launch context but the injected
 * `window.Telegram.WebApp` bridge global is absent.
 */
function sdkLaunchOnly(initData: string) {
  Object.defineProperty(window, 'Telegram', {
    value: undefined,
    configurable: true,
    writable: true,
  });
  mockTelegramEnv({
    launchParams: {
      tgWebAppData: new URLSearchParams(initData),
      tgWebAppVersion: '8.0',
      tgWebAppPlatform: 'ios',
      tgWebAppThemeParams: ({
        accent_text_color: '#6ab2f2' as `#${string}`,
        bg_color: '#17212b' as `#${string}`,
      }),
    },
  });
}

function clearSdkLaunch() {
  sessionStorage.removeItem('tapps/launchParams');
}

beforeEach(() => {
  vi.restoreAllMocks();
  clearSdkLaunch();
  lastAuth = null;
});

afterEach(() => {
  clearSdkLaunch();
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

function stubAuthSuccess() {
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
}

function stubAuthFailure(error: ApiError) {
  vi.spyOn(client, 'bootstrapSession').mockRejectedValue(error);
}

describe('AuthProvider', () => {
  it('shows outside_telegram when no launch context exists (plain Chrome)', async () => {
    setBridge('');
    renderProbe();
    await waitFor(() => expect(lastAuth?.status).toBe('outside_telegram'));
  });

  it('bootstraps from SDK launch params even when window.Telegram is undefined', async () => {
    // The P0-B regression: SDK says TMA, but the bridge global is absent.
    sdkLaunchOnly('user={"id":42,"first_name":"Test"}&auth_date=1700000000&hash=x&signature=sig');
    stubAuthSuccess();
    renderProbe();
    await waitFor(
      () => expect(lastAuth?.status).toBe('authenticated'),
      { timeout: 3000 },
    );
    expect(lastAuth?.isReviewer).toBe(true);
    expect(lastAuth?.user?.telegram_user_id).toBe(42);
  });

  it('bootstraps from the bridge fallback when SDK params are absent', async () => {
    setBridge('user={"id":42,"first_name":"Test"}&auth_date=1700000000&hash=x&signature=sig');
    stubAuthSuccess();
    renderProbe();
    await waitFor(() => expect(lastAuth?.status).toBe('authenticated'));
  });

  it('reports server_unavailable, not outside_telegram, on a 500 session error', async () => {
    sdkLaunchOnly('user={"id":42,"first_name":"Test"}&auth_date=1700000000&hash=x&signature=sig');
    stubAuthFailure(new ApiError(500, 'unknown', '服务器错误'));
    renderProbe();
    await waitFor(() => expect(lastAuth?.status).toBe('server_unavailable'));
  });

  it('reports auth_failed, not outside_telegram, on an invalid signature', async () => {
    sdkLaunchOnly('user={"id":42,"first_name":"Test"}&auth_date=1700000000&hash=x&signature=sig');
    stubAuthFailure(new ApiError(401, 'invalid_init_data_signature', '验签失败'));
    renderProbe();
    await waitFor(() => expect(lastAuth?.status).toBe('auth_failed'));
  });

  it('reports miniapp_disabled when the feature is off', async () => {
    sdkLaunchOnly('user={"id":42,"first_name":"Test"}&auth_date=1700000000&hash=x&signature=sig');
    stubAuthFailure(new ApiError(403, 'miniapp_disabled', '未启用'));
    renderProbe();
    await waitFor(() => expect(lastAuth?.status).toBe('miniapp_disabled'));
  });
});