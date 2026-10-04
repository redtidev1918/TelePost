export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  count?: number;
}

/**
 * Segmented (§ui-kit): mobile-first tab/filter control.
 *
 * One row of pills; horizontally scrollable so 320px screens never wrap the
 * control into two squeezed lines.
 */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  testId = 'segmented',
}: {
  value: T;
  options: SegmentedOption<T>[];
  onChange: (next: T) => void;
  testId?: string;
}) {
  return (
    <div className="segmented" role="tablist" data-testid={testId}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={option.value === value}
          data-testid={`${testId}-${option.value}`}
          className={`segmented__item${option.value === value ? ' segmented__item--on' : ''}`}
          onClick={() => onChange(option.value)}
        >
          <span>{option.label}</span>
          {typeof option.count === 'number' && option.count > 0 ? (
            <span className="segmented__count">{option.count}</span>
          ) : null}
        </button>
      ))}
    </div>
  );
}
