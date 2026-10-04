import type { ReactNode } from 'react';

export type StatusTone = 'neutral' | 'progress' | 'good' | 'bad' | 'warn';

/**
 * StatusBadge (§ui-kit): the single visual language for state.
 *
 * Database states never reach the screen verbatim — callers pass the
 * user-facing label and only choose a tone.
 */
export function StatusBadge({
  tone = 'neutral',
  children,
  testId,
}: {
  tone?: StatusTone;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <span className={`status-badge status-badge--${tone}`} data-testid={testId}>
      {children}
    </span>
  );
}
