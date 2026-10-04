import type { ReactNode } from 'react';

/**
 * ActionBar (§ui-kit): a page's decisions live in one place at the bottom.
 *
 * `primary` is the single visually dominant action; `secondary` actions share
 * one row underneath so the screen never turns into a button toolbox.
 */
export function ActionBar({
  primary,
  secondary,
  note,
  testId = 'action-bar',
}: {
  primary?: ReactNode;
  secondary?: ReactNode;
  note?: ReactNode;
  testId?: string;
}) {
  return (
    <div className="action-bar" data-testid={testId}>
      {primary ? <div className="action-bar__row">{primary}</div> : null}
      {secondary ? <div className="action-bar__row">{secondary}</div> : null}
      {note ? <div className="action-bar__note">{note}</div> : null}
    </div>
  );
}
