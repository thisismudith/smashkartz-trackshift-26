/**
 * Runtime narrowing at the network boundary, and the 2026 DRS request boundary.
 *
 * Two jobs, both of which exist because a TypeScript type is a compile-time fiction about
 * a synthetic service under active development:
 *
 * 1. VALIDATE WHAT ARRIVES. `as TimelineResponse` is a lie the compiler is happy to tell.
 *    If the backend renames `segments` to `steps` tomorrow (API.md 5.5 already disagrees
 *    with the running service on exactly that), an unchecked cast produces
 *    `segments.map is not a function` inside render — a blank page, no explanation. So
 *    every response is narrowed here and a shape that does not match becomes a `malformed`
 *    result carrying what was expected and what arrived, which the panel renders as a
 *    visible error state.
 *
 * 2. REFUSE TO SEND DRS ON A 2026 REQUEST. The backend already rejects raw `drs`,
 *    `historical_drs_*` and `PROXY_HISTORICAL_DRS` with FEATURE_SCHEMA_MISMATCH (verified
 *    against the running service). Guarding client-side too is not redundant: it means the
 *    forbidden field never crosses the wire at all, and the failure names the offending key
 *    at the call site rather than arriving as a 422 the user has to decode. An all-zero
 *    2026 DRS channel means UNAVAILABLE, not "closed", and the safest way to honour that is
 *    to never let the channel into a 2026 request in the first place.
 */
import type { SourceMode } from "./source";

export type FailureKind =
  /** The request never completed — backend down, CORS, DNS. */
  | "network"
  /** The service answered with a non-2xx status. `code` carries its error code if given. */
  | "http"
  /** 2xx, but the body is not the shape this UI knows how to render. */
  | "malformed"
  /** This delivery mode genuinely cannot answer this route (e.g. replay has no envelope). */
  | "unsupported";

export type Result<T> =
  | { ok: true; data: T; url: string; mode: SourceMode }
  | {
      ok: false;
      kind: FailureKind;
      message: string;
      code?: string;
      status?: number;
      url: string | null;
      mode: SourceMode;
    };

// ---------------------------------------------------------------------------
// 1. Shape narrowing
// ---------------------------------------------------------------------------

export function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * Describes what actually arrived, for the error state. Deliberately shallow and short —
 * it goes on screen, so it must be readable, not a JSON dump.
 */
export function describeShape(v: unknown): string {
  if (v === null) return "null";
  if (Array.isArray(v)) return `array(${v.length})`;
  if (isRecord(v)) {
    const keys = Object.keys(v);
    const head = keys.slice(0, 6).join(", ");
    return `object{${head}${keys.length > 6 ? `, +${keys.length - 6} more` : ""}}`;
  }
  return typeof v;
}

export interface ShapeCheck<T> {
  /** Name used in the error message, e.g. "GET /battles". */
  what: string;
  check: (body: unknown) => body is T;
  /** Plain-English statement of the requirement, shown when the check fails. */
  expected: string;
}

export const shapes = {
  meta: {
    what: "GET /meta",
    expected: "an object with a string `api_version`",
    check: (b: unknown): b is import("./types").MetaResponse =>
      isRecord(b) && typeof b.api_version === "string",
  },
  battles: {
    what: "GET /battles",
    expected: "an object with a `battles` array",
    check: (b: unknown): b is import("./types").BattlesResponse =>
      isRecord(b) && Array.isArray(b.battles),
  },
  timeline: {
    what: "GET /battles/{battle_id}/timeline",
    // Named explicitly because API.md 5.5 documents `steps` and the service sends
    // `segments`; if that ever flips, this message says so instead of crashing.
    expected: "an object with a `segments` array (API.md 5.5 calls this `steps`)",
    check: (b: unknown): b is import("./types").TimelineResponse =>
      isRecord(b) && Array.isArray(b.segments) && typeof b.battle_id === "string",
  },
  track: {
    what: "GET /track/{event}",
    expected: "an object with a string `event`",
    check: (b: unknown): b is import("./types").TrackResponse =>
      isRecord(b) && typeof b.event === "string",
  },
  rules: {
    what: "GET /rules/{event}",
    expected: "an object with a string `event`",
    check: (b: unknown): b is import("./types").RulesResponse =>
      isRecord(b) && typeof b.event === "string",
  },
  powerEnvelope: {
    what: "GET /rules/{event}/power_envelope",
    expected: "an object with a `curves` map of speed/power samples",
    check: (b: unknown): b is import("./types").PowerEnvelopeResponse =>
      isRecord(b) && isRecord(b.curves),
  },
  policies: {
    what: "GET /simulate/policies",
    expected: "an object with a `policies` array",
    check: (b: unknown): b is import("./types").PoliciesResponse =>
      isRecord(b) && Array.isArray(b.policies),
  },
  plan: {
    what: "POST /plan",
    expected: "an object (the planner result)",
    check: (b: unknown): b is import("./types").PlanResponse => isRecord(b),
  },
  simulate: {
    what: "POST /simulate",
    expected: "an object with a `summary`",
    check: (b: unknown): b is import("./types").SimulateResponse =>
      isRecord(b) && isRecord(b.summary),
  },
  validation: {
    what: "GET /validation",
    expected: "an object (per-component metrics)",
    check: (b: unknown): b is import("./types").ValidationResponse => isRecord(b),
  },
  passPredict: {
    what: "POST /pass/predict",
    expected: "an object with `p_pass_by_outcome_horizon`",
    check: (b: unknown): b is import("./types").PassPredictResponse =>
      isRecord(b) && "p_pass_by_outcome_horizon" in b,
  },
} as const;

// ---------------------------------------------------------------------------
// 2. The 2026 DRS request boundary
// ---------------------------------------------------------------------------

/**
 * Exact key `drs`, any key beginning `historical_drs_`, and the literal string value
 * `PROXY_HISTORICAL_DRS` anywhere in the payload. These are the three forms the backend
 * rejects (`validate_feature_admission`), matched here on the way out.
 */
const FORBIDDEN_EXACT = new Set(["drs"]);
const FORBIDDEN_PREFIX = "historical_drs_";
const FORBIDDEN_VALUE = "PROXY_HISTORICAL_DRS";

export interface DrsViolation {
  /** Dotted path to the offending key or value, e.g. `state.era.historical_drs_open`. */
  path: string;
  reason: string;
}

/**
 * Walks a request body and returns every forbidden DRS field. Returns `[]` for a clean
 * payload. Cycles are tolerated (a seen-set), because request bodies are assembled from
 * React state and a cycle would otherwise hang the tab.
 */
export function findDrsViolations(payload: unknown, path = ""): DrsViolation[] {
  const out: DrsViolation[] = [];
  const seen = new Set<object>();

  const walk = (node: unknown, at: string): void => {
    if (typeof node === "string") {
      if (node === FORBIDDEN_VALUE) {
        out.push({
          path: at || "(root)",
          reason: `${FORBIDDEN_VALUE} is a development proxy and cannot enter a 2026 consumer`,
        });
      }
      return;
    }
    if (typeof node !== "object" || node === null) return;
    if (seen.has(node)) return;
    seen.add(node);

    if (Array.isArray(node)) {
      node.forEach((item, i) => walk(item, `${at}[${i}]`));
      return;
    }
    for (const [key, value] of Object.entries(node)) {
      const here = at ? `${at}.${key}` : key;
      if (FORBIDDEN_EXACT.has(key) || key.startsWith(FORBIDDEN_PREFIX)) {
        out.push({
          path: here,
          reason:
            `raw/historical DRS input \`${key}\` is forbidden on a 2026 request; ` +
            "an all-zero 2026 raw DRS channel means unavailable, not closed",
        });
        continue;
      }
      walk(value, here);
    }
  };

  walk(payload, path);
  return out;
}

/** True when the payload is safe to send to a 2026 route. */
export function isDrsClean(payload: unknown): boolean {
  return findDrsViolations(payload).length === 0;
}
