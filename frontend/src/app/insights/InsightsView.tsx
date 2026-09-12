/**
 * Cross-event league tables, drawn entirely from params.<hash>.json.
 *
 * Every leaf in that artifact already carries `se`, `ci95`, `n` and a `note` explaining what
 * was fitted -- and until now none of it was displayed anywhere. These are estimates with
 * spread, so they are drawn as point-plus-interval rather than as bare bars: a circuit whose
 * interval crosses zero has no measurable effect, and a plain bar chart would hide that.
 *
 * The dirty-air parameter doubles as the per-track overtaking-difficulty index, which makes it
 * the most on-brief number in the artifact for a problem statement about overtaking.
 */
"use client";

import { useEffect, useMemo, useState } from "react";
import { CHART } from "@/lib/palette";
import { defaultSimSource, type SimIndex } from "@/sim/data/source";
import type { Catalogue } from "@/sim/data/catalogue";
import { FilterBar, useFilters, type FilterState } from "@/components/filters";
import type { FittedParams, Leaf } from "@/sim/engine/params";
import {
  ChartFrame,
  Plot,
  Grid,
  XAxis,
  ErrorBarsH,
  BandLabels,
  RefLine,
  niceTicks,
} from "@/sim/charts";
import s from "./insights.module.css";

type MetricKey = "dirtyAir" | "paceTrend";

interface Metric {
  key: MetricKey;
  label: string;
  units: string;
  blurb: string;
  /** Lower is "better" for some metrics; this only affects sort direction, never colour. */
  descending: boolean;
  pick: (p: FittedParams) => Record<string, Leaf>;
}

const METRICS: Metric[] = [
  {
    key: "dirtyAir",
    label: "Overtaking difficulty",
    units: "s lost per s of proximity",
    blurb:
      "Time a car loses per second of gap while running within 3 s of the car ahead, measured at the start of the lap. The higher the value, the harder this circuit is to follow at — and to pass at. Fitted per circuit; this is the same quantity the race engine uses as its dirty-air term.",
    descending: true,
    pick: (p) => p.dirtyAirLossPerSecondOfProximity,
  },
  {
    key: "paceTrend",
    label: "Session pace trend",
    units: "s per lap",
    blurb:
      "How much quicker each successive lap gets — fuel burn and track evolution combined. The artifact is explicit that the two are collinear in lap number and cannot be separated from lap data alone, so this is their sum, not a fuel-burn coefficient.",
    descending: false,
    pick: (p) => p.sessionPaceTrendPerLap.perTrack,
  },
];

export default function InsightsView({ initialFilters }: { initialFilters: FilterState }) {
  const [params, setParams] = useState<FittedParams | null>(null);
  const [cat, setCat] = useState<Catalogue | null>(null);
  const [index, setIndex] = useState<SimIndex | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [metricKey, setMetricKey] = useState<MetricKey>("dirtyAir");
  const [filters, setFilters] = useState<FilterState>(initialFilters);
  const { set, reset } = useFilters(initialFilters, filters, setFilters);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    let live = true;
    Promise.all([
      defaultSimSource.params(),
      defaultSimSource.catalogue<Catalogue>(),
      defaultSimSource.index(),
    ])
      .then(([p, c, i]) => {
        if (!live) return;
        setParams(p);
        setCat(c);
        setIndex(i);
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, []);

  const metric = METRICS.find((m) => m.key === metricKey)!;

  // params.json is keyed by full event name ("British Grand Prix"); the circuit filter is keyed
  // by slug. The catalogue is the only thing that knows both, so the mapping goes through it
  // rather than through a slugify() that would silently mismatch on an unexpected name.
  const slugByEvent = useMemo(() => {
    const m = new Map<string, string>();
    for (const t of cat?.tracks ?? []) m.set(t.event, t.slug);
    return m;
  }, [cat]);

  const trackRows = useMemo(() => {
    if (!params) return [];
    const src = metric.pick(params);
    const want = filters.circuits.length > 0 ? new Set(filters.circuits) : null;
    return Object.entries(src)
      .filter(([event]) => !want || want.has(slugByEvent.get(event) ?? ""))
      .map(([event, leaf]) => ({ label: shortEvent(event), event, leaf }))
      .filter((r) => Number.isFinite(r.leaf?.value))
      .filter((r) => !hidden.has(r.label))
      .sort((a, b) => (metric.descending ? b.leaf.value - a.leaf.value : a.leaf.value - b.leaf.value));
  }, [params, metric, filters.circuits, slugByEvent, hidden]);

  // A team selection narrows the driver list even when no driver is explicitly picked, so
  // "show me Ferrari" works without having to know who drives for them.
  const codesForTeams = useMemo(() => {
    if (!cat || filters.teams.length === 0) return null;
    const want = new Set(filters.teams);
    const codes = new Set<string>();
    for (const t of cat.tracks) for (const e of t.entries) if (want.has(e.team)) codes.add(e.code);
    return codes;
  }, [cat, filters.teams]);

  const driverRows = useMemo(() => {
    if (!params) return [];
    const picked = filters.drivers.length > 0 ? new Set(filters.drivers) : null;
    return Object.entries(params.driverOffsetSeconds)
      .filter(([code]) => !picked || picked.has(code))
      .filter(([code]) => !codesForTeams || codesForTeams.has(code))
      .map(([code, leaf]) => ({ label: code, leaf }))
      .filter((r) => Number.isFinite(r.leaf?.value))
      .sort((a, b) => a.leaf.value - b.leaf.value);
  }, [params, filters.drivers, codesForTeams]);

  if (error) {
    return (
      <main className={s.main}>
        <p className={s.lede}>Could not load fitted parameters: {error}</p>
      </main>
    );
  }
  if (!params || !cat || !index) {
    return (
      <main className={s.main}>
        <p className={s.lede}>Loading fitted parameters…</p>
      </main>
    );
  }

  return (
    <main className={s.main}>
      <header className={s.head}>
        <p className={s.kicker}>
          Cross-event · {trackRows.length} {trackRows.length === 1 ? "circuit" : "circuits"}
        </p>
        <h1 className={s.title}>
          Fitted <em>Parameters</em>
        </h1>
        <p className={s.lede}>
          Everything the race model believes about a circuit, fitted from the 2026 lap record and
          shown with the spread it was fitted to. Each point is an estimate; each bar is its 95%
          interval. Where an interval crosses zero, the circuit has no measurable effect — and the
          chart says so rather than drawing a confident-looking bar.
        </p>
      </header>

      <FilterBar
        catalogue={cat}
        index={index}
        state={filters}
        onChange={set}
        onReset={() => {
          reset();
          setHidden(new Set());
        }}
        show={["years", "circuits", "teams", "drivers"]}
      />

      <section className={s.block}>
        <div className={s.segmented} role="tablist" aria-label="Circuit metric">
          {METRICS.map((m) => (
            <button
              key={m.key}
              type="button"
              role="tab"
              aria-selected={m.key === metricKey}
              className={s.segment}
              onClick={() => setMetricKey(m.key)}
            >
              {m.label}
            </button>
          ))}
        </div>

        <ForestChart
          title={metric.label}
          units={metric.units}
          note={metric.blurb}
          rows={trackRows}
          height={Math.max(220, trackRows.length * 26 + 60)}
        />
      </section>

      <section className={s.block}>
        <ForestChart
          title="Driver pace offset"
          units="seconds per lap, relative to the field"
          note={
            <>
              Fitted with driver dummies against the full 2026 lap record; negative is quicker. The
              team offset is not added on top — the driver fit already absorbs it, and the team
              value exists only as a fallback for an entry the fit never saw.
              {params.modelFitR2 ? (
                <>
                  {" "}
                  Model fit R² = {params.modelFitR2.driver.toFixed(4)} over{" "}
                  {params.modelFitR2.n_driver.toLocaleString()} laps.
                </>
              ) : null}
            </>
          }
          rows={driverRows}
          height={Math.max(260, driverRows.length * 20 + 60)}
          compact
        />
      </section>

    </main>
  );
}

/* ------------------------------------------------------------------------ */

function ForestChart({
  title,
  units,
  note,
  rows,
  height,
  compact = false,
}: {
  title: string;
  units: string;
  note: React.ReactNode;
  rows: { label: string; leaf: Leaf }[];
  height: number;
  compact?: boolean;
}) {
  if (rows.length === 0) {
    return <ChartFrame title={title}>{null}</ChartFrame>;
  }

  const values = rows.map((r) => r.leaf.value);
  const lo = rows.map((r) => r.leaf.ci95?.[0] ?? NaN);
  const hi = rows.map((r) => r.leaf.ci95?.[1] ?? NaN);
  const all = [...values, ...lo, ...hi].filter(Number.isFinite);
  const { niceMin, niceMax } = niceTicks(Math.min(...all, 0), Math.max(...all, 0), 5);

  // An interval that spans zero means "not distinguishable from no effect". That is a
  // statement about evidence, not about the circuit, so it is shown in the muted status
  // colour rather than in a series hue.
  const crossesZero = (i: number) => Number.isFinite(lo[i]) && Number.isFinite(hi[i]) && lo[i] <= 0 && hi[i] >= 0;
  const colourFor = (i: number) => (crossesZero(i) ? CHART.status.muted : CHART.series[0]);

  const nCrossing = rows.filter((_, i) => crossesZero(i)).length;

  return (
    <ChartFrame
      title={title}
      units={units}
      provenance="DERIVED"
      note={
        <>
          {note}
          {nCrossing > 0 ? (
            <>
              {" "}
              <strong className={s.inlineWarn}>
                {nCrossing} of {rows.length} intervals cross zero
              </strong>{" "}
              and are drawn muted — for those the data does not distinguish the effect from none.
            </>
          ) : null}
        </>
      }
      table={{
        columns: ["", "value", "se", "ci95 low", "ci95 high", "n"],
        rows: rows.map((r) => [
          r.label,
          r.leaf.value.toFixed(4),
          r.leaf.se?.toFixed(4) ?? null,
          r.leaf.ci95?.[0]?.toFixed(4) ?? null,
          r.leaf.ci95?.[1]?.toFixed(4) ?? null,
          r.leaf.n ?? null,
        ]),
      }}
    >
      <Plot
        xDomain={[niceMin, niceMax]}
        yDomain={[0, rows.length]}
        height={height}
        width={compact ? 620 : 720}
        margin={{ left: compact ? 54 : 140, right: 22, top: 10, bottom: 36 }}
        ariaLabel={`${title}: estimate and 95% confidence interval for each of ${rows.length} entries`}
      >
        <Grid x y={false} />
        <RefLine x={0} label="no effect" />
        <XAxis label={units} count={5} format={(v) => (Math.abs(v) < 1 ? v.toFixed(2) : v.toFixed(1))} />
        <BandLabels labels={rows.map((r) => r.label)} />
        <ErrorBarsH values={values} lo={lo} hi={hi} colour={colourFor} r={compact ? 2.8 : 3.5} />
      </Plot>
    </ChartFrame>
  );
}

/** "British Grand Prix" -> "British". The suffix is on every row and carries no information. */
function shortEvent(event: string): string {
  return event.replace(/\s+Grand Prix$/i, "");
}
