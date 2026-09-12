/**
 * The cross-circuit energy league.
 *
 * Every energy surface in the app is one session at a time, so the question "who spends and
 * recovers energy WHERE" has never been answerable here. The per-lap twin output is already
 * shipping on every driver-lap of all 18 built sessions; this reads it across all of them.
 *
 * Two deliberate choices the page states on screen rather than burying:
 *
 * 1. MEDIAN, NOT MEAN. Pit laps, safety-car laps and the twin's own failures give these
 *    distributions long tails; a mean is dragged by them and would describe no lap anyone drove.
 * 2. THE BALANCE DOES NOT CLOSE. The headline number is strongly negative, and roughly three
 *    quarters of racing laps end pinned at the store floor. That is our twin failing to
 *    account, not cars running empty, and it is the first thing the page says.
 *
 * The manifests are 11.6 MB in total, so they are fetched one at a time and the figure is
 * redrawn as each lands. The .bin sample blobs (139 MB) are never touched: every number here
 * comes from the manifest's per-lap energy block.
 */
"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { CHART } from "@/lib/palette";
import { Segmented } from "@/components/ui";
import { defaultSimSource, type SessionRef } from "@/sim/data/source";
import { teamColour, type Catalogue } from "@/sim/data/catalogue";
import type { RawSessionManifest } from "@/sim/data/manifest";
import {
  ChartFrame,
  Plot,
  Grid,
  XAxis,
  HBars,
  ErrorBarsH,
  BandLabels,
  RefLine,
  NoData,
  niceTicks,
} from "@/sim/charts";
import s from "./league.module.css";

/* ------------------------------------------------------------------ data -- */

/** One racing driver-lap, reduced to the energy fields this page aggregates. */
interface LapRow {
  trackSlug: string;
  event: string;
  session: string;
  driver: string;
  team: string | null;
  deployMj: number;
  harvestMj: number;
  balanceMj: number;
  /** The twin's OWN stated uncertainty on the end-of-lap store. */
  uncertaintyMj: number;
  /** The twin clipped its power request against the envelope at least once on this lap. */
  capped: boolean;
  /** End-of-lap store sits at the floor -- the accounting ran out, not the car. */
  atFloor: boolean;
  warned: boolean;
}

interface SessionSlice {
  key: string;
  ref: SessionRef;
  event: string;
  rows: LapRow[];
  /** Laps the racing-lap filter removed, and laps the twin could not model at all. */
  excludedNonRacing: number;
  excludedNoEnergy: number;
}

/**
 * Reduced slices, keyed "<slug>/<session>" -- NOT the raw manifests.
 *
 * A manifest is mostly a byte index into the sample blob, which this page never reads; holding
 * 18 of them parsed would cost far more than the ~14 k small rows the aggregation actually
 * needs. Module-level so returning to /league within a page session redraws with no refetch.
 */
const SLICES = new Map<string, SessionSlice>();

/** Deleted, inaccurate and pit in/out laps are not laps driven at racing energy -- an in-lap's
 * deploy describes a pit entry, not the circuit. Removed here and counted, so the coverage
 * line can report exactly how many laps the figure declines to use. */
function reduceManifest(ref: SessionRef, m: RawSessionManifest): SessionSlice {
  const rows: LapRow[] = [];
  let excludedNonRacing = 0;
  let excludedNoEnergy = 0;
  for (const d of m.drivers) {
    for (const lap of d.laps) {
      if (lap.del || !lap.iacc || lap.pin !== null || lap.pout !== null) {
        excludedNonRacing++;
        continue;
      }
      const e = lap.energy;
      if (!e) {
        excludedNoEnergy++;
        continue;
      }
      rows.push({
        trackSlug: m.trackSlug,
        event: m.event,
        session: m.session,
        driver: d.driver,
        team: d.team,
        deployMj: e.ersEnergyUsedMj,
        harvestMj: e.ersEnergyHarvestedMj,
        balanceMj: e.energyBalanceMj,
        uncertaintyMj: e.socUncertaintyMj,
        capped: e.envelopeCapViolations > 0,
        atFloor: e.socEndMj <= 0,
        warned: e.warnings.length > 0,
      });
    }
  }
  return {
    key: `${ref.trackSlug}/${ref.session}`,
    ref,
    event: m.event,
    rows,
    excludedNonRacing,
    excludedNoEnergy,
  };
}

/* -------------------------------------------------------------- measures -- */

type MeasureKey = "deploy" | "harvest" | "balance" | "capped";
type BreakdownKey = "circuit" | "team" | "driver";

interface Measure {
  label: string;
  title: string;
  units: string;
  /** Per-lap quantity to take the median of. null for the cap-hit rate, which is a share of
   * laps and therefore has no per-lap distribution to summarise. */
  value: ((r: LapRow) => number) | null;
  format: (v: number) => string;
  note: ReactNode;
}

const MEASURES: Record<MeasureKey, Measure> = {
  deploy: {
    label: "Deploy",
    title: "Estimated ERS deploy per lap",
    units: "MJ per lap (median)",
    value: (r) => r.deployMj,
    format: (v) => v.toFixed(2),
    note: "Electrical energy the twin spent at the wheels over the lap, simulated from the speed and throttle traces against the 2026 envelope. The public feed carries no deploy channel, so this is a model output and not a measurement.",
  },
  harvest: {
    label: "Harvest",
    title: "Estimated ERS harvest per lap",
    units: "MJ per lap (median)",
    value: (r) => r.harvestMj,
    format: (v) => v.toFixed(2),
    note: "Energy the twin recovered under braking over the lap. It follows braking energy, so it tracks how much of the lap a circuit spends shedding speed rather than how efficient a car is.",
  },
  balance: {
    label: "Balance",
    title: "Estimated energy balance per lap",
    units: "MJ per lap (median), harvest minus deploy",
    value: (r) => r.balanceMj,
    format: (v) => (v > 0 ? `+${v.toFixed(2)}` : v.toFixed(2)),
    note: "Harvest minus deploy. A car that could run this way lap after lap would sit at zero. Ours does not: the median is several MJ short on almost every circuit, which is our twin's accounting failing to close rather than a field of cars running flat.",
  },
  capped: {
    label: "Cap hits",
    title: "Laps where our model hit its own power cap",
    units: "share of racing laps",
    value: null,
    format: (v) => `${(v * 100).toFixed(1)}%`,
    note: "Share of laps on which the twin clipped its requested power against the speed-dependent envelope at least once. This is a property of OUR model — either its power request is too eager or the envelope it is checked against is an unverified 2026 placeholder — and never a claim that a car exceeded a limit. It is drawn in the warning colour for that reason, and against the full 0–100% axis so a near-universal fault cannot masquerade as a ranking.",
  },
};

const MEASURE_OPTIONS = (Object.keys(MEASURES) as MeasureKey[]).map((k) => ({
  value: k,
  label: MEASURES[k].label,
  title: MEASURES[k].title,
}));

const BREAKDOWN_OPTIONS: { value: BreakdownKey; label: string }[] = [
  { value: "circuit", label: "Circuit" },
  { value: "team", label: "Team" },
  { value: "driver", label: "Driver" },
];

const BREAKDOWN_LABEL: Record<BreakdownKey, string> = {
  circuit: "Circuit",
  team: "Team",
  driver: "Driver",
};

/* ------------------------------------------------------------------ math -- */

/** Linear-interpolated quantile (the common "type 7") on an ascending array. */
function quantile(sorted: readonly number[], q: number): number {
  const n = sorted.length;
  if (n === 0) return NaN;
  if (n === 1) return sorted[0];
  const pos = (n - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

interface RankRow {
  key: string;
  label: string;
  /** Team used for colour when the breakdown IS teams. */
  team: string | null;
  /** Every team seen on this row's laps: one driver ran for two, and the table says so. */
  teamLabel: string;
  n: number;
  cappedLaps: number;
  value: number;
  lo: number;
  hi: number;
}

/** "British Grand Prix" -> "British". The suffix is on every row and carries no information. */
function shortEvent(event: string): string {
  return event.replace(/\s+Grand Prix$/i, "");
}

/* ------------------------------------------------------------------ view -- */

export default function LeagueView() {
  const [slices, setSlices] = useState<SessionSlice[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [cat, setCat] = useState<Catalogue | null>(null);
  const [failed, setFailed] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [measureKey, setMeasureKey] = useState<MeasureKey>("balance");
  const [breakdown, setBreakdown] = useState<BreakdownKey>("circuit");

  // One manifest at a time, appended as it lands. Fetching 11.6 MB in parallel would hold the
  // figure blank until the slowest response; the point here is that the reader watches it fill.
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const [idx, catalogue] = await Promise.all([
          defaultSimSource.index(),
          defaultSimSource.catalogue<Catalogue>(),
        ]);
        if (!live) return;
        setCat(catalogue);

        const refs: SessionRef[] = [];
        for (const [trackSlug, sessions] of Object.entries(idx.sessions)) {
          for (const session of Object.keys(sessions)) refs.push({ trackSlug, session });
        }
        refs.sort(
          (a, b) => a.trackSlug.localeCompare(b.trackSlug) || a.session.localeCompare(b.session),
        );
        setTotal(refs.length);

        for (const ref of refs) {
          if (!live) return;
          const key = `${ref.trackSlug}/${ref.session}`;
          let slice = SLICES.get(key);
          if (!slice) {
            try {
              slice = reduceManifest(ref, await defaultSimSource.sessionManifest(ref));
              SLICES.set(key, slice);
            } catch (err) {
              // One unreadable session is named and skipped rather than failing the page: a
              // partial league is still an answer, as long as the coverage line admits it.
              if (!live) return;
              const line = `${key} (${(err as Error).message})`;
              setFailed((f) => (f.includes(line) ? f : [...f, line]));
              continue;
            }
          }
          if (!live) return;
          const landed = slice;
          setSlices((prev) => (prev.some((p) => p.key === landed.key) ? prev : [...prev, landed]));
        }
      } catch (err) {
        if (live) setError((err as Error).message);
      }
    })();
    return () => {
      live = false;
    };
  }, []);

  const laps = useMemo(() => slices.flatMap((sl) => sl.rows), [slices]);
  const measure = MEASURES[measureKey];

  const headline = useMemo(() => {
    if (laps.length === 0) return null;
    const balances = laps.map((r) => r.balanceMj).sort((a, b) => a - b);
    const uncertainties = laps.map((r) => r.uncertaintyMj).sort((a, b) => a - b);
    return {
      medianBalance: quantile(balances, 0.5),
      medianUncertainty: quantile(uncertainties, 0.5),
      floorShare: laps.filter((r) => r.atFloor).length / laps.length,
      cappedShare: laps.filter((r) => r.capped).length / laps.length,
      warnedShare: laps.filter((r) => r.warned).length / laps.length,
    };
  }, [laps]);

  const ranked = useMemo<RankRow[]>(() => {
    const buckets = new Map<
      string,
      {
        label: string;
        team: string | null;
        teams: Set<string>;
        values: number[];
        n: number;
        capped: number;
      }
    >();
    const pick = measure.value;
    for (const r of laps) {
      let key: string;
      let label: string;
      if (breakdown === "circuit") {
        key = r.trackSlug;
        label = shortEvent(r.event);
      } else if (breakdown === "team") {
        key = r.team ?? "__unattributed__";
        label = r.team ?? "Unattributed";
      } else {
        key = r.driver;
        label = r.driver;
      }
      let b = buckets.get(key);
      if (!b) {
        b = { label, team: r.team, teams: new Set(), values: [], n: 0, capped: 0 };
        buckets.set(key, b);
      }
      b.n++;
      if (r.capped) b.capped++;
      if (r.team) b.teams.add(r.team);
      if (pick) b.values.push(pick(r));
    }

    const out: RankRow[] = [];
    for (const [key, b] of buckets) {
      const teamLabel = b.teams.size > 0 ? [...b.teams].sort().join(" / ") : "—";
      if (pick) {
        const sorted = b.values.sort((x, y) => x - y);
        out.push({
          key,
          label: b.label,
          team: b.team,
          teamLabel,
          n: b.n,
          cappedLaps: b.capped,
          value: quantile(sorted, 0.5),
          lo: quantile(sorted, 0.25),
          hi: quantile(sorted, 0.75),
        });
      } else {
        out.push({
          key,
          label: b.label,
          team: b.team,
          teamLabel,
          n: b.n,
          cappedLaps: b.capped,
          value: b.n > 0 ? b.capped / b.n : NaN,
          lo: NaN,
          hi: NaN,
        });
      }
    }
    // Largest at the top, on every measure -- one sort rule the reader learns once.
    out.sort((a, b) => b.value - a.value || a.label.localeCompare(b.label));
    return out;
  }, [laps, breakdown, measure]);

  const domain = useMemo<readonly [number, number]>(() => {
    // A share is bounded by definition, and stretching the axis to the observed range would
    // turn "97% against 100%" into a dramatic-looking ranking. Drawn against the full 0-100%.
    if (!measure.value) return [0, 1];
    const vals: number[] = [0];
    for (const r of ranked) {
      for (const v of [r.value, r.lo, r.hi]) if (Number.isFinite(v)) vals.push(v);
    }
    const { niceMin, niceMax } = niceTicks(Math.min(...vals), Math.max(...vals), 5);
    return [niceMin, niceMax];
  }, [ranked, measure]);

  const loaded = slices.length + failed.length;
  const complete = total !== null && loaded >= total;
  const circuits = new Set(slices.map((sl) => sl.ref.trackSlug)).size;
  const excludedNonRacing = slices.reduce((a, sl) => a + sl.excludedNonRacing, 0);
  const excludedNoEnergy = slices.reduce((a, sl) => a + sl.excludedNoEnergy, 0);
  const seenLaps = laps.length + excludedNonRacing + excludedNoEnergy;

  // Team names are the longest labels; a 3-letter code needs almost no gutter. Keeping the
  // margin tight on the driver view is what leaves bars readable at 400 px.
  const leftMargin = breakdown === "driver" ? 46 : breakdown === "team" ? 100 : 92;
  const showTeamColumn = breakdown === "driver";

  const colourFor = (i: number) => {
    const row = ranked[i];
    if (!row) return CHART.series[0];
    // Colour follows identity only when the rows ARE identities; otherwise a ranking is a
    // magnitude and gets one hue. Balance is the exception: its sign changes what a bar means,
    // which is what the diverging pair (with its neutral midpoint) exists for.
    if (breakdown === "team" && cat) return teamColour(cat.teams, row.team);
    // Cap hits are our own calibration fault, not a performance measure, so they wear the
    // reserved warning colour -- always beside the words that say whose fault it is.
    if (measureKey === "capped") return CHART.status.warning;
    if (measureKey === "balance") {
      return row.value >= 0 ? CHART.diverging.positive : CHART.diverging.negative;
    }
    return CHART.series[0];
  };

  return (
    <main className={s.main}>
      <header className={s.head}>
        <p className={s.kicker}>Cross-circuit · 2026 · estimated</p>
        <h1 className={s.title}>
          Energy <em>League</em>
        </h1>
        <p className={s.lede}>
          Who spends and recovers energy where, across every session we have built — the one
          question the per-session views cannot answer. <strong>Read the balance first.</strong> It
          is several MJ short on almost every circuit, and about three quarters of racing laps end
          pinned at the store floor. That is our energy twin failing to account for a lap, not a
          field of cars running empty: no battery state is published anywhere in the 2026 feed, so
          every figure below is simulated and carries the twin&rsquo;s own error with it.
        </p>
      </header>

      {error ? (
        <p className={s.fail}>
          Could not read the session index — {error}. Nothing below is shown.
        </p>
      ) : null}

      {!complete ? (
        <div className={s.loadRow}>
          <div
            className={s.track}
            role="progressbar"
            aria-label="Loading session manifests"
            aria-valuenow={loaded}
            aria-valuemin={0}
            aria-valuemax={total ?? 0}
          >
            <div
              className={s.fill}
              style={{ width: total ? `${(loaded / total) * 100}%` : "0%" }}
            />
          </div>
          <p className={s.loadText} aria-live="polite">
            {total === null
              ? "Reading index…"
              : `Loading session manifests — ${loaded} of ${total} · ${laps.length.toLocaleString()} driver-laps so far`}
          </p>
        </div>
      ) : null}

      {failed.length > 0 ? (
        <p className={s.fail}>
          {failed.length} session{failed.length > 1 ? "s" : ""} could not be read and{" "}
          {failed.length > 1 ? "are" : "is"} absent from every figure below: {failed.join("; ")}.
        </p>
      ) : null}

      {headline ? (
        <div className={s.tiles}>
          <div className={s.tile}>
            <span className={s.tileLabel}>Median balance</span>
            <span className={s.tileValue} data-tone="warn">
              {headline.medianBalance > 0 ? "+" : ""}
              {headline.medianBalance.toFixed(2)}
              <span className={s.tileUnit}>MJ / lap</span>
            </span>
            <span className={s.tileNote}>
              Over a lap it should sit near zero. It does not, and the gap is the twin&rsquo;s, not
              the car&rsquo;s.
            </span>
          </div>
          <div className={s.tile}>
            <span className={s.tileLabel}>Laps ending at the store floor</span>
            <span className={s.tileValue} data-tone="warn">
              {(headline.floorShare * 100).toFixed(1)}
              <span className={s.tileUnit}>%</span>
            </span>
            <span className={s.tileNote}>
              The estimated store reaches its floor and stays there — what an unclosed balance looks
              like from inside the model.
            </span>
          </div>
          <div className={s.tile}>
            <span className={s.tileLabel}>Twin&rsquo;s own uncertainty</span>
            <span className={s.tileValue}>
              ±{headline.medianUncertainty.toFixed(2)}
              <span className={s.tileUnit}>MJ / lap</span>
            </span>
            <span className={s.tileNote}>
              Median stated uncertainty on the end-of-lap store, comparable in size to the balance
              itself. No ranking below should be read to two decimal places.
            </span>
          </div>
          <div className={s.tile}>
            <span className={s.tileLabel}>Laps hitting our power cap</span>
            <span className={s.tileValue} data-tone="warn">
              {(headline.cappedShare * 100).toFixed(1)}
              <span className={s.tileUnit}>%</span>
            </span>
            <span className={s.tileNote}>
              Our model clipping its own request against an unverified 2026 envelope. A calibration
              fault of ours; never a car exceeding a limit.
            </span>
          </div>
        </div>
      ) : null}

      <section className={s.block}>
        <div className={s.controls}>
          <div className={s.control}>
            <span className={s.controlLabel}>Measure</span>
            <Segmented
              label="Energy measure"
              options={MEASURE_OPTIONS}
              value={measureKey}
              onChange={setMeasureKey}
            />
          </div>
          <div className={s.control}>
            <span className={s.controlLabel}>Broken down by</span>
            <Segmented
              label="Breakdown"
              options={BREAKDOWN_OPTIONS}
              value={breakdown}
              onChange={setBreakdown}
            />
          </div>
        </div>

        <div className={s.panel}>
          {ranked.length === 0 ? (
            <NoData
              title={measure.title}
              reason={
                error
                  ? "the session index could not be read"
                  : "no session manifest has landed yet — the figure draws itself as they arrive"
              }
            />
          ) : (
            <ChartFrame
              title={measure.title}
              units={measure.units}
              provenance="SIMULATED"
              note={
                <>
                  {measure.note}{" "}
                  {measure.value ? (
                    <>
                      <strong>Median, not mean</strong> — safety-car and recovery laps give these
                      distributions a long tail that a mean would follow and no lap would match. The
                      whisker is the interquartile range of the laps in each row.{" "}
                    </>
                  ) : null}
                  Non-racing laps (deleted, flagged inaccurate, or carrying a pit in/out) are
                  excluded: {excludedNonRacing.toLocaleString()} of {seenLaps.toLocaleString()} so
                  far.
                  {excludedNoEnergy > 0
                    ? ` A further ${excludedNoEnergy.toLocaleString()} racing laps carry no twin output at all and are absent rather than zero-filled.`
                    : null}
                </>
              }
              table={{
                columns: measure.value
                  ? [
                      BREAKDOWN_LABEL[breakdown],
                      "median",
                      "p25",
                      "p75",
                      "driver-laps",
                      ...(showTeamColumn ? ["team"] : []),
                    ]
                  : [
                      BREAKDOWN_LABEL[breakdown],
                      "share capped",
                      "capped laps",
                      "driver-laps",
                      ...(showTeamColumn ? ["team"] : []),
                    ],
                rows: ranked.map((r) =>
                  measure.value
                    ? [
                        r.label,
                        measure.format(r.value),
                        measure.format(r.lo),
                        measure.format(r.hi),
                        r.n.toLocaleString(),
                        ...(showTeamColumn ? [r.teamLabel] : []),
                      ]
                    : [
                        r.label,
                        measure.format(r.value),
                        r.cappedLaps.toLocaleString(),
                        r.n.toLocaleString(),
                        ...(showTeamColumn ? [r.teamLabel] : []),
                      ],
                ),
              }}
            >
              <Plot
                xDomain={domain}
                yDomain={[0, ranked.length]}
                height={Math.max(200, ranked.length * 24 + 58)}
                margin={{ left: leftMargin, right: 18, top: 8, bottom: 38 }}
                ariaLabel={`${measure.title}, ${measure.units}, ranked across ${ranked.length} rows from ${laps.length} racing driver-laps`}
              >
                <Grid x y={false} />
                <XAxis
                  label={measure.units}
                  count={5}
                  format={(v) =>
                    measure.value ? v.toFixed(Number.isInteger(v) ? 0 : 1) : `${Math.round(v * 100)}%`
                  }
                />
                {measureKey === "balance" ? <RefLine x={0} label="balance closes" /> : null}
                <BandLabels labels={ranked.map((r) => r.label)} />
                <HBars values={ranked.map((r) => r.value)} colour={colourFor} baseline={0} />
                {measure.value ? (
                  <ErrorBarsH
                    values={ranked.map((r) => r.value)}
                    lo={ranked.map((r) => r.lo)}
                    hi={ranked.map((r) => r.hi)}
                    colour={CHART.status.muted}
                    r={2.4}
                  />
                ) : null}
              </Plot>
            </ChartFrame>
          )}
        </div>

        {total !== null ? (
          <p className={s.coverage}>
            Coverage: <b>{slices.length}</b> of <b>{total}</b> built sessions across{" "}
            <b>{circuits}</b> circuit{circuits === 1 ? "" : "s"} ·{" "}
            <b>{laps.length.toLocaleString()}</b> racing driver-laps · <b>{ranked.length}</b> row
            {ranked.length === 1 ? "" : "s"} in the figure
            {complete ? "." : ", and still loading — every number here moves as sessions land."}
          </p>
        ) : null}
      </section>

      <section className={s.footnotes}>
        <h2>What this is, and is not</h2>
        <p>
          Every figure on this page is <b>SIMULATED</b>. The 2026 feed publishes no battery state,
          no MGU-K power and no fuel mass; deploy, harvest and balance are outputs of our energy
          twin run over the speed and throttle traces, and the &ldquo;store&rdquo; is an estimated
          accumulator inside that model, not a measured state of charge.
        </p>
        <p>
          The cap-hit measure is the one figure here that grades us rather than the cars, and it
          grades us badly: our request saturates the envelope on nearly every lap, so ranking
          circuits by it separates almost nothing.
          {headline && headline.warnedShare > 0 ? (
            <>
              {" "}
              <span className={s.warn}>{(headline.warnedShare * 100).toFixed(1)}%</span> of laps also
              carry an explicit warning from the twin — mostly a deploy-budget overrun measured
              against a RULE value the regulation snapshot itself marks unverified.
            </>
          ) : null}
        </p>
        <p>
          Rows are the drivers each session manifest actually records, not the wider entry list. One
          driver ran for two teams across the season, so his laps are split between them in the team
          breakdown rather than being assigned to one — the driver table names both.
        </p>
      </section>
    </main>
  );
}
