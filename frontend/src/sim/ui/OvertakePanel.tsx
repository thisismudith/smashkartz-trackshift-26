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
import { fetchRules, postEligibility, postPassPredict } from "@/api-contract/client";
import {
  overtakeGeometryFromRules, type OvertakeGeometry,
} from "../render/overtakeZones";
import type { SimRenderer } from "../render/scene";
import type { DashboardSnapshot } from "../contract/types";
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

/** The closest car-to-car battle on track right now, or null when the field is
 * strung out. Reads the leaderboard's own published interval. */
export function closestBattle(dashboard: DashboardSnapshot | null): {
  attacker: string; defender: string; gapS: number;
} | null {
  const rows = dashboard?.leaderboard;
  if (!rows || rows.length < 2) return null;
  let best: { attacker: string; defender: string; gapS: number } | null = null;
  for (let i = 1; i < rows.length; i++) {
    const row = rows[i];
    // A car in the pit lane is not contesting the car ahead of it on track.
    if (row.status === "pit" || rows[i - 1].status === "pit") continue;
    const gap = intervalSeconds(row.interval);
    if (gap === null) continue;
    if (best === null || gap < best.gapS) {
      best = { attacker: row.driver, defender: rows[i - 1].driver, gapS: gap };
    }
  }
  return best;
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

/** A service error trimmed to something a panel can show. The body is often a
 * whole JSON envelope; the message inside it is the part worth reading. */
export function readableError(raw: string | undefined): string {
  if (!raw) return "rules service unreachable";
  try {
    const parsed = JSON.parse(raw) as { detail?: { error?: { message?: string } } };
    const message = parsed?.detail?.error?.message;
    if (typeof message === "string" && message) return message.split(".")[0];
  } catch {
    /* not JSON; fall through to the raw text */
  }
  return raw.length > 160 ? `${raw.slice(0, 157)}…` : raw;
}

function sourceLabel(source: string | null): string {
  if (!source) return "unsourced";
  if (source === "DERIVED_TELEMETRY") return "measured in telemetry";
  if (source === "PROXY_HISTORICAL_DRS") return "historical-DRS proxy";
  if (source === "RULE_FIA") return "FIA regulation";
  return source.toLowerCase().replace(/_/g, " ");
}

export function OvertakePanel({
  event, renderer, dashboard,
}: {
  /** The event name the rules service knows this circuit by, e.g. "british_grand_prix". */
  event: string | null;
  renderer: SimRenderer | null;
  dashboard: DashboardSnapshot | null;
}) {
  const [geometry, setGeometry] = useState<OvertakeGeometry | null>(null);
  const [geometryError, setGeometryError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<PassAnswer | null>(null);
  const [eligibility, setEligibility] = useState<{
    marginS: number | null; pEligible: number | null; pReason: string | null;
  } | null>(null);
  const lastScored = useRef<number>(0);

  // --- geometry: fetched once per circuit, then handed to the renderer
  useEffect(() => {
    let disposed = false;
    setGeometry(null);
    setGeometryError(null);
    if (!event) return;
    void (async () => {
      const result = await fetchRules(rulesEventKey(event));
      if (disposed) return;
      if (!result.ok) {
        setGeometryError(readableError(result.error));
        return;
      }
      const parsed = overtakeGeometryFromRules(result.data);
      if (!parsed) {
        setGeometryError("this circuit has no sourced Detection Line or activation zone");
        return;
      }
      setGeometry(parsed);
    })();
    return () => { disposed = true; };
  }, [event]);

  // --- install on the scene; clear on unmount so a circuit swap leaves nothing
  useEffect(() => {
    renderer?.setOvertakeGeometry(geometry);
    return () => { renderer?.setOvertakeGeometry(null); };
  }, [renderer, geometry]);

  const battle = closestBattle(dashboard);
  const gapS = battle?.gapS ?? null;

  // --- eligibility: the margin against the arming threshold, and P(eligible)
  // when a projection is possible. API.md section 8 requires the probability to
  // be shown (not just an armed/not badge) precisely when armed is false and the
  // margin is small -- that is the case a binary badge hides.
  useEffect(() => {
    if (gapS === null || !event) { setEligibility(null); return; }
    let disposed = false;
    void (async () => {
      const result = await postEligibility(rulesEventKey(event), {
        gap: { time_gap_s: gapS },
        overtake_state: "NOT_ARMED",
      });
      if (disposed) return;
      if (!result.ok || !result.data) { setEligibility(null); return; }
      const body = result.data as Record<string, unknown>;
      const margin = (body.eligibility_margin_s ?? {}) as { value?: number | null };
      const p = (body.p_eligible ?? {}) as { value?: number | null; reason?: string };
      setEligibility({
        marginS: typeof margin.value === "number" ? margin.value : null,
        pEligible: typeof p.value === "number" ? p.value : null,
        // No time-to-line is sent from here, so the service refuses the forward
        // projection and says why. Shown rather than hidden: "we did not ask for
        // a horizon" is a different state from "the model could not answer".
        pReason: typeof p.reason === "string" ? p.reason : null,
      });
    })();
    return () => { disposed = true; };
  }, [gapS, event]);

  // --- score the battle, throttled
  useEffect(() => {
    if (gapS === null) { setAnswer(null); return; }
    const now = Date.now();
    if (now - lastScored.current < SCORE_INTERVAL_MS) return;
    lastScored.current = now;
    let disposed = false;
    void (async () => {
      const result = await postPassPredict({
        decision_checkpoint: "DETECTION", time_gap_s: gapS,
        event: event ? rulesEventKey(event) : null,
      });
      if (disposed) return;
      if (!result.ok || !result.data) { setAnswer(null); return; }
      const body = result.data as Record<string, unknown>;
      const interval = (body.interval ?? {}) as Record<string, number>;
      const support = (body.support ?? {}) as Record<string, number>;
      const comparison = body.era_comparison as { seasons?: EraRow[] } | null;
      setAnswer({
        p: typeof body.p_pass_by_outcome_horizon === "number"
          ? body.p_pass_by_outcome_horizon : null,
        low: typeof interval.low === "number" ? interval.low : null,
        high: typeof interval.high === "number" ? interval.high : null,
        n: typeof support.n === "number" ? support.n : null,
        provenance: String(body.provenance ?? "UNKNOWN"),
        stub: Boolean(result.stub),
        reason: String(body.reason ?? ""),
        eras: comparison?.seasons ?? [],
      });
    })();
    return () => { disposed = true; };
  }, [gapS, event]);

  const armed = geometry !== null && gapS !== null && gapS <= 1.0;

  return (
    <div className={styles.overtakePanel}>
      <div className={styles.overtakeHead}>
        <span>Overtake</span>
        {geometry ? (
          <span className={styles.overtakeTag} data-armed={armed}>
            {gapS === null ? "no battle" : armed ? "ARMED" : "not armed"}
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
            <div key={zone.zone} className={styles.overtakeZoneRow}>
              <dt>Zone {zone.zone}</dt>
              <dd>
                {zone.activationM !== null && zone.endM !== null
                  ? `${zone.activationM.toFixed(0)}–${zone.endM.toFixed(0)} m`
                  : "not sourced"}
                <span className={styles.overtakeSource}>{sourceLabel(zone.source)}</span>
              </dd>
            </div>
          ))}
        </dl>
      )}

      {battle ? (
        <div className={styles.overtakeBattle}>
          <p className={styles.overtakeBattleWho}>
            <strong>{battle.attacker}</strong> behind <strong>{battle.defender}</strong>
            {" · "}{battle.gapS.toFixed(2)} s
          </p>
          {eligibility?.marginS !== null && eligibility !== null ? (
            <p className={styles.overtakeNote}>
              {eligibility.marginS! >= 0
                ? `inside the arming threshold by ${eligibility.marginS!.toFixed(2)} s`
                : `outside the arming threshold by ${Math.abs(eligibility.marginS!).toFixed(2)} s`}
              {eligibility.pEligible !== null
                ? ` · P(eligible) ${(eligibility.pEligible * 100).toFixed(0)}%`
                : ""}
            </p>
          ) : null}
          {answer === null ? (
            <p className={styles.overtakeNote}>scoring…</p>
          ) : answer.p === null ? (
            <p className={styles.overtakeNote}>{answer.reason || "no answer for this gap"}</p>
          ) : (
            <>
              <p className={styles.overtakeP}>
                <span className={styles.overtakePValue}>{(answer.p * 100).toFixed(0)}%</span>
                {answer.low !== null && answer.high !== null ? (
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
                {answer.n !== null ? `, in ${answer.n} comparable 2026 approaches` : ""}
              </p>
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
      ) : null}
    </div>
  );
}
