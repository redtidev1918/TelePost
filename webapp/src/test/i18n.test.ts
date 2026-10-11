import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, bootstrapSession, clearSession } from '../api/client';
import { navigationForSpace } from '../lib/navigation';
import { reviewStatusLabel } from '../lib/reviewStatus';
import { setBotLanguage, tr } from '../lib/i18n';
import en from '../locales/en.json';
import zh from '../locales/zh.json';

afterEach(async () => {
  clearSession();
  vi.restoreAllMocks();
  await setBotLanguage('zh');
});

describe('Mini App languages', () => {
  it('explains file policy rejections in the Bot language', async () => {
    await setBotLanguage('en');
    expect(new ApiError(400, 'blocked_file_type', '服务器中文提示').message)
      .toBe('This file type is blocked. Send images, videos or TXT/PDF/MD documents instead.');
    expect(new ApiError(400, 'file_metadata_required', '服务器中文提示').message)
      .toBe('Provide the document filename so its file type can be checked.');
    await setBotLanguage('zh');
    expect(new ApiError(400, 'blocked_file_type', 'server message').message)
      .toContain('此文件类型已被拦截');
  });
  it('uses the authenticated Bot language for navigation and status labels', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      ok: true, data: { token: 'synthetic-session', expires_in: 1800, bot_language: 'en', user: { telegram_user_id: 42 } },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    await bootstrapSession('synthetic-init-data');
    expect(navigationForSpace(true).map((item) => item.label)).toEqual(['Home', 'Hot', 'Submit', 'Mine', 'Review']);
    expect(reviewStatusLabel('superseded')).toBe('Superseded');
    expect(document.documentElement.lang).toBe('en');
    expect(tr('附件 {{p0}}', { p0: '中文图片.png' })).toBe('Attachment 中文图片.png');
  });

  it('defaults to Chinese with an older session response', async () => {
    await setBotLanguage('en');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      ok: true, data: { token: 'synthetic-session', expires_in: 1800 },
    }), { status: 200 }));
    await bootstrapSession('synthetic-init-data');
    expect(navigationForSpace(false)[0].label).toBe('首页');
  });

  it('ships both catalogs with matching interpolation fields', () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(zh).sort());
    for (const [message, translated] of Object.entries(en)) {
      expect(translated.match(/\{\{\w+\}\}/g)?.sort() ?? []).toEqual(message.match(/\{\{\w+\}\}/g)?.sort() ?? []);
    }
  });
});
