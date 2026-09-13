/**
 * The only place in the UI that calls the model backend.
 *
 * Every function returns `Result<T>` rather than throwing or returning `T | null`. A null
 * would collapse four genuinely different situations into one blank panel — backend down,
 * 404 for this battle, a shape this build cannot render, and "the replay bundle has no
 * such file" all need different words on screen. `Result` keeps them apart so the panels
 * can say which one happened.
 *
 * Nothing here retries. A synthetic development service that fails is telling you
 * something, and silently trying again hides it.
 */
import {
  ROUTES,
  resolveUrl,
  type DataSource,
  type RouteResolution,
} from "./source";
import {
  describeShape,
  findDrsViolations,
  isRecord,
  shapes,
  type Result,
  type ShapeCheck,
} from "./guards";
import type {
  BattlesResponse,
  MetaResponse,
  PassPredictResponse,
  PlanResponse,
  PoliciesResponse,
  PowerEnvelopeResponse,
  RulesResponse,
  SimulateResponse,
  TimelineResponse,
  TrackResponse,
  ValidationResponse,
} from "./types";

/** Pulls `{"detail": {"code": ..., "message": ...}}` out of a FastAPI error body. */
function readErrorBody(body: unknown, fallback: string): { code?: string; message: string } {
  if (isRecord(body) && "detail" in body) {
    const d = body.detail;
    if (typeof d === "string") return { message: d };
    if (isRecord(d)) {
      return {
        code: typeof d.code === "string" ? d.code : undefined,
        message: typeof d.message === "string" ? d.message : fallback,
      };
    }
  }
  return { message: fallback };
}

interface RequestOpts {
  query?: Record<string, string | number>;
  body?: unknown;
  /** Forced to GET in replay mode — a bundle file cannot take a POST. */
  method?: "GET" | "POST";
}

async function request<T>(
  source: DataSource,
  route: RouteResolution,
  shape: ShapeCheck<T>,
  opts: RequestOpts = {},
): Promise<Result<T>> {
  const url = resolveUrl(source, route, opts.query);

  if (url === null) {
    return {
      ok: false,
      kind: "unsupported",
      message: route.replayGapReason ?? `${shape.what} is not available in ${source.mode} mode`,
      url: null,
      mode: source.mode,
    };
  }

  // Client-side half of the 2026 DRS boundary. The backend enforces it too; this keeps the
  // field off the wire entirely and names the key at the call site.
  if (opts.body !== undefined) {
    const violations = findDrsViolations(opts.body);
    if (violations.length > 0) {
      return {
        ok: false,
        kind: "malformed",
        code: "FEATURE_SCHEMA_MISMATCH",
        message:
          `refused to send ${shape.what}: ` +
          violations.map((v) => `${v.path} — ${v.reason}`).join("; "),
        url,
        mode: source.mode,
      };
    }
  }

  // Replay serves static files, so a POST route becomes a GET of its precomputed body.
  const method = source.mode === "replay" ? "GET" : opts.method ?? (opts.body ? "POST" : "GET");
  const sendBody = method === "POST" && opts.body !== undefined;

  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers: sendBody ? { "Content-Type": "application/json" } : undefined,
      body: sendBody ? JSON.stringify(opts.body) : undefined,
      cache: "no-store",
    });
  } catch (e) {
    return {
      ok: false,
      kind: "network",
      message:
        e instanceof Error ? e.message : "the request did not complete",
      url,
      mode: source.mode,
    };
  }

  let body: unknown = null;
  const text = await res.text().catch(() => "");
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      if (res.ok) {
        return {
          ok: false,
          kind: "malformed",
          message: `${shape.what} returned ${res.status} but the body is not JSON: ${text.slice(0, 160)}`,
          status: res.status,
          url,
          mode: source.mode,
        };
      }
    }
  }

  if (!res.ok) {
    const { code, message } = readErrorBody(body, text || res.statusText || `HTTP ${res.status}`);
    return { ok: false, kind: "http", status: res.status, code, message, url, mode: source.mode };
  }

  if (!shape.check(body)) {
    return {
      ok: false,
      kind: "malformed",
      message: `${shape.what} expected ${shape.expected}, got ${describeShape(body)}`,
      status: res.status,
      url,
      mode: source.mode,
    };
  }

  return { ok: true, data: body, url, mode: source.mode };
}

export const fetchMeta = (s: DataSource) =>
  request<MetaResponse>(s, ROUTES.meta(), shapes.meta);

export const fetchValidation = (s: DataSource) =>
  request<ValidationResponse>(s, ROUTES.validation(), shapes.validation);

export const fetchBattles = (s: DataSource) =>
  request<BattlesResponse>(s, ROUTES.battles(), shapes.battles);

export const fetchTrack = (s: DataSource, event: string) =>
  request<TrackResponse>(s, ROUTES.track(event), shapes.track);

export const fetchRules = (s: DataSource, event: string) =>
  request<RulesResponse>(s, ROUTES.rules(event), shapes.rules);

export const fetchPolicies = (s: DataSource) =>
  request<PoliciesResponse>(s, ROUTES.policies(), shapes.policies);

export const fetchPowerEnvelope = (s: DataSource, event: string, stepKmh = 5) =>
  request<PowerEnvelopeResponse>(s, ROUTES.powerEnvelope(event), shapes.powerEnvelope, {
    query: { mode: "both", step_kmh: stepKmh },
  });

/**
 * The bundle holds one timeline file and serves it for any id, so a replay hit is checked
 * against the id that was asked for. Showing another battle's laps under this battle's
 * name would be the worst kind of quiet wrong answer.
 */
export async function fetchTimeline(s: DataSource, battleId: string): Promise<Result<TimelineResponse>> {
  const r = await request<TimelineResponse>(s, ROUTES.timeline(battleId), shapes.timeline);
  if (r.ok && r.data.battle_id !== battleId) {
    return {
      ok: false,
      kind: "malformed",
      message:
        `asked for battle ${battleId} but the ${s.mode} source returned ${r.data.battle_id}. ` +
        "In replay mode the bundle contains a single battle; rebuild it with --battle for this id.",
      url: r.url,
      mode: s.mode,
    };
  }
  return r;
}

export const postPlan = (s: DataSource, body: Record<string, unknown>) =>
  request<PlanResponse>(s, ROUTES.plan(), shapes.plan, { body, method: "POST" });

export const postSimulate = (s: DataSource, body: Record<string, unknown>) =>
  request<SimulateResponse>(s, ROUTES.simulate(), shapes.simulate, { body, method: "POST" });

export const postPassPredict = (s: DataSource, body: Record<string, unknown>) =>
  request<PassPredictResponse>(s, { livePath: "/pass/predict", replayFile: null, replayGapReason: "the replay bundle does not include POST /pass/predict; pass probabilities appear inside the timeline once the backend joins C4" }, shapes.passPredict, { body, method: "POST" });

export type { Result } from "./guards";
