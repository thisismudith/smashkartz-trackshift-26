"use client";

/**
 * The Overtake layer over a replay: the circuit's Detection Line and activation
 * zones drawn on the track, and the live closest battle scored against the
 * measured 2026 pass rate.
 *
 * EVERY NUMBER HERE IS SOMEONE ELSE'S MEASUREMENT, fetched, never computed in
 * the browser:
 *
 *  - the line positions come from GET /rules/{event}, which reads
 *    config/rules/2026/*.yaml and carries a `value_source` per number. The
 *    Detection Line is DERIVED_TELEMETRY (Safety Car Line 1, located in the
 *    telemetry); an Activation Line is PROXY_HISTORICAL_DRS, a development
 *    stand-in derived from where DRS was open in 2022-2025. The badge says
 *    which, because they are not the same kind of fact.
 *  - the pass probability comes from POST /pass/predict. Today that is the
 *    empirical 2026 rate table (a counted frequency with a Wilson interval);
 *    when M10's artifact lands the same route answers with a calibrated model
 *    and this panel changes in no way at all.
 *
 * The GAP is the one number read from the replay itself, and it is the replay's
 * own published interval to the car ahead -- not re-derived here.
 *
 * With the service down the panel says so and draws nothing. It never falls
 * back to a plausible-looking line position: a Detection Line in the wrong
 * place is worse than no Detection Line, because the viewer cannot tell.
 */
import { useEffect, useRef, useState } from "react";
import { fetchRules, fetchTrack, postEligibility, postPassPredict } from "@/api-contract/client";
import {
  overtakeGeometryFromRules, type OvertakeGeometry,
} from "../render/overtakeZones";
import type { SimRenderer } from "../render/scene";
import type { DashboardRow, DashboardSnapshot, WeatherSeries } from "../contract/types";
import { buildPassFeatures, type PassFeatureRow } from "./passFeatures";
import { displayRating, rawRatingNote } from "./modelRating";
import styles from "./sim.module.css";

/** How often to re-score the battle, ms. The dashboard itself only advances at
 * ~10 Hz and the answer is a bucketed rate, so anything faster is network
 * traffic for a number that cannot have changed. */
const SCORE_INTERVAL_MS = 1500;

interface EraRow { season: string; era: string; rate: number; n: number }

interface PassAnswer {
  p: number | null;
  low: number | null;
  high: number | null;
  n: number | null;
  provenance: string;
  stub: boolean;
  reason: string;
  eras: EraRow[];
  /** The measured rate for this gap bucket, beside whatever the model said.
   * Shown together on purpose: "the model says 9% where 508 comparable
   * approaches produced 13%" is the comparison that says whether to believe it,
   * and neither number alone carries it. */
  empiricalP: number | null;
  /** How many of the model's own features this request actually carried.
   * INTEGRATION.md section 3 is explicit that a probability built from 3 of 15
   * features is a weaker claim than one built from all 15, and that the user
   * cannot tell the difference without being told. */
  supplied: number;
  total: number;
  missing: string[];
  grade: string | null;
  artifact: string | null;
}

/**
 * A service number and its provenance tag, whichever shape the field arrives in.
 *
 * API.md 5.8's example returns `p_pass_by_outcome_horizon` as a bare float;
 * INTEGRATION.md section 5 returns a Quantity carrying provenance and the
 * artifact version. The service emits the Quantity -- correctly, since API.md's
 * own first principle is that every displayed number carries its provenance --
 * and reading only the bare float is why this panel showed no probability at
 * all. The bare-number branch stays for the fields API.md still spells that way,
 * and reports a null tag rather than inventing one.
 */
export function readQuantity(node: unknown): { value: number | null; provenance: string | null } {
  if (typeof node === "number") {
    return { value: Number.isFinite(node) ? node : null, provenance: null };
  }
  if (node && typeof node === "object") {
    const q = node as { value?: unknown; provenance?: unknown };
    const provenance = typeof q.provenance === "string" && q.provenance ? q.provenance : null;
    if (typeof q.value === "number" && Number.isFinite(q.value)) {
      return { value: q.value, provenance };
    }
    return { value: null, provenance };
  }
  return { value: null, provenance: null };
}

/**
 * Why each absent feature is absent, for the tooltip.
 *
 * `sector` is the one that needs saying out loud. The simulation CAN work it out
 * -- the track model carries the two sector lines, so the Detection Line's
 * sector is a lookup -- but the artifact was fitted with `sector` null in every
 * row, so its only trained category is "missing". Supplying a real 2 returns the
 * identical probability to 5 decimal places while making the panel claim 15 of
 * 15 inputs. That reads as more evidence than exists, so it stays out, and this
 * says why rather than leaving it looking like an oversight.
 */
export function missingExplained(missing: string[]): string {
  return missing.map((name) => (
    name === "sector"
      ? "sector — the model was fitted with this null in every row, so supplying it changes nothing"
      : name
  )).join("\n");
}

/**
 * What a provenance tag means for a number on this panel, in one line.
 *
 * API.md section 2 requires the tag beside the number; a three-letter chip on
 * its own does not tell a viewer that P(eligible) is a projection rather than a
 * reading off the car. The tag is repeated in the sentence so that two numbers
 * carrying different tags stay separable -- the panel's own rule, stated in
 * sim.module.css beside .overtakeSource, is that the kind of fact a number is
 * gets rendered, never hidden in a tooltip.
 *
 * An unknown tag is named, not explained: the rule engine publishes a second
 * vocabulary (DERIVED_TELEMETRY, PROXY_HISTORICAL_DRS) on the same key, and
 * inventing a meaning for a tag this panel does not know is the failure the
 * whole provenance discipline exists to prevent.
 */
const PROVENANCE_MEANING: Record<string, string> = {
  OBSERVED: "measured in the session",
  DERIVED: "computed from measured telemetry",
  INFERRED: "a model's estimate, carrying uncertainty",
  SIMULATED: "produced by a model of the race, not measured in it",
  RULE: "read from the regulations",
};

export function provenanceMeaning(tags: ReadonlyArray<string | null>): string {
  const lines: string[] = [];
  for (const tag of tags) {
    const text = tag === null
      ? "untagged — the service published this number without a provenance"
      : `${tag.toLowerCase()} — ${PROVENANCE_MEANING[tag] ?? "vocabulary this panel does not define"}`;
    if (!lines.includes(text)) lines.push(text);
  }
  return lines.join(" · ");
}

/**
 * Seconds out of the leaderboard's own interval string, or null.
 *
 * The board publishes a display string, not a number: "+1.234" for a real gap
 * and a word -- "LEADER", "PIT", "LAP", an em dash -- whenever there is no
 * measured interval to show. Only the numeric form is a gap; every word is an
 * absence, and reading one as 0 would invent the closest battle on track.
 */
export function intervalSeconds(interval: string | null | undefined): number | null {
  if (typeof interval !== "string") return null;
  const match = /^\+?(\d+(?:\.\d+)?)$/.exec(interval.trim());
  if (!match) return null;
  const seconds = Number(match[1]);
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null;
}

/** One attacker chasing one defender, and the gap between them. */
export interface Battle {
  attacker: string;
  defender: string;
  gapS: number;
  /** Which side of the focused driver this is. "closest" when no driver is
   * focused and the panel fell back to the tightest gap in the field. */
  relation: "ahead" | "behind" | "closest";
}

/** A pair is only a contest if both cars are racing each other: a car in the
 * pit lane is not fighting the one it happens to be listed beside. */
function pairGap(rows: DashboardRow[], attackerIndex: number): number | null {
  const attacker = rows[attackerIndex];
  const defender = rows[attackerIndex - 1];
  if (!attacker || !defender) return null;
  if (attacker.status === "pit" || defender.status === "pit") return null;
  // A row's interval is its gap to the car directly ahead of it.
  return intervalSeconds(attacker.interval);
}

/**
 * The battles the FOCUSED driver is actually in: the car it is chasing, and the
 * car chasing it.
 *
 * This is the question a viewer watching one driver is asking. Scoring the
 * tightest gap anywhere in the field instead answers a different question, and
 * silently: the panel would show two cars the viewer is not watching while the
 * driver they clicked sits in clear air.
 */
export function driverBattles(
  dashboard: DashboardSnapshot | null, driver: string | null,
): Battle[] {
  const rows = dashboard?.leaderboard;
  if (!rows || rows.length < 2 || !driver) return [];
  const i = rows.findIndex((r) => r.driver === driver);
  if (i < 0) return [];
  const battles: Battle[] = [];
  const aheadGap = pairGap(rows, i);
  if (aheadGap !== null) {
    battles.push({
      attacker: driver, defender: rows[i - 1].driver, gapS: aheadGap, relation: "ahead",
    });
  }
  const behindGap = pairGap(rows, i + 1);
  if (behindGap !== null) {
    battles.push({
      attacker: rows[i + 1].driver, defender: driver, gapS: behindGap, relation: "behind",
    });
  }
  return battles;
}

/** The tightest battle anywhere in the field. Used only as the
 * no-driver-focused fallback, and labelled as such on screen. */
export function closestBattle(dashboard: DashboardSnapshot | null): Battle | null {
  const rows = dashboard?.leaderboard;
  if (!rows || rows.length < 2) return null;
  let best: Battle | null = null;
  for (let i = 1; i < rows.length; i++) {
    const gap = pairGap(rows, i);
    if (gap === null) continue;
    if (best === null || gap < best.gapS) {
      best = {
        attacker: rows[i].driver, defender: rows[i - 1].driver,
        gapS: gap, relation: "closest",
      };
    }
  }
  return best;
}

/** What the panel shows: the focused driver's own battles, else the tightest in
 * the field so the panel is never blank for no reason. */
export function battlesToShow(
  dashboard: DashboardSnapshot | null, driver: string | null,
): Battle[] {
  const own = driverBattles(dashboard, driver);
  if (own.length) return own;
  const closest = closestBattle(dashboard);
  return closest ? [closest] : [];
}

/**
 * The rules service's event key for a sim track slug.
 *
 * The two halves of this project spell the same circuit differently -- the sim
 * artifacts use `british-grand-prix`, config/rules/2026 uses
 * `british_grand_prix` -- and the service 404s on the wrong one. Normalising
 * here keeps that mismatch in one visible place instead of every call site.
 */
export function rulesEventKey(trackSlug: string): string {
  return trackSlug.trim().toLowerCase().replace(/-/g, "_");
}

/** A coded response from the service: the machine-readable `code` and the
 * sentence written for a person. */
export interface ServiceCode { code: string; message: string }

/**
 * The code and message inside an error body, or null when it carries neither.
 *
 * Two nestings are live: the service raises `{detail: {code, message}}` and the
 * older envelope wraps the same pair in `detail.error`. Reading only the wrapped
 * one is why a 422 reached the screen as its own raw JSON.
 */
export function readServiceCode(raw: string | undefined): ServiceCode | null {
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  const detail = (parsed as { detail?: unknown } | null)?.detail;
  if (!detail || typeof detail !== "object") return null;
  const node = ((detail as { error?: unknown }).error ?? detail) as
    { code?: unknown; message?: unknown };
  const code = typeof node.code === "string" ? node.code : "";
  const message = typeof node.message === "string" ? node.message : "";
  return code || message ? { code, message } : null;
}

/**
 * The panel's own lead for the two 422s that are ANSWERS, not failures.
 *
 * INTEGRATION.md section 4 is explicit that both must render as an explanatory
 * state. NOT_MODEL_ELIGIBLE is the Safety Car, VSC and pit case, which a replay
 * meets constantly: the model is fitted on green-flag normal-race rows only, so
 * what the service said is that this is not a question it answers -- not that
 * anything went wrong. A red error string there is a lie about the service and
 * hides a real statement about the model's domain.
 *
 * Every other code (ILLEGAL_STATE, UNKNOWN_EVENT, a 500) IS a failure and keeps
 * the failure path, so returning null here is what separates the two.
 */
export function refusalHeadline(code: string): string | null {
  if (code === "NOT_MODEL_ELIGIBLE") return "the model is not defined here";
  if (code === "CHECKPOINT_VIOLATION") {
    return "asked with information the DETECTION checkpoint cannot have";
  }
  return null;
}

/** A service error trimmed to something a panel can show. The body is often a
 * whole JSON envelope; the message inside it is the part worth reading. */
export function readableError(raw: string | undefined): string {
  if (!raw) return "rules service unreachable";
  const coded = readServiceCode(raw);
  if (coded?.message) return coded.message.split(".")[0];
  return raw.length > 160 ? `${raw.slice(0, 157)}…` : raw;
}

function sourceLabel(source: string | null): string {
  if (!source) return "unsourced";
  if (source === "DERIVED_TELEMETRY") return "measured in telemetry";
  if (source === "PROXY_HISTORICAL_DRS") return "historical-DRS proxy";
  if (source === "RULE_FIA") return "FIA regulation";
  return source.toLowerCase().replace(/_/g, " ");
}


/**
 * The circuit's Overtake geometry, fetched once per circuit.
 *
 * Lifted out of the panel because three surfaces need the same answer -- the
 * panel, the plan view and the legend -- and three independent fetches would be
 * three chances to disagree about whether this circuit has a Detection Line.
 */
export function useOvertakeGeometry(event: string | null): {
  geometry: OvertakeGeometry | null; error: string | null;
} {
  const [geometry, setGeometry] = useState<OvertakeGeometry | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let disposed = false;
    setGeometry(null);
    setError(null);
    if (!event) return;
    void (async () => {
      const result = await fetchRules(rulesEventKey(event));
      if (disposed) return;
      if (!result.ok) { setError(readableError(result.error)); return; }
      const parsed = overtakeGeometryFromRules(result.data);
      if (!parsed) {
        setError("this circuit has no sourced Detection Line or activation zone");
        return;
      }
      setGeometry(parsed);
    })();
    return () => { disposed = true; };
  }, [event]);
  return { geometry, error };
}

/** One segment of the circuit as GET /track/{event} publishes it. */
export interface TrackSegment {
  start_distance_m?: number | null;
  end_distance_m?: number | null;
  corner_type?: string | null;
}

/**
 * The circuit's derived segmentation, fetched once per circuit.
 *
 * Only `corner_type` is read from it here, and only because it cannot honestly
 * come from anywhere else: the server runs the same M03 classifier the pass
 * model was fitted with, so a label from here lands in a trained category.
 * Deriving one in the browser from curvature would produce labels the model has
 * never seen, which scores worse than sending nothing at all.
 */
export function useTrackSegments(event: string | null): TrackSegment[] | null {
  const [segments, setSegments] = useState<TrackSegment[] | null>(null);
  useEffect(() => {
    let disposed = false;
    setSegments(null);
    if (!event) return;
    void (async () => {
      const result = await fetchTrack(rulesEventKey(event));
      if (disposed || !result.ok) return;
      const body = result.data as unknown as { segments?: TrackSegment[] };
      setSegments(Array.isArray(body.segments) ? body.segments : []);
    })();
    return () => { disposed = true; };
  }, [event]);
  return segments;
}

/** Stable identity for one pairing, so scores survive a re-render. */
function battleKey(battle: Battle): string {
  return `${battle.attacker}>${battle.defender}`;
}

interface BattleScore {
  answer: PassAnswer | null;
  /** The service declining to answer, with its reason. Distinct from `answer`
   * being null, which means the request is still in flight. */
  refusal: ServiceCode | null;
  marginS: number | null;
  pEligible: number | null;
  /** The tags the eligibility route published with those two numbers. Both are
   * SIMULATED today -- the arming rule run forward over a projected gap -- and
   * printing either as a bare percentage reads as a measurement of the race. */
  marginProv: string | null;
  pEligibleProv: string | null;
  /** Why a feature was left out, from the row builder. */
  omitted: Record<string, string>;
}

export function OvertakePanel({
  event, renderer, dashboard, selectedDriver, geometry, geometryError,
  weather, headingRad, segments,
}: {
  /** The event name the rules service knows this circuit by, e.g. "british_grand_prix". */
  event: string | null;
  renderer: SimRenderer | null;
  dashboard: DashboardSnapshot | null;
  /** The car the viewer clicked. The panel follows THIS driver's battles; with
   * none selected it falls back to the tightest gap in the field and says so. */
  selectedDriver: string | null;
  /** From useOvertakeGeometry, owned by the stage so the plan view and the
   * legend read the same answer. */
  geometry: OvertakeGeometry | null;
  geometryError: string | null;
  /** The session's trackside feed. Four of the model's fifteen features come
   * from it, so without it the answer is measurably weaker and says so. */
  weather: WeatherSeries | null;
  /** Direction of travel at the focused car, for projecting wind onto the track. */
  headingRad: number | null;
  /** From useTrackSegments, owned by the stage so one fetch serves every panel. */
  segments: TrackSegment[] | null;
}) {
  const [scores, setScores] = useState<Record<string, BattleScore>>({});
  const lastScored = useRef<number>(0);
  /**
   * Which scoring run is the current one, and whether the panel still exists.
   *
   * These replace a per-effect `disposed` flag, which deadlocked the panel on
   * "scoring...". The effect re-runs on every 10 Hz gap change; its cleanup
   * cancelled the one run that had passed the 1.5 s throttle, and every
   * subsequent run returned early on that same throttle without starting
   * another. The result was never applied, and the throttle guaranteed it never
   * would be. A run id lets a re-render supersede an in-flight run without a
   * bailed-out re-render cancelling one.
   */
  const runId = useRef(0);
  const mounted = useRef(true);
  // Set on the way IN as well as cleared on the way out. React's dev StrictMode
  // mounts, unmounts and remounts every component once; a cleanup that only
  // clears this leaves it false for the rest of the session, and the scoring
  // effect below then discards every result it ever computes -- which is
  // indistinguishable on screen from the request never returning. The panel sat
  // on "scoring..." forever because of exactly this.
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  // Previous gap per pairing, for the closing rate. A rate needs two samples and
  // the sim is the only place both exist; the service cannot derive it from a
  // single request, and a missing closing rate is one of the fifteen gone.
  const previousGaps = useRef<Record<string, { gapS: number; atSessionTime: number }>>({});

  // --- install on the scene; clear on unmount so a circuit swap leaves nothing
  useEffect(() => {
    renderer?.setOvertakeGeometry(geometry);
    return () => { renderer?.setOvertakeGeometry(null); };
  }, [renderer, geometry]);

  const battles = battlesToShow(dashboard, selectedDriver);
  // Gaps only, rounded, as the effect's dependency: re-scoring on every 10 Hz
  // dashboard tick would hammer the service for an answer that is bucketed and
  // therefore cannot have changed.
  const gapKey = battles.map((b) => `${battleKey(b)}:${b.gapS.toFixed(2)}`).join("|");

  // --- score each battle: pass probability AND eligibility, throttled together.
  // API.md section 8 wants P(eligible) shown as a probability rather than only an
  // armed/not badge, which is exactly the case a binary badge hides.
  useEffect(() => {
    if (!battles.length || !event) { setScores({}); return; }
    const now = Date.now();
    if (now - lastScored.current < SCORE_INTERVAL_MS) return;
    lastScored.current = now;
    const id = ++runId.current;
    const sessionTime = dashboard?.sessionTime ?? 0;
    void (async () => {
      const next: Record<string, BattleScore> = {};
      await Promise.all(battles.map(async (battle) => {
        const key = battleKey(battle);
        const score: BattleScore = {
          answer: null, refusal: null, marginS: null, pEligible: null,
          marginProv: null, pEligibleProv: null, omitted: {},
        };

        // Eligibility FIRST, not alongside. P(eligible) is one of the pass
        // model's own fifteen features, so running the two in parallel -- as
        // this did -- guaranteed the pass request could never carry it.
        const elig = await postEligibility(rulesEventKey(event), {
          gap: { time_gap_s: battle.gapS },
          overtake_state: "NOT_ARMED",
          attacker: battle.attacker,
          defender: battle.defender,
        });
        if (elig.ok && elig.data) {
          const body = elig.data as Record<string, unknown>;
          const margin = readQuantity(body.eligibility_margin_s ?? body.margin_s);
          const eligible = readQuantity(body.p_eligible);
          score.marginS = margin.value;
          score.marginProv = margin.provenance;
          score.pEligible = eligible.value;
          score.pEligibleProv = eligible.provenance;
        }

        const rows = dashboard?.leaderboard ?? [];
        const { features, omitted } = buildPassFeatures({
          gapS: battle.gapS,
          attackerRow: rows.find((r) => r.driver === battle.attacker) ?? null,
          defenderRow: rows.find((r) => r.driver === battle.defender) ?? null,
          weather, sessionTime, headingRad,
          pEligible: score.pEligible,
          previous: previousGaps.current[key] ?? null,
          segments,
          detectionM: geometry?.detectionM ?? null,
        });
        score.omitted = omitted;
        previousGaps.current[key] = { gapS: battle.gapS, atSessionTime: sessionTime };

        // The request names the PAIR as well as the features. A bare gap would
        // let the service answer about "some cars 0.4 s apart" when the question
        // is always about these two specific cars -- and the identities are what
        // per-driver and per-team features key off once they are fitted.
        const pass = await postPassPredict({
          decision_checkpoint: "DETECTION",
          event: rulesEventKey(event),
          attacker: battle.attacker,
          defender: battle.defender,
          ...(features as PassFeatureRow as Record<string, unknown>),
        });

        if (pass.ok && pass.data) {
          const body = pass.data as Record<string, unknown>;
          const comparison = body.era_comparison as { seasons?: EraRow[] } | null;
          const empirical = body.empirical as {
            p_pass_by_outcome_horizon?: unknown;
            interval?: Record<string, number>;
            support?: Record<string, number>;
          } | undefined;
          // The band and the sample size belong to the COUNTED RATE, so they are
          // read out of `empirical` and rendered on its line -- never beside the
          // model's percentage. Pairing them put a point estimate outside its own
          // interval on screen ("16.3% (6.0-9.6%)") and attached a measured
          // frequency's uncertainty to a model's output. The top-level keys are
          // the fallback for the no-artifact reply, where the headline IS that
          // counted rate and the band genuinely describes it.
          const interval = (empirical?.interval ?? body.interval ?? {}) as Record<string, number>;
          const support = (empirical?.support ?? body.support ?? {}) as Record<string, number>;
          const headline = readQuantity(body.p_pass_by_outcome_horizon);
          const suppliedList = Array.isArray(body.features_supplied)
            ? (body.features_supplied as string[]) : [];
          const missingList = Array.isArray(body.features_missing)
            ? (body.features_missing as string[]) : [];
          score.answer = {
            p: headline.value,
            low: typeof interval.low === "number" ? interval.low : null,
            high: typeof interval.high === "number" ? interval.high : null,
            n: typeof support.n === "number" ? support.n : null,
            provenance: headline.provenance ?? String(body.provenance ?? "UNKNOWN"),
            stub: Boolean(pass.stub) || body.is_stub === true,
            reason: String(body.reason ?? ""),
            eras: comparison?.seasons ?? [],
            empiricalP: empirical
              ? readQuantity(empirical.p_pass_by_outcome_horizon).value : null,
            supplied: suppliedList.length,
            total: suppliedList.length + missingList.length,
            missing: missingList,
            grade: typeof body.evidence_grade === "string" ? body.evidence_grade : null,
            artifact: (() => {
              const q = body.p_pass_by_outcome_horizon as { artifact_version?: unknown };
              return q && typeof q.artifact_version === "string" ? q.artifact_version : null;
            })(),
          };
        } else if (!pass.ok) {
          // A refusal is the service answering, so it does not become a failed
          // answer with an error string in the reason slot -- it gets its own
          // state, and only a genuine failure falls through to this one.
          const coded = readServiceCode(pass.error);
          if (coded && refusalHeadline(coded.code)) {
            score.refusal = coded;
          } else {
            score.answer = {
              p: null, low: null, high: null, n: null, provenance: "UNKNOWN",
              stub: false, reason: readableError(pass.error), eras: [],
              empiricalP: null, supplied: 0, total: 0, missing: [], grade: null,
              artifact: null,
            };
          }
        }
        next[key] = score;
      }));
      // Applied unless a LATER run has started or the panel has gone. A
      // re-render that bailed on the throttle started no run, so it cannot
      // invalidate this one.
      if (mounted.current && id === runId.current) setScores(next);
    })();
    // `segments` is a dependency so a late GET /track re-scores with corner_type
    // rather than leaving the first answers permanently one feature short.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gapKey, event, segments]);

  // Armed is a property of a battle, not of the panel: the car ahead may be
  // inside the threshold while the one behind is not.
  const anyArmed = geometry !== null && battles.some((b) => b.gapS <= 1.0);


  return (
    <div className={styles.overtakePanel}>
      <div className={styles.overtakeHead}>
        <span>Overtake</span>
        {geometry ? (
          <span className={styles.overtakeTag} data-armed={anyArmed}>
            {battles.length === 0 ? "no battle" : anyArmed ? "ARMED" : "not armed"}
          </span>
        ) : null}
      </div>

      {geometryError ? (
        <p className={styles.overtakeNote}>{geometryError}</p>
      ) : !geometry ? (
        <p className={styles.overtakeNote}>loading circuit geometry…</p>
      ) : (
        <dl className={styles.overtakeFacts}>
          <dt>Detection Line</dt>
          <dd>
            {geometry.detectionM !== null
              ? `${geometry.detectionM.toFixed(0)} m`
              : "not sourced"}
            <span className={styles.overtakeSource}>
              {sourceLabel(geometry.detectionSource)}
            </span>
          </dd>
          {geometry.zones.map((zone) => (
            <div key={zone.label} className={styles.overtakeZoneRow}>
              <dt>Zone {zone.label}</dt>
              <dd>
                {/* A zone whose END is unsourced still has a sourced opening.
                    Requiring both made four measured Activation Lines read
                    "not sourced" because `zone_end_m` is UNVERIFIED in every
                    2026 config -- an absence in one field erasing another. */}
                {zone.activationM !== null
                  ? `from ${zone.activationM.toFixed(0)} m${
                      zone.endM !== null ? ` to ${zone.endM.toFixed(0)} m` : ", end unsourced"}`
                  : "not sourced"}
                <span className={styles.overtakeSource}>{sourceLabel(zone.source)}</span>
              </dd>
            </div>
          ))}
        </dl>
      )}

      {battles.length === 0 ? (
        <p className={styles.overtakeNote}>
          {selectedDriver
            ? `${selectedDriver} is in clear air — no measured gap either side`
            : "no measured gap anywhere in the field"}
        </p>
      ) : null}

      {battles.map((battle) => {
        const score = scores[battleKey(battle)];
        const answer = score?.answer ?? null;
        return (
          <div key={battleKey(battle)} className={styles.overtakeBattle}>
            <p className={styles.overtakeBattleWho}>
              <span className={styles.overtakeRelation}>
                {battle.relation === "ahead" ? "chasing"
                  : battle.relation === "behind" ? "defending from"
                  : "closest on track"}
              </span>
              <strong>{battle.attacker}</strong> behind <strong>{battle.defender}</strong>
              {" · "}{battle.gapS.toFixed(2)} s
            </p>
            {battle.relation === "closest" ? (
              <p className={styles.overtakeNote}>click a car to follow its own battles</p>
            ) : null}
            {/* Two numbers, two tags, two guards. They were one paragraph
                gated on the margin, so an absent margin also hid P(eligible),
                which had arrived -- and neither carried the provenance API.md
                section 2 requires beside every number on screen. */}
            {score && score.marginS !== null ? (
              <p className={styles.overtakeTagged}>
                <span>
                  {score.marginS >= 0
                    ? `inside the arming threshold by ${score.marginS.toFixed(2)} s`
                    : `outside the arming threshold by ${Math.abs(score.marginS).toFixed(2)} s`}
                </span>
                <span className={styles.overtakeProv}>{score.marginProv ?? "UNTAGGED"}</span>
              </p>
            ) : null}
            {score && score.pEligible !== null ? (
              <p className={styles.overtakeTagged}>
                <span>P(eligible) {(score.pEligible * 100).toFixed(0)}%</span>
                <span className={styles.overtakeProv}>{score.pEligibleProv ?? "UNTAGGED"}</span>
              </p>
            ) : null}
            {score && (score.marginS !== null || score.pEligible !== null) ? (
              <p className={styles.overtakeMeaning}>
                {provenanceMeaning([
                  ...(score.marginS !== null ? [score.marginProv] : []),
                  ...(score.pEligible !== null ? [score.pEligibleProv] : []),
                ])}
              </p>
            ) : null}
            {score?.refusal ? (
              /* INTEGRATION.md section 4: a refusal is an explanatory state,
                 not an error. role="status" and no alert styling, because
                 nothing failed -- the model declined to answer here. */
              <div className={styles.overtakeRefusal} role="status">
                <p className={styles.overtakeRefusalLead}>
                  {refusalHeadline(score.refusal.code) ?? score.refusal.code}
                </p>
                {score.refusal.message ? (
                  <p className={styles.overtakeNote}>{score.refusal.message}</p>
                ) : null}
              </div>
            ) : answer === null ? (
              <p className={styles.overtakeNote}>scoring…</p>
            ) : answer.p === null ? (
              <p className={styles.overtakeNote}>{answer.reason || "no answer for this gap"}</p>
            ) : (
              <>
                <p className={styles.overtakeP}>
                  <span className={styles.overtakePValue}
                        title={rawRatingNote(displayRating(answer.p))}>
                    {((displayRating(answer.p)?.shown ?? answer.p) * 100).toFixed(0)}%
                  </span>
                  {/* A range is drawn beside the headline ONLY when the headline
                      is the counted rate the range describes -- i.e. when there
                      is no separate empirical figure below. The model's own
                      spread is M12 and is not wired, so beside a model number
                      there is deliberately no band rather than a borrowed one. */}
                  {answer.empiricalP === null && answer.low !== null && answer.high !== null ? (
                    <span className={styles.overtakePRange}>
                      {(answer.low * 100).toFixed(0)}–{(answer.high * 100).toFixed(0)}%
                    </span>
                  ) : null}
                  <span className={styles.overtakeProv} data-stub={answer.stub}>
                    {answer.stub ? "STUB" : answer.provenance}
                  </span>
                </p>
                {/* API.md section 8: a pass probability must name the decision
                    checkpoint it belongs to, and DETECTION / ACTIVATION / BRAKING
                    are never merged into one number -- each sees different
                    information, so an unlabelled percentage is unreadable. */}
                <p className={styles.overtakeNote}>
                  at the <strong>DETECTION</strong> checkpoint · pass completed by zone exit
                  {/* The grade is the backend's own, read off this response --
                      it grades THIS artifact, so the artifact is named beside
                      it rather than leaving "interim evidence" to be read as a
                      verdict on the panel or on the race. */}
                  {answer.grade ? ` · ${answer.grade.toLowerCase()} evidence` : ""}
                  {answer.artifact ? ` · from ${answer.artifact}` : ""}
                </p>
                {/* INTEGRATION.md section 3: a probability built from 3 of 15
                    features is a weaker claim than one built from all 15, and
                    the viewer cannot tell the two apart without being told. */}
                {answer.total > 0 ? (
                  <p className={styles.overtakeNote} title={missingExplained(answer.missing)}>
                    from {answer.supplied} of {answer.total} model inputs
                    {answer.missing.length
                      ? ` · missing ${answer.missing.join(", ")}`
                      : ""}
                  </p>
                ) : null}
                {/* The counted rate beside the model's answer. Neither number
                    alone says whether to believe the model; together they do. */}
                {answer.empiricalP !== null ? (
                  <p className={styles.overtakeNote}>
                    measured rate for this gap {(answer.empiricalP * 100).toFixed(0)}%
                    {answer.low !== null && answer.high !== null
                      ? ` (${(answer.low * 100).toFixed(0)}–${(answer.high * 100).toFixed(0)}%, Wilson)`
                      : ""}
                    {answer.n !== null ? ` over ${answer.n} comparable 2026 approaches` : ""}
                  </p>
                ) : null}
                {/* API.md section 8: a 2022-2025 row is DRS-era and must be badged
                    "historical DRS", never dressed in 2026 Overtake iconography --
                    the two are different mechanisms and these rates differ by 2-4x
                    because of it, which is the point of showing them together. */}
                {answer.eras.length > 1 ? (
                  <ul className={styles.overtakeEras}>
                    {answer.eras.map((row) => (
                      <li key={row.season} data-era={row.era}>
                        <span>{row.season}</span>
                        <span className={styles.overtakeEraTag}>
                          {row.era === "DRS" ? "historical DRS" : "Overtake"}
                        </span>
                        <span>{(row.rate * 100).toFixed(0)}%</span>
                        <span className={styles.overtakeEraN}>n={row.n}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
