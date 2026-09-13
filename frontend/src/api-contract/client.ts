/**
 * Fetch calls against the TrackShift dev service (src/trackshift/serve/app.py).
 * Run it with `python scripts/serve/run_service.py` from the repo root, then this
 * points at it via NEXT_PUBLIC_TRACKSHIFT_API_BASE (default http://localhost:8000/api/v1).
 *
 * Every response is read honestly: `stub: true` means the backend set
 * `X-TrackShift-Stub` (API.md §3.7/§10) because the underlying model isn't built yet
 * and the body is a correctly-shaped placeholder, not a real prediction. Callers must
 * badge stub responses (API.md §8) rather than presenting them as real. A network
 * failure (backend not running at all) is a separate, `ok: false` case.
 */
import type {
  ApiCallResult,
  PlanRequestBody,
  ShadowPriceQuery,
  ShadowPriceResponse,
  PlanResponse,
  RulesResponse,
  TrackResponse,
} from "./types";

const API_BASE = (process.env.NEXT_PUBLIC_TRACKSHIFT_API_BASE || "http://localhost:8000/api/v1").replace(/\/$/, "");
// /internal/config/* sits beside /api/v1, not under it — same host, sibling prefix.
const INTERNAL_BASE = API_BASE.replace(/\/api\/v1$/, "/internal");

async function call<T>(
  method: "GET" | "POST",
  base: string,
  path: string,
  opts: { query?: Record<string, string | number>; body?: unknown } = {},
): Promise<ApiCallResult<T>> {
  const qs = opts.query
    ? `?${new URLSearchParams(Object.entries(opts.query).map(([k, v]) => [k, String(v)])).toString()}`
    : "";
  const url = `${base}${path}${qs}`;
  const request = { method, url, body: opts.body };

  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers: opts.body ? { "Content-Type": "application/json" } : undefined,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "network error", request };
  }

  const stub = res.headers.get("x-trackshift-stub") === "true";
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    return { ok: false, status: res.status, stub, error: text || res.statusText, request };
  }
  return { ok: true, status: res.status, stub, data: (await res.json()) as T, request };
}

const v1 = <T>(method: "GET" | "POST", path: string, opts?: { query?: Record<string, string | number>; body?: unknown }) =>
  call<T>(method, API_BASE, path, opts);
const internal = <T>(path: string) => call<T>("GET", INTERNAL_BASE, path);

export function fetchMeta() {
  return v1<{ api_version: string; git_commit: string; mode: string; stubs: string[] }>("GET", "/meta");
}

export function fetchRules(event: string) {
  return v1<RulesResponse>("GET", `/rules/${event}`);
}

export function fetchTrack(event: string) {
  return v1<TrackResponse>("GET", `/track/${event}`);
}

export function postLegalActions(event: string, state: Record<string, unknown>) {
  return v1<Record<string, unknown>>("POST", "/rules/legal_actions", { body: { event, state } });
}

/**
 * API.md 5.7 -- the Overtake state machine and P(eligible) at a state.
 *
 * `timeToLineS` is required for a projection and has no sensible default: the
 * probability of being eligible "at some unspecified point ahead" is not a
 * defined quantity, so the service refuses rather than assuming a horizon.
 * `trailingClosingRates` is the recent history the projection's sigma is taken
 * from; without at least two the service floors sigma and says so through
 * `sigma_floored`.
 */
export function postEligibility(
  event: string,
  state: Record<string, unknown>,
  opts: { timeToLineS?: number; trailingClosingRates?: number[] } = {},
) {
  return v1<Record<string, unknown>>("POST", "/rules/eligibility", {
    body: {
      event, state,
      time_to_line_s: opts.timeToLineS,
      trailing_closing_rates: opts.trailingClosingRates,
    },
  });
}

export function fetchShadowPrice(event: string, query: ShadowPriceQuery) {
  return v1<ShadowPriceResponse>("GET", `/value/${event}/shadow_price`, {
    query: { energy_kj: query.energy_kj, time_gap_s: query.time_gap_s, eligibility: query.eligibility },
  });
}

export function postPlan(body: PlanRequestBody) {
  return v1<PlanResponse>("POST", "/plan", { body });
}

export function postPassPredict(body: Record<string, unknown>) {
  return v1<Record<string, unknown>>("POST", "/pass/predict", { body });
}

export function postRivalState(body: Record<string, unknown>) {
  return v1<Record<string, unknown>>("POST", "/rival/state", { body });
}

/** Not part of API.md — backs the /config explorer with a live read of config/*.yaml. */
export function fetchConfigVariables() {
  return internal<{ categories: unknown[] }>("/config/variables");
}

export function fetchConfigEvents() {
  return internal<{ events: unknown[] }>("/config/events");
}
