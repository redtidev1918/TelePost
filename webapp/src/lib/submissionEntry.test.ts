import { describe, expect, it } from 'vitest';
import { SUBMIT_START_PARAM, submissionIntent } from '../lib/submissionEntry';

describe('submissionIntent (§submission-entrypoint)', () => {
  it('maps startapp=submit to the submit route', () => {
    expect(submissionIntent('submit')).toBe('/submit');
    expect(submissionIntent(SUBMIT_START_PARAM)).toBe('/submit');
  });

  it('ignores every other start param (pure navigation intent)', () => {
    expect(submissionIntent(undefined)).toBeNull();
    expect(submissionIntent(null)).toBeNull();
    expect(submissionIntent('')).toBeNull();
    expect(submissionIntent('referral-abc')).toBeNull();
    // Any identity-like payload must never be treated as navigation.
    expect(submissionIntent('user=12345')).toBeNull();
    expect(submissionIntent('token=xyz')).toBeNull();
  });
});