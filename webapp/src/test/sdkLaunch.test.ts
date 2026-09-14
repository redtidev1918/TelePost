import { expect, it } from 'vitest';
import { getLaunchInitData } from '../api/client';

it('reads signed Telegram launch data from the real SDK without the WebApp bridge', () => {
  delete window.Telegram;
  const raw = 'auth_date=1700000000&hash=test&signature=test&user=%7B%22id%22%3A42%2C%22first_name%22%3A%22Test%22%7D';
  const launch = new URLSearchParams({
    tgWebAppPlatform: 'ios',
    tgWebAppVersion: '8.0',
    tgWebAppThemeParams: '{}',
    tgWebAppData: raw,
  });
  window.history.replaceState({}, '', `/app/?bot=bot2#${launch}`);
  expect(getLaunchInitData()).toBe(raw);
});
