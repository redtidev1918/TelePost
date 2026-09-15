/**
 * Submission deep-link navigation intent (§submission-entrypoint).
 *
 * startapp=submit is NAVIGATION INTENT ONLY. It is never authentication or
 * authorization data — real identity always comes from server-validated
 * Telegram initData (AuthProvider). This module only translates the launch
 * parameter into a route.
 */
export const SUBMIT_START_PARAM = 'submit';

/**
 * Map a Telegram start_param to a route, or null when there is no submission
 * intent. Currently the only supported intent is `submit` → `/submit`.
 */
export function submissionIntent(startParam: string | undefined | null): string | null {
  return startParam === SUBMIT_START_PARAM ? '/submit' : null;
}