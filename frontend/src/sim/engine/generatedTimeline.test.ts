import { describe, expect, it } from "vitest";
import type { TrackModel } from "../contract/types";
import { GeneratedTimeline } from "./generatedTimeline";
import { runRace, type RaceConfig } from "./raceEngine";
import type { FittedParams } from "./params";

function makeTrack(): TrackModel {
  const length = 5000;
  const nBins = 500;
  return {
    slug: "gen-track", event: "Gen GP", lengthMetres: length,
    x: new Float32Array(1), y: new Float32Array(1), z: new Float32Array(1),
    halfWidth: new Float32Array([6]), widthBinMetres: length,
    timingLines: { sf: 0, s1: 1600, s2: 3300 },
    corners: [], grid: { order: [], pitchMetres: 8 },
    pitLane: { entryStation: null, exitStation: null, mergeStation: null, loopLateral: null },
    referenceProfile: { binMetres: 10, speedKph: new Float32Array(nBins).fill(250), gear: new Uint8Array(nBins).fill(7) },
  };
}

function makeParams(): FittedParams {
  return {
    sessionPaceTrendPerLap: { pooled: { value: -0.05, provenance: "DERIVED" }, perTrack: {} },
    tyreDegradation: {
      trackIndex: { "gen-track": { value: 0.05, provenance: "DERIVED" } },
      compoundMultiplierDefault: {
        SOFT: { value: 1.35, provenance: "DEFAULT" }, MEDIUM: { value: 1.0, provenance: "DEFAULT" },
        HARD: { value: 0.75, provenance: "DEFAULT" },
      },
    },
    driverOffsetSeconds: { AAA: { value: -1.2, provenance: "DERIVED" }, BBB: { value: 1.1, provenance: "DERIVED" } },
    teamOffsetSeconds: {},
    noise: {
      coreSigma: { value: 0.25, provenance: "DERIVED" },
      incidentProbability: { value: 0.02, provenance: "DERIVED" },
      incidentMeanExcessSeconds: { value: 0.8, provenance: "DERIVED" },
    },
    dirtyAirLossPerSecondOfProximity: { "gen-track": { value: 0.1, provenance: "DERIVED" } },
    pitLoss: { "gen-track": { netLossSeconds: { value: 22, provenance: "DERIVED" }, iqr: [20, 24] } },
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

function buildTimeline(seed: number) {
  const track = makeTrack();
  const config: RaceConfig = {
    track, params: makeParams(), totalLaps: 15, seed,
    entries: [{ driver: "AAA", team: "Team A" }, { driver: "BBB", team: "Team B" }],
  };
  const result = runRace(config);
  return new GeneratedTimeline(result, track);
}

describe("GeneratedTimeline", () => {
  it("reports SIMULATED provenance and a sim: runId", () => {
    const tl = buildTimeline(1);
    expect(tl.provenance).toBe("SIMULATED");
    expect(tl.runId.startsWith("sim:")).toBe(true);
  });

  it("moves a car's station forward over time within a lap", () => {
    const tl = buildTimeline(2);
    const early = tl.sampleAt(5).get("AAA")!;
    const later = tl.sampleAt(15).get("AAA")!;
    expect(later.lapProgress).toBeGreaterThan(early.lapProgress);
  });

  it("never produces NaN station or speed at any sampled time", () => {
    const tl = buildTimeline(3);
    for (let t = 0; t <= tl.duration; t += 37) {
      const states = tl.sampleAt(t);
      for (const s of states.values()) {
        expect(Number.isNaN(s.stationM)).toBe(false);
        expect(Number.isNaN(s.speedKph)).toBe(false);
      }
    }
  });

  it("assigns position 1 to exactly one car at any sampled time", () => {
    const tl = buildTimeline(4);
    for (let t = 10; t <= tl.duration; t += 50) {
      const states = [...tl.sampleAt(t).values()];
      const leaders = states.filter((s) => s.position === 1);
      expect(leaders).toHaveLength(1);
    }
  });

  it("marks a finisher's status as finished only after the full distance", () => {
    const tl = buildTimeline(5);
    const early = tl.sampleAt(1).get("AAA")!;
    expect(early.status).not.toBe("finished");
    const end = tl.sampleAt(tl.duration + 1).get("AAA")!;
    expect(["finished", "track", "pit"]).toContain(end.status);
  });

  it("keeps every neutralisation interval's end at or after its start", () => {
    const tl = buildTimeline(6);
    for (const n of tl.neutralisations()) {
      expect(n.end).toBeGreaterThanOrEqual(n.start);
    }
  });

  it("returns null weather (New Race configures weather as a fixed value, not a series)", () => {
    const tl = buildTimeline(7);
    expect(tl.weather()).toBeNull();
  });
});
