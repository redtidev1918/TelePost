import type { ReactNode } from 'react';

/**
 * PageSection (§ui-kit): label + content + optional footer action.
 *
 * The section never owns padding of its own — the page owns the gutter — so no
 * page can drift into a private spacing scale.
 */
export function PageSection({
  title,
  action,
  onAction,
  hint,
  children,
  testId,
}: {
  title?: ReactNode;
  action?: ReactNode;
  onAction?: () => void;
  hint?: ReactNode;
  children?: ReactNode;
  testId?: string;
}) {
  return (
    <section className="page-section" data-testid={testId}>
      {(title || action) && (
        <div className="page-section__header">
          {title ? <div className="page-section__title">{title}</div> : <span />}
          {action ? (
            <button type="button" className="page-section__action" onClick={onAction}>
              {action}
            </button>
          ) : null}
        </div>
      )}
      {children}
      {hint ? <div className="page-section__hint">{hint}</div> : null}
    </section>
  );
}
