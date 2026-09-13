/**
 * The regulation panel.
 *
 * Two obligations from API.md section 8 shape this page more than anything else:
 *   "Draw the power envelope as a curve against speed; never present peak kW as 'the' power limit"
 *   "Badge every number derived from an envelope key whose `verified` is false, and never claim
 *    legality by construction while `unverified_keys` is non-empty"
 *
 * So the curves are the content, the unverified state is stated at the top rather than buried,
 * and `describe_compliance()`'s own statement is rendered verbatim instead of paraphrased.
 *
 * The curves are drawn straight from the breakpoints, which are the polyline vertices of a
 * piecewise-linear function -- that is reproduction, not evaluation. Reading the cap at an
 * arbitrary speed is evaluation and belongs to rules.py (UI.md section 6.4).
 *
 * That distinction is also why the hover layer reads a table rather than a formula. A tooltip over
 * a 4-point polyline can only honestly report the four vertices; reporting a cap at 317 km/h would
 * mean evaluating the envelope in the browser, which is the second implementation AGENTS.md
 * section 32 forbids. So when the artifact carries `sampled_curves` -- the step table rules.py
 * emits from its OWN `max_electrical_power_kw` (API.md section 5.3a) -- the readout uses it, and
 * when it does not, the readout falls back to the breakpoints and SAYS that the cap between them
 * is not shown. Neither path invents a number.
 */
"use client";

import { useEffect, useId, useState, type ReactNode } from "react";
import { CHART } from "@/lib/palette";
import { defaultSimSource, type RuleSet } from "@/sim/data/source";
import {
  ChartFrame,
  Plot,
  Grid,
  XAxis,
  YAxis,
  Line,
  RefLine,
  XRegion,
  niceTicks,
  useSeriesToggle,
  type HoverSeries,
} from "@/sim/charts";
import s from "./rules.module.css";

const MODE_COLOUR: Record<string, string> = {
  normal: CHART.series[0],
  override: CHART.series[1],
};

/** The third categorical slot, unused by the two modes, so the reader's own figure never reads as
 * a regulation curve. It carries its value as text on the line, never colour alone. */
const DEPLOY_COLOUR = CHART.series[2];

export default function RulesView() {
  const [rules, setRules] = useState<RuleSet | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Kept as the raw string: an empty field is "no reference line", not 0 kW. */
  const [deployInput, setDeployInput] = useState("");
  const { hidden, toggle, visible } = useSeriesToggle();
  const deployId = useId();
  const deployNoteId = useId();

  useEffect(() => {
    let live = true;
    defaultSimSource
      .rules()
      .then((r) => {
        if (!live) return;
        if (!r) setError("The built artifacts predate the rule engine, so no thresholds are available.");
        else setRules(r);
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, []);

  if (error) {
    return (
      <main className={s.main}>
        <p className={s.lede}>{error}</p>
      </main>
    );
  }
  if (!rules) {
    return (
      <main className={s.main}>
        <p className={s.lede}>Loading rule configuration…</p>
      </main>
    );
  }

  const modes = Object.keys(rules.power_envelope);
  const allSpeeds = modes.flatMap((m) => rules.power_envelope[m].breakpoints_kmh);
  const allPower = modes.flatMap((m) => rules.power_envelope[m].max_power_kw);
  const maxSpeed = Math.max(...allSpeeds);
  // end the y axis on a tick above the data: a plateau drawn exactly on the frame reads as
  // clipped, and the reader cannot tell the curve from the chart border
  const powerTop = niceTicks(0, Math.max(...allPower), 4).niceMax;

  // Where the two curves stop coinciding. Below it, override confers no power advantage and
  // the mode is not observable at all (API.md section 3.11 / 20.2) -- that region is shaded
  // rather than left looking like ordinary chart space.
  const separation = separationSpeed(rules);

  // The sampled cap table, when this artifact has one. Read defensively and all-or-nothing: a
  // table covering one mode only would put a sampled row and a breakpoint row in the same
  // tooltip, and the note could then no longer describe the readout in one sentence.
  const sampled = readSampledTable(
    (rules as RuleSet & { sampled_curves?: unknown }).sampled_curves,
    modes,
  );

  const visibleModes = visible(modes, (m) => m);
  const colourOf = (m: string) => MODE_COLOUR[m] ?? CHART.series[2];

  const hoverSeries = alignOnSharedX(
    visibleModes.map((m) => {
      const curve = sampled?.byMode[m];
      return {
        label: m,
        colour: colourOf(m),
        // With no sampled table these are the SAME arrays the mark draws, so the crosshair can
        // only land on a vertex. With one, the samples lie on that polyline by construction --
        // rules.py evaluated the very curve these vertices describe.
        x: curve ? curve.speedKmh : rules.power_envelope[m].breakpoints_kmh,
        y: curve ? curve.powerKw : rules.power_envelope[m].max_power_kw,
      };
    }),
  );

  const deployKw = parseDeploy(deployInput);
  const deployUnreadable = deployInput.trim() !== "" && deployKw === null;
  const deployOnScale = deployKw !== null && deployKw <= powerTop;

  return (
    <main className={s.main}>
      <header className={s.head}>
        <p className={s.kicker}>Regulation · {rules.competition.configuration_version}</p>
        <h1 className={s.title}>
          2026 <em>Envelope</em>
        </h1>
        <p className={s.lede}>
          Maximum electrical deployment is a function of speed, not a constant. These are the two
          piecewise-linear curves the rule engine evaluates, and the three energy quantities it
          accounts against — drawn from the one configuration every model in the system reads.
        </p>
      </header>

      {!rules.compliance.all_values_verified ? (
        <section className={s.warning} role="note">
          <h2 className={s.warningHead}>Unverified regulation values</h2>
          <p className={s.warningBody}>{rules.compliance.statement}</p>
          <p className={s.warningBody}>
            <strong>{rules.compliance.unverified_keys.length} keys</strong> carry{" "}
            <code>verified: false</code>:{" "}
            {rules.compliance.unverified_keys.map((k) => (
              <code key={k} className={s.keyChip}>
                {k}
              </code>
            ))}
          </p>
          <p className={s.warningBody}>
            <code>legal_by_construction</code> is{" "}
            <strong>{String(rules.compliance.legal_by_construction)}</strong>. Nothing on this page
            should be read as a statement of what the regulations require.
          </p>
        </section>
      ) : null}

      <section className={s.snapshot} aria-label="Rule snapshot status">
        <div className={s.snapshotIntro}>
          <span className={s.snapshotEyebrow}>Configuration snapshot</span>
          <strong>{rules.competition.configuration_version}</strong>
          <p>{rules.regulation_snapshot.note}</p>
        </div>
        <div className={s.snapshotGrid}>
          <SnapshotItem label="Season" value={String(rules.regulation_snapshot.season)} />
          <SnapshotItem label="Schema" value={`v${rules.schema_version}`} />
          <SnapshotItem label="Snapshot" value={rules.regulation_snapshot.verified ? "Verified" : "Unverified"} tone={rules.regulation_snapshot.verified ? "ok" : "warn"} />
          <SnapshotItem label="Legal mask" value={rules.compliance.legal_by_construction ? "Enabled" : "Not enabled"} tone={rules.compliance.legal_by_construction ? "ok" : "warn"} />
        </div>
      </section>

      <section className={s.block}>
        <ChartFrame
          title="Speed-dependent power envelope"
          units="kW vs km/h"
          provenance="RULE"
          legend={modes.map((m) => ({ label: m, colour: colourOf(m) }))}
          hidden={hidden}
          onToggleSeries={toggle}
          controls={
            <div className={s.headroom}>
              <label className={s.headroomLabel} htmlFor={deployId}>
                deploy power
              </label>
              <input
                id={deployId}
                className={s.headroomInput}
                type="number"
                inputMode="numeric"
                min={0}
                step={10}
                placeholder="—"
                value={deployInput}
                aria-describedby={deployNoteId}
                onChange={(e) => setDeployInput(e.target.value)}
              />
              <span className={s.headroomUnit}>kW</span>
              {deployInput !== "" ? (
                <button type="button" className={s.headroomClear} onClick={() => setDeployInput("")}>
                  clear
                </button>
              ) : null}
              <span id={deployNoteId} className={s.headroomNote} role="status">
                {headroomNote({
                  deployKw,
                  deployUnreadable,
                  deployOnScale,
                  powerTop,
                  sampled,
                  visibleModes,
                })}
              </span>
            </div>
          }
          note={
            <>
              The curves coincide below{" "}
              {separation === null ? "the separation speed" : `${separation.toFixed(0)} km/h`}: there,
              override confers no power advantage and the mode is not observable from the outside.
              Every value is an unverified reported figure, derived from the formulas transcribed in{" "}
              <code>Math.md §7.2</code> rather than typed in.{" "}
              {sampled ? (
                <>
                  The readout snaps to the nearest point of this artifact&apos;s sampled cap table —
                  no two samples more than {formatSpeed(sampled.maxGapKmh)} km/h apart — which{" "}
                  <code>rules.py</code> produced with the same{" "}
                  <code>max_electrical_power_kw</code> the simulator calls, so nothing is
                  interpolated in the browser.
                </>
              ) : (
                <>
                  This artifact carries no sampled cap table, so the readout can only report the
                  breakpoints themselves: hovering snaps to a vertex, and{" "}
                  <strong>the cap between two breakpoints is not shown</strong>. Evaluating the
                  curve is <code>rules.py</code>&apos;s job, not the browser&apos;s (
                  <code>AGENTS.md §32</code>).
                </>
              )}{" "}
              {visibleModes.length === 0
                ? "Both modes are hidden — use the legend to bring one back."
                : null}
            </>
          }
          table={{
            columns: ["mode", "speed km/h", "max power kW"],
            rows: modes.flatMap((m) =>
              rules.power_envelope[m].breakpoints_kmh.map((v, i) => [
                m,
                v,
                rules.power_envelope[m].max_power_kw[i],
              ]),
            ),
          }}
        >
          <Plot
            xDomain={[0, Math.ceil(maxSpeed / 20) * 20]}
            yDomain={[0, powerTop]}
            height={300}
            ariaLabel="Maximum electrical power against car speed, for normal and override modes"
            hover={
              hoverSeries.length > 0
                ? { series: hoverSeries, xLabel: "speed", xFormat: formatSpeed }
                : undefined
            }
          >
            <Grid />
            {separation !== null ? (
              <XRegion from={0} to={separation} label="mode not discriminable" />
            ) : null}
            <XAxis label="car speed km/h" />
            <YAxis label="max electrical power kW" />
            {separation !== null ? (
              <RefLine x={separation} label={`${separation.toFixed(0)} km/h`} />
            ) : null}
            {deployKw !== null && deployOnScale ? (
              <RefLine
                y={deployKw}
                label={`deploy ${Math.round(deployKw)} kW`}
                colour={DEPLOY_COLOUR}
              />
            ) : null}
            {visibleModes.map((m) => (
              <Line
                key={m}
                x={rules.power_envelope[m].breakpoints_kmh}
                y={rules.power_envelope[m].max_power_kw}
                colour={colourOf(m)}
                label={m}
                labelIndex={Math.max(0, rules.power_envelope[m].breakpoints_kmh.length - 2)}
              />
            ))}
          </Plot>
        </ChartFrame>

        <div className={s.derivations}>
          {modes.map((m) => (
            <details key={m} className={s.details}>
              <summary className={s.summary}>
                <span className={s.swatch} style={{ background: colourOf(m) }} />
                {m} — how this curve was derived
              </summary>
              <p className={s.detailBody}>{rules.power_envelope[m].derivation}</p>
              <p className={s.detailMeta}>
                <span className={s.metaKey}>source</span> {rules.power_envelope[m].source}
              </p>
              <p className={s.detailMeta}>
                <span className={s.metaKey}>citation</span> {rules.power_envelope[m].citation}
              </p>
            </details>
          ))}
        </div>
      </section>

      <section className={s.block}>
        <h2 className={s.sectionTitle}>Three energy quantities</h2>
        <p className={s.sectionLede}>
          Deliberately three different numbers with three different accounting windows, so they
          cannot be silently interchanged. A store capacity is an instantaneous bound; a budget is
          spent over a lap.
        </p>
        <div className={s.quantities}>
          {budgetsOf(rules).map(({ key, label, q }) => (
            <article key={key} className={s.quantity}>
              <h3 className={s.quantityLabel}>{label}</h3>
              <p className={s.quantityValue}>
                {q.value_mj.toFixed(1)} <span className={s.quantityUnit}>MJ</span>
              </p>
              <p className={s.quantityWindow}>{q.accounting_window.replace(/_/g, " ")}</p>
              {!q.verified ? <span className={s.unverified}>unverified</span> : null}
              <p className={s.quantityNote}>{q.note}</p>
            </article>
          ))}
        </div>
      </section>

      <section className={s.block}>
        <h2 className={s.sectionTitle}>Provenance</h2>
        <dl className={s.provList}>
          <div className={s.provRow}>
            <dt>Season</dt>
            <dd>{rules.regulation_snapshot.season}</dd>
          </div>
          <div className={s.provRow}>
            <dt>Configuration</dt>
            <dd>{rules.competition.configuration_version}</dd>
          </div>
          <div className={s.provRow}>
            <dt>Retrieved</dt>
            <dd>{rules.regulation_snapshot.retrieved_at ?? "— never independently retrieved"}</dd>
          </div>
          <div className={s.provRow}>
            <dt>Models speed-dependent envelope</dt>
            <dd>{String(rules.compliance.models_speed_dependent_envelope)}</dd>
          </div>
          <div className={s.provRow}>
            <dt>Sampled cap table</dt>
            <dd>
              {sampled
                ? `${sampled.byMode[modes[0]].speedKmh.length} points per mode, ≤ ${formatSpeed(sampled.maxGapKmh)} km/h apart`
                : "— not in this artifact"}
            </dd>
          </div>
        </dl>
        <p className={s.sectionLede}>{rules.regulation_snapshot.note}</p>
        {/* the sampled table's own statement, verbatim: it is the thing that licenses the hover
            readout, so paraphrasing it here would hide what the reader is trusting */}
        {sampled?.note ? <p className={s.sectionLede}>{sampled.note}</p> : null}
        <ul className={s.sources}>
          {rules.regulation_snapshot.source_documents.map((d, i) => (
            <li key={i}>{d}</li>
          ))}
        </ul>
      </section>
    </main>
  );
}

function SnapshotItem({ label, value, tone = "neutral" }: { label: string; value: string; tone?: "ok" | "warn" | "neutral" }) {
  return <div className={`${s.snapshotItem} ${tone === "warn" ? s.snapshotWarn : tone === "ok" ? s.snapshotOk : ""}`}><span>{label}</span><strong>{value}</strong></div>;
}

function budgetsOf(rules: RuleSet) {
  return [
    { key: "ers_store_capacity", label: "Energy store", q: rules.energy_budget.ers_store_capacity },
    { key: "deploy_budget", label: "Deploy budget", q: rules.energy_budget.deploy_budget },
    { key: "harvest_budget", label: "Harvest budget", q: rules.energy_budget.harvest_budget },
  ];
}

/**
 * The lowest speed at which the two curves stop agreeing.
 *
 * Read off the breakpoints rather than computed by evaluating both envelopes across a sweep:
 * evaluating the cap is rules.py's job, and a second implementation here is exactly what
 * AGENTS.md section 32 forbids. Returns null when there are not two modes to compare, in which
 * case the page simply omits the marker rather than inventing one.
 */
function separationSpeed(rules: RuleSet): number | null {
  const normal = rules.power_envelope.normal;
  const override = rules.power_envelope.override;
  if (!normal || !override) return null;
  // Both curves start flat at the same ceiling; they part where the lower one begins to
  // taper, so the separation speed is the last breakpoint still at full power.
  const taper = normal.max_power_kw.findIndex((p, i) => i > 0 && p < normal.max_power_kw[i - 1]);
  return taper > 0 ? normal.breakpoints_kmh[taper - 1] : null;
}

/* --------------------------------------------------------------- readout --- */

/**
 * Speeds as the reader writes them.
 *
 * Whole km/h print without decimals, which is every point of a sampled table. The breakpoint
 * fallback is the reason for the other half: the cliff vertex sits at 339.999 km/h, and rounding
 * THAT to "340" would caption a 150 kW reading with the speed at which the cap is already zero.
 */
function formatSpeed(v: number): string {
  return Number.isInteger(v) ? v.toFixed(0) : String(Number(v.toFixed(3)));
}

/**
 * Put every hover series on ONE shared x grid: the union of the speeds the series actually carry,
 * with a gap (NaN) wherever a series has no sample of its own at that speed.
 *
 * Plot snaps the crosshair to the nearest x of ANY series, then reads each series at its own
 * nearest point and tolerates a ~2% mismatch. On the breakpoint fallback that tolerance is enough
 * to caption one curve's vertex with another curve's value: hovering the override vertex at
 * 337.5 km/h read `normal` off its 339.999 km/h vertex and printed "337.5 km/h · normal 150 kW",
 * a cap the normal curve does not have at that speed (it is 162.5 kW there) -- an interpolated
 * number by accident, which is the one thing this page must not produce.
 *
 * Sharing the grid makes the readout exact by construction: a series either has a sample at the
 * snapped speed or is left out of the tooltip entirely. Nothing is invented to fill the gaps.
 */
function alignOnSharedX(
  series: readonly { label: string; colour: string; x: readonly number[]; y: readonly number[] }[],
): HoverSeries[] {
  const grid = [...new Set(series.flatMap((sr) => [...sr.x]))].sort((a, b) => a - b);
  return series.map((sr) => {
    const at = new Map<number, number>();
    for (let i = 0; i < sr.x.length; i++) at.set(sr.x[i], sr.y[i]);
    return {
      label: sr.label,
      colour: sr.colour,
      x: grid,
      // `?? NaN` and not `|| NaN`: 0 kW past the cutoff is a real cap, not a missing sample
      y: grid.map((v) => at.get(v) ?? NaN),
      format: (v: number) => `${Math.round(v)} kW`,
    };
  });
}

/** The reader's own number. An empty or unreadable field is no line at all, never 0 kW. */
function parseDeploy(raw: string): number | null {
  const t = raw.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

/** What the headroom control says beneath itself. Every branch states what is NOT drawn. */
function headroomNote({
  deployKw,
  deployUnreadable,
  deployOnScale,
  powerTop,
  sampled,
  visibleModes,
}: {
  deployKw: number | null;
  deployUnreadable: boolean;
  deployOnScale: boolean;
  powerTop: number;
  sampled: SampledTable | null;
  visibleModes: string[];
}): ReactNode {
  if (deployUnreadable) return "Not a power in kW — no line drawn.";
  if (deployKw === null) return "Draws your figure across the envelope, so the gap to the cap is visible.";
  if (!deployOnScale) return `Above the ${powerTop} kW axis ceiling — no line drawn.`;
  if (visibleModes.length === 0) return "Drawn, but both modes are hidden.";
  if (!sampled) return "Drawn. The cap between breakpoints is not evaluated here, so no crossing speed is given.";
  // A lookup in the shipped table, not a solve: the first sample whose cap is under the reader's
  // figure. The true crossing lies within one step below it, which the suffix says.
  const parts = visibleModes.map((m) => {
    const curve = sampled.byMode[m];
    const at = firstSpeedBelow(curve, deployKw);
    return at === null
      ? `${m} never (sampled to ${formatSpeed(curve.speedKmh[curve.speedKmh.length - 1])} km/h)`
      : `${m} ${formatSpeed(at)} km/h`;
  });
  return `Cap under ${Math.round(deployKw)} kW from: ${parts.join(" · ")} (first sample below; samples at most ${formatSpeed(sampled.maxGapKmh)} km/h apart).`;
}

/** First sampled speed whose cap is below `kw`. No monotonicity is assumed: this is literally
 * "the first sample below", which is all the caption claims. */
function firstSpeedBelow(curve: SampledCurve, kw: number): number | null {
  for (let i = 0; i < curve.speedKmh.length; i++) {
    if (curve.powerKw[i] < kw) return curve.speedKmh[i];
  }
  return null;
}

/* --------------------------------------------------- sampled cap table --- */

interface SampledCurve {
  speedKmh: number[];
  powerKw: number[];
}

interface SampledTable {
  byMode: Record<string, SampledCurve>;
  /**
   * Widest gap between consecutive samples, over every mode.
   *
   * Measured rather than taken from the artifact's declared `step_kmh`, because the two are not
   * the same claim: this table is a step grid UNIONED with every breakpoint, so its spacing is
   * irregular by design. The hover snaps to the nearest sample, so the widest gap is what bounds
   * how far the readout can sit from the speed under the pointer -- that is the number the note
   * owes the reader.
   */
  maxGapKmh: number;
  /** The table's own provenance sentence, shown verbatim rather than paraphrased. */
  note: string | null;
}

const SPEED_KEYS = ["speed_kmh", "speedKmh", "kmh"];
const POWER_KEYS = ["max_power_kw", "power_kw", "maxPowerKw", "powerKw", "kw"];

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function numberAt(o: Record<string, unknown>, keys: readonly string[]): number | null {
  for (const k of keys) {
    const v = o[k];
    if (typeof v === "number" && Number.isFinite(v)) return v;
  }
  return null;
}

function numberArrayAt(o: Record<string, unknown>, keys: readonly string[]): number[] | null {
  for (const k of keys) {
    const v = o[k];
    if (Array.isArray(v) && v.every((n) => typeof n === "number" && Number.isFinite(n))) {
      return v as number[];
    }
  }
  return null;
}

/**
 * One mode's samples, out of whichever shape the artifact carries.
 *
 * Written tolerantly on purpose: this reader ships before the writer does, and the alternative to
 * tolerance is a page that silently loses its readout to a key-name mismatch. What it will NOT do
 * is repair a table -- anything non-finite, out of order, or shorter than two points returns null
 * and the page falls back to breakpoint-only hover with the note that says so.
 */
function readCurve(raw: unknown): SampledCurve | null {
  const speedKmh: number[] = [];
  const powerKw: number[] = [];

  if (Array.isArray(raw)) {
    for (const pt of raw) {
      if (Array.isArray(pt)) {
        if (typeof pt[0] !== "number" || typeof pt[1] !== "number") return null;
        speedKmh.push(pt[0]);
        powerKw.push(pt[1]);
        continue;
      }
      if (!isRecord(pt)) return null;
      const v = numberAt(pt, SPEED_KEYS);
      const p = numberAt(pt, POWER_KEYS);
      if (v === null || p === null) return null;
      speedKmh.push(v);
      powerKw.push(p);
    }
  } else if (isRecord(raw)) {
    const vs = numberArrayAt(raw, [...SPEED_KEYS, "speeds_kmh"]);
    const ps = numberArrayAt(raw, POWER_KEYS);
    if (!vs || !ps || vs.length !== ps.length) return null;
    speedKmh.push(...vs);
    powerKw.push(...ps);
  } else {
    return null;
  }

  // Two points is the least that can say anything a breakpoint pair cannot, and Plot's nearest-x
  // scan reads the series as given -- an unsorted table would snap the crosshair backwards.
  if (speedKmh.length < 2) return null;
  for (let i = 0; i < speedKmh.length; i++) {
    if (!Number.isFinite(speedKmh[i]) || !Number.isFinite(powerKw[i])) return null;
    if (i > 0 && speedKmh[i] <= speedKmh[i - 1]) return null;
  }
  return { speedKmh, powerKw };
}

/** All modes or none: see the call site for why partial coverage is refused. */
function readSampledTable(raw: unknown, modes: string[]): SampledTable | null {
  if (!isRecord(raw) || modes.length === 0) return null;
  // API.md section 5.3a nests the modes under `curves`; a flatter emission puts them at the top.
  const src = isRecord(raw.curves) ? raw.curves : raw;
  const byMode: Record<string, SampledCurve> = {};
  let maxGapKmh = 0;
  for (const m of modes) {
    const curve = readCurve(src[m]);
    if (!curve) return null;
    byMode[m] = curve;
    for (let i = 1; i < curve.speedKmh.length; i++) {
      maxGapKmh = Math.max(maxGapKmh, curve.speedKmh[i] - curve.speedKmh[i - 1]);
    }
  }
  const note = raw.note;
  return { byMode, maxGapKmh, note: typeof note === "string" ? note : null };
}
