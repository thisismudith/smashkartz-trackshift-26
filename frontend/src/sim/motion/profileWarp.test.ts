import { describe, expect, it } from "vitest";
import type { TrackModel } from "../contract/types";
import { buildLapWarp, buildRefTimeTable, refTimeAtStation } from "./profileWarp";

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
