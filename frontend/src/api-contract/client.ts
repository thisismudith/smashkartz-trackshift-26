/**
 * Real fetch calls against the API.md service. No backend implements these routes yet
 * (no src/trackshift/serve/), so every call here fails with a network error today — that
 * failure is surfaced to the caller, never swallowed into a fabricated response. The day
 * the service exists, point NEXT_PUBLIC_TRACKSHIFT_API_BASE at it and these calls start
 * succeeding with no code change.
 */
import type { ApiCallResult, PlanRequestBody, ShadowPriceQuery, ShadowPriceResponse, PlanResponse } from "./types";

const API_BASE = (process.env.NEXT_PUBLIC_TRACKSHIFT_API_BASE || "http://localhost:8000/api/v1").replace(/\/$/, "");

async function call<T>(
  method: "GET" | "POST",
  path: string,
  opts: { query?: Record<string, string | number>; body?: unknown } = {},
): Promise<ApiCallResult<T>> {
  const qs = opts.query
    ? `?${new URLSearchParams(Object.entries(opts.query).map(([k, v]) => [k, String(v)])).toString()}`
    : "";
  const url = `${API_BASE}${path}${qs}`;
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

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    return { ok: false, status: res.status, error: text || res.statusText, request };
  }
  return { ok: true, status: res.status, data: (await res.json()) as T, request };
}

export function fetchShadowPrice(event: string, query: ShadowPriceQuery) {
  return call<ShadowPriceResponse>("GET", `/value/${event}/shadow_price`, {
    query: { energy_kj: query.energy_kj, time_gap_s: query.time_gap_s, eligibility: query.eligibility },
  });
}

export function postPlan(body: PlanRequestBody) {
  return call<PlanResponse>("POST", "/plan", { body });
}
