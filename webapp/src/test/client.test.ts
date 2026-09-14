import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { mockTelegramEnv } from '@telegram-apps/sdk';
import { getLaunchInitData } from '../api/client';

const LAUNCH_KEY = 'tapps/launchParams';

/** Valid launch params fixture (same shape main.tsx uses for dev mock). */
function launchParams(initData: string) {
  return {
    tgWebAppData: new URLSearchParams(initData),
    tgWebAppVersion: '8.0',
    tgWebAppPlatform: 'ios',
    tgWebAppThemeParams: ({
      accent_text_color: '#6ab2f2' as `#${string}`,
      bg_color: '#17212b' as `#${string}`,
    }),
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  sessionStorage.removeItem(LAUNCH_KEY);
});

afterEach(() => {
  sessionStorage.removeItem(LAUNCH_KEY);
});

describe('getLaunchInitData (canonical launch-data source)', () => {
  it('returns SDK initDataRaw when window.Telegram is undefined (P0-B regression)', () => {
    const raw = 'user=%7B%22id%22%3A42%2C%22first_name%22%3A%22Test%22%7D&auth_date=1700000000&hash=x&signature=sig';
    Object.defineProperty(window, 'Telegram', {
      value: undefined,
      configurable: true,
      writable: true,
    });
    mockTelegramEnv({ launchParams: launchParams(raw) });
    expect(getLaunchInitData()).toBe(raw);
  });

  it('prefers SDK launch params over a conflicting bridge value', () => {
    const raw = 'user=%7B%22id%22%3A42%2C%22first_name%22%3A%22Test%22%7D&auth_date=1700000000&hash=x&signature=sig';
    mockTelegramEnv({ launchParams: launchParams(raw) });
    Object.defineProperty(window, 'Telegram', {
      value: { WebApp: { initData: 'user=%7B%22id%22%3A999%7D&hash=stale' } },
      configurable: true,
      writable: true,
    });
    expect(getLaunchInitData()).toBe(raw);
  });

  it('falls back to the WebApp bridge when no SDK launch params exist', () => {
    Object.defineProperty(window, 'Telegram', {
      value: { WebApp: { initData: 'bridge-only-data' } },
      configurable: true,
      writable: true,
    });
    expect(getLaunchInitData()).toBe('bridge-only-data');
  });

  it('returns empty when neither SDK params nor the bridge are present', () => {
    Object.defineProperty(window, 'Telegram', {
      value: undefined,
      configurable: true,
      writable: true,
    });
    expect(getLaunchInitData()).toBe('');
  });
});