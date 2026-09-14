/**
 * One schema, two delivery modes (API.md 2 / 6).
 *
 * Every panel calls `fetchX(source, ...)` and never learns which mode answered. That is
 * the whole point of this module: the replay bundle and the live service return the same
 * bodies, so the UI is written once. `API.md 2` also says the UI must be buildable against
 * the bundle FIRST, with no running backend and no raw-data dependency — so replay is not
 * a fallback here, it is a first-class mode selected by `?source=replay`.
 *
 * Mapping logical route -> bundle file follows `scripts/serve/build_replay_bundle.py`,
 * which writes exactly nine files. Two consequences the UI must be honest about:
 *
 *  1. The bundle holds ONE battle (the one it was built for). `/battles/{id}/timeline`
 *     resolves to `timeline.json` regardless of id, so we verify the id in the body
 *     matches what was asked for and surface a mismatch instead of showing the wrong car.
 *  2. `/rules/{event}/power_envelope` is NOT in the bundle. Rather than re-implementing
 *     the C3 interpolation client-side (API.md 5.3a exists precisely so we don't), replay
 *     mode reports the sampled envelope as unavailable and the panel falls back to
 *     plotting the rule breakpoints, labelled as breakpoints.
 *
 * The bundle is served as STATIC FILES from `public/api/replay/`, copied there by the
 * `replay:public` npm script before the build. It used to be streamed by a Next route
 * handler from a directory outside the repo; `output: "export"` has no server to run one,
 * so the files are staged into `public/` at build time instead and `.gitignore` keeps the
 * generated copies out of the tree.
 */

export type SourceMode = "live" | "replay";

export interface DataSource {
  mode: SourceMode;
  /** Human-readable origin, shown in the UI so it is always clear what was queried. */
  label: string;
  /** Live base URL for this source. Ignored in replay mode. */
  base: string;
}

/**
 * Where "live" points, in order of precedence:
 *
 *   1. a `base` passed to `makeSource`  — the console reads one from `?api=`
 *   2. NEXT_PUBLIC_TRACKSHIFT_API_BASE  — baked in at build time
 *   3. `/api/v1` on the current origin   — only useful behind a reverse proxy
 *
 * Under `output: "export"` the backend's address has to be a BUILD-time setting: there is
 * no server left to hold a runtime one, and the browser now calls the service directly
 * rather than through a same-origin proxy. So NEXT_PUBLIC_TRACKSHIFT_API_BASE must be set
 * to the container's absolute URL when the site is built, and that URL must appear in the
 * service's TRACKSHIFT_ALLOWED_ORIGINS — a cross-origin call the backend has not been told
 * to accept fails at the preflight. `?api=` remains for pointing one page somewhere else
 * without rebuilding.
 */
export const LIVE_BASE = (
  process.env.NEXT_PUBLIC_TRACKSHIFT_API_BASE || "/api/v1"
).replace(/\/+$/, "");

/**
 * Static replay bundle, staged into `public/api/replay/` by the `replay:public` npm
 * script. Still `/api/replay` because the path is what the exported site serves the files
 * at — there is no route handler behind it any more, just nine JSON files.
 */
export const REPLAY_BASE = "/api/replay";

export function makeSource(mode: SourceMode, base?: string): DataSource {
  const resolved = (base || LIVE_BASE).replace(/\/+$/, "");
  return {
    mode,
    base: resolved,
    label: mode === "replay" ? "replay bundle (static)" : resolved,
  };
}

/** The nine files `build_replay_bundle.py` writes. */
export type BundleFile =
  | "meta"
  | "track"
  | "rules"
  | "battles"
  | "validation"
  | "timeline"
  | "policies"
  | "plan"
  | "simulate";

/**
 * What a logical route resolves to in each mode.
 *
 * `null` for a replay file means the bundle genuinely has no answer for that route. The
 * caller turns that into an `unavailable` result with a reason, which is the required
 * behaviour — not a silent empty object and not a zero.
 */
export interface RouteResolution {
  livePath: string;
  replayFile: BundleFile | null;
  /** Why replay cannot answer, shown to the user verbatim when replayFile is null. */
  replayGapReason?: string;
}

export const ROUTES = {
  meta: (): RouteResolution => ({ livePath: "/meta", replayFile: "meta" }),
  validation: (): RouteResolution => ({ livePath: "/validation", replayFile: "validation" }),
  battles: (): RouteResolution => ({ livePath: "/battles", replayFile: "battles" }),
  timeline: (battleId: string): RouteResolution => ({
    livePath: `/battles/${encodeURIComponent(battleId)}/timeline`,
    replayFile: "timeline",
  }),
  track: (event: string): RouteResolution => ({
    livePath: `/track/${encodeURIComponent(event)}`,
    replayFile: "track",
  }),
  rules: (event: string): RouteResolution => ({
    livePath: `/rules/${encodeURIComponent(event)}`,
    replayFile: "rules",
  }),
  powerEnvelope: (event: string): RouteResolution => ({
    livePath: `/rules/${encodeURIComponent(event)}/power_envelope`,
    replayFile: null,
    replayGapReason:
      "the replay bundle does not include /rules/{event}/power_envelope; " +
      "build_replay_bundle.py writes nine files and this is not one of them",
  }),
  policies: (): RouteResolution => ({ livePath: "/simulate/policies", replayFile: "policies" }),
  plan: (): RouteResolution => ({ livePath: "/plan", replayFile: "plan" }),
  simulate: (): RouteResolution => ({ livePath: "/simulate", replayFile: "simulate" }),
} as const;

export function resolveUrl(source: DataSource, route: RouteResolution, query?: Record<string, string | number>): string | null {
  if (source.mode === "replay") {
    return route.replayFile ? `${REPLAY_BASE}/${route.replayFile}.json` : null;
  }
  // Fetched DIRECTLY, absolute or not. An absolute base used to be rewritten through a
  // same-origin proxy because the service mounted no CORS; it does now, and the exported
  // site has no server to proxy through, so the browser calls the service itself.
  const params = new URLSearchParams(
    query ? Object.entries(query).map(([k, v]) => [k, String(v)]) : [],
  );
  const qs = params.toString();
  return `${source.base}${route.livePath}${qs ? `?${qs}` : ""}`;
}
