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
 * The bundle is read through a Next route handler (`/api/replay/...`) that streams it from
 * a directory OUTSIDE the repo, so generated bundle files are never copied into
 * `public/` and never end up committed.
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
 *   3. the same-origin proxy            — which forwards to TRACKSHIFT_API_ORIGIN,
 *                                         defaulting to http://127.0.0.1:8000/api/v1,
 *                                         the address in the integration brief
 *
 * The default is the proxy rather than the absolute URL so that the address of the backend
 * is a SERVER-side setting. Baking it into the client bundle means it can only be changed by
 * rebuilding, and it is exactly the sort of thing that differs between machines — 8000 is
 * not reliably free. `?api=` remains for pointing one page somewhere else without touching
 * configuration at all.
 */
export const LIVE_BASE = (
  process.env.NEXT_PUBLIC_TRACKSHIFT_API_BASE || "/api/live"
).replace(/\/+$/, "");

/** Served by src/app/api/replay/[...path]/route.ts from TRACKSHIFT_REPLAY_DIR. */
export const REPLAY_BASE = "/api/replay";


/**
 * Same-origin proxy to the model service (src/app/api/live/[...path]/route.ts).
 *
 * The backend mounts no CORS middleware, so the browser cannot call it directly: the request
 * is blocked before it leaves and surfaces as an unexplained network failure. Absolute live
 * bases are therefore rewritten through this proxy, which runs server-side where the same
 * origin policy does not apply. A base that is already a path is left alone — it is
 * same-origin by definition.
 */
export const PROXY_BASE = "/api/live";

function isAbsolute(base: string): boolean {
  return /^https?:\/\//i.test(base);
}

export function makeSource(mode: SourceMode, base?: string): DataSource {
  const resolved = (base || LIVE_BASE).replace(/\/+$/, "");
  return {
    mode,
    base: resolved,
    label:
      mode === "replay"
        ? "replay bundle (TRACKSHIFT_REPLAY_DIR)"
        : resolved === PROXY_BASE
          ? "TRACKSHIFT_API_ORIGIN via /api/live"
          : resolved,
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
  const params = new URLSearchParams(
    query ? Object.entries(query).map(([k, v]) => [k, String(v)]) : [],
  );

  if (isAbsolute(source.base)) {
    // `__base` tells the proxy which loopback service to forward to; it validates the host.
    params.set("__base", source.base);
    return `${PROXY_BASE}${route.livePath}?${params.toString()}`;
  }

  const qs = params.toString();
  return `${source.base}${route.livePath}${qs ? `?${qs}` : ""}`;
}
