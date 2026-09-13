"use client";

/**
 * The model's evidence reel: the approaches where its Detection-Line probability
 * agreed with what the cars actually did, played one after another.
 *
 * THE HEADER IS NOT DECORATION. Showing only the calls the model got right is
 * cherry-picking unless the population it was drawn from is on screen beside it,
 * so the count, the base rate and the ROC AUC over ALL opportunities are rendered
 * above the reel and cannot be collapsed away. "6 of 412, AUC 0.68" turns a
 * highlight tape into a claim a viewer can check; without it the same tape is an
 * advertisement.
 *
 * Nothing here scores anything. The agreement was computed by
 * scripts/features/export_rivalry_showcase.py against the real artifact and the
 * real outcome labels; this component only renders what that produced, which is
 * why it can be trusted to be the same number the model actually gave.
 */
import { HAAS } from "@/lib/palette";
import type { ShowcaseEvent, ShowcaseRivalry } from "../data/source";
import { displayRating, rawRatingNote } from "./modelRating";
import { ChartLegend, WindowChart, type ChartSeries } from "./WindowChart";
import styles from "./sim.module.css";

function pct(v: number | null | undefined, digits = 0): string {
  return typeof v === "number" && Number.isFinite(v)
    ? `${(v * 100).toFixed(digits)}%` : "—";
}

function num(v: number | null | undefined, digits = 2, unit = ""): string {
  return typeof v === "number" && Number.isFinite(v)
    ? `${v.toFixed(digits)}${unit}` : "—";
}

export function ShowcasePanel({
  showcase, entries, index, headS, onStep, onReplay, onExit,
}: {
  showcase: ShowcaseEvent;
  /** Filtered to the session on screen, in reel order. */
  entries: ShowcaseRivalry[];
  index: number;
  /** Where the replay is, in seconds from the pass. The charts' playhead reads
   * the stage's own clock rather than animating separately, so the line and the
   * cars cannot drift apart. */
  headS: number | null;
  onStep: (next: number) => void;
  onReplay: () => void;
  onExit: () => void;
}) {
  const entry = entries[index];
  const pop = showcase.population;
  const window = entry?.window ?? null;

  // Both cars on one time axis, anchored to the window rather than to each car's
  // own samples: they are sampled at different stations, so per-series scaling
  // would slide them apart in time and make the closing gap unreadable.
  const series: ChartSeries[] = window
    ? [
        { label: entry.attacker, points: window.attacker, colour: HAAS.red },
        { label: entry.defender, points: window.defender, colour: HAAS.grey, dashed: true },
      ]
    : [];
  const tSpan: [number, number] = window
    ? [-window.beforeS, window.afterS]
    : [-3, 3];

  // The displayed rating. `raw` stays reachable: the reel's own selection, its
  // ROC AUC and the agreement it claims were all computed on the unscaled value,
  // and they must keep being read that way.
  const rating = entry ? displayRating(entry.pPass) : null;

  // How many times the circuit's own base rate this is. THE number that makes
  // the percentage readable: a percentage means nothing until you know that only
  // about a tenth of approaches convert here. Taken from the SHOWN value so the
  // panel does not contradict itself on screen.
  const lift = rating && pop.baseRate > 0 ? rating.shown / pop.baseRate : null;

  return (
    <div className={styles.showcase} role="region" aria-label="Model evidence reel">
      <div className={styles.showcaseHead}>
        <span className={styles.showcaseTitle}>Model evidence</span>
        <button type="button" className={styles.showcaseExit} onClick={onExit}>
          Esc · exit
        </button>
      </div>

      {/* The line the reel must never be shown without. */}
      <p className={styles.showcasePopulation}>
        <strong>{entries.length}</strong> calls shown of{" "}
        <strong>{pop.opportunities}</strong> Detection-Line approaches ·{" "}
        {pop.passes} became passes ({pct(pop.baseRate, 1)} base rate) ·{" "}
        ROC AUC <strong>{pop.rocAuc === null ? "—" : pop.rocAuc.toFixed(2)}</strong> over
        all {pop.opportunities}
      </p>

      {!entry ? (
        <p className={styles.overtakeNote}>
          No scored approaches for this session. The reel is built per session, and this
          one produced none the model called confidently either way.
        </p>
      ) : (
        <>
          <div className={styles.showcaseNav}>
            <button type="button" onClick={() => onStep(index - 1)} disabled={index <= 0}>
              ‹ prev
            </button>
            <span>{index + 1} / {entries.length}</span>
            <button
              type="button"
              onClick={() => onStep(index + 1)}
              disabled={index >= entries.length - 1}
            >
              next ›
            </button>
          </div>

          <p className={styles.showcaseWho}>
            <strong>{entry.attacker}</strong> behind <strong>{entry.defender}</strong>
            <span className={styles.showcaseLap}>
              lap {entry.lap ?? "—"}
              {entry.session ? ` · ${entry.session}` : ""}
              {entry.zone !== null ? ` · zone ${entry.zone}` : ""}
            </span>
          </p>

          {/* The model's claim and the outcome, side by side. That juxtaposition
              is the entire content of this panel. */}
          <div className={styles.showcaseVerdict} data-call={entry.call}>
            <div>
              <span className={styles.showcaseLabel}>
                chance of completing the pass, judged at the Detection Line
              </span>
              <span className={styles.showcaseBig} title={rawRatingNote(rating)}>
                {pct(rating ? rating.shown : null)}
              </span>
              {/* Against the base rate, because the raw percentage is not
                  readable on its own: only ~10% of approaches here convert, so
                  48% is a strong call, not a coin flip, and a reader who assumes
                  50% is the dividing line reads it backwards. */}
              {lift !== null ? (
                <span className={styles.showcaseLift}>
                  {lift >= 1
                    ? `${lift.toFixed(1)}× the ${pct(pop.baseRate, 1)} base rate here`
                    : `${(1 / lift).toFixed(1)}× less likely than the ${pct(pop.baseRate, 1)} base rate`}
                </span>
              ) : null}
            </div>
            <div>
              <span className={styles.showcaseLabel}>what the cars actually did</span>
              <span className={styles.showcaseBig}>
                {entry.outcome ? "passed" : "held position"}
              </span>
              <span className={styles.showcaseLift}>
                {entry.call === "TRUE_POSITIVE"
                  ? "the model rated this in its top 10% — and it converted"
                  : "the model rated this in its bottom 10% — and it did not"}
              </span>
            </div>
          </div>

          {/* Every input the model was given, with its value. The probability
              above is a function of exactly these; showing the output without
              them is asking the reader to take the number on trust. */}
          <dl className={styles.showcaseFacts}>
            <dt>Gap at Detection</dt><dd>{num(entry.gapS, 2, " s")}</dd>
            <dt>Closing rate</dt>
            <dd>
              {num(entry.closingRateSPerS, 3, " s/s")}
              <span className={styles.overtakeSource}>positive is closing</span>
            </dd>
            <dt>P(eligible)</dt><dd>{pct(entry.pEligible)}</dd>
            <dt>Detection Line</dt><dd>{num(entry.detectionDistanceM, 0, " m")}</dd>
            <dt>Shadow price</dt>
            <dd>
              <span className={styles.showcaseAwaiting}>awaiting M22</span>
              <span className={styles.overtakeSource}>
                the DP that values a kJ here is not built (CP-10)
              </span>
            </dd>
          </dl>

          {/* The traces. A probability and an outcome say the model was right;
              these say what actually happened in those six seconds. */}
          {window && window.attacker.length > 1 ? (
            <div className={styles.showcaseCharts}>
              <p className={styles.chartHead}>
                <span>
                  {window.centredOn === "POSITION_SWAP"
                    ? "centred on the position swap"
                    : "centred on the closest the attacker got"}
                </span>
                <span className={styles.chartClock}>
                  {headS === null ? "—" : `${headS >= 0 ? "+" : ""}${headS.toFixed(1)} s`}
                </span>
                <button type="button" onClick={onReplay}>replay</button>
              </p>
              <ChartLegend attacker={entry.attacker} defender={entry.defender} />
              {/* The pair gap, not the feed's gap-to-whoever-is-ahead: this one
                  crosses zero at the overtake instead of jumping to a different
                  car the instant it completes. */}
              <WindowChart
                title={`Gap ${entry.attacker} → ${entry.defender}`} unit="m"
                channel="gapToRivalM" series={[series[0]]} tSpan={tSpan}
                digits={0} headS={headS} zeroLine
              />
              <WindowChart
                title="Speed" unit="km/h" channel="speedKph"
                series={series} tSpan={tSpan} digits={0} headS={headS}
              />
              <WindowChart
                title="Throttle" unit="%" channel="throttlePct"
                series={series} tSpan={tSpan} digits={0} headS={headS}
              />
              {/* Energy. SIMULATED throughout -- the public feed carries no
                  battery, no MGU-K power and no fuel flow, so this is the twin's
                  estimate of what the car must have been doing, not a reading
                  off it. Labelled so on the chart, not only in a tooltip. */}
              <WindowChart
                title="ERS deploy (estimated)" unit="kW" channel="ersDeployKw"
                series={[series[0]]} tSpan={tSpan} digits={0} headS={headS}
              />
              <WindowChart
                title="ERS harvest (estimated)" unit="kW" channel="ersHarvestKw"
                series={[series[0]]} tSpan={tSpan} digits={0} headS={headS}
              />
              <p className={styles.chartFoot}>
                Energy is SIMULATED by the physics twin from speed, throttle and
                braking — never measured. {entry.attacker} only.
              </p>
              <p className={styles.chartFoot}>
                {window.attacker.length} measured samples across{" "}
                {(window.beforeS + window.afterS).toFixed(0)} s · OBSERVED, 20 m resampled
              </p>
            </div>
          ) : (
            <p className={styles.overtakeNote}>
              No telemetry window for this approach — the session&rsquo;s feed has no samples
              either side of the line.
            </p>
          )}

        </>
      )}
    </div>
  );
}
