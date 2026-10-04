import type { ReactNode } from 'react';

/**
 * PageHeader (§ui-kit): one title scale for every route.
 *
 * Secondary pages get their back affordance from the Telegram BackButton, so
 * the header never renders its own back button — a page must not own two back
 * affordances at once (§back).
 */
export function PageHeader({
  title,
  subtitle,
  action,
  testId = 'page-header',
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  testId?: string;
}) {
  return (
    <header className="page-header" data-testid={testId}>
      <div className="page-header__main">
        <h1 className="page-header__title">{title}</h1>
        {subtitle ? <div className="page-header__subtitle">{subtitle}</div> : null}
      </div>
      {action ? <div className="page-header__action">{action}</div> : null}
    </header>
  );
}
