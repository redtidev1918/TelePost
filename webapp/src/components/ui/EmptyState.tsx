import type { ReactNode } from 'react';

/**
 * EmptyState (§ui-kit): an empty list must read as "nothing here yet", never as
 * a broken screen — it always explains what is empty and what to do next.
 */
export function EmptyState({
  title,
  hint,
  action,
  testId = 'empty-state',
}: {
  title: ReactNode;
  hint?: ReactNode;
  action?: ReactNode;
  testId?: string;
}) {
  return (
    <div className="empty-state" data-testid={testId}>
      <div className="empty-state__title">{title}</div>
      {hint ? <div className="empty-state__hint">{hint}</div> : null}
      {action ? <div className="empty-state__action">{action}</div> : null}
    </div>
  );
}
