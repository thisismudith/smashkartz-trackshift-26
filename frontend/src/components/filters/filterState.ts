/**
 * Filter state and its URL encoding.
 *
 * Deliberately NOT a "use client" module. `page.tsx` is a server component and resolves the
 * filters from searchParams before the first paint; a client-only module cannot be called from
 * there, and importing one throws "Attempted to call parseFilters() from the server".
 *
 * The URL is the store. Not context, not a module singleton: a filtered view is a thing you
 * want to send someone, and a chart that cannot be linked to is a chart you have to describe in
 * words instead. It also means back/forward work, and a reload lands where you were.
 *
 * Encoding is `?circuits=british-grand-prix,monaco-grand-prix&drivers=HAM,LEC`. A facet absent
 * from the URL is absent from the selection -- there is no "empty means all" shorthand, because
 * that makes "I cleared this" and "I never touched this" indistinguishable.
 */
export const FACETS = ["years", "circuits", "sessions", "teams", "drivers"] as const;
export type Facet = (typeof FACETS)[number];

export type FilterState = Record<Facet, string[]>;

export const EMPTY_FILTERS: FilterState = {
  years: [],
  circuits: [],
  sessions: [],
  teams: [],
  drivers: [],
};

/** Parse a filter state out of a query string. Unknown params are ignored, not an error. */
export function parseFilters(search: string | URLSearchParams): FilterState {
  const q = typeof search === "string" ? new URLSearchParams(search) : search;
  const out: FilterState = { ...EMPTY_FILTERS };
  for (const f of FACETS) {
    const raw = q.get(f);
    out[f] = raw
      ? raw
          .split(",")
          .map((v) => decodeURIComponent(v).trim())
          .filter(Boolean)
      : [];
  }
  return out;
}

/** Serialise to a query string, omitting empty facets so a clean state has a clean URL. */
export function serialiseFilters(state: FilterState, base?: URLSearchParams): URLSearchParams {
  const q = new URLSearchParams(base);
  for (const f of FACETS) {
    const v = state[f];
    if (v.length === 0) q.delete(f);
    else q.set(f, v.map(encodeURIComponent).join(","));
  }
  return q;
}

