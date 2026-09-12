/**
 * Click-a-legend-entry-to-hide-a-series.
 *
 * Kept as a hook returning a hidden SET rather than as state inside ChartFrame, because the
 * frame does not build the series and must not have to. The caller filters what it passes to the
 * marks, which also means the y domain recomputes from the VISIBLE series -- hiding the outlier
 * rescales the chart, which is the whole reason to hide it.
 */
"use client";

import { useCallback, useMemo, useState } from "react";

export function useSeriesToggle(initial?: Iterable<string>) {
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set(initial ?? []));

  const toggle = useCallback((label: string) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  }, []);

  const clear = useCallback(() => setHidden(new Set()), []);

  /** Filter a series list by the hidden set. */
  const visible = useCallback(
    <T,>(items: readonly T[], key: (t: T) => string) => items.filter((t) => !hidden.has(key(t))),
    [hidden],
  );

  const anyHidden = useMemo(() => hidden.size > 0, [hidden]);

  return { hidden, toggle, clear, visible, anyHidden };
}
