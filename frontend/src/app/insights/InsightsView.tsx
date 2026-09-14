/**
 * Cross-event league tables, drawn from params.<hash>.json and rivalries.<hash>.json.
 *
 * Every leaf in those artifacts already carries `se`, `ci95`, `n` and a `note` explaining what
 * was fitted or counted -- and until now none of it was displayed anywhere. These are estimates
 * with spread, so they are drawn as point-plus-interval rather than as bare bars: a circuit
 * whose FITTED interval crosses zero has no measurable effect, and a plain bar chart would hide
 * that. That reading belongs to fitted effects and to nothing else. A measured share cannot be
 * negative, so its interval touching zero is a rate of zero rather than a test that failed, and
 * the metrics say which they are (`zeroIsNoEffect`) instead of leaving it to be guessed from the
 * interval's type.
 *
 * The dirty-air parameter doubles as the per-track overtaking-difficulty index, which makes it
 * the most on-brief number in the artifact for a problem statement about overtaking. It sits
 * one card away from "Pass conversion" on purpose: that metric is the MEASURED outcome the
 * fitted index predicts, so a reader can check the model against the season rather than take
 * the coefficient on trust. The two agree at both extremes -- Monaco hardest to follow and
 * lowest converting, Monza easiest and highest -- and disagree through the middle of the
 * field, which is a fact about the model and is meant to be visible.
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

import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { CHART } from "@/lib/palette";
import {
  defaultSimSource,
  splitPairKey,
  type PairCounts,
  type RivalrySet,
  type SimIndex,
} from "@/sim/data/source";
import { teamColour, type Catalogue } from "@/sim/data/catalogue";
import { FilterBar, parseFilters, useFilters, type FilterState } from "@/components/filters";
import { Segmented } from "@/components/ui";
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

type MetricKey = "dirtyAir" | "tyreDeg" | "paceTrend" | "pitLoss" | "passRate";

/**
 * How the rivalry artifact resolved. Three outcomes, kept as one value so they cannot drift
 * apart in state, and so "still loading" stays distinguishable from "loaded, and there is
 * nothing there".
 *
 * That distinction is the whole point. `rivalries()` answers null for an index built before the
 * exporter existed, which is a legitimate settled answer, not a failure and not a zero. A bare
 * `RivalrySet | null` piece of state cannot tell that apart from the moment before the fetch
 * returns, and the difference decides whether the page says "loading" or says why there is no
 * measured rate.
 */
type RivalryLoad =
  | { kind: "present"; set: RivalrySet }
  /** The index carries no `rivalries` key: these artifacts predate the exporter. */
  | { kind: "absent" }
  /** The index named a file that could not be read. Carries the fetch's own message. */
  | { kind: "failed"; reason: string };

/**
 * Why a metric has nothing to show, written three times because it is read in three places with
 * very different room. `short` sits on the nav card, which is a 7rem tile shared with four others
 * and will stretch every one of them if a paragraph is poured into it; `full` replaces the chart
 * once that card is selected, where there is space to say what was missing and how to produce it;
 * `kicker` is the uppercase count line in the page header, which otherwise reads "0 circuits" and
 * has room for about four words.
 *
 * Three strings rather than one truncated one: a reason clipped mid-sentence by CSS is not a
 * reason, and the short form has to stand alone for a reader who never clicks.
 */
interface Unavailable {
  /** Four words at most -- it replaces a count in the header, beside the driver count. */
  kicker: string;
  short: string;
  full: string;
}

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

/* ---------------------------------------------------------------- head-to-head */

/**
 * A directed pair, flattened into a `Row` plus the two things a Row cannot carry: who is on
 * each side, and how many chances there were.
 *
 * It extends `Row` rather than wrapping one so it drops straight into `RankedEffects` -- the
 * same trick that let the measured conversion rate reuse the fitted-parameter chart. The two
 * extra fields exist because the pair filter is a question about PEOPLE ("is either side a
 * driver I picked?") and the key is the only place the people are, and re-splitting the string
 * on every keystroke of the filter would be parsing display text to make a decision.
 */
interface PairRow extends Row {
  attacker: string;
  defender: string;
  /** Labelled chances that ended with the attacker ahead, or null where the artifact carried
   * no count for this key. Null rather than 0: "the exporter did not say" and "it never
   * happened" are the two claims this page spends the most effort keeping apart. */
  passes: number | null;
}

/**
 * Minimum labelled opportunities a pair needs before it is ranked at all.
 *
 * 312 directed pairs exist and most are two drivers who happened to be near each other twice;
 * ranking those alongside a season-long rivalry puts a 1-of-2 pair at 50% above everything real.
 * The gate is a control rather than a constant because the right threshold is a judgement about
 * how much evidence the reader wants, not a fact about the data -- and because moving it is the
 * honest way to narrow the field, as opposed to cropping the ranking and calling it a top ten.
 */
const PAIR_GATES = ["10", "25", "50"] as const;
type PairGate = (typeof PAIR_GATES)[number];

/**
 * How the ledger is ordered.
 *
 * Three orders, not one, because the bottom of a conversion ranking is not noise to be scrolled
 * past: a driver who went 0 from 40 against one rival is a finding, and a descending-only chart
 * with a row cap would cut exactly those rows. "Most chances" is the third question a reader
 * actually asks -- who met whom most often, regardless of who won.
 */
const PAIR_ORDERS = ["top", "bottom", "volume"] as const;
type PairOrder = (typeof PAIR_ORDERS)[number];

/**
 * Comparators for the three orders. Every one breaks ties on the labelled count, descending, so
 * that where two pairs share a rate the better-evidenced one is the one that survives the cap.
 * That matters most at the bottom: 0 of 40 and 0 of 10 are both "0%", and only the first is
 * worth a reader's attention.
 */
const PAIR_SORTS: Record<PairOrder, (a: PairRow, b: PairRow) => number> = {
  top: (a, b) => b.value - a.value || (b.n ?? 0) - (a.n ?? 0),
  bottom: (a, b) => a.value - b.value || (b.n ?? 0) - (a.n ?? 0),
  volume: (a, b) => (b.n ?? 0) - (a.n ?? 0) || b.value - a.value,
};

const PAIR_ORDER_LABELS: Record<PairOrder, { label: string; title: string; topMeans: string }> = {
  top: {
    label: "Converts most",
    title: "Highest conversion rate first",
    topMeans: "highest conversion rate",
  },
  bottom: {
    label: "Converts least",
    title: "Lowest conversion rate first, best-evidenced of the ties at the top",
    topMeans: "lowest conversion rate",
  },
  volume: {
    label: "Most chances",
    title: "Most labelled opportunities first, whatever the rate",
    topMeans: "most chances",
  },
};

/**
 * How many pairs are drawn.
 *
 * 142 pairs clear the default gate, which is a 3,400px chart nobody reads and which defeats the
 * banding the rest of the page relies on. The cap is stated in the chart's note along with the
 * number it removed -- a ranking that silently stops is a ranking that lies about its own
 * extent, and the reader has two controls (the gate and the order) that move the window rather
 * than one that hides it.
 */
const PAIR_ROW_CAP = 30;

/** Which of the two whole-season tallies the attack/defence chart is showing. */
type SideKey = "attack" | "defend";

/** A proportion, in the notation a reader thinks in. The axis stays in shares; see `passRate`. */
const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

/**
 * The hover line for one pair: the exporter's own note, and the exclusion it may not mention.
 *
 * A rate whose denominator was quietly trimmed is the thing this page is most careful about, so
 * every pair's hover has to say how many of its chances were set aside. The exporter normally
 * writes that into the leaf's own note; where it has, repeating it from `counts` would read as
 * two separate facts about the same pair. So this fills the gap and otherwise stays out of the
 * way -- and it counts the set-aside chances rather than restating them as a share of anything,
 * because `counts.opportunities` tracks the LABELLED denominator and is not a grand total to
 * divide by.
 */
function pairNote(note: string | undefined, counts: PairCounts | undefined): string | undefined {
  if (!counts || counts.unlabelled <= 0) return note;
  if (note?.includes("unlabelled")) return note;
  const plural = counts.unlabelled === 1 ? "opportunity" : "opportunities";
  const excluded = `${counts.unlabelled} further ${plural} carried no outcome label and were excluded`;
  return note ? `${note}; ${excluded}` : excluded;
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
   * What the interval on each row actually is, which decides only what it is CALLED -- "95% CI"
   * or "IQR" -- in the gutter, the table header and the hover text. It does not decide whether
   * an interval spanning zero means anything; `zeroIsNoEffect` below does that, and the two
   * deliberately do not agree for every metric.
   */
  interval: "ci95" | "iqr";
  /**
   * Whether "this interval spans zero" is a statement about EVIDENCE here -- the row greyed,
   * tagged ns, counted in the headline tile and in the chart's standing note.
   *
   * Declared per metric rather than read off `interval`, because the interval's type does not
   * decide it. Pass conversion carries a real 95% interval -- a Wilson score interval -- and the
   * test still means nothing on it. A share of opportunities cannot be negative, so its interval
   * can only reach zero when the circuit converted NOTHING; on any other build the tile reads
   * "0 of 8 - every interval clears zero", which announces a check that no circuit could have
   * failed and tells the reader precisely nothing.
   *
   * The case that does real harm is the other one. Zero passes from 200 labelled opportunities
   * has a Wilson low of exactly 0.0, and greying that row as "no measurable effect" would state
   * the opposite of what the data says: a fitted coefficient at zero means the effect could not
   * be told apart from none, while a measured rate at zero means it did not happen on 200
   * occasions when it could have. The second is a strong, well-powered finding about a circuit
   * where following almost never pays, and it is the one a race engineer most needs to see.
   *
   * Not derived from `zeroLabel !== null` either, though that happens to give the right answer
   * for the current five. The agreement is a coincidence of these particular metrics, and the
   * next one added would silently inherit a significance test nobody chose for it.
   */
  zeroIsNoEffect: boolean;
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
  /**
   * The rows to rank.
   *
   * Takes the rivalry artifact as well as the fitted parameters because one metric is measured
   * rather than fitted and lives in the other file. It is passed as `RivalrySet | null`, and a
   * metric that reads it must answer [] for null -- never a zero row. The four params-backed
   * metrics ignore the second argument; params.json is mandatory, so if it were missing the
   * page has already failed with an error before any of this runs.
   */
  rows: (p: FittedParams, r: RivalrySet | null) => Row[];
  /**
   * The fourth headline tile, for the metrics where `zeroIsNoEffect` is false and "N of M
   * intervals span zero" would be a dead number on every build.
   *
   * It is handed the rows already on screen and must stay an aggregate of them: the tile is the
   * top of the page and the least scrutinised thing on it, so it is not a place to introduce a
   * figure the chart underneath cannot be checked against. Metrics with `zeroIsNoEffect` true
   * have that tile decided for them and leave this undefined.
   */
  evidenceTile?: (rows: Row[]) => { label: string; value: string; meta: string };
  /**
   * Why a circuit the page ranks can be absent from THIS metric's rows, as one clause appended
   * to the generic "the filter is not the reason" notice.
   *
   * Optional, and deliberately left off where the artifact does not say why -- pit loss is short
   * one circuit and nothing in params.json explains it, and a guessed cause printed in the
   * page's own voice is worse than no cause at all.
   */
  gapMeans?: string;
  /**
   * Why this metric has nothing to show, when the reason is "this build never produced the
   * data" rather than "the filter excluded everything". Only defined by metrics that read an
   * OPTIONAL artifact, and it takes the load outcome rather than the set so it can name WHICH
   * kind of absence this is -- never built, or built and unreadable.
   *
   * This is the whole-artifact case only. The per-circuit case -- the artifact is here and this
   * circuit is not in it -- is `missingCircuitNotice` below, computed for every metric rather
   * than declared by each, and used only when this returns null.
   *
   * The page's remaining empty state, "no rows match the current filter", is a true statement
   * only when there was data to filter. Returning a notice from either of these swaps it for the
   * true one instead of letting the reader conclude that clearing a filter would bring the
   * metric back.
   */
  unavailable?: (load: RivalryLoad) => Unavailable | null;
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
    zeroIsNoEffect: true,
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
    zeroIsNoEffect: true,
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
    zeroIsNoEffect: true,
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
    /* An IQR is a spread of observed stops, not an interval on an estimate: it can no more fail
       a zero test than a list of lap times can. */
    zeroIsNoEffect: false,
    topMeans: "costliest stop",
    bottomMeans: "cheapest stop",
    zeroLabel: null,
    format: (v) => v.toFixed(1),
    evidenceTile: (rows) => ({
      label: "circuits measured",
      value: String(rows.length),
      meta: "interval is an IQR of observed stops, not a confidence interval — there is no zero test here to pass or fail",
    }),
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
  {
    key: "passRate",
    label: "Pass conversion",
    units: "share of following opportunities that ended in a pass",
    /*
     * The axis caption says "share", not "%", because the tick numbers under it are shares:
     * RankedEffects formats the x axis for all five metrics with one shared formatter, so the
     * ticks read 0.00 to 0.30 and the caption has to describe what it is actually labelling.
     * The value gutter, the tiles and the table all use `format` below and read as percent,
     * which is the same quantity in the notation a reader thinks in. The two never disagree:
     * 0.27 on the axis is 27.1% in the gutter.
     */
    axis: "share of opportunities converted",
    blurb:
      "MEASURED, not fitted: the share of following opportunities at this circuit that ended with the car behind in front. An opportunity is a car close enough behind to attempt a pass; it converts when it comes out of the zone ahead. This is the outcome the fitted overtaking-difficulty index above is meant to predict — they agree at both ends of the field and part company in the middle, and reading them together is the point of having both. The interval is a 95% Wilson score interval on the proportion, so a circuit with few opportunities gets a wide one rather than a confident-looking rate — and, unlike a symmetric error bar, one that never runs below zero or above one. Opportunities whose outcome could not be labelled are dropped from the denominator, never counted as failed passes — n is the labelled count, not every opportunity seen.",
    descending: true,
    interval: "ci95",
    zeroIsNoEffect: false,
    topMeans: "most passes per chance",
    bottomMeans: "fewest passes per chance",
    evidenceTile: (rows) => ({
      label: "circuits measured",
      value: String(rows.length),
      meta:
        "every interval is a 95% Wilson interval on a proportion — it can only reach zero where " +
        "nothing converted, so there is no zero test here to pass or fail",
    }),
    gapMeans:
      "opportunity data has only been built for the circuits listed above; the rest have no " +
      "measured conversion rate in this build, which is not the same as a rate of zero",
    /*
     * No zero rule, for the same reason pit loss has none: a conversion rate cannot be
     * negative, so zero is the left frame of the plot and a rule drawn there marks the edge of
     * the chart rather than anything a reader was asking about. A circuit where nothing
     * A circuit where nothing converted is NOT tagged ns either: see `zeroIsNoEffect` above.
     * Its Wilson interval reaches zero, but zero here means "no pass happened in n chances",
     * which is a measurement, not a failure to measure.
     */
    zeroLabel: null,
    format: (v) => `${(v * 100).toFixed(1)}%`,
    // The artifact stores each event's rate as the same Leaf the fitted parameters use, so this
    // is the identical flattening the other circuit metrics get -- no second code path, and no
    // opportunity for the measured numbers to be reshaped on the way to the chart.
    rows: (_p, r) => (r ? fromLeaves(r.passConversionRate) : []),
    unavailable: (load) => {
      if (load.kind === "present") return null;
      if (load.kind === "absent") {
        return {
          kicker: "metric not in this build",
          short: "Not in this build — these artifacts predate the rivalry exporter",
          full:
            "No measured conversion rate in this build. These sim artifacts were written before " +
            "the rivalry exporter existed, so their index carries no rivalries file and there is " +
            "nothing here to rank. The card is empty rather than showing 0%: never measured and " +
            "measured at zero are different claims, and only one of them would be in the data. " +
            "Rebuild the artifacts from the repo root — `python scripts/build_sim_data.py --year " +
            "2026 --all --jobs 0 --fresh` — to produce it. The other four metrics are fitted from " +
            "params.json and are unaffected.",
        };
      }
      return {
        kicker: "metric unreadable",
        short: "The rivalries artifact in this build could not be read",
        full:
          "This build's index names a rivalries artifact, but it could not be read, so there is " +
          `no measured conversion rate to show: ${load.reason}`,
      };
    },
  },
];

export default function InsightsView() {
  /**
   * Filters come off the URL HERE rather than from the server.
   *
   * A static export serves one prebuilt HTML file for every query string, so resolving
   * these on the server would bake in whichever set the build happened to see and every
   * shared link after that would render the wrong filters.
   *
   * Repeated params keep the behaviour the server had: `?event=a&event=b` becomes
   * `event=a,b`, which is the comma form `parseFilters` splits on. Dropping the join would
   * silently keep only the last value of a multi-select someone shared.
   */
  const search = useSearchParams();
  const initialFilters = useMemo<FilterState>(() => {
    const q = new URLSearchParams();
    for (const key of new Set(search.keys())) {
      const all = search.getAll(key);
      if (all.length) q.set(key, all.join(","));
    }
    return parseFilters(q);
    // Read once, at mount: it seeds `useState` below, and re-parsing on every URL change
    // would fight the user's own filter edits, which write back to the address bar.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [params, setParams] = useState<FittedParams | null>(null);
  const [cat, setCat] = useState<Catalogue | null>(null);
  const [index, setIndex] = useState<SimIndex | null>(null);
  /** null while the fetch is still in flight; one of the three settled kinds afterwards. */
  const [rivalryLoad, setRivalryLoad] = useState<RivalryLoad | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [metricKey, setMetricKey] = useState<MetricKey>("dirtyAir");
  const [filters, setFilters] = useState<FilterState>(initialFilters);
  const { set, reset } = useFilters(initialFilters, filters, setFilters);
  /** Teams switched off from the driver chart's legend. */
  const [hiddenTeams, setHiddenTeams] = useState<ReadonlySet<string>>(new Set());
  /** Minimum labelled opportunities a pair needs to be ranked. Default 10; see PAIR_GATES. */
  const [pairGate, setPairGate] = useState<PairGate>("10");
  const [pairOrder, setPairOrder] = useState<PairOrder>("top");
  const [sideKey, setSideKey] = useState<SideKey>("attack");

  useEffect(() => {
    let live = true;
    Promise.all([
      defaultSimSource.params(),
      defaultSimSource.catalogue<Catalogue>(),
      defaultSimSource.index(),
      /*
       * The rivalry artifact is OPTIONAL, so its failure must not be the page's failure: four
       * of the five metrics come from params.json and have nothing to do with it. Catching
       * here rather than letting Promise.all reject is what keeps a missing or unreadable
       * rivalries file from blanking the whole page — the fifth card explains itself and the
       * rest of the page carries on. The reason is kept, not swallowed: it is what the card
       * shows instead of a number.
       */
      defaultSimSource
        .rivalries()
        .then((set): RivalryLoad => (set ? { kind: "present", set } : { kind: "absent" }))
        .catch(
          (e: unknown): RivalryLoad => ({
            kind: "failed",
            reason: e instanceof Error ? e.message : String(e),
          }),
        ),
    ])
      .then(([p, c, i, riv]) => {
        if (!live) return;
        setParams(p);
        setCat(c);
        setIndex(i);
        setRivalryLoad(riv);
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
        if (!params || !rivalryLoad) {
          return { metric: candidate, rows: [] as Row[], unavailable: null as Unavailable | null };
        }
        // Absence is decided BEFORE filtering, so a metric with no artifact behind it says so
        // even when a circuit filter is also active. The filter is the reason a row is missing
        // only once there were rows.
        const whole = candidate.unavailable?.(rivalryLoad) ?? null;
        const set = rivalryLoad.kind === "present" ? rivalryLoad.set : null;
        const want = filters.circuits.length > 0 ? new Set(filters.circuits) : null;
        const all = candidate.rows(params, set);
        const rows = all
          .filter((r) => !want || want.has(slugByEvent.get(r.key) ?? ""))
          .sort((a, b) => (candidate.descending ? b.value - a.value : a.value - b.value));
        /*
         * The per-circuit case. "No rows match the current filter" is true whenever the rows are
         * empty, but it is only the REASON when the metric had a row for that circuit to begin
         * with. Pass conversion ranks 8 circuits where the fitted metrics rank 13, so filtering
         * to Hungary empties the chart for a reason no amount of clearing the filter will fix --
         * and pointing the reader at a control that cannot help is how they conclude the data is
         * there and they mis-clicked. Named, because "some circuits" is not a reason either.
         */
        const missing =
          whole || rows.length > 0 || !want
            ? null
            : namedMissingCircuits(want, all, slugByEvent);
        const unavailable =
          whole ??
          (missing
            ? {
                kicker: "no data for this circuit",
                short: `Not measured at ${missing}`,
                full:
                  `${missing} ${missing.includes(" and ") ? "are" : "is"} not in this metric` +
                  `${candidate.gapMeans ? ` — ${candidate.gapMeans}` : ""}. The filter is not the ` +
                  "reason and clearing it will not produce a row: there is no value here to show, " +
                  "which is a different thing from a value of zero.",
              }
            : null);
        return { metric: candidate, rows, unavailable };
      }),
    [params, rivalryLoad, filters.circuits, slugByEvent],
  );

  const activeMetric = useMemo(
    () => circuitMetricRows.find(({ metric: candidate }) => candidate.key === metricKey),
    [circuitMetricRows, metricKey],
  );
  const trackRows = activeMetric?.rows ?? [];
  /** Non-null when the selected metric's data was never built; the chart is replaced by it. */
  const trackRowsUnavailable = activeMetric?.unavailable ?? null;

  /**
   * The chart's standing note, plus the counts the honest reading of a rate depends on.
   *
   * A conversion rate is a fraction, and a fraction is only as honest as its denominator. The
   * artifact's totals say how many opportunities were seen and how many of those carried no
   * outcome label at all; the unlabelled ones are in neither the numerator nor the denominator
   * of any row above. That is the right treatment -- an unlabelled opportunity is missing
   * evidence, not a failed pass -- but it is a treatment, and one a reader has to be told
   * about before the percentages mean what they appear to mean.
   *
   * The numbers come from the artifact rather than from this file, so they cannot go stale
   * against the data they describe, and they disappear with the metric when it has no data.
   */
  const metricNote = useMemo((): React.ReactNode => {
    const set = rivalryLoad?.kind === "present" ? rivalryLoad.set : null;
    if (metricKey !== "passRate" || !set) return metric.blurb;
    const t = set.totals;
    return (
      <>
        {metric.blurb}{" "}
        <strong className={s.inlineWarn}>
          {t.unlabelled.toLocaleString()} of the {t.opportunities.toLocaleString()} opportunities
          in this season carry no outcome label
        </strong>{" "}
        and are excluded from every rate above — not counted as failed passes. Season-wide that
        leaves {t.labelled.toLocaleString()} labelled opportunities and{" "}
        {t.passes.toLocaleString()} passes over {t.events} events (
        {t.sessions.join(" and ")} sessions), under the “{set.labelDefinition}” definition of a
        conversion; a circuit filter narrows the rows charted above but not these totals. Each
        row&rsquo;s n is that circuit&rsquo;s own labelled count, and its hover text says how many
        of its own opportunities were set aside.
      </>
    );
  }, [metricKey, metric, rivalryLoad]);

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

  /* ------------------------------------------------------------- head-to-head */

  /** The rivalry artifact itself, once the load has settled to something readable. */
  const rivalrySet = rivalryLoad?.kind === "present" ? rivalryLoad.set : null;

  /**
   * Every directed pair as a chart row, BEFORE the gate, the filters and the cap.
   *
   * Kept whole on purpose. Each of those three steps removes rows, and a count taken after them
   * can only report what survived -- "30 pairs" written under a chart built from 312 is the kind
   * of number that reads as the whole truth. The section quotes all four counts, so it needs the
   * population they are measured against.
   */
  const allPairRows = useMemo((): PairRow[] => {
    const h2h = rivalrySet?.headToHead;
    if (!h2h || !cat) return [];
    return Object.entries(h2h.pairs)
      .filter(([, leaf]) => Number.isFinite(leaf?.value))
      .map(([key, leaf]): PairRow => {
        // A key that will not split is shown verbatim rather than dropped: the rate behind it is
        // still a measurement, and hiding a row because this file could not parse its label
        // would be the UI deciding what the artifact is allowed to contain.
        const sides = splitPairKey(key);
        const attacker = sides?.attacker ?? key;
        const defender = sides?.defender ?? "";
        const counts = h2h.counts[key];
        const attackerTeam = teamByCode.get(attacker) ?? null;
        const defenderTeam = defender ? (teamByCode.get(defender) ?? null) : null;
        return {
          label: key,
          key,
          value: leaf.value,
          lo: leaf.ci95?.[0] ?? NaN,
          hi: leaf.ci95?.[1] ?? NaN,
          // n is the LABELLED count, which is the denominator of the rate and therefore the
          // thing worth gating on. `opportunities` below is the larger, rawer number.
          n: leaf.n ?? null,
          passes: counts?.passes ?? null,
          attacker,
          defender,
          // Colour is the ATTACKER's team, because the rate is the attacker's. The defender is
          // named in the label and in the hover; encoding both sides in one dot would need a
          // two-tone mark and would still be unreadable at a 5px chip.
          colour: teamColour(cat.teams, attackerTeam),
          // "Mercedes attacking Mercedes" is accurate and reads like a bug. A same-team pair is
          // the one case where the two teams carry no information and the RELATIONSHIP does.
          sub: defenderTeam
            ? attackerTeam === defenderTeam
              ? `${attackerTeam} team-mates`
              : `${attackerTeam ?? "unknown team"} attacking ${defenderTeam}`
            : (attackerTeam ?? undefined),
          note: pairNote(leaf.note, counts),
        };
      });
  }, [rivalrySet, cat, teamByCode]);

  /**
   * The ledger's four populations: everything, what clears the gate, what the filters leave, and
   * what is actually drawn. One memo rather than four because the numbers are only meaningful
   * against each other -- the note underneath the chart is a sentence about the differences.
   *
   * A pair is in scope if EITHER side matches the selection. A rivalry is two people, and a
   * reader who picks HAM wants the rows where Hamilton is attacking AND the rows where he is
   * being attacked; keeping only the first would answer half the question while looking complete.
   */
  const pairSelection = useMemo(() => {
    const min = Number(pairGate);
    const picked = filters.drivers.length > 0 ? new Set(filters.drivers) : null;
    const gated = allPairRows.filter((r) => (r.n ?? 0) >= min);
    const scoped = gated.filter(
      (r) =>
        (!picked || picked.has(r.attacker) || picked.has(r.defender)) &&
        (!codesForTeams || codesForTeams.has(r.attacker) || codesForTeams.has(r.defender)),
    );
    const ranked = [...scoped].sort(PAIR_SORTS[pairOrder]);
    return {
      total: allPairRows.length,
      gated: gated.length,
      scoped,
      rows: ranked.slice(0, PAIR_ROW_CAP),
      cut: Math.max(0, ranked.length - PAIR_ROW_CAP),
    };
  }, [allPairRows, pairGate, pairOrder, filters.drivers, codesForTeams]);

  /**
   * The whole-season tally for one side of the fight.
   *
   * Attack ranks descending -- the top row converts most of the chances it gets. Defence ranks
   * ASCENDING, so the top row is the driver passed least often, because "best" on this chart is
   * the low end and a reader should not have to work that out from the axis. The chart's note
   * says which way it is pointing rather than leaving it to the sort order.
   */
  const sideRows = useMemo((): Row[] => {
    const src = sideKey === "attack" ? rivalrySet?.attackerTotals : rivalrySet?.defenderTotals;
    if (!src || !cat) return [];
    const picked = filters.drivers.length > 0 ? new Set(filters.drivers) : null;
    return Object.entries(src)
      .filter(([, leaf]) => Number.isFinite(leaf?.value))
      .filter(([code]) => !picked || picked.has(code))
      .filter(([code]) => !codesForTeams || codesForTeams.has(code))
      .map(([code, leaf]): Row => {
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
          note: leaf.note,
        };
      })
      .sort((a, b) => (sideKey === "attack" ? b.value - a.value : a.value - b.value));
  }, [rivalrySet, sideKey, cat, filters.drivers, codesForTeams, teamByCode]);

  /**
   * Why the whole head-to-head family has nothing, or null. Resolved before any filtering, for
   * the same reason the circuit metrics resolve theirs first: an artifact that never carried
   * head-to-head data must say so even when a driver filter is also active, or the reader clears
   * the filter and is told the same nothing twice.
   *
   * Section-level rather than per-chart because the three keys are written by one exporter, and
   * on an older artifact all three are missing for one reason. Two charts each printing the same
   * paragraph would read as two separate faults.
   */
  const familyUnavailable = useMemo(
    () =>
      rivalryLoad
        ? headToHeadUnavailable(
            rivalryLoad,
            (r) => !!r.headToHead || !!r.attackerTotals || !!r.defenderTotals,
            "head-to-head",
          )
        : null,
    [rivalryLoad],
  );
  /** The per-chart cases, for the odd artifact that carried one key and not another. */
  const pairsUnavailable = useMemo(
    () => (rivalryLoad ? headToHeadUnavailable(rivalryLoad, (r) => !!r.headToHead, "pair") : null),
    [rivalryLoad],
  );
  const sideUnavailable = useMemo(
    () =>
      rivalryLoad
        ? headToHeadUnavailable(
            rivalryLoad,
            (r) => !!(sideKey === "attack" ? r.attackerTotals : r.defenderTotals),
            sideKey === "attack" ? "per-attacker" : "per-defender",
          )
        : null,
    [rivalryLoad, sideKey],
  );

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
  // rivalryLoad is in the gate because "not fetched yet" and "fetched, and there is nothing"
  // are different answers and only the second one is safe to render. It never rejects -- a
  // failure settles as { kind: "failed" } -- so waiting on it cannot hang the page.
  if (!params || !cat || !index || !rivalryLoad) {
    return (
      <main className={s.main}>
        <p className={s.lede}>Loading fitted parameters…</p>
      </main>
    );
  }

  return (
    <main className={s.main}>
      <header className={s.head}>
        {/* "0 circuits" is a count, and a count implies something was counted. When the selected
            metric's artifact was never built there is nothing to count, so the kicker says that
            instead — the number and the reason must not contradict the box below. */}
        <p className={s.kicker}>
          Cross-event ·{" "}
          {trackRowsUnavailable
            ? "metric not in this build"
            : `${trackRows.length} ${trackRows.length === 1 ? "circuit" : "circuits"}`}{" "}
          · {driverRows.length} drivers ·{" "}
          {/* Third count, on the same rule as the first: a number here implies something was
              counted, so when the artifact carried no head-to-head data this says that instead
              of printing "0 rivalries" over a box explaining why there are none. */}
          {familyUnavailable
            ? familyUnavailable.kicker
            : `${pairSelection.total} rivalries`}
        </p>
        {/* Not "Fitted Parameters" any more: four of the five circuit metrics are fitted
            coefficients, pass conversion is a measured outcome, and a title that contradicts its
            own page is worse than one that is merely broad. "Evidence" is the word this codebase
            already uses for the same idea (the session page's "Evidence surface"). */}
        <h1 className={s.title}>
          Season <em>Evidence</em>
        </h1>
        <p className={s.lede}>
          What the race model believes about a circuit, and what the season actually did. Four of
          the five circuit metrics are coefficients fitted from the 2026 lap record and shown with
          the spread they were fitted to; where a fitted 95% interval crosses zero the circuit has
          no measurable effect, and those rows are greyed and tagged{" "}
          <span className={s.ns}>ns</span> rather than drawn as a confident-looking bar. The
          fifth, <strong className={s.tileStrong}>pass conversion, is measured, not fitted</strong>{" "}
          — a count of what happened, kept beside the fitted overtaking-difficulty index so that
          index can be read against the outcome it claims to explain. A measured proportion gets
          no zero test: see its own tile.
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

        {/*
          * A metric whose artifact this build never produced gets the reason in place of the
          * chart, not an empty chart and not a zero. The chart's own empty state is about
          * filters, and would send the reader off to clear a selection that was never the
          * problem. Same dashed-box treatment so it reads as the same kind of statement.
          */}
        {trackRowsUnavailable ? (
          <p className={s.emptyRows}>{trackRowsUnavailable.full}</p>
        ) : (
          <>
            <Headline metric={metric} rows={trackRows} />

            <RankedEffects
              title={metric.label}
              units={metric.units}
              axis={metric.axis}
              note={metricNote}
              zeroLabel={metric.zeroLabel}
              rows={trackRows}
              format={metric.format}
              significance={metric.zeroIsNoEffect}
              intervalName={metric.interval === "ci95" ? "95% CI" : "IQR"}
              rowHeight={26}
              labelGutter={104}
              emphasise={(i) => i === 0}
            />
          </>
        )}
      </section>

      {/*
        * WHO, not where.
        *
        * The section above ranks circuits and the one below ranks drivers against the field.
        * Neither answers the question actually asked over the radio, which is not "is this circuit
        * hard to pass at" but "can I get past THAT car". That question is directed, it is measured
        * rather than fitted, and the artifact already answers it: one Wilson interval per ordered
        * pair of drivers, in the same Leaf shape the charts above already draw. So this section
        * introduces no new statistics and no new chart -- it is the same ranked point-and-interval
        * plot pointed at a different key.
        */}
      <section className={s.block}>
        <header className={s.sectionHead}>
          <p className={s.kicker}>Head-to-head · measured, not fitted</p>
          <h2 className={s.sectionTitle}>Who gets past whom</h2>
          <p className={s.sectionLede}>
            A pair is <strong className={s.tileStrong}>directed</strong>: ANT → RUS counts the
            times Antonelli was following Russell and came out ahead, and RUS → ANT is a different
            rivalry with a different denominator. Both are ranked and neither is the average of the
            two — averaging them would answer neither question. Opportunities whose outcome could
            not be labelled are excluded from every rate here, never counted as failed passes. Each
            interval is a 95% Wilson interval on a proportion, so{" "}
            <strong className={s.tileStrong}>nothing on these charts is tagged ns</strong>: a pair
            at 0% converted nothing in the chances it had, which is a measurement — and often the
            most useful one on the page — rather than a test that failed.
          </p>
        </header>

        {/* One reason for the whole family, not two copies of it: the three keys come from one
            exporter, so on an older artifact all three are missing for the same cause. */}
        {familyUnavailable ? (
          <p className={s.emptyRows}>{familyUnavailable.full}</p>
        ) : (
          <>
            {pairsUnavailable ? (
              <p className={s.emptyRows}>{pairsUnavailable.full}</p>
            ) : (
              <>
                <PairHeadline
                  scoped={pairSelection.scoped}
                  drawn={pairSelection.rows.length}
                  gate={pairGate}
                />

                <RankedEffects
                  title="Head-to-head ledger"
                  units="share of one driver's labelled chances on one rival that ended in a pass"
                  axis="share of chances converted"
                  note={
                    <LedgerNote
                      exporterNote={rivalrySet?.headToHead?.note}
                      sel={pairSelection}
                      gate={pairGate}
                    />
                  }
                  zeroLabel={null}
                  rows={pairSelection.rows}
                  format={pct}
                  /* A proportion, so an interval reaching zero says the pair converted nothing --
                     not that the evidence failed a test. Same reasoning as `passRate` above. */
                  significance={false}
                  intervalName="95% CI"
                  rowHeight={24}
                  /* "ANT → RUS" is about three times the width of the driver chart's bare code,
                     plus the team chip and its gap. */
                  labelGutter={88}
                  emphasise={(i) => i === 0}
                  controls={
                    <>
                      <span className={s.controlLabel}>min. labelled chances</span>
                      <Segmented
                        label="Minimum labelled opportunities per pair"
                        options={PAIR_GATES.map((g) => ({
                          value: g,
                          label: g,
                          title: `Rank only pairs with at least ${g} labelled opportunities`,
                        }))}
                        value={pairGate}
                        onChange={setPairGate}
                      />
                      <span className={s.controlLabel}>order</span>
                      <Segmented
                        label="Ledger order"
                        options={PAIR_ORDERS.map((o) => ({
                          value: o,
                          label: PAIR_ORDER_LABELS[o].label,
                          title: PAIR_ORDER_LABELS[o].title,
                        }))}
                        value={pairOrder}
                        onChange={setPairOrder}
                      />
                    </>
                  }
                  emptyNote={
                    pairSelection.gated === 0
                      ? `Not one of this season's ${pairSelection.total} directed pairs reached ${pairGate} labelled opportunities. Lower the gate to see the thinner rivalries: their rates are real, but a pair with a handful of chances carries an interval wide enough to contain almost anything.`
                      : `None of the ${pairSelection.gated} pairs at this gate involve the drivers or teams you selected. A pair is in scope when EITHER side matches, so clearing a facet — or lowering the gate — will bring rows back.`
                  }
                />
              </>
            )}

            {sideUnavailable ? (
              <p className={s.emptyRows}>{sideUnavailable.full}</p>
            ) : (
              <RankedEffects
                title={
                  sideKey === "attack" ? "Attack — chances taken" : "Defence — chances conceded"
                }
                units={
                  sideKey === "attack"
                    ? "share of every labelled chance this driver had, on anyone, that ended in a pass"
                    : "share of every labelled chance taken against this driver that ended in a pass"
                }
                axis={
                  sideKey === "attack"
                    ? "share of chances converted"
                    : "share of chances conceded — lower is a better defence"
                }
                note={
                  <>
                    {sideKey === "attack" ? (
                      <>
                        Every labelled chance this driver had, pooled over all their pairs: who
                        converts what they get. The top row has the largest share, not the most
                        chances — n in the gutter is the count, and a driver with a dozen chances
                        sits beside one with three hundred carrying a far wider interval for it.
                      </>
                    ) : (
                      <>
                        Every labelled chance taken <em>against</em> this driver, pooled over all
                        their pairs: who concedes least when attacked. Ranked{" "}
                        <strong className={s.tileStrong}>ascending</strong>, so the top row here is
                        the best defence rather than the biggest number — the axis runs the same
                        way as every other chart on the page and only the ordering is inverted.
                      </>
                    )}{" "}
                    Counted by the exporter from the labelled opportunities directly, not averaged
                    in the browser from the pairs above: averaging pair rates would weight a
                    two-chance rivalry the same as a hundred-chance one, and the interval would no
                    longer be a Wilson interval on anything. Unlabelled opportunities are excluded
                    here as well, so n is the labelled count rather than every chance seen. No row
                    is tagged <span className={s.ns}>ns</span>, for the reason the ledger gives:
                    this is a proportion, and zero on it is an outcome.
                  </>
                }
                zeroLabel={null}
                rows={sideRows}
                format={pct}
                significance={false}
                intervalName="95% CI"
                /* 22, not 20: below that pitch RowValues drops n, and n is what separates a
                   well-evidenced 0% from one unlucky afternoon. */
                rowHeight={22}
                labelGutter={52}
                emphasise={(i) => i === 0}
                controls={
                  <>
                    <span className={s.controlLabel}>side of the fight</span>
                    <Segmented
                      label="Attack or defence"
                      options={[
                        {
                          value: "attack" as SideKey,
                          label: "Attack",
                          title: "Share of their own chances this driver converts",
                        },
                        {
                          value: "defend" as SideKey,
                          label: "Defence",
                          title: "Share of chances against this driver that were converted",
                        },
                      ]}
                      value={sideKey}
                      onChange={setSideKey}
                    />
                  </>
                }
                emptyNote="No driver matches the current selection. Clear a team or driver filter to bring rows back."
              />
            )}
          </>
        )}
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
 * Five concise readings before the detailed chart. Each control is a real tab, not a decorative
 * dashboard tile: selecting it expands that metric's ranked uncertainty chart immediately below.
 *
 * A card with no lead row has to say WHY it has none. "No circuit matches the current filter" is
 * the right answer when a filter emptied it and a lie when the data behind the metric was never
 * built -- it points the reader at a control that will not help. So the reason travels with the
 * rows, and the generic filter message is only the fallback.
 */
function MetricNavigator({
  metrics,
  active,
  onSelect,
}: {
  metrics: { metric: Metric; rows: Row[]; unavailable?: Unavailable | null }[];
  active: MetricKey;
  onSelect: (key: MetricKey) => void;
}) {
  return (
    <div className={s.metricNav} aria-label="Circuit metric">
      {metrics.map(({ metric, rows, unavailable }) => {
        const lead = rows[0];
        const isActive = metric.key === active;
        const hasInterval = Number.isFinite(lead?.lo) && Number.isFinite(lead?.hi);
        const crossesZero =
          metric.zeroIsNoEffect && hasInterval && lead.lo <= 0 && lead.hi >= 0;
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
                  {metric.topMeans} ·{" "}
                  {metric.format(lead.value)} {metric.axis}
                </span>
                {hasInterval && lead ? (
                  <span className={s.metricInterval} data-warn={crossesZero ? "true" : undefined}>
                    {intervalLabel} {metric.format(lead.lo)} → {metric.format(lead.hi)}
                    {crossesZero ? " · ns" : ""}
                  </span>
                ) : null}
              </>
            ) : (
              <span className={s.metricMeta}>
                {unavailable?.short ?? "No circuit matches the current filter"}
              </span>
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

  // A metric that opts out of the zero test shows its own evidence tile instead. Resolved here
  // so the tile below reads one branch rather than re-deriving the opt-out three times.
  const evidence = metric.zeroIsNoEffect ? null : metric.evidenceTile?.(rows) ?? null;

  const crossing =
    metric.zeroIsNoEffect
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
          {evidence ? evidence.label : "no measurable effect"}
        </span>
        <span className={s.tileValue} data-warn={!evidence && crossing ? "true" : undefined}>
          {evidence ? evidence.value : `${crossing} of ${rows.length}`}
        </span>
        <span className={s.tileMeta}>
          {evidence
            ? evidence.meta
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
          {format(quickest.value)} → {format(slowest.value)} from quickest to largest positive offset
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
  controls,
  emptyNote,
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
  /**
   * Chart-local controls, docked into the frame header by ChartFrame.
   *
   * They belong to the chart rather than to the page because they change what THIS chart shows
   * and nothing else: the page-wide filters already have a bar of their own at the top, and a
   * reader who finds a gate control up there will reasonably expect it to apply to every chart
   * below it.
   */
  controls?: React.ReactNode;
  /**
   * What to say when there are no rows, replacing the generic filter message.
   *
   * The default sends the reader to clear a filter, which is the right advice exactly when a
   * filter is the cause. A chart with its own threshold control has a second cause, and telling
   * someone to clear a selection they never made is how they conclude the page is broken.
   */
  emptyNote?: React.ReactNode;
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
        controls={controls}
      >
        <p className={s.emptyRows}>
          {emptyNote ??
            (legend?.length
              ? "Every series is switched off. Use the legend above, or clear a filter, to bring rows back."
              : "No rows match the current filter. Clear a circuit, team or driver selection to bring them back.")}
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
      controls={controls}
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

/**
 * The ledger's standing note: what a pair is, and every count between the season and the chart.
 *
 * Four numbers, in the order the rows were removed -- counted, gated, filtered, drawn. A chart
 * that stops at thirty rows without saying so is a chart that misrepresents its own extent, and
 * "top 30" alone does not tell a reader whether they are looking at 30 of 31 or 30 of 142. The
 * singular/plural care is not fussiness: at a 50-chance gate with one driver selected this note
 * routinely renders with a count of one, and "the 1 ... are drawn" reads as a template that got
 * away from its author, which is the last impression a page about honest numbers wants to give.
 */
function LedgerNote({
  exporterNote,
  sel,
  gate,
}: {
  /** The artifact's own sentence on what converting means; absent on an older build. */
  exporterNote?: string;
  sel: { total: number; gated: number; scoped: PairRow[]; rows: PairRow[]; cut: number };
  gate: PairGate;
}) {
  return (
    <>
      {exporterNote ? <>{exporterNote} </> : null}
      Colour is the <strong className={s.tileStrong}>attacker&rsquo;s</strong> team; the defender
      is named in the label and in the row&rsquo;s hover text. {sel.total.toLocaleString()}{" "}
      directed pairs were counted this season, {sel.gated.toLocaleString()}{" "}
      {sel.gated === 1 ? "of them carries" : "of them carry"} at least {gate} labelled
      opportunities, the driver and team filters leave {sel.scoped.length.toLocaleString()}, and
      the {sel.rows.length === 1 ? "one" : sel.rows.length} at the top of this order{" "}
      {sel.rows.length === 1 ? "is" : "are"} drawn
      {sel.cut > 0 ? (
        <>
          {" \u2014 "}
          <strong className={s.inlineWarn}>
            {sel.cut.toLocaleString()} more {sel.cut === 1 ? "clears" : "clear"} the gate and{" "}
            {sel.cut === 1 ? "is" : "are"} not on this chart
          </strong>
          . Raise the gate to narrow the field on evidence rather than on rank, or switch the
          order to read the other end of it
        </>
      ) : (
        <>, which is every pair that clears the gate</>
      )}
      .{" "}
      {/* Cited from the rows on screen rather than typed into the prose: a worked example of a
          wide interval goes stale the moment the season does, and the rule on this page is that
          every number quoted can be checked against the chart underneath it. */}
      <WidestIntervalNote rows={sel.rows} />
      Each row&rsquo;s n is its own labelled count, and its hover says how many of that
      pair&rsquo;s opportunities carried no outcome label and were set aside.
    </>
  );
}

/**
 * The at-a-glance layer for the head-to-head ledger.
 *
 * Same four-tile shape as the two above, and the same rule: every figure is an aggregate of rows
 * already on screen, so there is nothing here a reader cannot check against the chart. Two of the
 * four are deliberately the ENDS of the ranking rather than the top of it -- a ledger whose
 * headline only ever names the best converter would bury the more interesting half of the season,
 * which is the pairs that had the chances and did nothing with them.
 *
 * The tiles do not follow the order control. They answer fixed questions, and a tile whose
 * meaning changed when a sort changed would be a fourth thing to keep track of.
 */
function PairHeadline({
  scoped,
  drawn,
  gate,
}: {
  /** Every pair that clears the gate and matches the filters -- not just the drawn slice. */
  scoped: PairRow[];
  drawn: number;
  gate: PairGate;
}) {
  if (scoped.length === 0) return null;

  // Sorted copies rather than a single scan, so the tie-breaks are exactly the chart's: where two
  // pairs share a rate the better-evidenced one is named, which at the bottom of the table is the
  // difference between "0 from 40" and "0 from 10".
  const best = [...scoped].sort(PAIR_SORTS.top)[0];
  const worst = [...scoped].sort(PAIR_SORTS.bottom)[0];
  const busiest = [...scoped].sort(PAIR_SORTS.volume)[0];

  return (
    <div className={s.tiles}>
      <div className={s.tile} data-lead="true">
        <span className={s.tileLabel}>converts most</span>
        <span className={s.tileValue}>{best.label}</span>
        <span className={s.tileMeta}>
          {pairTally(best)} · 95% CI {pct(best.lo)} → {pct(best.hi)}
        </span>
      </div>

      <div className={s.tile}>
        <span className={s.tileLabel}>converts least</span>
        <span className={s.tileValue}>{worst.label}</span>
        <span className={s.tileMeta}>
          {pairTally(worst)} · 95% CI {pct(worst.lo)} → {pct(worst.hi)}
          {worst.value === 0 ? " — nothing converted, which is a result, not a missing number" : ""}
        </span>
      </div>

      <div className={s.tile}>
        <span className={s.tileLabel}>most contested</span>
        <span className={s.tileValue}>{busiest.label}</span>
        <span className={s.tileMeta}>
          {busiest.n ?? 0} labelled chances · {pct(busiest.value)} converted
        </span>
      </div>

      <div className={s.tile}>
        <span className={s.tileLabel}>pairs in view</span>
        <span className={s.tileValue}>
          {drawn} of {scoped.length}
        </span>
        <span className={s.tileMeta}>
          drawn from the directed pairs with at least {gate} labelled chances that match the
          current filters — every interval is a 95% Wilson interval on a proportion, so there is
          no zero test here to pass or fail
        </span>
      </div>
    </div>
  );
}

/** "10 of 101 labelled chances — 9.9%", or the rate alone where the artifact carried no count. */
function pairTally(r: PairRow): string {
  if (r.passes == null || r.n == null) return `${pct(r.value)} converted`;
  return `${r.passes} of ${r.n} labelled chances — ${pct(r.value)}`;
}

/**
 * One sentence naming the widest interval actually drawn.
 *
 * A wide interval on a ten-chance pair is the honest output of a Wilson interval, not a fault in
 * the data, and saying so in the abstract convinces nobody. Naming the specific row makes the
 * claim checkable in the chart immediately below -- and because it is computed from the rows on
 * screen, it moves with the gate, the order and the filters instead of going stale the way a
 * worked example typed into the prose would.
 */
function WidestIntervalNote({ rows }: { rows: Row[] }) {
  const w = widestInterval(rows);
  if (!w || w.n == null) return null;
  return (
    <>
      A thin pair gets a wide interval, and the width is the finding rather than a defect: the
      widest drawn here is {w.label} at {pct(w.value)} from {w.n} labelled chances, 95% CI{" "}
      {pct(w.lo)} → {pct(w.hi)} — a span of {pct(w.hi - w.lo)}.{" "}
    </>
  );
}

/** The row with the widest finite interval, or null when no row carries one. */
function widestInterval(rows: Row[]): Row | null {
  let best: Row | null = null;
  for (const r of rows) {
    if (!Number.isFinite(r.lo) || !Number.isFinite(r.hi)) continue;
    if (!best || r.hi - r.lo > best.hi - best.lo) best = r;
  }
  return best;
}

/**
 * Why the head-to-head charts have nothing to show, or null when they do.
 *
 * Three different absences, and they are not interchangeable. The index carrying no rivalries
 * file at all is a build that predates the exporter; a file that would not load is a fault worth
 * quoting verbatim; a file that loaded but has no head-to-head keys is a build from an exporter
 * that did not yet emit them. Only the first two are shared with the pass-conversion card above,
 * which is why this is a separate function rather than a reuse of that metric's `unavailable`.
 *
 * `present` is passed in rather than tested here because the same three sentences serve the
 * section, the ledger and the attack/defence chart; `what` names the missing thing in the
 * reader's language rather than as a JSON key.
 */
function headToHeadUnavailable(
  load: RivalryLoad,
  present: (set: RivalrySet) => boolean,
  what: string,
): Unavailable | null {
  if (load.kind === "absent") {
    return {
      kicker: "no head-to-head data",
      short: "Not in this build — these artifacts predate the rivalry exporter",
      full:
        "No head-to-head data in this build. These sim artifacts were written before the rivalry " +
        "exporter existed, so their index carries no rivalries file at all and there is nothing " +
        "to rank by pair. Nothing is drawn as 0%: a rivalry that was never counted and a rivalry " +
        "in which nobody got past are different claims, and only the second would be in the data. " +
        "Rebuild the artifacts from the repo root — `python scripts/build_sim_data.py --year 2026 " +
        "--all --jobs 0 --fresh` — to produce them. The fitted metrics above come from params.json " +
        "and are unaffected.",
    };
  }
  if (load.kind === "failed") {
    return {
      kicker: "head-to-head unreadable",
      short: "The rivalries artifact in this build could not be read",
      full:
        "This build's index names a rivalries artifact, but it could not be read, so there are no " +
        `head-to-head rates to show: ${load.reason}`,
    };
  }
  if (present(load.set)) return null;
  return {
    kicker: "no head-to-head data",
    short: "This build's rivalries artifact carries no head-to-head counts",
    full:
      `This build's rivalries artifact carries per-circuit conversion rates but no ${what} counts ` +
      "— it was written before the exporter began emitting them. The chart is replaced by this " +
      "notice rather than drawn at 0%, because never counted is not the same claim as counted and " +
      "found to be zero. Re-run the rivalry export to add them; every other chart on this page is " +
      "unaffected.",
  };
}

/**
 * The selected circuits this metric has no row for, as prose ("Hungary and Austria"), or null
 * when every selected circuit is one the metric ranks.
 *
 * Works off the metric's OWN unfiltered rows rather than a hardcoded list, so a metric that
 * gains or loses a circuit in a later build tells the truth without this being updated.
 */
function namedMissingCircuits(
  want: ReadonlySet<string>,
  all: Row[],
  slugByEvent: Map<string, string>,
): string | null {
  const has = new Set(all.map((r) => slugByEvent.get(r.key) ?? ""));
  const names: string[] = [];
  for (const [event, slug] of slugByEvent) {
    if (want.has(slug) && !has.has(slug)) names.push(shortEvent(event));
  }
  if (names.length === 0) return null;
  names.sort();
  if (names.length === 1) return names[0];
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** "British Grand Prix" -> "British". The suffix is on every row and carries no information. */
function shortEvent(event: string): string {
  return event.replace(/\s+Grand Prix$/i, "");
}
