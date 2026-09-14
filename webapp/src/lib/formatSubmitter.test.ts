import { describe, expect, it } from 'vitest';
import { formatSubmitter } from './formatSubmitter';

describe('formatSubmitter', () => {
  it('prefers @username over display name and ID', () => {
    expect(formatSubmitter('redtide', '红潮', 123)).toBe('@redtide');
    expect(formatSubmitter('@prefixed', '', 1)).toBe('@prefixed');
  });

  it('falls back to the display name when there is no username', () => {
    expect(formatSubmitter('', '红潮', 123)).toBe('红潮');
  });

  it('falls back to the numeric ID when neither is present', () => {
    expect(formatSubmitter('', '', 123456789)).toBe('123456789');
  });

  it('returns empty only when nothing is known', () => {
    expect(formatSubmitter(undefined, undefined, undefined)).toBe('');
    expect(formatSubmitter(null, '  ', null)).toBe('');
  });
});