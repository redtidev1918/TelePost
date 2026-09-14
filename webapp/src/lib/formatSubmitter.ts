/**
 * Presentation-only identity formatting for the Mini App Web UI.
 *
 * Priority: @username → display name → numeric ID. This renders in the
 * WebView DOM only; it never creates a Telegram message entity. Telegram
 * review/system messages stay mention-free (server-side invariant, §ghost-
 * mention) — @-display here must never leak into caption/summary formatters.
 */
export function formatSubmitter(
  username?: string | null,
  displayName?: string | null,
  id?: number | string | null,
): string {
  const uname = (username ?? '').trim().replace(/^@/, '');
  if (uname) return `@${uname}`;
  const dname = (displayName ?? '').trim();
  if (dname) return dname;
  return id !== undefined && id !== null && String(id).trim() !== '' ? String(id) : '';
}