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
 *
 * READING SPEED IS THE FEATURE. A race engineer does not read a forest plot row by row; they
 * need the answer off one look and the evidence underneath it when they want it. So the page
 * is layered:
 *
 *   1. a headline row -- the extreme, the spread, and how much of the metric is real;
 *   2. the ranked chart, banded and with the numbers in a right-hand gutter;
 *   3. the table view, unchanged, for the full leaf.
 *
 * Nothing here invents a number. Every tile is an aggregate of leaves already on screen.
 */
"use client";

import { useEffect, useMemo, useState } from "react";
import { CHART } from "@/lib/palette";
import { defaultSimSource, type SimIndex } from "@/sim/data/source";
import { teamColour, type Catalogue } from "@/sim/data/catalogue";
import { FilterBar, useFilters, type FilterState } from "@/components/filters";
import type { FittedParams, Leaf } from "@/sim/engine/params";
import {
  ChartFrame,
  Plot,
  Grid,
  XAxis,
  ErrorBarsH,
  BandLabels,
  RowBands,
  RowValues,
  RowHover,
  RefLine,
  niceTicks,
  type LegendEntry,
} from "@/sim/charts";
import s from "./insights.module.css";

type MetricKey = "dirtyAir" | "tyreDeg" | "paceTrend" | "pitLoss";

/** A leaf flattened into what a row needs, so the four metrics can share one chart. */
interface Row {
  /** Short display name ("Monaco"). */
  label: string;
  /** The artifact's own key, for the tooltip and the table. */
  key: string;
  value: number;
  lo: number;
  hi: number;
  n: number | null;
  note?: string;
  /** Entity colour, where the entity has one. Circuits do not; drivers wear their team. */
  colour?: string;
  /** Secondary identity, shown in the tooltip. */
  sub?: string;
}

interface Metric {
  key: MetricKey;
  label: string;
  units: string;
  /** Short axis caption. The full `units` string is too long to sit under an axis. */
  axis: string;
  blurb: string;
  /** Highest value first. */
  descending: boolean;
  /**
   * What the interval on each row actually is. A 95% interval that spans zero means "no
   * measurable effect"; an inter-quartile range is a spread of observed stops and says nothing
   * of the kind, so the significance treatment is only applied to the former.
   */
  interval: "ci95" | "iqr";
  /** Plain-language name for the top of the ranking -- the tile has to say what "highest" means. */
  topMeans: string;
  /** ...and the bottom. */
  bottomMeans: string;
  /**
   * Caption for the zero rule, or null to leave it off.
   *
   * A pit stop always costs time, so zero sits hard against the left edge of that domain and a
   * rule there marks nothing a reader was asking about. The other three metrics are effects
   * that genuinely could be zero, and there the rule is the whole point.
   */
  zeroLabel: string | null;
  format: (v: number) => string;
  rows: (p: FittedParams) => Row[];
}

/** Circuit leaves keyed by full event name, flattened. */
function fromLeaves(src: Record<string, Leaf>): Row[] {
  return Object.entries(src)
    .filter(([, leaf]) => Number.isFinite(leaf?.value))
    .map(([event, leaf]) => ({
      label: shortEvent(event),
      key: event,
      value: leaf.value,
      lo: leaf.ci95?.[0] ?? NaN,
      hi: leaf.ci95?.[1] ?? NaN,
      n: leaf.n ?? null,
      note: leaf.note,
    }));
}

const METRICS: Metric[] = [
  {
    key: "dirtyAir",
    label: "Overtaking difficulty",
    units: "seconds lost per second of proximity",
    axis: "s lost per s of proximity",
    blurb:
      "Time a car loses per second of gap while running within 3 s of the car ahead, measured at the start of the lap. The higher the value, the harder this circuit is to follow at — and to pass at. Fitted per circuit; this is the same quantity the race engine uses as its dirty-air term.",
    descending: true,
    interval: "ci95",
    topMeans: "hardest to follow",
    bottomMeans: "easiest to follow",
    zeroLabel: "no measurable effect",
    format: (v) => v.toFixed(3),
    rows: (p) => fromLeaves(p.dirtyAirLossPerSecondOfProximity),
  },
  {
    key: "tyreDeg",
    label: "Tyre degradation",
    units: "seconds per lap, per lap of tyre life",
    axis: "s/lap per lap of life",
    blurb:
      "Mean of the per-compound degradation slopes fitted at this circuit — how much slower each successive lap on a set gets. n is the number of compounds that carried a usable slope, not the number of laps, so the intervals here are wide by construction.",
    descending: true,
    interval: "ci95",
    topMeans: "harshest on tyres",
    bottomMeans: "kindest on tyres",
    zeroLabel: "no measurable effect",
    format: (v) => v.toFixed(3),
    rows: (p) => fromLeaves(p.tyreDegradation.trackIndex),
  },
  {
    key: "paceTrend",
    label: "Session pace trend",
    units: "seconds per lap",
    axis: "s per lap",
    blurb:
      "How much quicker each successive lap gets — fuel burn and track evolution combined. The artifact is explicit that the two are collinear in lap number and cannot be separated from lap data alone, so this is their sum, not a fuel-burn coefficient.",
    descending: false,
    interval: "ci95",
    topMeans: "most lap-on-lap gain",
    bottomMeans: "least lap-on-lap gain",
    zeroLabel: "no gain",
    format: (v) => v.toFixed(4),
    rows: (p) => fromLeaves(p.sessionPaceTrendPerLap.perTrack),
  },
  {
    key: "pitLoss",
    label: "Pit-stop loss",
    units: "seconds lost, green-flag stops",
    axis: "seconds",
    blurb:
      "Net time a green-flag stop costs: in-lap plus out-lap, minus twice the driver's own clean median. The interval is the INTER-QUARTILE RANGE of the observed stops, not a confidence interval — it describes how much stops varied, so it is never read as significance.",
    descending: true,
    interval: "iqr",
    topMeans: "costliest stop",
    bottomMeans: "cheapest stop",
    zeroLabel: null,
    format: (v) => v.toFixed(1),
    rows: (p) =>
      Object.entries(p.pitLoss)
        .filter(([, v]) => Number.isFinite(v?.netLossSeconds?.value))
        .map(([event, v]) => ({
          label: shortEvent(event),
          key: event,
          value: v.netLossSeconds.value,
          lo: v.iqr?.[0] ?? NaN,
          hi: v.iqr?.[1] ?? NaN,
          n: v.netLossSeconds.n ?? null,
          note: v.netLossSeconds.note,
        })),
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
  /** Teams switched off from the driver chart's legend. */
  const [hiddenTeams, setHiddenTeams] = useState<ReadonlySet<string>>(new Set());

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

  const circuitMetricRows = useMemo(
    () =>
      METRICS.map((candidate) => {
        if (!params) return { metric: candidate, rows: [] as Row[] };
        const want = filters.circuits.length > 0 ? new Set(filters.circuits) : null;
        const rows = candidate
          .rows(params)
          .filter((r) => !want || want.has(slugByEvent.get(r.key) ?? ""))
          .sort((a, b) => (candidate.descending ? b.value - a.value : a.value - b.value));
        return { metric: candidate, rows };
      }),
    [params, filters.circuits, slugByEvent],
  );

  const trackRows = useMemo(() => {
    return circuitMetricRows.find(({ metric: candidate }) => candidate.key === metricKey)?.rows ?? [];
  }, [circuitMetricRows, metricKey]);

  /**
   * Driver code -> team, from the catalogue.
   *
   * A driver who changed team mid-season appears once per team, so the entry with the most
   * sessions wins: the offset was fitted over the whole record and belongs to whichever seat
   * the driver actually spent the season in. Picking the first entry would colour a one-race
   * stand-in by the team they left.
   */
  const teamByCode = useMemo(() => {
    const best = new Map<string, { team: string; sessions: number }>();
    for (const d of cat?.drivers ?? []) {
      const prev = best.get(d.code);
      if (!prev || d.sessions > prev.sessions) best.set(d.code, { team: d.team, sessions: d.sessions });
    }
    return new Map([...best].map(([code, v]) => [code, v.team]));
  }, [cat]);

  // A team selection narrows the driver list even when no driver is explicitly picked, so
  // "show me Ferrari" works without having to know who drives for them.
  const codesForTeams = useMemo(() => {
    if (!cat || filters.teams.length === 0) return null;
    const want = new Set(filters.teams);
    const codes = new Set<string>();
    for (const t of cat.tracks) for (const e of t.entries) if (want.has(e.team)) codes.add(e.code);
    return codes;
  }, [cat, filters.teams]);

  const driverRows = useMemo((): Row[] => {
    if (!params || !cat) return [];
    const picked = filters.drivers.length > 0 ? new Set(filters.drivers) : null;
    return Object.entries(params.driverOffsetSeconds)
      .filter(([code]) => !picked || picked.has(code))
      .filter(([code]) => !codesForTeams || codesForTeams.has(code))
      .filter(([, leaf]) => Number.isFinite(leaf?.value))
      .map(([code, leaf]) => {
        const team = teamByCode.get(code) ?? null;
        return {
          label: code,
          key: code,
          value: leaf.value,
          lo: leaf.ci95?.[0] ?? NaN,
          hi: leaf.ci95?.[1] ?? NaN,
          n: leaf.n ?? null,
          colour: teamColour(cat.teams, team),
          sub: team ?? undefined,
        };
      })
      .filter((r) => !r.sub || !hiddenTeams.has(r.sub))
      .sort((a, b) => a.value - b.value);
  }, [params, cat, filters.drivers, codesForTeams, teamByCode, hiddenTeams]);

  /** One legend entry per team actually on the chart, in the order the rows run. */
  const teamLegend = useMemo((): LegendEntry[] => {
    if (!params || !cat) return [];
    const seen = new Set<string>();
    const out: LegendEntry[] = [];
    for (const code of Object.keys(params.driverOffsetSeconds)) {
      const team = teamByCode.get(code);
      if (!team || seen.has(team)) continue;
      seen.add(team);
      out.push({ label: team, colour: teamColour(cat.teams, team), shape: "dot" });
    }
    return out.sort((a, b) => a.label.localeCompare(b.label));
  }, [params, cat, teamByCode]);

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
          Cross-event · {trackRows.length} {trackRows.length === 1 ? "circuit" : "circuits"} ·{" "}
          {driverRows.length} drivers
        </p>
        <h1 className={s.title}>
          Fitted <em>Parameters</em>
        </h1>
        <p className={s.lede}>
          Everything the race model believes about a circuit, fitted from the 2026 lap record and
          shown with the spread it was fitted to. Each point is an estimate; each bar is its
          interval. Where a 95% interval crosses zero the circuit has no measurable effect — those
          rows are greyed and tagged <span className={s.ns}>ns</span> rather than drawn as a
          confident-looking bar.
        </p>
      </header>

      <FilterBar
        catalogue={cat}
        index={index}
        state={filters}
        onChange={set}
          onReset={() => {
            reset();
            setHiddenTeams(new Set());
          }}
        show={["years", "circuits", "teams", "drivers"]}
      />

      <section className={s.block}>
        <MetricNavigator
          metrics={circuitMetricRows}
          active={metricKey}
          onSelect={setMetricKey}
        />

        <Headline metric={metric} rows={trackRows} />

        <RankedEffects
          title={metric.label}
          units={metric.units}
          axis={metric.axis}
          note={metric.blurb}
          zeroLabel={metric.zeroLabel}
          rows={trackRows}
          format={metric.format}
          significance={metric.interval === "ci95"}
          intervalName={metric.interval === "ci95" ? "95% CI" : "IQR"}
          rowHeight={26}
          labelGutter={104}
          emphasise={(i) => i === 0}
        />
      </section>

      <section className={s.block}>
        <DriverHeadline rows={driverRows} />

        <RankedEffects
          title="Driver pace offset"
          units="seconds per lap, relative to the field"
          axis="s per lap — negative is quicker"
          note={
            <>
              Fitted with driver dummies against the full 2026 lap record; negative is quicker.
              Colour is the driver&rsquo;s team, so the chart reads as a grid order rather than as
              twenty-three anonymous rows — the three-letter code carries the identity, the chip
              only reinforces it. The team offset is not added on top: the driver fit already
              absorbs it, and the team value exists only as a fallback for an entry the fit never
              saw.
              {params.modelFitR2 ? (
                <>
                  {" "}
                  Model fit R² = {params.modelFitR2.driver.toFixed(4)} over{" "}
                  {params.modelFitR2.n_driver.toLocaleString()} laps.
                </>
              ) : null}
            </>
          }
          zeroLabel="field reference"
          rows={driverRows}
          format={(v) => (v >= 0 ? `+${v.toFixed(3)}` : v.toFixed(3))}
          significance={false}
          intervalName="95% CI"
          /* 22, not 20: below a 22px pitch RowValues drops the sample size to stay off its
             own value, and n swings from 107 to 683 across this field — which is exactly the
             number that says how much to trust a driver's offset. */
          rowHeight={22}
          labelGutter={52}
          legend={teamLegend}
          hidden={hiddenTeams}
          onToggleSeries={(team) =>
            setHiddenTeams((prev) => {
              const next = new Set(prev);
              if (next.has(team)) next.delete(team);
              else next.add(team);
              return next;
            })
          }
          /* Both ends of the grid, and nothing between them: emphasis stops working if it is
             on every row. */
          emphasise={(i, n) => i === 0 || i === n - 1}
        />
      </section>
    </main>
  );
}

/* ------------------------------------------------------------------------ */

/**
 * Four concise readings before the detailed chart. Each control is a real tab, not a decorative
 * dashboard tile: selecting it expands that metric's ranked uncertainty chart immediately below.
 */
function MetricNavigator({
  metrics,
  active,
  onSelect,
}: {
  metrics: { metric: Metric; rows: Row[] }[];
  active: MetricKey;
  onSelect: (key: MetricKey) => void;
}) {
  return (
    <div className={s.metricNav} aria-label="Circuit metric">
      {metrics.map(({ metric, rows }) => {
        const lead = rows[0];
        const isActive = metric.key === active;
        const hasInterval = Number.isFinite(lead?.lo) && Number.isFinite(lead?.hi);
        const crossesZero =
          metric.interval === "ci95" && hasInterval && lead.lo <= 0 && lead.hi >= 0;
        const intervalLabel = metric.interval === "ci95" ? "95% CI" : "IQR";
        return (
          <button
            key={metric.key}
            type="button"
            className={s.metricCard}
            data-active={isActive ? "true" : undefined}
            aria-pressed={isActive}
            onClick={() => onSelect(metric.key)}
          >
            <span className={s.metricLabel}>{metric.label}</span>
            {lead ? (
              <>
                <span className={s.metricLead}>
                  {lead.label}
                </span>
                <span className={s.metricMeta}>
                  {metric.topMeans} Â·{" "}
                  {metric.format(lead.value)} {metric.axis}
                </span>
                {hasInterval && lead ? (
                  <span className={s.metricInterval} data-warn={crossesZero ? "true" : undefined}>
                    {intervalLabel} {metric.format(lead.lo)} â†’ {metric.format(lead.hi)}
                    {crossesZero ? " Â· ns" : ""}
                  </span>
                ) : null}
              </>
            ) : (
              <span className={s.metricMeta}>No circuit matches the current filter</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/**
 * The headline row: the answer before the chart.
 *
 * Four aggregates of the rows already on screen, so nothing here is a new claim. The outlier
 * tile is the one that earns its place -- Monaco's dirty-air term is 2.7x the next circuit, and
 * that ratio is invisible in a ranked chart where it is simply the top row.
 */
function Headline({ metric, rows }: { metric: Metric; rows: Row[] }) {
  if (rows.length === 0) return null;

  const top = rows[0];
  const bottom = rows[rows.length - 1];
  const values = rows.map((r) => r.value);
  const spread = Math.max(...values) - Math.min(...values);

  // "x times the next one" only means anything away from zero and with a row to compare to.
  const second = rows[1];
  const ratio =
    second && Math.abs(second.value) > 1e-9 ? Math.abs(top.value) / Math.abs(second.value) : null;

  const crossing =
    metric.interval === "ci95"
      ? rows.filter((r) => Number.isFinite(r.lo) && Number.isFinite(r.hi) && r.lo <= 0 && r.hi >= 0).length
      : null;

  return (
    <div className={s.tiles}>
      <div className={s.tile} data-lead="true">
        <span className={s.tileLabel}>{metric.topMeans}</span>
        <span className={s.tileValue}>{top.label}</span>
        <span className={s.tileMeta}>
          {metric.format(top.value)} {metric.axis}
          {ratio && ratio >= 1.5 ? (
            <>
              {" · "}
              <strong className={s.tileStrong}>{ratio.toFixed(1)}× the next</strong>
            </>
          ) : null}
        </span>
      </div>

      <div className={s.tile}>
        <span className={s.tileLabel}>{metric.bottomMeans}</span>
        <span className={s.tileValue}>{bottom.label}</span>
        <span className={s.tileMeta}>
          {metric.format(bottom.value)} {metric.axis}
        </span>
      </div>

      <div className={s.tile}>
        <span className={s.tileLabel}>spread across circuits</span>
        <span className={s.tileValue}>{metric.format(spread)}</span>
        <span className={s.tileMeta}>
          {metric.format(Math.min(...values))} → {metric.format(Math.max(...values))}
        </span>
      </div>

      <div className={s.tile}>
        <span className={s.tileLabel}>
          {crossing === null ? "circuits fitted" : "no measurable effect"}
        </span>
        <span className={s.tileValue} data-warn={crossing ? "true" : undefined}>
          {crossing === null ? rows.length : `${crossing} of ${rows.length}`}
        </span>
        <span className={s.tileMeta}>
          {crossing === null
            ? "interval is an IQR of observed stops, not a confidence interval"
            : crossing === 0
              ? "every interval clears zero"
              : "95% interval spans zero — tagged ns below"}
        </span>
      </div>
    </div>
  );
}

/**
 * The same at-a-glance layer for the grid-order chart.
 *
 * Driver offsets are deliberately kept separate from the circuit-metric headline above: they
 * have a different reference point (the field rather than zero effect) and their ordering is
 * inverted (more negative is quicker). Keeping those semantics in the copy prevents a reader
 * from having to infer the sign convention from the axis while they are trying to make a quick
 * comparison.
 */
function DriverHeadline({ rows }: { rows: Row[] }) {
  if (rows.length === 0) return null;

  // `driverRows` is ranked ascending: a negative offset is quicker than the field reference.
  const quickest = rows[0];
  const slowest = rows[rows.length - 1];
  const values = rows.map((r) => r.value);
  const spread = Math.max(...values) - Math.min(...values);
  const format = (value: number) => (value >= 0 ? `+${value.toFixed(3)}` : value.toFixed(3));

  return (
    <div className={s.tiles}>
      <div className={s.tile} data-lead="true">
        <span className={s.tileLabel}>quickest relative pace</span>
        <span className={s.tileValue}>{quickest.label}</span>
        <span className={s.tileMeta}>{format(quickest.value)} s/lap to field reference</span>
      </div>

      <div className={s.tile}>
        <span className={s.tileLabel}>largest positive offset</span>
        <span className={s.tileValue}>{slowest.label}</span>
        <span className={s.tileMeta}>{format(slowest.value)} s/lap to field reference</span>
      </div>

      <div className={s.tile}>
        <span className={s.tileLabel}>fitted grid spread</span>
        <span className={s.tileValue}>{spread.toFixed(3)} s/lap</span>
        <span className={s.tileMeta}>
          {format(quickest.value)} â†’ {format(slowest.value)} from quickest to largest positive offset
        </span>
      </div>

      <div className={s.tile}>
        <span className={s.tileLabel}>drivers fitted</span>
        <span className={s.tileValue}>{rows.length}</span>
        <span className={s.tileMeta}>each point and interval below is a fitted estimate, not a result</span>
      </div>
    </div>
  );
}

/**
 * A ranked point-and-interval chart, one row per entity.
 *
 * The form is unchanged from the forest plot this replaces -- the estimates still have spread
 * and are still drawn as such. What changed is everything around the marks: rows are banded so
 * a label tracks to its estimate across the full width, the estimate is repeated as a number in
 * the right gutter so nobody has to read it off the axis, a faint stem to zero restores the
 * length cue a bare dot loses, and "no measurable effect" is now a word next to the row instead
 * of a grey that the reader has to know the meaning of.
 */
function RankedEffects({
  title,
  units,
  axis,
  note,
  zeroLabel,
  rows,
  format,
  significance,
  intervalName,
  rowHeight,
  labelGutter,
  legend,
  hidden,
  onToggleSeries,
  emphasise,
}: {
  title: string;
  units: string;
  axis: string;
  note: React.ReactNode;
  zeroLabel: string | null;
  rows: Row[];
  format: (v: number) => string;
  /** Treat an interval spanning zero as "no measurable effect". Only true for a 95% CI. */
  significance: boolean;
  intervalName: string;
  rowHeight: number;
  /** Width reserved for the row labels, in plot units. */
  labelGutter: number;
  legend?: LegendEntry[];
  hidden?: ReadonlySet<string>;
  onToggleSeries?: (label: string) => void;
  emphasise?: (i: number, n: number) => boolean;
}) {
  if (rows.length === 0) {
    // The legend stays mounted. Switching every team off is a normal thing to do by accident,
    // and a bare empty state would strand the reader with no control to switch them back on.
    return (
      <ChartFrame
        title={title}
        units={units}
        legend={legend}
        hidden={hidden}
        onToggleSeries={onToggleSeries}
      >
        <p className={s.emptyRows}>
          {legend?.length
            ? "Every series is switched off. Use the legend above, or clear a filter, to bring rows back."
            : "No rows match the current filter. Clear a circuit, team or driver selection to bring them back."}
        </p>
      </ChartFrame>
    );
  }

  const values = rows.map((r) => r.value);
  const all = [...values, ...rows.map((r) => r.lo), ...rows.map((r) => r.hi)].filter(Number.isFinite);
  const { niceMin, niceMax } = niceTicks(Math.min(...all, 0), Math.max(...all, 0), 5);

  // An interval that spans zero means "not distinguishable from no effect". That is a
  // statement about evidence, not about the circuit, so it is shown in the muted status
  // colour -- and, since status never travels as colour alone, tagged "ns" in the gutter.
  const crossesZero = (i: number) =>
    significance &&
    Number.isFinite(rows[i].lo) &&
    Number.isFinite(rows[i].hi) &&
    rows[i].lo <= 0 &&
    rows[i].hi >= 0;

  const colourFor = (i: number) =>
    crossesZero(i) ? CHART.status.muted : (rows[i].colour ?? CHART.series[0]);

  const nCrossing = rows.filter((_, i) => crossesZero(i)).length;

  // The right gutter holds the value column; the left holds the row labels. Both are sized
  // here rather than by the caller so the plotting area is what is left over, never negative.
  const valueGutter = 76;

  return (
    <ChartFrame
      title={title}
      units={units}
      provenance="DERIVED"
      legend={legend}
      hidden={hidden}
      onToggleSeries={onToggleSeries}
      note={
        <>
          {note}
          {nCrossing > 0 ? (
            <>
              {" "}
              <strong className={s.inlineWarn}>
                {nCrossing} of {rows.length} intervals cross zero
              </strong>{" "}
              and are drawn muted and tagged <span className={s.ns}>ns</span> — for those the data
              does not distinguish the effect from none.
            </>
          ) : null}
        </>
      }
      table={{
        columns: ["", "value", `${intervalName} low`, `${intervalName} high`, "n"],
        rows: rows.map((r) => [
          r.label,
          format(r.value),
          Number.isFinite(r.lo) ? format(r.lo) : null,
          Number.isFinite(r.hi) ? format(r.hi) : null,
          r.n ?? null,
        ]),
      }}
    >
      <Plot
        key={title}
        xDomain={[niceMin, niceMax]}
        yDomain={[0, rows.length]}
        height={rows.length * rowHeight + 54}
        margin={{ left: labelGutter, right: valueGutter, top: 8, bottom: 38 }}
        ariaLabel={`${title}: estimate and ${intervalName} for each of ${rows.length} entries, ranked`}
      >
        <RowBands n={rows.length} highlight={emphasise ? (i) => emphasise(i, rows.length) : undefined} />
        <Grid x y={false} />
        {zeroLabel ? <RefLine x={0} label={zeroLabel} /> : null}
        <XAxis label={axis} count={5} format={(v) => (Math.abs(v) < 1 ? v.toFixed(2) : v.toFixed(1))} />
        <BandLabels
          labels={rows.map((r) => r.label)}
          swatch={rows.some((r) => r.colour) ? (i) => rows[i].colour ?? null : undefined}
          emphasis={emphasise ? (i) => emphasise(i, rows.length) : undefined}
        />
        <ErrorBarsH
          values={values}
          lo={rows.map((r) => r.lo)}
          hi={rows.map((r) => r.hi)}
          colour={colourFor}
          r={rowHeight >= 26 ? 4 : 3.4}
          stem
          caps
        />
        <RowValues
          values={values}
          format={format}
          tag={(i) => (crossesZero(i) ? "ns" : null)}
          meta={(i) => (rows[i].n != null ? `n=${rows[i].n}` : null)}
        />
        <RowHover titles={rows.map((r, i) => rowTitle(r, i, format, intervalName, crossesZero(i)))} />
      </Plot>
    </ChartFrame>
  );
}

/** The long form of a row, for the hover title. Everything the gutter had no room for. */
function rowTitle(
  r: Row,
  _i: number,
  format: (v: number) => string,
  intervalName: string,
  ns: boolean,
): string {
  const parts = [`${r.key}${r.sub ? ` — ${r.sub}` : ""}`, `${format(r.value)}`];
  if (Number.isFinite(r.lo) && Number.isFinite(r.hi)) {
    parts.push(`${intervalName} ${format(r.lo)} to ${format(r.hi)}`);
  }
  if (r.n != null) parts.push(`n = ${r.n}`);
  if (ns) parts.push("interval spans zero — no measurable effect");
  if (r.note) parts.push(r.note);
  return parts.join("\n");
}

/** "British Grand Prix" -> "British". The suffix is on every row and carries no information. */
function shortEvent(event: string): string {
  return event.replace(/\s+Grand Prix$/i, "");
}
