/**
 * The single segmented control.
 *
 * There were five spellings of this in the codebase -- two with role="tablist"/aria-selected,
 * three with role="group"/data-active -- all rendering the same thing. This is the one with the
 * correct semantics: a tablist whose buttons are tabs, so a screen reader announces "2 of 3"
 * rather than reading three unrelated buttons.
 */
"use client";

import s from "./ui.module.css";

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  /** Shown on hover; also the accessible description. */
  title?: string;
  disabled?: boolean;
}

export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  /** Accessible name for the group, e.g. "View" or "Circuit metric". */
  label: string;
  options: readonly SegmentedOption<T>[];
  value: T;
  onChange: (next: T) => void;
}) {
  return (
    <div className={s.segmented} role="tablist" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={o.value === value}
          className={s.segment}
          disabled={o.disabled}
          title={o.title}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
