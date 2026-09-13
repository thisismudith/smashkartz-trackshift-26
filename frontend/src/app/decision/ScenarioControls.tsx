/**
 * Parameter controls for the decision-state variables API.md's routes take as input
 * (energy, gap, eligibility, tyre, rival belief, horizon), plus the context those routes
 * are scoped by (year and event). Pressing a route button builds the exact request body
 * API.md defines and calls the real client in ./api-contract/client.ts, against
 * src/trackshift/serve/app.py (`python scripts/serve/run_service.py`).
 *
 * Results are shown as a short list of the fields that actually answer the question the
 * route was asked, with the full body one click away behind "Raw JSON". The raw dump used
 * to BE the answer here, which meant reading a probability required scrolling past twelve
 * keys of provenance metadata. The metadata still matters — it is what makes the number
 * trustworthy — so nothing is dropped, only demoted.
 *
 * Nothing on this page recomputes a result locally. A stub is badged as a stub, a null is
 * shown as unavailable with the backend's own reason, and a network error is shown as
 * exactly that.
 */
"use client";

import { useCallback, useState } from "react";
import { fetchShadowPrice, postPlan, postPassPredict, postRivalState } from "@/api-contract/client";
import { EVENT_PROFILES } from "@/config-explorer/events";
import type { ApiCallResult, PlanRequestBody, PlanResponse, ShadowPriceResponse } from "@/api-contract/types";
import s from "./scenario.module.css";

/**
 * Only 2026 has a rule config (config/rules/2026/), and a 2026 Overtake decision is not
 * defined in the DRS era anyway (AGENTS.md 41). The control exists so the scoping is
 * visible and so it stops being a hidden constant in the request body — not to imply
 * there is a season here to choose between.
 */
const YEARS = [2026] as const;

const RIVAL_PRESETS = {
  BALANCED: { CONSERVING: 0.12, BALANCED: 0.31, DEPLOYING: 0.49, DERATING: 0.08 },
  CONSERVING: { CONSERVING: 0.55, BALANCED: 0.3, DEPLOYING: 0.1, DERATING: 0.05 },
  DEPLOYING: { CONSERVING: 0.05, BALANCED: 0.15, DEPLOYING: 0.7, DERATING: 0.1 },
  DERATING: { CONSERVING: 0.1, BALANCED: 0.2, DEPLOYING: 0.15, DERATING: 0.55 },
} as const;

type Pending<T> = ApiCallResult<T> | "pending" | null;
type Json = Record<string, unknown>;

/* ------------------------------------------------------------------ reading a response */

const isRecord = (v: unknown): v is Json => typeof v === "object" && v !== null && !Array.isArray(v);

/** `at(body, "shadow_price", "marginal_value_per_mj")`, undefined at the first missing hop. */
function at(root: unknown, ...path: string[]): unknown {
  let node: unknown = root;
  for (const key of path) {
    if (!isRecord(node)) return undefined;
    node = node[key];
  }
  return node;
}

/** Unwraps `{value, provenance}` as well as a bare number. Non-finite reads as absent. */
function num(v: unknown): number | null {
  const raw = isRecord(v) ? v.value : v;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

function str(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

/** One line of a result summary. A null `value` renders as Unavailable, never as a zero. */
interface Row {
  label: string;
  value: string | null;
  /** Why the value is missing, when it is. Comes from the backend wherever it says. */
  reason?: string | null;
  tag?: string | null;
  strong?: boolean;
}

const fixed = (v: number | null, digits: number, unit = "") =>
  v === null ? null : `${v.toFixed(digits)}${unit}`;
const pct = (v: number | null, digits = 1) => (v === null ? null : `${(v * 100).toFixed(digits)}%`);

/* --------------------------------------------------------------------------- rendering */

function SummaryRow({ row }: { row: Row }) {
  return (
    <div className={s.row}>
      <dt className={s.rowLabel}>{row.label}</dt>
      <dd className={`${s.rowValue} ${row.strong ? s.rowValueStrong : ""}`}>
        {row.value !== null ? (
          <span>{row.value}</span>
        ) : (
          <abbr className={s.na} title={row.reason ?? "The backend returned no value for this field."}>
            Unavailable
          </abbr>
        )}
        {row.tag ? <span className={s.tag}>{row.tag}</span> : null}
      </dd>
    </div>
  );
}

/** The rival belief distribution, which is the whole answer of that route rather than a field of it. */
function Distribution({ p }: { p: Record<string, number> }) {
  const entries = Object.entries(p).sort((a, b) => b[1] - a[1]);
  return (
    <div className={s.dist}>
      {entries.map(([state, weight]) => (
        <div key={state} className={s.distRow}>
          <span className={s.distName}>{state}</span>
          <span className={s.distTrack}>
            <span className={s.distFill} style={{ width: `${Math.max(weight * 100, 0.4)}%` }} />
          </span>
          <span className={s.distValue}>{(weight * 100).toFixed(weight < 0.001 ? 4 : 1)}%</span>
        </div>
      ))}
    </div>
  );
}

interface Summary {
  rows: Row[];
  extra?: React.ReactNode;
}

function ResultCard<T>({
  label,
  route,
  result,
  summarise,
}: {
  label: string;
  route: string;
  result: Pending<T>;
  summarise: (data: T) => Summary;
}) {
  const [raw, setRaw] = useState(false);

  if (result === null) return null;

  if (result === "pending") {
    return (
      <div className={s.card}>
        <p className={s.cardHead}>
          <span className={s.cardTitle}>{label}</span>
          <code className={s.cardRoute}>{route}</code>
          <span className={s.pendingDot}>sending…</span>
        </p>
      </div>
    );
  }

  if (!result.ok) {
    return (
      <div className={`${s.card} ${s.cardError}`}>
        <p className={s.cardHead}>
          <span className={s.cardTitle}>{label}</span>
          <code className={s.cardRoute}>{route}</code>
          <span className={s.badgeError}>{result.status ? `HTTP ${result.status}` : "NO RESPONSE"}</span>
        </p>
        <p className={s.errorText}>{readError(result.error)}</p>
        <p className={s.note}>
          {result.status
            ? "The service answered and refused the request. The body above is its own reason."
            : "The service did not answer at all. Start it with " +
              "`python scripts/serve/run_service.py`, or point NEXT_PUBLIC_TRACKSHIFT_API_BASE at it."}
        </p>
        <button type="button" className={s.rawToggle} onClick={() => setRaw(!raw)}>
          {raw ? "Hide request" : "Show request"}
        </button>
        {raw ? (
          <pre className={s.pre}>
            {result.request.method} {result.request.url}
            {result.request.body ? `\n${JSON.stringify(result.request.body, null, 2)}` : ""}
          </pre>
        ) : null}
      </div>
    );
  }

  // `ok` does not narrow `data` -- ApiCallResult is not a discriminated union -- and a 200
  // with an empty body is a real thing a proxy can return. Say so rather than crash.
  if (result.data === undefined) {
    return (
      <div className={`${s.card} ${s.cardError}`}>
        <p className={s.cardHead}>
          <span className={s.cardTitle}>{label}</span>
          <code className={s.cardRoute}>{route}</code>
          <span className={s.badgeError}>EMPTY BODY</span>
        </p>
        <p className={s.note}>The service answered {result.status ?? "200"} with no JSON body.</p>
      </div>
    );
  }

  const { rows, extra } = summarise(result.data);

  return (
    <div className={`${s.card} ${result.stub ? s.cardStub : ""}`}>
      <p className={s.cardHead}>
        <span className={s.cardTitle}>{label}</span>
        <code className={s.cardRoute}>{route}</code>
        {result.stub ? (
          <span className={s.badgeStub} title="The backend set X-TrackShift-Stub: true (API.md 3.7)">
            STUB
          </span>
        ) : (
          <span className={s.badgeOk}>{result.status} OK</span>
        )}
        <button type="button" className={s.rawToggle} onClick={() => setRaw(!raw)}>
          {raw ? "Hide raw JSON" : "Raw JSON"}
        </button>
      </p>

      {result.stub ? (
        <p className={s.note}>
          {str(at(result.data, "reason")) ??
            "The underlying model is not built yet. This is a correctly-shaped placeholder, not a prediction."}
        </p>
      ) : null}

      <dl className={s.rows}>
        {rows.map((r) => (
          <SummaryRow key={r.label} row={r} />
        ))}
      </dl>
      {extra}

      {raw ? <pre className={s.pre}>{JSON.stringify(result.data, null, 2)}</pre> : null}
    </div>
  );
}

/** FastAPI puts the useful part under `detail`; the client hands us the raw text. */
function readError(text: string | undefined): string {
  if (!text) return "no message";
  try {
    const parsed: unknown = JSON.parse(text);
    const detail = at(parsed, "detail");
    if (isRecord(detail)) {
      const code = str(detail.code);
      const message = str(detail.message) ?? text;
      return code ? `${code} — ${message}` : message;
    }
    if (typeof detail === "string") return detail;
  } catch {
    /* not JSON; show it as sent */
  }
  return text;
}

/* --------------------------------------------------------------- per-route summaries */

function summariseShadowPrice(d: ShadowPriceResponse): Summary {
  const body = d as unknown as Json;
  const sp = at(body, "shadow_price");
  const perMj = num(at(sp, "marginal_value_per_mj"));
  const timeBased = at(sp, "time_based_shadow_price");
  const profile = at(body, "profile");
  return {
    rows: [
      {
        label: "Marginal value of energy",
        value: perMj === null ? null : `${perMj.toFixed(4)} ${str(at(sp, "marginal_value_units")) ?? "per MJ"}`,
        reason: str(at(sp, "reason")),
        tag: str(at(sp, "provenance")) ?? str(at(body, "provenance")),
        strong: true,
      },
      {
        label: "As a time price",
        value: fixed(num(timeBased), 4, ` ${str(at(timeBased, "unit")) ?? "s/MJ"}`),
        reason: str(at(timeBased, "reason")),
        tag: str(at(timeBased, "provenance")),
      },
      { label: "Status", value: str(at(sp, "status")) },
      {
        label: "Probe",
        value: `Δ${num(at(sp, "delta_energy_mj")) ?? "?"} MJ, same state otherwise`,
      },
      {
        label: "Per-segment profile",
        value: Array.isArray(profile) ? `${profile.length} segment${profile.length === 1 ? "" : "s"}` : null,
      },
      { label: "Schema", value: str(at(sp, "schema_version")) },
    ],
  };
}

function summarisePlan(d: PlanResponse): Summary {
  const body = d as unknown as Json;
  const action = at(body, "recommended_action");
  const deploy = num(at(action, "deploy_level"));
  const lift = num(at(action, "lift_amount"));
  const legal = at(body, "legal_actions");
  const excluded = at(body, "excluded_actions");
  const violations = num(at(body, "rule_violations"));
  return {
    rows: [
      {
        label: "Recommended action",
        value: deploy === null ? null : `deploy ${(deploy * 100).toFixed(0)}%${lift ? `, lift ${(lift * 100).toFixed(0)}%` : ""}`,
        reason: str(at(body, "reason")) ?? "The planner returned no recommended action.",
        tag: str(at(body, "provenance")),
        strong: true,
      },
      {
        label: "Power cap here",
        value: fixed(num(at(action, "cap_kw")), 0, " kW"),
        tag: str(at(action, "applicable_mode")),
      },
      { label: "Expected value", value: fixed(num(at(body, "expected_value")), 3, " utility") },
      { label: "Status", value: str(at(body, "status")), reason: str(at(body, "reason")) },
      { label: "Dominant mechanism", value: str(at(body, "decision", "dominant_mechanism")) },
      { label: "Primary constraint", value: str(at(body, "decision", "primary_constraint")) },
      {
        label: "Action set",
        value:
          Array.isArray(legal) && Array.isArray(excluded)
            ? `${legal.length} legal, ${excluded.length} excluded by rule`
            : null,
      },
      {
        label: "Rule violations",
        value: violations === null ? null : String(violations),
        tag: violations === 0 ? null : "MUST BE ZERO",
      },
    ],
  };
}

function summarisePass(d: Json): Summary {
  const p = at(d, "p_pass_by_outcome_horizon");
  // The top-level `interval` belongs to the EMPIRICAL rate table, not to the model's
  // point estimate — the two are separate answers to the same question and the model's
  // can sit outside the empirical band. Printing the band under the model probability
  // as "95% interval" claims an uncertainty the model never reported, so each is
  // labelled with what produced it and the rate carries its own support count.
  const interval = at(d, "interval");
  const low = num(at(interval, "low"));
  const high = num(at(interval, "high"));
  const rate = num(at(d, "empirical", "p_pass_by_outcome_horizon"));
  const n = num(at(d, "support", "n"));
  const bucket = at(d, "support", "gap_bucket_s");
  const supplied = at(d, "features_supplied");
  const missing = at(d, "features_missing");
  return {
    rows: [
      {
        label: "P(pass) — model",
        value: pct(num(p)),
        reason: str(at(p, "reason")),
        tag: str(at(p, "provenance")),
        strong: true,
      },
      {
        label: "P(pass) — observed rate",
        value:
          rate === null
            ? null
            : `${pct(rate)}${low === null || high === null ? "" : ` (${pct(low)} – ${pct(high)})`}`,
        reason: "No empirical rate was returned for this state.",
        tag:
          n === null
            ? str(at(interval, "method"))
            : `n=${n}${Array.isArray(bucket) ? `, gap ${bucket.join("–")}s` : ""}`,
      },
      { label: "Model", value: str(at(p, "model")), tag: str(at(p, "artifact_version")) },
      { label: "Checkpoint", value: str(at(d, "decision_checkpoint")) },
      {
        label: "Calibration",
        value: str(at(d, "calibration")),
        tag: str(at(d, "evidence_grade")),
      },
      {
        label: "Features",
        value:
          Array.isArray(supplied) && Array.isArray(missing)
            ? `${supplied.length} supplied, ${missing.length} missing`
            : null,
        tag: Array.isArray(missing) && missing.length > 0 ? "PARTIAL INPUT" : null,
      },
    ],
  };
}

function summariseRival(d: Json): Summary {
  const p = at(d, "p");
  const dist =
    isRecord(p) && Object.values(p).every((v) => typeof v === "number")
      ? (p as Record<string, number>)
      : null;
  const top = dist ? Object.entries(dist).sort((a, b) => b[1] - a[1])[0] : null;
  const fixture = str(at(d, "synthetic_fixture"));
  return {
    rows: [
      {
        label: "Most likely state",
        value: top ? `${top[0]} at ${(top[1] * 100).toFixed(1)}%` : null,
        reason: "The model returned no distribution.",
        tag: str(at(d, "provenance")),
        strong: true,
      },
      { label: "Entropy", value: fixed(num(at(d, "uncertainty", "entropy")), 4, " nats") },
      { label: "Causal cutoff", value: fixed(num(at(d, "causal_cutoff")), 0, " segments observed") },
      { label: "Model", value: str(at(d, "model_version")), tag: str(at(d, "split_version")) },
      {
        label: "Segments used",
        value: fixture ? "the service's synthetic fixture" : "the segments this page sent",
        tag: fixture ? "SYNTHETIC" : null,
      },
    ],
    extra: dist ? <Distribution p={dist} /> : undefined,
  };
}

/* ------------------------------------------------------------------------- the surface */

export function ScenarioControls() {
  const [year, setYear] = useState<number>(2026);
  const [event, setEvent] = useState("british_grand_prix");
  const [energyKj, setEnergyKj] = useState(1420);
  const [timeGapS, setTimeGapS] = useState(0.78);
  const [closingRate, setClosingRate] = useState(0.05);
  const [speedKmh, setSpeedKmh] = useState(240);
  const [eligibility, setEligibility] = useState<"NOT_ARMED" | "ARMED">("NOT_ARMED");
  const [tyreCompound, setTyreCompound] = useState("MEDIUM");
  const [tyreLife, setTyreLife] = useState(12);
  const [horizonLaps, setHorizonLaps] = useState<1 | 2>(2);
  const [rivalPreset, setRivalPreset] = useState<keyof typeof RIVAL_PRESETS>("BALANCED");

  const [shadowPriceResult, setShadowPriceResult] = useState<Pending<ShadowPriceResponse>>(null);
  const [planResult, setPlanResult] = useState<Pending<PlanResponse>>(null);
  const [passResult, setPassResult] = useState<Pending<Json>>(null);
  const [rivalResult, setRivalResult] = useState<Pending<Json>>(null);

  const planRequest: PlanRequestBody = {
    state: {
      ref: { year, event, session: "Race", lap: 31, segment_id: 22, distance_m: 3140.0, lap_fraction: 0.53 },
      energy: { energy_kj: energyKj, deployed_kj: 0, harvested_kj: 0 },
      tyre: { compound: tyreCompound, life_laps: tyreLife },
      gap: { time_gap_s: timeGapS, distance_gap_m: null, relative_speed_mps: null, relative_acceleration_mps2: null, gap_rate_s_per_s: closingRate },
      overtake_state: eligibility,
      race_control: { overtake_disabled: false },
      power_envelope: { regime: "NORMAL" },
      speed_kmh: speedKmh,
    },
    rival_state: { p: RIVAL_PRESETS[rivalPreset] },
    horizon_laps: horizonLaps,
    include_baselines: true,
    risk: { cvar_alpha: 0.2 },
  };

  const runShadowPrice = useCallback(async () => {
    setShadowPriceResult("pending");
    setShadowPriceResult(await fetchShadowPrice(event, { energy_kj: energyKj, time_gap_s: timeGapS, eligibility }));
  }, [event, energyKj, timeGapS, eligibility]);

  const runPlan = useCallback(async () => {
    setPlanResult("pending");
    setPlanResult(await postPlan({ ...planRequest, event }));
    // planRequest is rebuilt from the same state this callback already depends on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [event, energyKj, timeGapS, closingRate, speedKmh, eligibility, tyreCompound, tyreLife, horizonLaps, rivalPreset, year]);

  const runPassPredict = useCallback(async () => {
    setPassResult("pending");
    // Field names are the MODEL's schema, not the UI's vocabulary (INTEGRATION.md 3).
    // Sending `attacker_tyre_life` instead of `attacker_tyre_life_laps` does not fail —
    // it silently lands in `features_missing`, and the probability comes back built on
    // fewer inputs than the page thinks it sent.
    setPassResult(
      await postPassPredict({
        event,
        year,
        decision_checkpoint: "DETECTION",
        features: {
          gap_at_checkpoint: timeGapS,
          closing_rate_s_per_s: closingRate,
          p_eligible: eligibility === "ARMED" ? 1 : 0,
          attacker_tyre_compound: tyreCompound,
          attacker_tyre_life_laps: tyreLife,
        },
      }),
    );
  }, [event, year, timeGapS, closingRate, eligibility, tyreCompound, tyreLife]);

  const runRivalState = useCallback(async () => {
    setRivalResult("pending");
    // `segments` is OMITTED, not sent empty. C10 needs at least one causal battle segment
    // and this page has none to give — it holds a single state, not a battle window. An
    // empty list is a claim that the window was observed and was empty, which the service
    // refuses (422 INSUFFICIENT_SEGMENTS). Omitting it asks for the labelled fixture, and
    // the response says so through `synthetic_fixture`.
    setRivalResult(await postRivalState({ event, year }));
  }, [event, year]);

  const runAll = useCallback(() => {
    void runShadowPrice();
    void runPlan();
    void runPassPredict();
    void runRivalState();
  }, [runShadowPrice, runPlan, runPassPredict, runRivalState]);

  return (
    <div className={s.panel}>
      <div className={s.contextBar}>
        <label className={s.contextField}>
          <span>Year</span>
          <select value={year} onChange={(e) => setYear(Number(e.target.value))}>
            {YEARS.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </label>
        <label className={s.contextField}>
          <span>Event</span>
          <select value={event} onChange={(e) => setEvent(e.target.value)}>
            {EVENT_PROFILES.map((p) => (
              <option key={p.event} value={p.event}>
                {p.eventDisplay}
              </option>
            ))}
          </select>
        </label>
        <p className={s.contextNote}>
          Each event carries its own rule config (<code>config/rules/{year}/{event}.yaml</code>) —
          detection line, corner geometry, lap length. 2026 is the only season with one: the earlier
          seasons are the DRS era, and a DRS zone is not an Overtake fact.
        </p>
      </div>

      <h2 className={s.heading}>Scenario parameters</h2>
      <p className={s.sub}>
        The StrategicState fields API.md&apos;s routes take as input (3.4, 5.12, 5.13). Moving a
        control changes only the request — nothing here recomputes a result locally.
      </p>

      <div className={s.grid}>
        <label className={s.field}>
          <span>Energy (kJ)</span>
          <input type="range" min={0} max={4000} step={50} value={energyKj} onChange={(e) => setEnergyKj(Number(e.target.value))} />
          <span className={s.value}>{energyKj}</span>
        </label>

        <label className={s.field}>
          <span>Time gap (s)</span>
          <input type="range" min={0} max={3} step={0.05} value={timeGapS} onChange={(e) => setTimeGapS(Number(e.target.value))} />
          <span className={s.value}>{timeGapS.toFixed(2)}</span>
        </label>

        <label className={s.field}>
          <span>Closing rate (s/s)</span>
          <input type="range" min={-0.3} max={0.3} step={0.01} value={closingRate} onChange={(e) => setClosingRate(Number(e.target.value))} />
          <span className={s.value}>{closingRate > 0 ? "+" : ""}{closingRate.toFixed(2)}</span>
        </label>

        <label className={s.field}>
          <span>Speed (km/h)</span>
          <input type="range" min={60} max={340} step={5} value={speedKmh} onChange={(e) => setSpeedKmh(Number(e.target.value))} />
          <span className={s.value}>{speedKmh}</span>
        </label>

        <label className={s.field}>
          <span>Eligibility</span>
          <select value={eligibility} onChange={(e) => setEligibility(e.target.value as "NOT_ARMED" | "ARMED")}>
            <option value="NOT_ARMED">NOT_ARMED</option>
            <option value="ARMED">ARMED</option>
          </select>
        </label>

        <label className={s.field}>
          <span>Tyre compound</span>
          <select value={tyreCompound} onChange={(e) => setTyreCompound(e.target.value)}>
            <option>SOFT</option>
            <option>MEDIUM</option>
            <option>HARD</option>
          </select>
        </label>

        <label className={s.field}>
          <span>Tyre life (laps)</span>
          <input type="range" min={0} max={40} step={1} value={tyreLife} onChange={(e) => setTyreLife(Number(e.target.value))} />
          <span className={s.value}>{tyreLife}</span>
        </label>

        <label className={s.field}>
          <span>Rival belief preset</span>
          <select value={rivalPreset} onChange={(e) => setRivalPreset(e.target.value as keyof typeof RIVAL_PRESETS)}>
            {Object.keys(RIVAL_PRESETS).map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
        </label>

        <label className={s.field}>
          <span>Horizon (laps)</span>
          <select value={horizonLaps} onChange={(e) => setHorizonLaps(Number(e.target.value) as 1 | 2)}>
            <option value={1}>1</option>
            <option value={2}>2</option>
          </select>
        </label>
      </div>

      <div className={s.actions}>
        <button type="button" className={`${s.askButton} ${s.askPrimary}`} onClick={runAll}>
          Ask all four
        </button>
        <AskButton
          onClick={runShadowPrice}
          title="What is a megajoule worth here?"
          route="GET /value/{event}/shadow_price"
        />
        <AskButton onClick={runPlan} title="What should we do this segment?" route="POST /plan" />
        <AskButton
          onClick={runPassPredict}
          title="Will the pass come off?"
          route="POST /pass/predict"
        />
        <AskButton
          onClick={runRivalState}
          title="What is the rival doing with their energy?"
          route="POST /rival/state"
        />
      </div>

      <ResultCard
        label="What a megajoule is worth here"
        route="GET /value/{event}/shadow_price"
        result={shadowPriceResult}
        summarise={summariseShadowPrice}
      />
      <ResultCard
        label="What to do this segment"
        route="POST /plan"
        result={planResult}
        summarise={summarisePlan}
      />
      <ResultCard
        label="Whether the pass comes off"
        route="POST /pass/predict"
        result={passResult}
        summarise={summarisePass}
      />
      <ResultCard
        label="What the rival is doing"
        route="POST /rival/state"
        result={rivalResult}
        summarise={summariseRival}
      />
    </div>
  );
}

function AskButton({ onClick, title, route }: { onClick: () => void; title: string; route: string }) {
  return (
    <button type="button" className={s.askButton} onClick={onClick}>
      <span className={s.askTitle}>{title}</span>
      <code className={s.askRoute}>{route}</code>
    </button>
  );
}
