import { describe, expect, it } from "vitest";
import type { TrackModel } from "../contract/types";
import { runRace, type RaceConfig } from "./raceEngine";
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
