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
import type { DashboardRow, DashboardSnapshot } from "../contract/types";
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

/** Stable identity for one pairing, so scores survive a re-render. */
function battleKey(battle: Battle): string {
  return `${battle.attacker}>${battle.defender}`;
}

interface BattleScore {
  answer: PassAnswer | null;
  marginS: number | null;
  pEligible: number | null;
}

export function OvertakePanel({
  event, renderer, dashboard, selectedDriver, geometry, geometryError,
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
}) {
  const [scores, setScores] = useState<Record<string, BattleScore>>({});
  const lastScored = useRef<number>(0);

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
    let disposed = false;
    void (async () => {
      const next: Record<string, BattleScore> = {};
      await Promise.all(battles.map(async (battle) => {
        const [pass, elig] = await Promise.all([
          // The request names the PAIR, not just a gap. A bare number would let
          // the service answer about "some cars 0.4 s apart" when the question is
          // always about these two specific cars -- and once M10 lands it needs
          // the identities to pick up per-driver and per-team features at all.
          postPassPredict({
            decision_checkpoint: "DETECTION",
            time_gap_s: battle.gapS,
            event: rulesEventKey(event),
            attacker: battle.attacker,
            defender: battle.defender,
          }),
          postEligibility(rulesEventKey(event), {
            gap: { time_gap_s: battle.gapS },
            overtake_state: "NOT_ARMED",
            attacker: battle.attacker,
            defender: battle.defender,
          }),
        ]);
        const score: BattleScore = { answer: null, marginS: null, pEligible: null };
        if (pass.ok && pass.data) {
          const body = pass.data as Record<string, unknown>;
          const interval = (body.interval ?? {}) as Record<string, number>;
          const support = (body.support ?? {}) as Record<string, number>;
          const comparison = body.era_comparison as { seasons?: EraRow[] } | null;
          score.answer = {
            p: typeof body.p_pass_by_outcome_horizon === "number"
              ? body.p_pass_by_outcome_horizon : null,
            low: typeof interval.low === "number" ? interval.low : null,
            high: typeof interval.high === "number" ? interval.high : null,
            n: typeof support.n === "number" ? support.n : null,
            provenance: String(body.provenance ?? "UNKNOWN"),
            stub: Boolean(pass.stub),
            reason: String(body.reason ?? ""),
            eras: comparison?.seasons ?? [],
          };
        }
        if (elig.ok && elig.data) {
          const body = elig.data as Record<string, unknown>;
          const margin = (body.eligibility_margin_s ?? {}) as { value?: number | null };
          const p = (body.p_eligible ?? {}) as { value?: number | null };
          score.marginS = typeof margin.value === "number" ? margin.value : null;
          score.pEligible = typeof p.value === "number" ? p.value : null;
        }
        next[battleKey(battle)] = score;
      }));
      if (!disposed) setScores(next);
    })();
    return () => { disposed = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gapKey, event]);

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
            {score?.marginS !== null && score !== undefined ? (
              <p className={styles.overtakeNote}>
                {score.marginS! >= 0
                  ? `inside the arming threshold by ${score.marginS!.toFixed(2)} s`
                  : `outside the arming threshold by ${Math.abs(score.marginS!).toFixed(2)} s`}
                {score.pEligible !== null
                  ? ` · P(eligible) ${(score.pEligible * 100).toFixed(0)}%`
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
        );
      })}
    </div>
  );
}
