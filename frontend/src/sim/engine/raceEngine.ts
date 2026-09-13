/**
 * The New Race generator: a deterministic, seeded, per-lap simulation loop. Every
 * car's lap time is composed from the fitted parameters (lapModel.ts), split into
 * three sector times, and the field advances lap by lap re-running each car's pit
 * decision whenever a neutralisation opens (the plan's key strategic behaviour: 23%
 * of real stops happened inside one). Output is the SAME shape as a replay session's
 * lap table (lST/sesT/compound/stint/life/pin/pout/status), so GeneratedTimeline can
 * reuse the exact station/gap/tower logic pattern the replay adapter uses.
 *
 * It also lays out the STANDING START. Every number in that block is read from the
 * `standingStart` block of params.json (fitted in scripts/simdata/launch.py from the
 * 2026 feed); nothing about a grid, a launch or a lap-1 penalty is derived here.
 */
import type { Provenance, TrackModel } from "../contract/types";
import { launchTimeLossS, type LaunchKinematics } from "../motion/profileWarp";
// The car's true length, 5.6 m, from the ONE place it is defined. It is the physical
// non-interpenetration gap the launch is run with: two solid bodies cannot share a metre
// of track, so this is geometry, not a tuned parameter.
import { CAR_RENDER_LENGTH_M } from "../render/presentation";
import { composeLapTime, splitIntoSectors } from "./lapModel";
import type { FittedParams } from "./params";
import { mulberry32 } from "./prng";

export interface RaceConfig {
  track: TrackModel;
  params: FittedParams;
  /** IN GRID ORDER: index 0 is pole. That is already how the configurator sends them. */
  entries: { driver: string; team: string | null }[];
  totalLaps: number;
  seed: number;
  pitLapWindow?: [number, number]; // laps, inclusive; default a third to two-thirds distance
}

export interface GeneratedLap {
  lap: number;
  lST: number;
  sesT: number;
  s1: number; s2: number; s3: number;
  compound: "SOFT" | "MEDIUM" | "HARD";
  stint: number;
  life: number;
  pin: number | null;
  pout: number | null;
  status: "1" | "4" | "6"; // green / SC / VSC, enough for the dashboard and gaps
}

export interface GeneratedDriverResult {
  driver: string;
  team: string | null;
  laps: GeneratedLap[];
}

// --------------------------------------------------------------------------------------
// The shipped standingStart block, as read (never as re-derived).
//
// These interfaces describe params.json's OWN shape. They are declared here rather than
// in params.ts because that file is not this task's to edit; the typed `standingStart`
// field belongs on FittedParams and should move there.  A leaf's `value` is
// `number | null` on purpose: launch.py emits null for a quantity the feed cannot
// support (the lateral stagger, an unresolved session's pitch), and null must survive
// as null all the way to the engine.
// --------------------------------------------------------------------------------------

export interface NumericLeaf {
  value: number | null;
  se?: number;
  ci95?: [number, number];
  n?: number;
  provenance: string;
  note?: string;
  source?: string;
}

export interface StandingStartBlock {
  grid: {
    slotPitchMetres: NumericLeaf;
    perSessionPitchMetres?: Record<string, NumericLeaf>;
    anchorMetresPastTimingLine: {
      provenance: string;
      definition?: string;
      perTrack: Record<string, NumericLeaf>;
      summary?: NumericLeaf;
    };
    lateralStagger: {
      columnSign: { value: string; provenance: string; note?: string; source?: string };
      offsetMetres: NumericLeaf;
    };
  };
  launch: {
    accelerationMps2: NumericLeaf;
    reactionSeconds: NumericLeaf;
    populationSigma: { accelerationMps2: NumericLeaf; reactionSeconds: NumericLeaf };
    perDriver?: Record<string, { accelMps2: NumericLeaf; reactionSeconds: NumericLeaf }>;
    validToSpeedKph: NumericLeaf;
  };
  lap1: {
    /** MEAN over the whole grid (n=167), NOT the value at pole. Read for display only:
     * using it as the slot-1 intercept and then adding the full per-slot slope on top is
     * the double count `excessSecondsAtPole` exists to end. */
    excessSecondsVsCleanLap: NumericLeaf;
    /** The intercept: the excess at grid slot 1, i.e. excessSecondsVsCleanLap minus
     * excessSecondsPerGridSlot * (meanGridSlot - 1). Produced by scripts/simdata, never
     * re-derived here -- this side has no access to the sample's mean grid rank and
     * would have to invent one. */
    excessSecondsAtPole?: NumericLeaf;
    /** The mean grid rank of the excess sample, which is what makes the intercept above
     * meaningful. Carried for display; nothing here computes with it. */
    meanGridSlot?: NumericLeaf;
    excessSecondsPerGridSlot: NumericLeaf;
    penaltySplit: { launchLossSeconds: NumericLeaf; remainderSeconds: NumericLeaf };
  };
  unavailable?: Record<string, string[]>;
}

/** params.json carries `standingStart`; FittedParams does not yet declare it. */
type ParamsWithStandingStart = FittedParams & { standingStart?: StandingStartBlock };

export interface GridPlacement {
  driver: string;
  /** 1-based; 1 is pole. */
  slot: number;
  /** Signed metres past the timing line: anchor - (slot - 1) * pitch (launch.slot_offset_m). */
  offsetM: number;
  /** Ring station of the box, wrapped into [0, lengthMetres). */
  stationM: number;
  /** One ring minus the head start (launch.lap1_distance_m). */
  lap1DistanceM: number;
  launch: LaunchKinematics;
  /** "perDriver" = this driver's own fitted launch; "populationDraw" = the pooled median
   * plus a draw from standingStart.launch.populationSigma, because the fit never saw him. */
  launchSource: "perDriver" | "populationDraw";
  /** +1 / -1 by slot parity, pole on +1. RULE: the two-column STRUCTURE is a regulation
   * fact. The metric offset is NOT -- see StandingStart.lateralStagger. */
  lateralColumnSign: 1 | -1;
  /** Seconds this launch costs against a car already at handover speed (launch.py's
   * launch_time_loss_s). Reproduced by running the launch, so it is not added twice. */
  launchLossSeconds: number;
}

export interface StandingStart {
  placements: GridPlacement[];
  byDriver: Map<string, GridPlacement>;
  pitchMetres: number;
  /** Which leaf the pitch came from, so a reader can see whether this circuit resolved
   * its own boxes or is standing on the pooled 208-car figure. */
  pitchSource: string;
  /** Signed metres past the timing line of the front occupied box, or null when the feed
   * carries no grid for this circuit at all. NEVER the artifact's `summary` median: that
   * is a summary of ten circuits spanning -32 m to +291 m and is wrong everywhere. */
  anchorMetres: number | null;
  /** DERIVED when the anchor was measured for this circuit; DEFAULT when it was not and
   * the boxes are laid out from the line instead; the spacing and order stay DERIVED. */
  placementProvenance: Provenance;
  /** The artifact's own words for why this circuit has no anchor, verbatim. */
  anchorUnavailable: string[] | null;
  /** Handover speed, m/s. Equal to standingStart.launch.validToSpeedKph / 3.6. */
  handoverSpeedMps: number;
  validToSpeedKph: number;
  /** Hard non-interpenetration gap used through the launch, metres. */
  minGapMetres: number;
  /** The two-column stagger, RULE, with its metric offset still null. */
  lateralStagger: {
    columnSign: string;
    offsetMetres: null;
    provenance: Provenance;
    note: string;
  };
  lap1: {
    /** standingStart.lap1.excessSecondsAtPole, as shipped: the lap-1 excess at grid slot
     * 1. THE INTERCEPT OF THE SHIPPED LINEAR MODEL, and the reason this block exists --
     * the engine used to use the whole-grid MEAN (9.009 s) here and then add the full
     * per-slot slope on top of it, handing every car in every race about +3.4 s it had
     * already been charged once. */
    excessAtPoleSeconds: number;
    /** standingStart.lap1.meanGridSlot: the mean grid rank the intercept above was
     * shifted from. Null when the artifact carries none; nothing computes with it. */
    meanGridSlot: number | null;
    /** standingStart.lap1.penaltySplit.launchLossSeconds, as shipped -- the POOLED launch
     * loss that is already inside the intercept above. */
    pooledLaunchLossSeconds: number;
    /** excessAtPoleSeconds - pooledLaunchLossSeconds: what is left of the pole car's
     * excess once the launch is accounted for (first-corner congestion, cold tyres, a
     * full fuel load). This is the part added to lap 1, because each car's OWN fitted
     * launch loss is added separately -- the artifact's pooled figure would throw away
     * the per-driver launch the same block ships. */
    remainderAtPoleSeconds: number;
    /** standingStart.lap1.penaltySplit.remainderSeconds, as shipped. Carried for display
     * so a reader can see it is the remainder at the sample's MEAN grid slot, not at
     * pole, and therefore is not what gets applied. */
    shippedRemainderSeconds: number;
    /** standingStart.lap1.excessSecondsPerGridSlot, as shipped. */
    excessPerGridSlotSeconds: number;
    /** pitch / lapAverageSpeed: the part of the above the engine already produces by
     * driving each car its own lap-1 distance. */
    geometricPerGridSlotSeconds: number;
    /** What is actually applied per slot: the shipped slope minus the geometric term.
     * NOT clamped at zero: if the circuit's geometry alone exceeded the measured slope
     * that would be a real disagreement between two measurements, and clamping it would
     * turn it into a silent "no penalty". */
    appliedPerGridSlotSeconds: number;
  };
}

export interface GeneratedRaceResult {
  runId: string;
  seed: number;
  entries: GeneratedDriverResult[];
  neutralisations: { kind: "SC" | "VSC"; startLap: number; endLap: number }[];
  totalLaps: number;
  duration: number;
  /** The grid this race started from, or null when params.json carries no standingStart
   * block at all -- in which case `standingStartRefusal` says so and the timeline falls
   * back to a flying lap 1 rather than inventing a grid. */
  standingStart: StandingStart | null;
  standingStartRefusal: string | null;
}

const COMPOUND_SEQUENCE: Array<"SOFT" | "MEDIUM" | "HARD"> = ["MEDIUM", "HARD"];

function leafNumber(leaf: NumericLeaf | undefined): number | null {
  return leaf && typeof leaf.value === "number" && Number.isFinite(leaf.value) ? leaf.value : null;
}

/** Box-Muller, from the same uniform PRNG the lap model uses. */
function gaussian(rng: () => number): number {
  const u1 = Math.max(1e-9, rng());
  const u2 = rng();
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
}

/**
 * Lays out the grid and each car's launch from the shipped block. Pure apart from the
 * seeded `rng`, which is only ever used to DRAW a launch for a driver the fit never saw.
 *
 * Returns null (with a reason) rather than a plausible grid whenever params.json has no
 * standingStart block.
 */
export function buildStandingStart(
  track: TrackModel,
  params: FittedParams,
  entries: { driver: string; team: string | null }[],
  rng: () => number,
  trackBaseSeconds: number,
): { start: StandingStart | null; refusal: string | null } {
  const block = (params as ParamsWithStandingStart).standingStart;
  if (!block?.grid || !block.launch) {
    return {
      start: null,
      refusal: "params.json carries no standingStart block: this artifact predates "
        + "scripts/simdata/launch.py, so no grid, launch or lap-1 penalty is available",
    };
  }

  const perSession = block.grid.perSessionPitchMetres?.[track.event];
  const perSessionPitch = leafNumber(perSession);
  const pooledPitch = leafNumber(block.grid.slotPitchMetres);
  const pitchMetres = perSessionPitch ?? pooledPitch;
  if (pitchMetres === null || !(pitchMetres > 0)) {
    return {
      start: null,
      refusal: "standingStart.grid.slotPitchMetres is unavailable, so the boxes have no "
        + "measured spacing and no grid is laid out",
    };
  }
  const pitchSource = perSessionPitch !== null
    ? `grid.perSessionPitchMetres["${track.event}"] (n=${perSession?.n ?? "?"})`
    : `grid.slotPitchMetres (pooled, n=${block.grid.slotPitchMetres.n ?? "?"})`;

  const anchorLeaf = block.grid.anchorMetresPastTimingLine?.perTrack?.[track.event];
  const measuredAnchor = leafNumber(anchorLeaf);
  const anchorUnavailable = measuredAnchor === null
    ? (block.unavailable?.[track.event] ?? [
      `standingStart.grid.anchorMetresPastTimingLine.perTrack has no entry for `
      + `"${track.event}"`,
    ])
    : null;
  // The anchor is a PER-CIRCUIT fact (-32.2 m at Canadian, +291.1 m at Italian); the
  // artifact's `summary` median is explicitly a summary and wrong everywhere, so it is
  // never substituted. Where the feed has none, the boxes are still laid out with the
  // FITTED pitch in grid order -- only their position along the lap becomes a documented
  // DEFAULT (pole's box on the timing line) instead of a measurement.
  const anchorM = measuredAnchor ?? 0;
  const placementProvenance: Provenance = measuredAnchor === null ? "DEFAULT" : "DERIVED";

  const validToSpeedKph = leafNumber(block.launch.validToSpeedKph);
  if (validToSpeedKph === null || !(validToSpeedKph > 0)) {
    return {
      start: null,
      refusal: "standingStart.launch.validToSpeedKph is unavailable, so there is no "
        + "speed at which the fitted launch legitimately ends",
    };
  }
  const handoverSpeedMps = validToSpeedKph / 3.6;

  const pooledAccel = leafNumber(block.launch.accelerationMps2);
  const pooledReaction = leafNumber(block.launch.reactionSeconds);
  if (pooledAccel === null || !(pooledAccel > 0) || pooledReaction === null) {
    return {
      start: null,
      refusal: "standingStart.launch has no fitted acceleration/reaction, so no car can "
        + "be launched from rest",
    };
  }
  // The car-to-car spread, NOT the SE of the median: drawing from the SE would give a
  // field of identical launches, which is the one thing the fitted spread rules out.
  const sigmaAccel = leafNumber(block.launch.populationSigma?.accelerationMps2) ?? 0;
  const sigmaReaction = leafNumber(block.launch.populationSigma?.reactionSeconds) ?? 0;

  const L = track.lengthMetres;
  const placements: GridPlacement[] = entries.map((e, i) => {
    const slot = i + 1;
    const own = block.launch.perDriver?.[e.driver];
    const ownAccel = leafNumber(own?.accelMps2);
    const ownReaction = leafNumber(own?.reactionSeconds);
    let accelMps2: number, reactionS: number;
    let launchSource: GridPlacement["launchSource"];
    if (ownAccel !== null && ownAccel > 0 && ownReaction !== null) {
      accelMps2 = ownAccel;
      reactionS = ownReaction;
      launchSource = "perDriver";
    } else {
      // A draw can land on a non-positive acceleration or reaction, neither of which is
      // a launch; those fall back to the fitted median rather than being clipped to a
      // made-up floor.
      const drawnAccel = pooledAccel + gaussian(rng) * sigmaAccel;
      const drawnReaction = pooledReaction + gaussian(rng) * sigmaReaction;
      accelMps2 = drawnAccel > 0 ? drawnAccel : pooledAccel;
      reactionS = drawnReaction >= 0 ? drawnReaction : pooledReaction;
      launchSource = "populationDraw";
    }
    const launch: LaunchKinematics = { reactionS, accelMps2, handoverSpeedMps };
    const offsetM = anchorM - (slot - 1) * pitchMetres;
    return {
      driver: e.driver,
      slot,
      offsetM,
      stationM: ((offsetM % L) + L) % L,
      lap1DistanceM: L - offsetM,
      launch,
      launchSource,
      lateralColumnSign: slot % 2 === 1 ? 1 : -1,
      launchLossSeconds: launchTimeLossS(launch),
    };
  });

  // Every one of these is required, and every one REFUSES rather than falling back: a
  // `?? 0` here does not mean "no measurement", it means "no lap-1 penalty", which is a
  // plausible-looking answer to a question the artifact did not answer. AGENTS.md 42.5.
  const excessAtPoleSeconds = leafNumber(block.lap1?.excessSecondsAtPole);
  if (excessAtPoleSeconds === null) {
    return {
      start: null,
      refusal: "standingStart.lap1.excessSecondsAtPole is unavailable, so the lap-1 excess "
        + "has no intercept at grid slot 1. It is NOT substituted with "
        + "excessSecondsVsCleanLap, which is the mean over the whole grid and would be "
        + "charged again by the per-slot slope. Rebuild the sim artifacts "
        + "(scripts/build_sim_data.py) against a scripts/simdata that ships it",
    };
  }
  const pooledLaunchLossSeconds = leafNumber(block.lap1?.penaltySplit?.launchLossSeconds);
  if (pooledLaunchLossSeconds === null) {
    return {
      start: null,
      refusal: "standingStart.lap1.penaltySplit.launchLossSeconds is unavailable, so the "
        + "part of the pole car's excess that its own launch already accounts for cannot "
        + "be separated from the part that must be added",
    };
  }
  const shippedRemainderSeconds = leafNumber(block.lap1?.penaltySplit?.remainderSeconds);
  if (shippedRemainderSeconds === null) {
    return {
      start: null,
      refusal: "standingStart.lap1.penaltySplit.remainderSeconds is unavailable, so the "
        + "shipped penalty split is incomplete",
    };
  }
  const excessPerGridSlotSeconds = leafNumber(block.lap1?.excessSecondsPerGridSlot);
  if (excessPerGridSlotSeconds === null) {
    return {
      start: null,
      refusal: "standingStart.lap1.excessSecondsPerGridSlot is unavailable, so how much "
        + "lap 1 costs further down the grid is not measured and no slope is applied",
    };
  }
  // The shipped slope contains a purely geometric part -- slot k drives (k-1) * pitch
  // further -- which this engine already produces by advancing each car over its own
  // lap-1 distance. The artifact's own note says to subtract it rather than pay it twice.
  const lapAverageSpeed = L / Math.max(1e-6, trackBaseSeconds);
  const geometricPerGridSlotSeconds = pitchMetres / lapAverageSpeed;
  const appliedPerGridSlotSeconds = excessPerGridSlotSeconds - geometricPerGridSlotSeconds;
  // The intercept carries the POOLED launch loss inside it; each car's OWN launch loss is
  // added separately in standingLapOneTime, so the pooled one comes back out here. What
  // is left is the pole car's congestion/cold-tyre/fuel excess.
  const remainderAtPoleSeconds = excessAtPoleSeconds - pooledLaunchLossSeconds;

  return {
    start: {
      placements,
      byDriver: new Map(placements.map((p) => [p.driver, p])),
      pitchMetres,
      pitchSource,
      anchorMetres: measuredAnchor,
      placementProvenance,
      anchorUnavailable,
      handoverSpeedMps,
      validToSpeedKph,
      minGapMetres: CAR_RENDER_LENGTH_M,
      lateralStagger: {
        columnSign: block.grid.lateralStagger?.columnSign?.value
          ?? "alternating +1 / -1 by slot parity, pole on +1",
        // Stays null: the feed snaps every stationary car to the centreline (median
        // per-session lateral SD 0.19 m over 208 cars), which is evidence the offset is
        // ABSENT, not that it is zero.
        offsetMetres: null,
        provenance: "RULE",
        note: block.grid.lateralStagger?.offsetMetres?.note
          ?? "the two-column structure is a regulation fact; the metric offset is not in "
          + "the feed and ships as null",
      },
      lap1: {
        excessAtPoleSeconds,
        meanGridSlot: leafNumber(block.lap1?.meanGridSlot),
        pooledLaunchLossSeconds,
        remainderAtPoleSeconds,
        shippedRemainderSeconds,
        excessPerGridSlotSeconds,
        geometricPerGridSlotSeconds,
        appliedPerGridSlotSeconds,
      },
    },
    refusal: null,
  };
}

export function runRace(config: RaceConfig): GeneratedRaceResult {
  const { track, params, entries, totalLaps, seed } = config;
  const rng = mulberry32(seed);
  const baseSeconds = trackBaseSecondsFor(track);
  const [pitLo, pitHi] = config.pitLapWindow ?? [
    Math.round(totalLaps * 0.35), Math.round(totalLaps * 0.65),
  ];

  const { start: standingStart, refusal: standingStartRefusal } =
    buildStandingStart(track, params, entries, rng, baseSeconds);

  const pitLap = new Map<string, number>();
  for (const e of entries) {
    pitLap.set(e.driver, pitLo + Math.floor(rng() * Math.max(1, pitHi - pitLo)));
  }

  const results = new Map<string, GeneratedDriverResult>();
  for (const e of entries) results.set(e.driver, { driver: e.driver, team: e.team, laps: [] });

  const lastSesT = new Map<string, number>(entries.map((e) => [e.driver, 0]));
  const neutralIntervals: GeneratedRaceResult["neutralisations"] = [];
  let openNeutral: { kind: "SC" | "VSC"; startLap: number } | null = null;

  const scHazard = params.neutralisation.safetyCarLapHazard.value;
  const vscHazard = params.neutralisation.virtualSafetyCarLapHazard.value;

  for (let lap = 1; lap <= totalLaps; lap++) {
    // one field-wide neutralisation roll per lap (never open two at once)
    let thisLapNeutral: "SC" | "VSC" | null = null;
    if (openNeutral) {
      thisLapNeutral = openNeutral.kind;
      if (rng() < 0.3) { // ~3-lap average duration
        neutralIntervals.push({ kind: openNeutral.kind, startLap: openNeutral.startLap, endLap: lap });
        openNeutral = null;
      }
    } else if (rng() < scHazard) {
      openNeutral = { kind: "SC", startLap: lap };
      thisLapNeutral = "SC";
    } else if (rng() < vscHazard) {
      openNeutral = { kind: "VSC", startLap: lap };
      thisLapNeutral = "VSC";
    }

    // order at the start of this lap, by cumulative session time, for proximity
    const order = [...lastSesT.entries()].sort((a, b) => a[1] - b[1]);
    const gapAhead = new Map<string, number | null>();
    for (let i = 0; i < order.length; i++) {
      gapAhead.set(order[i][0], i === 0 ? null : order[i][1] - order[i - 1][1]);
    }

    for (const e of entries) {
      const driverResult = results.get(e.driver)!;
      const priorLaps = driverResult.laps;
      const stint = priorLaps.length ? priorLaps[priorLaps.length - 1].stint : 1;
      const life = priorLaps.length
        ? (priorLaps[priorLaps.length - 1].pin !== null ? 1 : priorLaps[priorLaps.length - 1].life + 1)
        : 1;
      const compound = priorLaps.length
        ? (priorLaps[priorLaps.length - 1].pin !== null
          ? COMPOUND_SEQUENCE[Math.min(COMPOUND_SEQUENCE.length - 1, stint)]
          : priorLaps[priorLaps.length - 1].compound)
        : COMPOUND_SEQUENCE[0];

      const isPitLap = lap === pitLap.get(e.driver);
      let pitLossThisLap = 0;
      if (isPitLap) {
        // Keyed by track.event (the display name fit_params.py groups by), same as every
        // other per-track lookup in this file -- track.slug never matches an entry here and
        // was silently forcing every stop, on every track, onto the two literals below.
        const trackPit = params.pitLoss[track.event];
        const green = trackPit?.netLossSeconds.value ?? 22;
        // A stop taken under SC/VSC is typically a measured "free" stop (often a large
        // NEGATIVE net loss vs. green) -- prefer that fitted per-track median; only fall
        // back to a flat discount of the green-flag loss where too few neutralised stops
        // were observed at this track to fit one.
        pitLossThisLap = thisLapNeutral
          ? trackPit?.neutralisedNetLossSeconds?.value ?? green * 0.4
          : green;
      }

      const composed = composeLapTime(
        {
          trackEvent: track.event,
          trackBaseSeconds: baseSeconds,
          driver: e.driver,
          team: e.team,
          lapIndex: lap - 1,
          tyreLifeMinusOne: Math.max(0, life - 1),
          compound,
          gapAheadAtLapStart: gapAhead.get(e.driver) ?? null,
          neutralisation: thisLapNeutral,
          pitLossThisLap,
        },
        params,
        rng,
      );
      const lapTime = lap === 1 && standingStart
        ? standingLapOneTime(composed, standingStart, e.driver, track.lengthMetres)
        : composed;
      const sectors = splitIntoSectors(lapTime, track);
      const start = lastSesT.get(e.driver)!;
      const end = start + lapTime;
      lastSesT.set(e.driver, end);

      driverResult.laps.push({
        lap, lST: start, sesT: end, s1: sectors.s1, s2: sectors.s2, s3: sectors.s3,
        compound, stint: isPitLap ? stint + 1 : stint, life,
        pin: isPitLap ? start + lapTime * 0.4 : null,
        pout: isPitLap ? start + lapTime * 0.4 : null,
        status: thisLapNeutral === "SC" ? "4" : thisLapNeutral === "VSC" ? "6" : "1",
      });
    }
  }
  if (openNeutral) {
    neutralIntervals.push({ kind: openNeutral.kind, startLap: openNeutral.startLap, endLap: totalLaps });
  }

  const duration = Math.max(...[...lastSesT.values()]);
  return {
    runId: `sim:${track.slug}/${entries.length}c/${totalLaps}L/s=${seed}`,
    seed,
    entries: entries.map((e) => results.get(e.driver)!),
    neutralisations: neutralIntervals,
    totalLaps,
    duration,
    standingStart,
    standingStartRefusal,
  };
}

/**
 * Lap 1 from a standing start, in seconds from the signal to this car's line crossing.
 *
 *   clean pace over the car's OWN lap-1 distance   (produces the anchor and the
 *                                                   (slot-1) * pitch geometry)
 * + launch loss                                    (reaction + v / 2a; the engine runs
 *                                                   the launch, so this is the time the
 *                                                   running of it actually costs)
 * + (excessSecondsAtPole - penaltySplit.launchLossSeconds)
 *                                                  (the POLE car's first-corner
 *                                                   congestion, cold tyres and fuel: the
 *                                                   shipped intercept with the pooled
 *                                                   launch loss taken back out, because
 *                                                   the line above already charged this
 *                                                   car's own)
 * + (slot - 1) * (excessSecondsPerGridSlot - pitch / lapAverageSpeed)
 *
 * TWO double counts are being avoided here, and both were real.
 *
 * THE LEVEL. The intercept is `excessSecondsAtPole`, the shipped excess AT GRID SLOT 1.
 * It is emphatically not `excessSecondsVsCleanLap` (9.009 s), which is the mean over the
 * whole grid, n=167, at a mean grid rank of about 10.9 -- so using it as the slot-1
 * intercept and then adding the full slope on top charged every car in the field the
 * slope's whole run down the grid a second time, about +3.4 s each. The correction is
 * mean - slope * (meanGridSlot - 1), and it is made in scripts/simdata, not here: this
 * side never saw the sample and cannot know its mean grid rank.
 *
 * THE GEOMETRY. The slope's own note says it plainly -- part of it is that slot k simply
 * drives (k - 1) x pitch further on lap 1. An engine that advances cars from their own
 * slot stations, as the first line above does, already produces that part, so it is
 * subtracted rather than paid twice.
 */
function standingLapOneTime(
  composed: number, start: StandingStart, driver: string, lengthMetres: number,
): number {
  const p = start.byDriver.get(driver);
  if (!p) return composed;
  return composed * (p.lap1DistanceM / lengthMetres)
    + p.launchLossSeconds
    + start.lap1.remainderAtPoleSeconds
    + (p.slot - 1) * start.lap1.appliedPerGridSlotSeconds;
}

/** The track's own median clean lap time is not currently in params.json (that lives
 * in the catalogue's per-track table, section 5.1) -- as a self-contained fallback
 * here, it is reconstructed from the reference speed profile by integrating ds/v,
 * which is exactly the quantity the plan validated to within 0.25% of the real lap
 * time. This keeps the engine runnable from params.json + the track model alone. */
function trackBaseSecondsFor(track: TrackModel): number {
  const { speedKph, binMetres } = track.referenceProfile;
  let t = 0;
  for (let i = 0; i < speedKph.length; i++) {
    const mps = Math.max(10, speedKph[i]) / 3.6;
    t += binMetres / mps;
  }
  return t;
}
