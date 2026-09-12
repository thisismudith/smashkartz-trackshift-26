import { describe, expect, it } from "vitest";
import type { TrackModel } from "../contract/types";
import { mulberry32 } from "./prng";
import { composeLapTime, splitIntoSectors, type LapModelContext } from "./lapModel";
import type { FittedParams } from "./params";

function makeTrack(): TrackModel {
  const length = 3000;
  return {
    slug: "warp-track", event: "Warp GP", lengthMetres: length,
    x: new Float32Array(1), y: new Float32Array(1), z: new Float32Array(1),
    halfWidth: new Float32Array([6]), widthBinMetres: length,
    timingLines: { sf: 0, s1: 1000, s2: 2000 },
    corners: [], grid: { order: [], pitchMetres: 8 },
    pitLanePath: null,
    pitLane: { entryStation: null, exitStation: null, mergeStation: null, loopLateral: null },
    referenceProfile: { binMetres: 10, speedKph: new Float32Array([250]), gear: new Uint8Array([7]) },
  };
}

function makeParams(): FittedParams {
  return {
    sessionPaceTrendPerLap: { pooled: { value: -0.05, provenance: "DERIVED" }, perTrack: {} },
    tyreDegradation: {
      trackIndex: { "warp-track": { value: 0.05, provenance: "DERIVED" } },
      compoundMultiplierDefault: {
        SOFT: { value: 1.35, provenance: "DEFAULT" },
        MEDIUM: { value: 1.0, provenance: "DEFAULT" },
        HARD: { value: 0.75, provenance: "DEFAULT" },
      },
    },
    driverOffsetSeconds: { FAST: { value: -1.5, provenance: "DERIVED" }, SLOW: { value: 1.5, provenance: "DERIVED" } },
    teamOffsetSeconds: {},
    noise: {
      coreSigma: { value: 0.3, provenance: "DERIVED" },
      incidentProbability: { value: 0, provenance: "DERIVED" }, // disabled for determinism tests
      incidentMeanExcessSeconds: { value: 0.9, provenance: "DERIVED" },
    },
    dirtyAirLossPerSecondOfProximity: { "warp-track": { value: 0.1, provenance: "DERIVED" } },
    pitLoss: {},
    neutralisation: {
      safetyCarLapHazard: { value: 0.01, provenance: "DERIVED" },
      virtualSafetyCarLapHazard: { value: 0.02, provenance: "DERIVED" },
      redFlagLapHazard: { value: 0.003, provenance: "DERIVED" },
      perTrackMultiplierDefault: { value: 1, provenance: "DEFAULT" },
    },
    retirement: { perCarPerRace: { value: 0.19, provenance: "DERIVED" }, perLapHazard: { value: 0.003, provenance: "DERIVED" } },
    freshTyreGainSecondsPerLapDefault: { value: 1.0, provenance: "DERIVED" },
    defaultLaps: { formula: "round(305000 / lapLengthMetres)" },
  };
}

const baseCtx: LapModelContext = {
  trackSlug: "warp-track", trackBaseSeconds: 90, driver: "FAST", team: null,
  lapIndex: 5, tyreLifeMinusOne: 3, compound: "MEDIUM",
  gapAheadAtLapStart: null, neutralisation: null, pitLossThisLap: 0,
};

describe("composeLapTime", () => {
  const params = makeParams();

  it("is deterministic for a fixed seed", () => {
    const a = composeLapTime(baseCtx, params, mulberry32(42));
    const b = composeLapTime(baseCtx, params, mulberry32(42));
    expect(a).toBe(b);
  });

  it("gives the faster driver a quicker lap than the slower one, all else equal", () => {
    const rngA = mulberry32(1), rngB = mulberry32(1);
    const fast = composeLapTime(baseCtx, params, rngA);
    const slow = composeLapTime({ ...baseCtx, driver: "SLOW" }, params, rngB);
    expect(fast).toBeLessThan(slow);
  });

  it("makes a soft tyre degrade faster than a hard tyre at the same age", () => {
    const rng1 = mulberry32(7), rng2 = mulberry32(7);
    const soft = composeLapTime({ ...baseCtx, compound: "SOFT" }, params, rng1);
    const hard = composeLapTime({ ...baseCtx, compound: "HARD" }, params, rng2);
    expect(soft).toBeGreaterThan(hard);
  });

  it("applies the safety-car multiplier when neutralised", () => {
    const rng1 = mulberry32(3), rng2 = mulberry32(3);
    const green = composeLapTime(baseCtx, params, rng1);
    const underSC = composeLapTime({ ...baseCtx, neutralisation: "SC" }, params, rng2);
    expect(underSC).toBeGreaterThan(green * 1.3);
  });

  it("adds proximity loss only when the car ahead is inside 3 seconds", () => {
    const rng1 = mulberry32(9), rng2 = mulberry32(9);
    const clearAir = composeLapTime({ ...baseCtx, gapAheadAtLapStart: 10 }, params, rng1);
    const closeUp = composeLapTime({ ...baseCtx, gapAheadAtLapStart: 0.5 }, params, rng2);
    expect(closeUp).toBeGreaterThan(clearAir);
  });

  it("never returns a lap time below 70% of the track base (guards against a noise blow-up)", () => {
    for (let seed = 0; seed < 50; seed++) {
      const lap = composeLapTime(baseCtx, params, mulberry32(seed));
      expect(lap).toBeGreaterThanOrEqual(baseCtx.trackBaseSeconds * 0.7);
    }
  });
});

describe("splitIntoSectors", () => {
  it("sums back to the total lap time", () => {
    const track = makeTrack();
    const { s1, s2, s3 } = splitIntoSectors(93, track);
    expect(s1 + s2 + s3).toBeCloseTo(93, 6);
  });

  it("splits proportionally to the track's sector-boundary stations", () => {
    const track = makeTrack(); // s1 at 1000/3000, s2 at 2000/3000 -> equal thirds
    const { s1, s2, s3 } = splitIntoSectors(90, track);
    expect(s1).toBeCloseTo(30, 5);
    expect(s2).toBeCloseTo(30, 5);
    expect(s3).toBeCloseTo(30, 5);
  });
});
