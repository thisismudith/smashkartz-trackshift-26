import type {
  CarState, DashboardRow, DashboardSnapshot, NeutralisationInterval, Provenance, RaceEvent,
} from "../contract/types";
import type { OrderSource } from "./timeline";

export type { OrderSource };

/** A CarState that may carry the replay timeline's order label. Sources that do not
 * produce one (the generated New Race timeline) simply omit it. */
export type OrderedCarState = CarState & { orderSource?: OrderSource };

/** DashboardRow plus the labels. Widening rather than editing the shared contract keeps
 * every existing consumer of DashboardRow/DashboardSnapshot valid. */
export interface OrderedDashboardRow extends DashboardRow {
  /** Which signal decided this row's place: the official classification at the last
   * line crossing, live measured distance within the current lap, or a rule placement.
   * The UI can mark a MEASURED row as provisional -- it is an on-track order the timing
   * tower has not confirmed at a line crossing yet. */
  orderSource: OrderSource;
  /** Where this car's POSITION came from, carried onto the row so a panel can say so
   * without re-deriving it. OBSERVED = projected from a real x/y trace. RULE = a
   * documented placement (grid slot, pit-lane start, parked queue). DERIVED = a
   * position frame B lap, reconstructed from the wheel-speed distance channel because
   * the x/y trace was unusable. */
  positionProvenance: Provenance | null;
  /** The provenance of the NUMBER printed in `gapToLeader`/`interval`, or null when no
   * number is printed. Never "OBSERVED" unless the position it was measured from was
   * itself observed. */
  gapProvenance: Provenance | null;
}

export interface OrderedDashboardSnapshot extends DashboardSnapshot {
  leaderboard: OrderedDashboardRow[];
}

/**
 * The provenances a timing gap may be printed under.
 *
 * A gap is a claim about where two cars are relative to one another, so it may only be
 * printed when the positions behind it came from the same source as the gap itself.
 * OBSERVED qualifies: both sides are the same projected telemetry. SIMULATED qualifies
 * too -- in New Race the position and the gap are both outputs of the same engine and
 * the whole timeline is already flagged SIMULATED, so suppressing the number would hide
 * a self-consistent quantity rather than expose a dishonest one.
 *
 * RULE does not: a grid slot or a parked queue is a placement, and the distance between
 * two placements is not a time. DERIVED and INFERRED do not either: a frame B station
 * is a wheel-speed integral stretched onto a ring, and the instant "the leader passed
 * that station" is not a measurement of this car.
 */
const GAP_BEARING_PROVENANCE: readonly Provenance[] = ["OBSERVED", "SIMULATED"];

/** What a timing column prints instead of a gap when the position behind it is not a
 * measurement. Deliberately not "—": a dash reads as "no data yet", and the truth here
 * is stronger and more specific -- this position is a placement or a reconstruction, so
 * no gap exists to print. The tokens are the contract's own provenance vocabulary
 * (AGENTS.md 13.6) so the leaderboard says the same word the panels do. */
const UNMEASURED_TOKEN: Record<Exclude<Provenance, "OBSERVED">, string> = {
  RULE: "PLACED",
  DERIVED: "DERIVED",
  INFERRED: "INFERRED",
  SIMULATED: "SIM",
  DEFAULT: "DEFAULT",
};

function bearsGap(car: CarState): boolean {
  // A null provenance is an ABSENT position, so it can never bear a gap -- `includes`
  // on a Provenance[] would reject it anyway, but stating it is clearer than relying on
  // that.
  return car.positionProvenance !== null
    && GAP_BEARING_PROVENANCE.includes(car.positionProvenance);
}

function unmeasuredToken(p: Provenance | null): string {
  // Not "—": a dash reads as "no data yet". NO POS says the producer looked, found
  // nothing it could stand behind, and withdrew the position rather than invent one.
  if (p === null) return "NO POS";
  return p === "OBSERVED" ? "—" : UNMEASURED_TOKEN[p];
}

/** Placements that carry no deficit of any kind. A car on its grid slot has not started
 * and a car parked at the side of the road is not losing laps to a leader still
 * circulating -- the lap counter simply keeps running without it. Measured on the built
 * Monaco race pack, retired cars reach "+78 LAPS" at the flag of a 78-lap race, a number
 * with no referent. Both are stated as what they are instead. */
function placementToken(car: CarState): string | null {
  if (car.status === "grid") return "GRID";
  if (car.status === "retired") return "OUT";
  return null;
}

function fmtGap(car: CarState): string {
  // Placements first: "—" and "this number does not exist" are different statements.
  const placed = placementToken(car);
  if (placed) return placed;
  if (car.lapsDownFromLeader > 0) return `+${car.lapsDownFromLeader} LAP${car.lapsDownFromLeader > 1 ? "S" : ""}`;
  if (car.inPit) return "PIT";
  if (car.position === 1) return "LEADER";
  // Before any number: a gap computed from a placed or reconstructed position would
  // dress a rule or a wheel-speed integral up as timing data.
  if (!bearsGap(car)) return unmeasuredToken(car.positionProvenance);
  if (car.gapToLeaderS === null) return "—";
  return `+${car.gapToLeaderS.toFixed(1)}`;
}

function fmtInterval(car: CarState): string {
  const placed = placementToken(car);
  if (placed) return placed;
  if (car.position === 1) return "—";
  if (car.inPit) return "PIT";
  if (!bearsGap(car)) return unmeasuredToken(car.positionProvenance);
  if (car.intervalS === null) return "—";
  return `+${car.intervalS.toFixed(1)}`;
}

/** The provenance of the printed gap/interval number, or null when no number is
 * printed. This is what lets a panel state the basis of a figure it is showing without
 * re-deriving the formatter's decision. */
function gapProvenanceOf(car: CarState): Provenance | null {
  if (!bearsGap(car)) return null;
  if (placementToken(car) !== null || car.inPit || car.lapsDownFromLeader > 0) return null;
  if (car.gapToLeaderS === null && car.intervalS === null) return null;
  return car.positionProvenance;
}

/** How to describe a car's position provenance to a viewer. Returned as data, not JSX,
 * so the wording is unit-testable: the panels only render it. */
export function describePositionProvenance(
  p: Provenance | null,
): { label: string; note: string } {
  // null is ABSENCE, not a provenance. Handled before the switch so it can never fall
  // through to "Default", which would claim a fallback value exists when none does.
  if (p === null) {
    return {
      label: "No position",
      note: "The feed carries no usable position for this car at this instant, so none is"
        + " shown. It is not placed anywhere, and no gap is computed from it.",
    };
  }
  switch (p) {
    case "OBSERVED":
      return {
        label: "Measured",
        note: "Position projected from this lap's own x/y trace. Gaps and intervals are"
          + " measured against it.",
      };
    case "RULE":
      return {
        label: "Placed",
        note: "Position is a documented placement the feed does not contain -- a grid"
          + " slot, a pit-lane start or the parked queue. No gap is computed from it,"
          + " because the distance between two placements is not a time.",
      };
    case "DERIVED":
      return {
        label: "Derived",
        note: "No usable x/y trace for this lap (position frame B). The station is the"
          + " car's own wheel-speed distance channel stretched onto the ring, and the"
          + " lateral offset is zero rather than measured. No gap is computed from it.",
      };
    case "INFERRED":
      return {
        label: "Inferred",
        note: "Position reconstructed from a model rather than read from the feed. No"
          + " gap is computed from it.",
      };
    case "SIMULATED":
      return {
        label: "Simulated",
        note: "Position produced by the race engine, not by a measurement. Gaps come"
          + " from the same engine and are consistent with it.",
      };
    default:
      return {
        label: "Default",
        note: "Position fell back to a default. It is not a measurement and no gap is"
          + " computed from it.",
      };
  }
}

// ---------------------------------------------------------------------------
// Energy meters
//
// These live here rather than in DriverPanel.tsx because vitest only collects
// `src/**/*.test.ts` -- logic inside a .tsx cannot be tested at all. The panel renders
// what these return and makes no judgement of its own.
// ---------------------------------------------------------------------------

/** Bands, not a gradient, because the limits themselves are unverified placeholders --
 * a smooth scale would imply a precision the numbers do not have.
 *
 * "unclosed" is the fifth state and the important one: the value is past its limit, but
 * that limit is flagged `verified:false` in the rule engine. Exceeding an unsourced
 * placeholder is the reconstruction failing to close, not a competitor breaking a rule,
 * and it must not be painted in the same red as a real breach. */
export type MeterLevel = "low" | "mid" | "high" | "over" | "unclosed";

export interface MeterState {
  /** `data-level` for the CSS. "unclosed" is deliberately not styled by
   * panels.module.css, so the number stays white instead of turning red. */
  level: MeterLevel | undefined;
  /** Bar fill, 0..100. Pinned at 100 when the value is past the limit. */
  fillPct: number;
  /** Fraction of the limit the value uses, unpinned, so an overshoot can be stated
   * exactly. null when there is no usable limit. */
  fraction: number | null;
  /** How far past the limit the value sits, in the meter's own unit. null when within
   * the limit or when there is no limit. */
  excess: number | null;
  /** true when `excess` is measured against a limit the rule engine flags
   * verified:false -- i.e. the model does not close, rather than a rule being broken. */
  modelDoesNotClose: boolean;
}

const INVERTED: Record<"low" | "mid" | "high", MeterLevel> = {
  low: "over", mid: "high", high: "mid",
};

/**
 * Where a value sits against its regulation limit.
 *
 * @param verified whether the rule engine traced the limit to a regulation. Defaults to
 *   true so a caller that genuinely has a sourced number does not have to say so; every
 *   2026 energy budget in rules.py is currently false.
 * @param invert true when a LOW value is the concerning one (a nearly empty store).
 */
export function meterState(
  value: number,
  limit: number | null,
  opts: { verified?: boolean; invert?: boolean } = {},
): MeterState {
  const v = Math.abs(value);
  if (limit === null || !Number.isFinite(limit) || limit <= 0 || !Number.isFinite(v)) {
    return { level: undefined, fillPct: 0, fraction: null, excess: null, modelDoesNotClose: false };
  }
  const fraction = v / limit;
  const verified = opts.verified !== false;
  const over = fraction > 1;

  let level: MeterLevel;
  if (over) level = verified ? "over" : "unclosed";
  else if (fraction >= 0.66) level = "high";
  else if (fraction >= 0.33) level = "mid";
  else level = "low";

  // Inverting swaps the comfortable end for the alarming one -- but an OVERSHOOT stays
  // an overshoot. The previous mapping sent "over" to "low", which painted a store
  // holding more than its own capacity in the comfortable colour.
  if (opts.invert && level !== "over" && level !== "unclosed") {
    level = INVERTED[level as "low" | "mid" | "high"];
  }

  return {
    level,
    fillPct: Math.min(100, fraction * 100),
    fraction,
    excess: over ? v - limit : null,
    modelDoesNotClose: over && !verified,
  };
}

// ---------------------------------------------------------------------------
// Session-level position integrity
// ---------------------------------------------------------------------------

/**
 * What the Python build reports about the positions it could and could not measure in
 * this session.
 *
 * Every field is nullable and null means UNKNOWN -- this build did not report it. An
 * absent count is never read as zero: "no positions were withdrawn" and "nobody
 * counted" are different statements and the panel prints them differently.
 */
/**
 * The manifest's `positionIntegrity` block, named EXACTLY as scripts/simdata/replay.py
 * writes it.
 *
 * This interface originally used its own invented names (positionsWithdrawn,
 * positionSamples, derivedFrameLaps, totalLaps) against a producer that emits
 * samplesPositionAbsent, samples, lapsFrameB and three lap counters. The result was a
 * readout that could never display anything: the numbers sat in the manifest and the
 * panel printed "unknown" forever. The wire shape is the contract, so the consumer
 * follows the producer rather than the other way round. Every field is optional because
 * a pack built before this block existed simply has none -- and absent must read as
 * "unknown", never as 0.
 */
export interface SessionPositionIntegrity {
  /** Total position samples in the session: the denominator for the counts below. */
  samples?: number | null;
  /** Samples with NO position at all -- the producer refused to place them rather than
   * invent a coordinate. Includes both sentinel withdrawals and rejected projections. */
  samplesPositionAbsent?: number | null;
  /** The subset withdrawn because the feed had frozen on a session sentinel. */
  samplesSentinelWithdrawn?: number | null;
  /** The subset rejected for projecting implausibly far from the racing line. */
  samplesLateralRejected?: number | null;
  /** Laps whose station came from a measured x/y trace. */
  lapsFrameA?: number | null;
  /** Laps placed by the derived distance frame rather than a measured trace. */
  lapsFrameB?: number | null;
  /** Laps with neither a usable position channel nor a usable distance channel. */
  lapsFrameNone?: number | null;
}

/** Laps across all three frames, or null when the producer reported none of them. */
function totalLaps(i: SessionPositionIntegrity): number | null {
  const parts = [i.lapsFrameA, i.lapsFrameB, i.lapsFrameNone]
    .filter((v): v is number => typeof v === "number");
  return parts.length ? parts.reduce((a, b) => a + b, 0) : null;
}

export interface IntegrityLine {
  label: string;
  value: string;
  /** true when the line reports a real loss of measurement, so the panel can mark it.
   * An honest "unknown" is not an alert. */
  alert: boolean;
}

/** 12345 -> "12 345". Grouped by hand rather than through toLocaleString so the string
 * does not depend on the runtime's locale data. */
function groupDigits(n: number): string {
  const s = Math.round(n).toString();
  let out = "";
  for (let i = 0; i < s.length; i++) {
    if (i > 0 && (s.length - i) % 3 === 0) out += " "; // thin space
    out += s[i];
  }
  return out;
}

function ratioLine(
  label: string, count: number | null, total: number | null, noun: string,
): IntegrityLine {
  if (count === null) return { label, value: "unknown", alert: false };
  if (count === 0) return { label, value: "none", alert: false };
  const pct = total !== null && total > 0 ? ` (${(count / total * 100).toFixed(1)}%)` : "";
  return { label, value: `${groupDigits(count)} ${noun}${pct}`, alert: true };
}

/** The session's position-integrity lines. Absent counts print "unknown", never "0". */
export function describePositionIntegrity(
  integrity: SessionPositionIntegrity | null | undefined,
): IntegrityLine[] {
  const i: SessionPositionIntegrity = integrity ?? {};
  const laps = totalLaps(i);
  return [
    ratioLine("No position", i.samplesPositionAbsent ?? null, i.samples ?? null, "samples"),
    ratioLine("Derived laps", i.lapsFrameB ?? null, laps, "laps"),
    ratioLine("Unplaceable laps", i.lapsFrameNone ?? null, laps, "laps"),
  ];
}

/** true when nothing about this session's position integrity is known, so the panel can
 * explain the silence instead of implying a clean session. */
export function positionIntegrityUnknown(
  integrity: SessionPositionIntegrity | null | undefined,
): boolean {
  if (!integrity) return true;
  return [integrity.samplesPositionAbsent, integrity.lapsFrameB, integrity.lapsFrameNone]
    .every((v) => typeof v !== "number");
}

export function buildDashboardSnapshot(
  states: Map<string, OrderedCarState>,
  sessionTime: number,
  events: RaceEvent[],
  neutralisations: NeutralisationInterval[],
  recentWindowS = 30,
): OrderedDashboardSnapshot {
  const leaderboard: OrderedDashboardRow[] = [...states.values()]
    .sort((a, b) => a.position - b.position)
    .map((car) => ({
      position: car.position,
      // A source that does not label its order still gets an honest label: a measured
      // position means the row was placed by measurement, a placed one by rule.
      orderSource: car.orderSource
        ?? (car.positionProvenance === "OBSERVED" ? "MEASURED" : "RULE"),
      positionProvenance: car.positionProvenance,
      gapProvenance: gapProvenanceOf(car),
      driver: car.driver,
      team: car.team,
      gapToLeader: fmtGap(car),
      interval: fmtInterval(car),
      compound: car.tyreCompound,
      tyreLife: car.tyreLife,
      status: car.status,
      lastLapS: null,
      energy: car.energy,
      speedKph: car.speedKph,
      gear: car.gear,
      throttlePct: car.throttlePct,
      brake: car.brake,
      lapProgress: car.lapProgress,
    }));

  const activeNeutralisation = neutralisations.find(
    (n) => n.start <= sessionTime && sessionTime <= n.end,
  ) ?? null;

  const recentEvents = events
    .filter((e) => e.sessionTime <= sessionTime && e.sessionTime > sessionTime - recentWindowS)
    .sort((a, b) => b.sessionTime - a.sessionTime);

  return { sessionTime, leaderboard, activeNeutralisation, recentEvents };
}
