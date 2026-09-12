/**
 * The client-side half of the filter system: reading and writing the address bar.
 *
 * The pure encoding lives in filterState.ts so a server component can use it too.
 */
"use client";

import { useCallback, useMemo } from "react";
import { FACETS, EMPTY_FILTERS, serialiseFilters, type Facet, type FilterState } from "./filterState";

/**
 * Read/write filter state against the address bar.
 *
 * `initial` is resolved on the server and passed in, so the first client render already matches
 * the URL and there is no read-then-correct flash. Writes use replaceState: a filter change is a
 * refinement of the current view, not a new page, and stacking every checkbox click into history
 * makes the back button useless.
 */
export function useFilters(
  initial: FilterState,
  state: FilterState,
  setState: (next: FilterState) => void,
) {
  const commit = useCallback(
    (next: FilterState) => {
      setState(next);
      if (typeof window === "undefined") return;
      const url = new URL(window.location.href);
      const q = serialiseFilters(next, url.searchParams);
      url.search = q.toString();
      window.history.replaceState(null, "", url);
    },
    [setState],
  );

  const set = useCallback(
    (facet: Facet, values: string[]) => commit({ ...state, [facet]: values }),
    [commit, state],
  );

  const reset = useCallback(() => commit({ ...EMPTY_FILTERS }), [commit]);

  const activeCount = useMemo(
    () => FACETS.reduce((n, f) => n + state[f].length, 0),
    [state],
  );

  const isDirty = useMemo(
    () => FACETS.some((f) => state[f].join(",") !== initial[f].join(",")),
    [state, initial],
  );

  return { set, reset, commit, activeCount, isDirty };
}

/**
 * Narrow a candidate list by the facets that constrain it.
 *
 * Returns BOTH the in-scope values and the out-of-scope ones that are nonetheless still selected,
 * so a caller can show "you picked HAM, but your team filter excludes him" rather than silently
 * dropping the pick. Silently dropping is how a filter UI starts lying about what it is showing.
 */
export function narrow<T>(
  items: T[],
  selected: readonly string[],
  key: (t: T) => string,
  inScope: (t: T) => boolean,
): { scoped: T[]; strandedSelections: string[] } {
  const scoped = items.filter(inScope);
  const scopedKeys = new Set(scoped.map(key));
  return {
    scoped,
    strandedSelections: selected.filter((v) => !scopedKeys.has(v)),
  };
}
