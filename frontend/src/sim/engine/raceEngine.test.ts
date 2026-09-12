import { describe, expect, it } from "vitest";
import type { TrackModel } from "../contract/types";
import { launchTimeLossS } from "../motion/profileWarp";
import { runRace, type RaceConfig, type StandingStartBlock } from "./raceEngine";
import type { FittedParams } from "./params";

function makeTrack(): TrackModel {
  const length = 5000;
  const nBins = 500;
  const speedKph = new Float32Array(nBins).fill(250);
  const gear = new Uint8Array(nBins).fill(7);
  return {
    slug: "engine-track", event: "Engine GP", lengthMetres: length,
    x: new Float32Array(1), y: new Float32Array(1), z: new Float32Array(1),
    halfWidth: new Float32Array([6]), widthBinMetres: length,
    timingLines: { sf: 0, s1: 1600, s2: 3300 },
    corners: [], grid: { order: [], pitchMetres: 8 },
    pitLanePath: null,
    pitLane: { entryStation: null, exitStation: null, mergeStation: null, loopLateral: null },
    referenceProfile: { binMetres: 10, speedKph, gear },
  };
}

function makeParams(): FittedParams {
  return {
    sessionPaceTrendPerLap: { pooled: { value: -0.05, provenance: "DERIVED" }, perTrack: {} },
    tyreDegradation: {
      trackIndex: { "engine-track": { value: 0.05, provenance: "DERIVED" } },
      compoundMultiplierDefault: {
        SOFT: { value: 1.35, provenance: "DEFAULT" }, MEDIUM: { value: 1.0, provenance: "DEFAULT" },
        HARD: { value: 0.75, provenance: "DEFAULT" },
      },
    },
    driverOffsetSeconds: {
      AAA: { value: -1.2, provenance: "DERIVED" }, BBB: { value: 0.3, provenance: "DERIVED" },
      CCC: { value: 1.1, provenance: "DERIVED" },
    },
    teamOffsetSeconds: {},
    noise: {
      coreSigma: { value: 0.25, provenance: "DERIVED" },
      incidentProbability: { value: 0.05, provenance: "DERIVED" },
      incidentMeanExcessSeconds: { value: 0.8, provenance: "DERIVED" },
    },
    dirtyAirLossPerSecondOfProximity: { "engine-track": { value: 0.1, provenance: "DERIVED" } },
    pitLoss: { "engine-track": { netLossSeconds: { value: 22, provenance: "DERIVED" }, iqr: [20, 24] } },
    neutralisation: {
      safetyCarLapHazard: { value: 0.02, provenance: "DERIVED" },
      virtualSafetyCarLapHazard: { value: 0.03, provenance: "DERIVED" },
      redFlagLapHazard: { value: 0.005, provenance: "DERIVED" },
      perTrackMultiplierDefault: { value: 1, provenance: "DEFAULT" },
    },
    retirement: { perCarPerRace: { value: 0.19, provenance: "DERIVED" }, perLapHazard: { value: 0.003, provenance: "DERIVED" } },
    freshTyreGainSecondsPerLapDefault: { value: 1.0, provenance: "DERIVED" },
    defaultLaps: { formula: "round(305000 / lapLengthMetres)" },
  };
}

const entries = [
  { driver: "AAA", team: "Team A" },
  { driver: "BBB", team: "Team B" },
  { driver: "CCC", team: "Team C" },
];

function baseConfig(seed: number): RaceConfig {
  return { track: makeTrack(), params: makeParams(), entries, totalLaps: 20, seed };
}

describe("runRace", () => {
  it("is fully deterministic for a fixed seed", () => {
    const a = runRace(baseConfig(12345));
    const b = runRace(baseConfig(12345));
    expect(a.runId).toBe(b.runId);
    for (const driver of ["AAA", "BBB", "CCC"]) {
      const la = a.entries.find((e) => e.driver === driver)!.laps;
      const lb = b.entries.find((e) => e.driver === driver)!.laps;
      expect(la).toEqual(lb);
    }
  });

  it("produces a different race for a different seed", () => {
    const a = runRace(baseConfig(1));
    const b = runRace(baseConfig(2));
    const lapsA = a.entries[0].laps.map((l) => l.sesT);
    const lapsB = b.entries[0].laps.map((l) => l.sesT);
    expect(lapsA).not.toEqual(lapsB);
  });

  it("gives every driver exactly totalLaps laps, strictly increasing session time", () => {
    const result = runRace(baseConfig(7));
    for (const entry of result.entries) {
      expect(entry.laps).toHaveLength(20);
      for (let i = 1; i < entry.laps.length; i++) {
        expect(entry.laps[i].sesT).toBeGreaterThan(entry.laps[i - 1].sesT);
        expect(entry.laps[i].lST).toBe(entry.laps[i - 1].sesT); // laps chain exactly
      }
    }
  });

  it("never produces a negative or zero lap time", () => {
    const result = runRace(baseConfig(99));
    for (const entry of result.entries) {
      for (const lap of entry.laps) {
        expect(lap.sesT - lap.lST).toBeGreaterThan(0);
        expect(lap.s1 + lap.s2 + lap.s3).toBeCloseTo(lap.sesT - lap.lST, 6);
      }
    }
  });

  it("gives the faster driver (AAA) a better cumulative time than the slower one (CCC) on average", () => {
    // average over several seeds to average out noise/incidents/neutralisations
    let aaaWins = 0;
    const trials = 15;
    for (let seed = 0; seed < trials; seed++) {
      const result = runRace(baseConfig(seed));
      const aaa = result.entries.find((e) => e.driver === "AAA")!;
      const ccc = result.entries.find((e) => e.driver === "CCC")!;
      if (aaa.laps[aaa.laps.length - 1].sesT < ccc.laps[ccc.laps.length - 1].sesT) aaaWins++;
    }
    expect(aaaWins).toBeGreaterThan(trials * 0.7);
  });

  it("keeps every reported neutralisation interval within the race's lap range", () => {
    // scan many seeds; at least one should trigger a neutralisation given the hazards
    let sawOne = false;
    for (let seed = 0; seed < 30; seed++) {
      const result = runRace(baseConfig(seed));
      for (const n of result.neutralisations) {
        sawOne = true;
        expect(n.startLap).toBeGreaterThanOrEqual(1);
        expect(n.endLap).toBeLessThanOrEqual(result.totalLaps);
        expect(n.endLap).toBeGreaterThanOrEqual(n.startLap);
      }
    }
    expect(sawOne).toBe(true);
  });
});

// --------------------------------------------------------------------------------------
// Standing start. The values below are the ones params.json actually ships under
// `standingStart` (scripts/simdata/launch.py), transcribed so the assertions are about
// what the engine DOES with them, not about the numbers themselves.
// --------------------------------------------------------------------------------------

const ANCHOR_M = 117.12;
const PITCH_M = 8.029;

function leaf(value: number | null, extra: Record<string, unknown> = {}) {
  return { value, provenance: "DERIVED", ...extra };
}

function makeStandingStart(overrides: Partial<StandingStartBlock> = {}): StandingStartBlock {
  return {
    grid: {
      slotPitchMetres: leaf(PITCH_M, { se: 0.031155, n: 208 }),
      perSessionPitchMetres: {},
      anchorMetresPastTimingLine: {
        provenance: "DERIVED",
        perTrack: { "Engine GP": leaf(ANCHOR_M, { se: 1.1, n: 21 }) },
        // deliberately absurd: the engine must never fall back to the summary median,
        // which spans -32 m to +291 m over ten circuits and is wrong at every one
        summary: leaf(151.842986, { n: 5 }),
      },
      lateralStagger: {
        columnSign: {
          value: "alternating +1 / -1 by slot parity, pole on +1", provenance: "RULE",
        },
        offsetMetres: {
          value: null, provenance: "RULE", n: 208,
          note: "NOT IN THE FEED, so null rather than a plausible number",
        },
      },
    },
    launch: {
      accelerationMps2: leaf(9.777419, { se: 0.085227, n: 234 }),
      reactionSeconds: leaf(0.534759, { se: 0.009, n: 234 }),
      populationSigma: {
        accelerationMps2: leaf(1.040228, { n: 234 }),
        reactionSeconds: leaf(0.109971, { n: 234 }),
      },
      perDriver: {
        AAA: { accelMps2: leaf(10.166934, { n: 11 }), reactionSeconds: leaf(0.503865, { n: 11 }) },
        BBB: { accelMps2: leaf(9.614373, { n: 10 }), reactionSeconds: leaf(0.528462, { n: 10 }) },
        CCC: { accelMps2: leaf(9.158508, { n: 11 }), reactionSeconds: leaf(0.584001, { n: 11 }) },
      },
      validToSpeedKph: leaf(120),
    },
    lap1: {
      excessSecondsVsCleanLap: leaf(9.008838, { n: 167 }),
      excessSecondsPerGridSlot: leaf(0.345004, { se: 0.029919, n: 145 }),
      penaltySplit: {
        launchLossSeconds: leaf(2.239367, { n: 234 }),
        remainderSeconds: leaf(6.769471, { se: 1.261133, n: 167 }),
      },
    },
    unavailable: {
      "Chinese Grand Prix": ["18 of 18 lap-1 coordinates are shared to within 0.05 m"],
    },
    ...overrides,
  };
}

function startingConfig(
  seed: number, block: StandingStartBlock | null = makeStandingStart(),
): RaceConfig {
  const params = makeParams() as FittedParams & { standingStart?: StandingStartBlock };
  if (block) params.standingStart = block;
  return { track: makeTrack(), params, entries, totalLaps: 20, seed };
}

describe("runRace standing start", () => {
  it("refuses, by name, when params.json carries no standingStart block", () => {
    const result = runRace(startingConfig(1, null));
    expect(result.standingStart).toBeNull();
    expect(result.standingStartRefusal).toContain("standingStart");
  });

  it("places each slot at anchor - (slot - 1) * pitch, in the order the entries arrive", () => {
    const start = runRace(startingConfig(2)).standingStart!;
    expect(start.placements.map((p) => p.driver)).toEqual(entries.map((e) => e.driver));
    start.placements.forEach((p, i) => {
      expect(p.slot).toBe(i + 1);
      expect(p.offsetM).toBeCloseTo(ANCHOR_M - i * PITCH_M, 9);
      expect(p.stationM).toBeCloseTo(((p.offsetM % 5000) + 5000) % 5000, 9);
      expect(p.lap1DistanceM).toBeCloseTo(5000 - p.offsetM, 9);
      if (i > 0) {
        expect(start.placements[i - 1].offsetM - p.offsetM).toBeCloseTo(PITCH_M, 9);
      }
    });
  });

  it("reads the anchor from perTrack keyed by event name, never from the summary median", () => {
    const start = runRace(startingConfig(3)).standingStart!;
    expect(start.anchorMetres).toBeCloseTo(ANCHOR_M, 9);
    expect(start.placementProvenance).toBe("DERIVED");
    expect(start.anchorUnavailable).toBeNull();
  });

  it("keeps the grid but marks it DEFAULT when the circuit has no measured anchor", () => {
    const block = makeStandingStart();
    block.grid.anchorMetresPastTimingLine.perTrack = {};
    block.unavailable = { "Engine GP": ["only 2 usable stationary boxes remain of 22 cars"] };
    const start = runRace(startingConfig(4, block)).standingStart!;
    expect(start.anchorMetres).toBeNull();
    expect(start.placementProvenance).toBe("DEFAULT");
    // the artifact's own words are carried, not paraphrased
    expect(start.anchorUnavailable).toEqual(["only 2 usable stationary boxes remain of 22 cars"]);
    // the SPACING is still the fitted one, and the order is still grid order
    expect(start.pitchMetres).toBeCloseTo(PITCH_M, 9);
    expect(start.placements[0].offsetM).toBe(0);
    expect(start.placements[1].offsetM).toBeCloseTo(-PITCH_M, 9);
  });

  it("prefers a circuit's own resolved pitch over the pooled one, and says which", () => {
    const block = makeStandingStart();
    block.grid.perSessionPitchMetres = { "Engine GP": leaf(7.984, { n: 21 }) };
    const start = runRace(startingConfig(5, block)).standingStart!;
    expect(start.pitchMetres).toBeCloseTo(7.984, 9);
    expect(start.pitchSource).toContain("perSessionPitchMetres");

    block.grid.perSessionPitchMetres = { "Engine GP": leaf(null, { n: 21 }) }; // unresolved
    const pooled = runRace(startingConfig(5, block)).standingStart!;
    expect(pooled.pitchMetres).toBeCloseTo(PITCH_M, 9);
    expect(pooled.pitchSource).toContain("slotPitchMetres");
  });

  it("uses each driver's own fitted launch when the artifact has one", () => {
    const start = runRace(startingConfig(6)).standingStart!;
    for (const p of start.placements) expect(p.launchSource).toBe("perDriver");
    expect(start.byDriver.get("AAA")!.launch.accelMps2).toBeCloseTo(10.166934, 9);
    expect(start.byDriver.get("CCC")!.launch.reactionS).toBeCloseTo(0.584001, 9);
    // hands over at the fitted validity ceiling, not above it
    expect(start.validToSpeedKph).toBe(120);
    expect(start.handoverSpeedMps).toBeCloseTo(120 / 3.6, 9);
    for (const p of start.placements) {
      expect(p.launch.handoverSpeedMps).toBeCloseTo(120 / 3.6, 9);
    }
  });

  it("draws from the population spread for a driver the fit never saw, not the SE", () => {
    const block = makeStandingStart();
    block.launch.perDriver = {}; // nobody is in the fit
    const start = runRace(startingConfig(7, block)).standingStart!;
    const accels = start.placements.map((p) => p.launch.accelMps2);
    for (const p of start.placements) {
      expect(p.launchSource).toBe("populationDraw");
      expect(p.launch.accelMps2).toBeGreaterThan(0);
      expect(p.launch.reactionS).toBeGreaterThanOrEqual(0);
    }
    // a field of identical launches is the failure mode the shipped populationSigma
    // exists to prevent; the spread must be of that order, not of the SE (0.085)
    const spread = Math.max(...accels) - Math.min(...accels);
    expect(spread).toBeGreaterThan(0.2);
  });

  it("keeps the two-column stagger RULE and its metric offset null", () => {
    const start = runRace(startingConfig(8)).standingStart!;
    expect(start.lateralStagger.provenance).toBe("RULE");
    expect(start.lateralStagger.offsetMetres).toBeNull();
    expect(start.lateralStagger.columnSign).toContain("alternating");
    expect(start.placements.map((p) => p.lateralColumnSign)).toEqual([1, -1, 1]);
  });

  it("subtracts the geometric part of the per-slot penalty instead of paying it twice", () => {
    const start = runRace(startingConfig(9)).standingStart!;
    expect(start.lap1.excessPerGridSlotSeconds).toBeCloseTo(0.345004, 9);
    expect(start.lap1.geometricPerGridSlotSeconds).toBeGreaterThan(0);
    expect(start.lap1.appliedPerGridSlotSeconds).toBeCloseTo(
      0.345004 - start.lap1.geometricPerGridSlotSeconds, 9,
    );
  });

  it("adds the launch loss and the remainder to lap 1 exactly once each", () => {
    const withStart = runRace(startingConfig(11));
    const without = runRace(startingConfig(11, null));
    const start = withStart.standingStart!;
    for (let i = 0; i < entries.length; i++) {
      const driver = entries[i].driver;
      const a = withStart.entries.find((e) => e.driver === driver)!.laps[0];
      const b = without.entries.find((e) => e.driver === driver)!.laps[0];
      const composed = b.sesT - b.lST;
      const p = start.byDriver.get(driver)!;
      const expected = composed * (p.lap1DistanceM / 5000)
        + launchTimeLossS(p.launch)
        + start.lap1.remainderSeconds
        + (p.slot - 1) * start.lap1.appliedPerGridSlotSeconds;
      expect(a.sesT - a.lST).toBeCloseTo(expected, 6);
      // the whole +9.009 s excessSecondsVsCleanLap is NOT added on top of the two parts
      expect(a.sesT - a.lST).toBeLessThan(composed + 9.008838 + 4);
    }
  });

  it("charges the lap-1 penalty to lap 1 only, never again on a later lap", () => {
    const withStart = runRace(startingConfig(12));
    const without = runRace(startingConfig(12, null));
    for (const e of entries) {
      const a = withStart.entries.find((x) => x.driver === e.driver)!.laps;
      const b = without.entries.find((x) => x.driver === e.driver)!.laps;
      expect((a[0].sesT - a[0].lST) - (b[0].sesT - b[0].lST)).toBeGreaterThan(5);
      for (let lap = 1; lap < a.length; lap++) {
        // Laps 2+ are not identical, and should not be: a longer lap 1 moves the field
        // apart, and composeLapTime's dirty-air term reads that gap. What must not appear
        // is another launch loss or remainder, both of which are seconds, not tenths.
        const delta = Math.abs((a[lap].sesT - a[lap].lST) - (b[lap].sesT - b[lap].lST));
        expect(delta).toBeLessThan(0.5);
      }
    }
  });
});
