import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { CarState, TrackModel } from "../contract/types";
import { parseTrackModel, type RawTrackModel } from "../data/manifest";
import { CAR_RENDER_LENGTH_M } from "../render/presentation";
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
    pitLanePath: null,
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

// --------------------------------------------------------------------------------------
// The standing start, measured on the SHIPPED artifacts rather than a fixture: the grid
// this places is the one params.json fitted from data/2026, and the defect it replaces
// was measured the same way -- at t = 0 every car sat on stationM exactly 0 at 195 to
// 335 kph, which is every one of the 190 to 231 pairs interpenetrating on all 13
// circuits. frontend/public/sim is a gitignored build output, so these run where a
// build exists.
// --------------------------------------------------------------------------------------

const SIM_DIR = fileURLToPath(new URL("../../../public/sim/", import.meta.url));

function shipped(): { params: FittedParams; tracks: Record<string, string> } | null {
  if (!existsSync(`${SIM_DIR}index.json`)) return null;
  const latest = JSON.parse(readFileSync(`${SIM_DIR}index.json`, "utf8")).latest as string;
  const top = JSON.parse(readFileSync(`${SIM_DIR}${latest}`, "utf8"));
  if (!top?.params || !top?.tracks) return null;
  return {
    params: JSON.parse(readFileSync(`${SIM_DIR}${top.params}`, "utf8")) as FittedParams,
    tracks: top.tracks,
  };
}

const built = shipped();

/** Every car's along-ring position relative to the leader's, unwrapped, so a field
 * straddling the timing line is not read as a 5 km spread. Ascending. */
function relativePositions(states: CarState[], lengthMetres: number): number[] {
  const lead = states[0].stationM;
  return states.map((s) => {
    let d = s.stationM - lead;
    if (d > lengthMetres / 2) d -= lengthMetres;
    if (d < -lengthMetres / 2) d += lengthMetres;
    return d;
  }).sort((a, b) => a - b);
}

/** Unordered pairs of cars closer together along the ring than one car is long. */
function interpenetratingPairs(states: CarState[], lengthMetres: number): number {
  const p = relativePositions(states, lengthMetres);
  let n = 0;
  for (let i = 0; i < p.length; i++) {
    for (let j = i + 1; j < p.length; j++) {
      const d = Math.abs(p[i] - p[j]);
      if (Math.min(d, lengthMetres - d) < CAR_RENDER_LENGTH_M - 1e-6) n++;
    }
  }
  return n;
}

function shippedRace(slug: string, file: string, seed = 7) {
  const track = parseTrackModel(
    JSON.parse(readFileSync(`${SIM_DIR}${file}`, "utf8")) as RawTrackModel,
  );
  // Two circuits ship a short or empty grid order (the feed's "position unknown"
  // sentinel covers Chinese and most of Monaco), so a New Race there is configured from
  // the driver list, exactly as the UI does it.
  const pool = Object.keys(built!.params.driverOffsetSeconds).sort();
  const order = track.grid.order.length >= 10 ? track.grid.order : pool.slice(0, 20);
  const entries = order.map((driver) => ({ driver, team: null }));
  const result = runRace({ track, params: built!.params, entries, totalLaps: 10, seed });
  return { slug, track, entries, result, timeline: new GeneratedTimeline(result, track) };
}

describe.skipIf(!built)("GeneratedTimeline standing start, on every shipped circuit", () => {
  const circuits = Object.entries(built?.tracks ?? {});

  it.each(circuits)("%s: at t=0 every car is stationary on its own box", (slug, file) => {
    const { track, entries, result, timeline } = shippedRace(slug, file);
    const start = result.standingStart;
    expect(start).not.toBeNull();
    const states = [...timeline.sampleAt(0).values()];
    expect(states).toHaveLength(entries.length);
    for (const s of states) {
      expect(s.speedKph).toBe(0);
      expect(s.status).toBe("grid");
      expect(s.throttlePct).toBe(0);
      expect(Number.isFinite(s.stationM)).toBe(true);
      expect(s.stationM).toBeGreaterThanOrEqual(0);
      expect(s.stationM).toBeLessThan(track.lengthMetres);
    }
    // in grid order: the car in entry slot i is reported in position i + 1
    const byDriver = timeline.sampleAt(0);
    entries.forEach((e, i) => expect(byDriver.get(e.driver)!.position).toBe(i + 1));
  });

  it.each(circuits)("%s: at t=0 consecutive boxes are the FITTED pitch apart", (slug, file) => {
    const { track, result, timeline } = shippedRace(slug, file);
    const pitch = result.standingStart!.pitchMetres;
    // the pooled fit is 8.029 m +/- 0.031 over 208 cars; a circuit that resolved its own
    // boxes uses that instead. Either way it is metres, not the 0.00-0.34 m this replaces.
    expect(pitch).toBeGreaterThan(7.5);
    expect(pitch).toBeLessThan(8.5);
    const p = relativePositions([...timeline.sampleAt(0).values()], track.lengthMetres);
    for (let i = 1; i < p.length; i++) expect(p[i] - p[i - 1]).toBeCloseTo(pitch, 6);
  });

  it.each(circuits)("%s: no pair interpenetrates at t=0 or anywhere in the launch", (slug, file) => {
    const { track, result, timeline } = shippedRace(slug, file);
    const start = result.standingStart!;
    expect(interpenetratingPairs([...timeline.sampleAt(0).values()], track.lengthMetres)).toBe(0);
    const handover = Math.max(...start.placements.map(
      (p) => p.launch.reactionS + p.launch.handoverSpeedMps / p.launch.accelMps2,
    ));
    for (let k = 0; k * 0.05 < handover; k++) {
      const states = [...timeline.sampleAt(k / 20).values()];
      expect(interpenetratingPairs(states, track.lengthMetres)).toBe(0);
    }
  });

  it.each(circuits)("%s: the field accelerates from rest instead of starting at speed", (slug, file) => {
    const { result, timeline } = shippedRace(slug, file);
    const start = result.standingStart!;
    const fastestReaction = Math.min(...start.placements.map((p) => p.launch.reactionS));
    // nothing moves before the fastest fitted reaction
    for (const s of timeline.sampleAt(fastestReaction * 0.99).values()) expect(s.speedKph).toBe(0);
    // and nothing exceeds the fitted validity ceiling while the launch owns the car
    const handover = Math.max(...start.placements.map(
      (p) => p.launch.reactionS + p.launch.handoverSpeedMps / p.launch.accelMps2,
    ));
    let sawMotion = false;
    for (let k = 0; k * 0.05 < handover; k++) {
      for (const s of timeline.sampleAt(k / 20).values()) {
        expect(s.speedKph).toBeLessThanOrEqual(start.validToSpeedKph + 1e-6);
        if (s.speedKph > 0) sawMotion = true;
      }
    }
    expect(sawMotion).toBe(true);
  });

  it.each(circuits)("%s: the launch joins the pace model with no jump in position", (slug, file) => {
    const { track, timeline } = shippedRace(slug, file);
    const prev = new Map<string, number>();
    let worst = 0;
    for (let k = 0; k <= 12 * 60; k++) {
      const t = k / 60;
      for (const [driver, s] of timeline.sampleAt(t)) {
        const before = prev.get(driver);
        if (before !== undefined) {
          let step = s.stationM - before;
          if (step < -track.lengthMetres / 2) step += track.lengthMetres;
          worst = Math.max(worst, step - (s.speedKph / 3.6) / 60);
        }
        prev.set(driver, s.stationM);
      }
    }
    // one 60 Hz frame of free motion is at most ~1.4 m; anything materially beyond that
    // is a teleport. Measured across the 13 circuits, the worst is 0.07 m.
    expect(worst).toBeLessThan(0.5);
  });

  it.each(circuits)("%s: reports a measured anchor, or names why it has none", (slug, file) => {
    const { result } = shippedRace(slug, file);
    const start = result.standingStart!;
    if (start.anchorMetres === null) {
      expect(start.placementProvenance).toBe("DEFAULT");
      expect(start.anchorUnavailable?.length).toBeGreaterThan(0);
    } else {
      expect(start.placementProvenance).toBe("DERIVED");
      expect(start.anchorUnavailable).toBeNull();
      // a per-circuit fact, never the shipped summary median (151.84 m) stamped on all
      expect(Number.isFinite(start.anchorMetres)).toBe(true);
    }
    // the metric stagger is absent from the feed and must stay absent
    expect(start.lateralStagger.offsetMetres).toBeNull();
    expect(start.lateralStagger.provenance).toBe("RULE");
  });
});
