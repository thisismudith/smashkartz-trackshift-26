import { describe, expect, it } from "vitest";
import type { TrackModel } from "../contract/types";
import {
  buildLapWarp, buildRefTimeTable, buildStandingStartLap, launchProfile, launchStateAt,
  launchTimeLossS, progressAtRefTime, refTimeAtProgress, refTimeAtStation,
  type LaunchKinematics,
} from "./profileWarp";

function makeTrack(): TrackModel {
  const length = 3000;
  const bin = 10;
  const nBins = length / bin;
  // a simple profile: fast on two long straights, slow through one corner in the middle
  const speedKph = new Float32Array(nBins);
  for (let i = 0; i < nBins; i++) {
    const station = i * bin;
    speedKph[i] = station > 1400 && station < 1600 ? 90 : 280;
  }
  const gear = new Uint8Array(nBins).fill(7);
  const n = 300;
  const x = new Float32Array(n), y = new Float32Array(n), z = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const a = (i / n) * 2 * Math.PI;
    x[i] = Math.cos(a) * (length / (2 * Math.PI));
    y[i] = Math.sin(a) * (length / (2 * Math.PI));
  }
  return {
    slug: "warp-track", event: "Warp GP", lengthMetres: length, x, y, z,
    halfWidth: new Float32Array([6]), widthBinMetres: length,
    timingLines: { sf: 0, s1: 1000, s2: 2000 },
    corners: [], grid: { order: [], pitchMetres: 8 },
    pitLanePath: null,
    pitLane: { entryStation: null, exitStation: null, mergeStation: null, loopLateral: null },
    referenceProfile: { binMetres: bin, speedKph, gear },
  };
}

describe("profileWarp", () => {
  const track = makeTrack();
  const refTable = buildRefTimeTable(track);

  it("produces a reference time table that is monotonically increasing", () => {
    for (let i = 1; i < refTable.cumTime.length; i++) {
      expect(refTable.cumTime[i]).toBeGreaterThan(refTable.cumTime[i - 1]);
    }
  });

  it("reconstructs the exact input sector times at the sector boundaries", () => {
    const sectors = { s1: 13.0, s2: 15.5, s3: 12.8 };
    const warp = buildLapWarp(track, refTable, sectors);
    // scan forward in small steps and record the time at which station crosses each
    // sector boundary, since sampleAt maps time -> station (the direction the engine
    // actually needs), not station -> time
    const steps = 20000;
    let crossedS1 = -1, crossedS2 = -1, crossedEnd = -1;
    for (let i = 0; i <= steps; i++) {
      const t = (i / steps) * warp.lapTime;
      const { stationM } = warp.sampleAt(t);
      if (crossedS1 < 0 && stationM >= 1000) crossedS1 = t;
      if (crossedS2 < 0 && stationM >= 2000) crossedS2 = t;
      if (i === steps) crossedEnd = t;
    }
    expect(crossedS1).toBeCloseTo(sectors.s1, 1);
    expect(crossedS2).toBeCloseTo(sectors.s1 + sectors.s2, 1);
    expect(crossedEnd).toBeCloseTo(sectors.s1 + sectors.s2 + sectors.s3, 1);
  });

  it("shows a higher speed for a car with a faster sector than the reference profile implies", () => {
    const refS1Time = refTimeAtStation(refTable, 1000);
    const fastCar = buildLapWarp(track, refTable, { s1: refS1Time * 0.8, s2: 15.5, s3: 12.8 });
    const slowCar = buildLapWarp(track, refTable, { s1: refS1Time * 1.2, s2: 15.5, s3: 12.8 });
    // sample near the start of sector 1 (well inside the fast straight in the profile)
    const fastSpeed = fastCar.sampleAt(1).speedKph;
    const slowSpeed = slowCar.sampleAt(1).speedKph;
    expect(fastSpeed).toBeGreaterThan(slowSpeed);
  });

  it("keeps station within [0, lapTime-covered distance] and never produces NaN", () => {
    const warp = buildLapWarp(track, refTable, { s1: 13, s2: 15.5, s3: 12.8 });
    for (let t = -5; t <= warp.lapTime + 5; t += 1) {
      const { stationM, speedKph } = warp.sampleAt(t);
      expect(Number.isNaN(stationM)).toBe(false);
      expect(Number.isNaN(speedKph)).toBe(false);
      expect(stationM).toBeGreaterThanOrEqual(0);
    }
  });
});

// The fitted medians shipped in params.json standingStart.launch, used below only as a
// realistic ARGUMENT to the pure functions -- nothing here re-derives them.
const FITTED: LaunchKinematics = {
  reactionS: 0.534759, accelMps2: 9.777419, handoverSpeedMps: 120 / 3.6,
};

describe("launchProfile (port of launch.launch_profile)", () => {
  it("keeps a car stationary in its box until the fitted reaction has elapsed", () => {
    for (const t of [0, 0.2, 0.5, FITTED.reactionS - 1e-9]) {
      const p = launchProfile(t, FITTED);
      expect(p.distanceM).toBe(0);
      expect(p.speedMps).toBe(0);
      expect(p.phase).toBe("GRID");
    }
  });

  it("follows v = a (t - reaction) and s = a (t - reaction)^2 / 2 through the launch", () => {
    const tau = 1.5;
    const p = launchProfile(FITTED.reactionS + tau, FITTED);
    expect(p.phase).toBe("LAUNCH");
    expect(p.speedMps).toBeCloseTo(FITTED.accelMps2 * tau, 9);
    expect(p.distanceM).toBeCloseTo(0.5 * FITTED.accelMps2 * tau * tau, 9);
  });

  it("holds the handover speed once the validity ceiling is reached, continuously", () => {
    const tHand = FITTED.reactionS + FITTED.handoverSpeedMps / FITTED.accelMps2;
    const before = launchProfile(tHand - 1e-6, FITTED);
    const after = launchProfile(tHand + 1e-6, FITTED);
    expect(before.distanceM).toBeCloseTo(after.distanceM, 3);
    expect(before.speedMps).toBeCloseTo(after.speedMps, 4);
    expect(after.phase).toBe("HANDOVER");
    // the third phase is a constant-speed coast: that is what lets the whole field hand
    // over at ONE instant instead of each car jumping to racing speed on its own
    expect(launchProfile(tHand + 2, FITTED).speedMps).toBeCloseTo(FITTED.handoverSpeedMps, 9);
  });

  it("never accepts a non-positive acceleration or handover speed", () => {
    expect(() => launchProfile(1, { ...FITTED, accelMps2: 0 })).toThrow();
    expect(() => launchProfile(1, { ...FITTED, handoverSpeedMps: -1 })).toThrow();
  });

  it("reports the launch loss as reaction + v / 2a, the shipped launchLossSeconds", () => {
    // params.json standingStart.lap1.penaltySplit.launchLossSeconds = 2.239367
    expect(launchTimeLossS(FITTED)).toBeCloseTo(2.239367, 5);
  });
});

describe("unwrapped reference time", () => {
  const track = makeTrack();
  const table = buildRefTimeTable(track);

  it("is monotone across a negative progress, a lap join and a second lap", () => {
    let last = -Infinity;
    for (let p = -400; p <= 2 * track.lengthMetres; p += 7) {
      const t = refTimeAtProgress(table, p);
      expect(t).toBeGreaterThan(last);
      last = t;
    }
  });

  it("round-trips progress through time to within a metre, including behind the line", () => {
    for (const p of [-52.3, -8, 0, 1, 1500, track.lengthMetres - 1, track.lengthMetres]) {
      expect(progressAtRefTime(table, refTimeAtProgress(table, p))).toBeCloseTo(p, 1);
    }
  });

  it("puts a car 50 m BEHIND the line behind one on the line, not a lap ahead", () => {
    expect(refTimeAtProgress(table, -50)).toBeLessThan(refTimeAtProgress(table, 0));
    // the failure this pins: the WRAPPED lookup reports the back of the grid as almost a
    // full lap of reference time, i.e. a 3 km lead over pole
    expect(refTimeAtStation(table, -50)).toBeGreaterThan(refTimeAtProgress(table, 0));
  });
});

describe("launchStateAt (port of launch.launch_state)", () => {
  const L = 3000;
  const anchor = 117.1, pitch = 8.029;
  const grid = Array.from({ length: 20 }, (_, i) => ({
    driver: `D${i + 1}`,
    offsetM: anchor - i * pitch,
    // a spread of accelerations wide enough to close an 8 m gap inside the launch, which
    // is what the fitted population sigma (1.04 m/s^2) actually implies
    launch: { ...FITTED, accelMps2: 9.0 + (i % 5) * 0.5, reactionS: 0.45 + (i % 3) * 0.1 },
  }));

  it("puts every car on its own box, stationary, one fitted pitch apart at t = 0", () => {
    const state = launchStateAt(grid, 0, { ringLengthM: L, minGapM: 5.6 });
    expect(state).toHaveLength(20);
    for (let i = 0; i < state.length; i++) {
      expect(state[i].speedKph).toBe(0);
      expect(state[i].phase).toBe("GRID");
      expect(state[i].gridPosition).toBe(i + 1);
      expect(state[i].progressM).toBeCloseTo(anchor - i * pitch, 9);
      if (i > 0) expect(state[i - 1].progressM - state[i].progressM).toBeCloseTo(pitch, 9);
    }
  });

  it("wraps a box that sits BEHIND the line onto the far end of the ring", () => {
    const state = launchStateAt(grid, 0, { ringLengthM: L, minGapM: null });
    const behind = state.filter((c) => c.progressM < 0);
    expect(behind.length).toBeGreaterThan(0);
    for (const c of behind) expect(c.stationM).toBeCloseTo(L + c.progressM, 6);
  });

  it("lets free-air launches interpenetrate, and min_gap_m stops them", () => {
    let freeOverlaps = 0;
    for (let k = 0; k <= 100; k++) {
      const t = k / 20;
      const free = launchStateAt(grid, t, { ringLengthM: L, minGapM: null });
      for (let i = 1; i < free.length; i++) {
        if (free[i - 1].progressM - free[i].progressM < 5.6) freeOverlaps++;
      }
      const held = launchStateAt(grid, t, { ringLengthM: L, minGapM: 5.6 });
      for (let i = 1; i < held.length; i++) {
        expect(held[i - 1].progressM - held[i].progressM).toBeGreaterThanOrEqual(5.6 - 1e-9);
      }
    }
    expect(freeOverlaps).toBeGreaterThan(0); // the constraint is doing real work
  });

  it("tags a held car QUEUED rather than quietly slowing it", () => {
    let sawQueued = false;
    for (let k = 0; k <= 100; k++) {
      const held = launchStateAt(grid, k / 20, { ringLengthM: L, minGapM: 5.6 });
      if (held.some((c) => c.phase === "QUEUED")) sawQueued = true;
    }
    expect(sawQueued).toBe(true);
  });

  it("refuses a negative gap and a non-positive ring", () => {
    expect(() => launchStateAt(grid, 1, { ringLengthM: L, minGapM: -1 })).toThrow();
    expect(() => launchStateAt(grid, 1, { ringLengthM: 0, minGapM: 5.6 })).toThrow();
  });
});

describe("buildStandingStartLap", () => {
  const track = makeTrack();
  const table = buildRefTimeTable(track);
  const handoverTimeS = 4.2, lapTime = 60;

  it("starts exactly where the launch left the car, for a box past OR behind the line", () => {
    for (const handoverProgressM of [174.1, -12.4, 0]) {
      const lap = buildStandingStartLap(track, table, { handoverTimeS, handoverProgressM, lapTime });
      expect(lap.sampleAt(handoverTimeS).progressM).toBeCloseTo(handoverProgressM, 1);
    }
  });

  it("finishes lap 1 exactly one ring past the line, whatever the grid slot", () => {
    for (const handoverProgressM of [174.1, -12.4]) {
      const lap = buildStandingStartLap(track, table, { handoverTimeS, handoverProgressM, lapTime });
      expect(lap.sampleAt(lapTime).progressM).toBeCloseTo(track.lengthMetres, 1);
    }
  });

  it("advances monotonically and never reports NaN", () => {
    const lap = buildStandingStartLap(track, table, { handoverTimeS, handoverProgressM: -12.4, lapTime });
    let last = -Infinity;
    for (let t = handoverTimeS; t <= lapTime; t += 0.05) {
      const s = lap.sampleAt(t);
      expect(Number.isNaN(s.progressM)).toBe(false);
      expect(Number.isNaN(s.speedKph)).toBe(false);
      expect(s.progressM).toBeGreaterThanOrEqual(last - 1e-6);
      expect(s.stationM).toBeGreaterThanOrEqual(0);
      expect(s.stationM).toBeLessThanOrEqual(track.lengthMetres + 1e-6);
      last = s.progressM;
    }
  });

  it("drives a car starting behind the line FURTHER than one whose box is past it", () => {
    const back = buildStandingStartLap(track, table, { handoverTimeS, handoverProgressM: -40, lapTime });
    const front = buildStandingStartLap(track, table, { handoverTimeS, handoverProgressM: 120, lapTime });
    const backDistance = back.sampleAt(lapTime).progressM - back.sampleAt(handoverTimeS).progressM;
    const frontDistance = front.sampleAt(lapTime).progressM - front.sampleAt(handoverTimeS).progressM;
    expect(backDistance - frontDistance).toBeCloseTo(160, 0);
  });
});
