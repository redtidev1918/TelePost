import { describe, expect, it, afterEach } from 'vitest';
import { apiBase } from '../api/client';

afterEach(() => {
  // restore a clean URL
  window.history.replaceState({}, '', '/app/');
});

describe('apiBase', () => {
  it('defaults to bot1 when no bot param', () => {
    window.history.replaceState({}, '', '/app/');
    expect(apiBase()).toBe('/api/bot1/v1');
  });

  it('uses the bot param from the launch URL', () => {
    window.history.replaceState({}, '', '/app/?bot=bot2');
    expect(apiBase()).toBe('/api/bot2/v1');
  });

  it('keeps custom query params alongside bot', () => {
    window.history.replaceState({}, '', '/app/?start=abc&bot=bot2');
    expect(apiBase()).toBe('/api/bot2/v1');
  });
});
