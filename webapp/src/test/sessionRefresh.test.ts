import { afterEach, expect, it, vi } from 'vitest';
import { retrieveRawInitData } from '@telegram-apps/sdk';
import { apiFetch, clearSession, setSession } from '../api/client';

vi.mock('@telegram-apps/sdk', () => ({ retrieveRawInitData: vi.fn() }));

afterEach(() => {
  clearSession();
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/app/');
});

it('renews an expired session from SDK initData and retries once on the same bot', async () => {
  delete window.Telegram;
  window.history.replaceState({}, '', '/app/?bot=bot2');
  vi.mocked(retrieveRawInitData).mockReturnValue('signed-raw');
  setSession('ma_v1.old', 1800);
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({ status: 401 })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ data: { token: 'ma_v1.new', expires_in: 1800 } }) })
    .mockResolvedValueOnce({ status: 200, ok: true, json: async () => ({ data: { id: 7 } }) });
  vi.stubGlobal('fetch', fetchMock);

  expect(await apiFetch('/me')).toEqual({ id: 7 });
  expect(fetchMock).toHaveBeenCalledTimes(3);
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    '/api/bot2/v1/me', '/api/bot2/v1/miniapp/session', '/api/bot2/v1/me',
  ]);
  expect(JSON.parse(fetchMock.mock.calls[1][1].body).initData).toBe('signed-raw');
  expect(fetchMock.mock.calls[2][1].headers.Authorization).toBe('Bearer ma_v1.new');
});
