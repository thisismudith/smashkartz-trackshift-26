import { afterEach, describe, expect, it } from "vitest";
import { SAMPLE_BYTES, LATERAL_ABSENT_CM, decodeLap } from "./codec";
import {
  alongLapDistance,
  clearSessionSampleCache,
  cumulativeDelta,
  loadSessionSamples,
  resampleByStation,
  sampleLapFromBuffer,
  stationGrid,
} from "./lapSamples";
import type { RawLapEntry } from "./manifest";

/**
 * These tests exist because this module is the only place in the frontend that turns a
 * modulo-L station channel into an x AXIS. Three things can go wrong silently there and each
 * one produces a chart that looks fine:
 *
 *   - the start/finish wrap read as a 5.8 km backwards jump,
 *   - a state channel (gear, brake) interpolated into a value the car never held,
 *   - a teleporting station (Hungary's stale-anchor hold) drawn as a continuous trace.
 *
 * Every case below is one of those.
 */

/** Build a replay blob by hand, in the exact wire layout replay.py writes. */
function blob(
  samples: { dtMs: number; station: number; speed: number; gear?: number; brake?: 0 | 1; throttle?: number }[],
): ArrayBuffer {
  const buf = new ArrayBuffer(samples.length * SAMPLE_BYTES);
  const view = new DataView(buf);
  samples.forEach((s, i) => {
    const o = i * SAMPLE_BYTES;
    view.setUint16(o, s.dtMs, true);
    view.setFloat32(o + 2, s.station, true);
    view.setInt16(o + 6, Number.isFinite(s.station) ? 0 : LATERAL_ABSENT_CM, true);
    view.setUint16(o + 8, s.speed, true);
    view.setUint8(o + 10, ((s.brake ?? 0) << 7) | (s.gear ?? 4));
    view.setUint8(o + 11, s.throttle ?? 50);
  });
  return buf;
}

function lapOf(samples: Parameters<typeof blob>[0]) {
  const buf = blob(samples);
  return decodeLap(buf, 0, samples.length);
}

/** A constant-speed lap: `n` samples spaced `stepM` apart from `station0`, wrapping at L. */
function evenLap(n: number, stepM: number, metresPerSecond: number, station0 = 0, L = 100) {
  const dtMs = Math.round((stepM / metresPerSecond) * 1000);
  const speed = Math.round(metresPerSecond * 3.6);
  return lapOf(
    Array.from({ length: n }, (_, i) => ({
      dtMs: i === 0 ? 0 : dtMs,
      station: (station0 + i * stepM) % L,
      speed,
    })),
  );
}

/* -------------------------------------------------------------- the blob --- */

describe("loadSessionSamples", () => {
  const url = "/sim/test-race.deadbeef.bin";
  const original = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = original;
    clearSessionSampleCache();
  });

  it("fetches a session blob once and shares it across concurrent callers", async () => {
    const payload = new ArrayBuffer(SAMPLE_BYTES * 4);
    let calls = 0;
    globalThis.fetch = (async () => {
      calls++;
      return { ok: true, status: 200, arrayBuffer: async () => payload };
    }) as unknown as typeof fetch;

    // concurrent: the second caller must join the first request, not open a second one
    const [a, b] = await Promise.all([loadSessionSamples(url), loadSessionSamples(url)]);
    const c = await loadSessionSamples(url);

    expect(calls).toBe(1);
    expect(a).toBe(payload);
    expect(b).toBe(payload);
    expect(c).toBe(payload);
  });

  it("does not cache a failure — the next call retries", async () => {
    const payload = new ArrayBuffer(SAMPLE_BYTES);
    let calls = 0;
    globalThis.fetch = (async () => {
      calls++;
      if (calls === 1) return { ok: false, status: 503, arrayBuffer: async () => payload };
      return { ok: true, status: 200, arrayBuffer: async () => payload };
    }) as unknown as typeof fetch;

    await expect(loadSessionSamples(url)).rejects.toThrow(/503/);
    await expect(loadSessionSamples(url)).resolves.toBe(payload);
    expect(calls).toBe(2);
  });
});

describe("sampleLapFromBuffer", () => {
  const entry = (over: Partial<RawLapEntry>) =>
    ({ lap: 7, byteOffset: 0, sampleCount: 2, ...over }) as RawLapEntry;

  it("decodes the slice the manifest points at", () => {
    const buf = blob([
      { dtMs: 0, station: 10, speed: 100 },
      { dtMs: 200, station: 20, speed: 110 },
      { dtMs: 200, station: 30, speed: 120 },
    ]);
    const lap = sampleLapFromBuffer(buf, entry({ byteOffset: SAMPLE_BYTES, sampleCount: 2 }));
    expect(lap.n).toBe(2);
    expect(lap.stationM[0]).toBeCloseTo(20, 5);
    expect(lap.speedKph[1]).toBe(120);
  });

  it("names the lap when the manifest and the blob disagree, rather than throwing a RangeError", () => {
    const buf = blob([{ dtMs: 0, station: 10, speed: 100 }]);
    expect(() => sampleLapFromBuffer(buf, entry({ lap: 42, sampleCount: 99 }))).toThrow(/lap 42/);
  });
});

/* ------------------------------------------------------------- unwrapping --- */

describe("alongLapDistance", () => {
  it("unwraps the start/finish wrap instead of reading it as a jump backwards", () => {
    const L = 5800;
    // a lap that begins 10 m before the line: 5790, 5795, 0, 5
    const lap = evenLap(4, 5, 50, 5790, L);
    const along = alongLapDistance(lap, L);

    expect(Array.from(along.distanceM)).toEqual([
      expect.closeTo(0, 3),
      expect.closeTo(5, 3),
      expect.closeTo(10, 3),
      expect.closeTo(15, 3),
    ]);
    expect(along.station0).toBeCloseTo(5790, 3);
    expect(along.coveredM).toBeCloseTo(15, 3);
    expect(along.anomalies).toBe(0);
  });

  it("flags a station step the speed channel contradicts, and leaves ordinary steps alone", () => {
    const L = 5800;
    // 5 m, 5 m, then a 2000 m teleport while the speed channel still says 5 m — the signature
    // of the Hungary stale-anchor hold
    const lap = lapOf([
      { dtMs: 0, station: 0, speed: 180 },
      { dtMs: 100, station: 5, speed: 180 },
      { dtMs: 100, station: 10, speed: 180 },
      { dtMs: 100, station: 2010, speed: 180 },
    ]);
    const along = alongLapDistance(lap, L);

    expect(Array.from(along.credible)).toEqual([1, 1, 1, 0]);
    expect(along.anomalies).toBe(1);
    expect(along.anomalyFraction).toBeCloseTo(1 / 3, 6);
  });

  it("counts absent samples and bridges across them rather than restarting the axis", () => {
    const L = 5800;
    const lap = lapOf([
      { dtMs: 0, station: 0, speed: 180 },
      { dtMs: 100, station: NaN, speed: 180 },
      { dtMs: 100, station: 10, speed: 180 },
    ]);
    const along = alongLapDistance(lap, L);

    expect(along.absent).toBe(1);
    expect(along.positioned).toBe(2);
    expect(Number.isNaN(along.distanceM[1])).toBe(true);
    expect(along.distanceM[2]).toBeCloseTo(10, 3);
  });
});

describe("stationGrid", () => {
  it("ascends across [0, L) without repeating the start/finish line at both ends", () => {
    const grid = stationGrid(100, 5);
    expect(grid.length).toBe(20);
    expect(grid[0]).toBe(0);
    expect(grid[grid.length - 1]).toBeLessThan(100);
    for (let i = 1; i < grid.length; i++) expect(grid[i]).toBeGreaterThan(grid[i - 1]);
  });
});

/* -------------------------------------------------------------- resample --- */

describe("resampleByStation", () => {
  const L = 100;
  const grid = stationGrid(L, 5);
  const at = (station: number) => Math.round((station / L) * grid.length);

  /** 10 samples 10 m apart, alternating speed/gear/brake so held vs blended is visible. */
  const lap = lapOf(
    Array.from({ length: 10 }, (_, i) => ({
      dtMs: i === 0 ? 0 : 1000,
      station: i * 10,
      speed: i % 2 === 0 ? 100 : 200,
      gear: i % 2 === 0 ? 3 : 7,
      brake: (i % 2 === 0 ? 1 : 0) as 0 | 1,
      throttle: i % 2 === 0 ? 0 : 100,
    })),
  );

  it("interpolates the continuous channels and HOLDS the state channels", () => {
    const r = resampleByStation(lap, grid, L);

    // midway between sample 0 (100 km/h) and sample 1 (200 km/h)
    expect(r.speedKph[at(5)]).toBeCloseTo(150, 4);
    expect(r.throttlePct[at(5)]).toBeCloseTo(50, 4);
    // a gear is a state: 3 and 7 must not average to 5
    expect(r.gear[at(5)]).toBe(3);
    expect(r.brake[at(5)]).toBe(1);
    expect(r.gear[at(15)]).toBe(7);
    expect(r.brake[at(15)]).toBe(0);
    // and on the samples themselves
    expect(r.speedKph[at(10)]).toBeCloseTo(200, 4);
    expect(r.gear[at(10)]).toBe(7);
  });

  it("leaves the part of the ring the lap never reached as NaN, not as its last value", () => {
    const r = resampleByStation(lap, grid, L);
    expect(Number.isNaN(r.speedKph[at(95)])).toBe(true);
    expect(Number.isNaN(r.gear[at(95)])).toBe(true);
    expect(r.covered).toBeLessThan(grid.length);
  });

  it("aligns two laps that start at different points on the ring onto the same stations", () => {
    // same circuit, but this lap's first sample is 30 m further round
    const shifted = lapOf(
      Array.from({ length: 10 }, (_, i) => ({
        dtMs: i === 0 ? 0 : 1000,
        station: (30 + i * 10) % L,
        speed: 100,
      })),
    );
    const r = resampleByStation(shifted, grid, L);
    // station 30 is this lap's own start, so it is covered and reads its first sample
    expect(r.speedKph[at(30)]).toBeCloseTo(100, 4);
    expect(r.elapsedS[at(30)]).toBeCloseTo(0, 4);
    // station 0 is 70 m further on for this lap, i.e. 7 s in
    expect(r.elapsedS[at(0)]).toBeCloseTo(7, 3);
  });

  it("breaks the trace at a station step the speed channel contradicts", () => {
    const jumpy = lapOf([
      { dtMs: 0, station: 0, speed: 36 },
      { dtMs: 1000, station: 10, speed: 36 },
      { dtMs: 1000, station: 60, speed: 36 }, // 50 m claimed, 10 m driven
      { dtMs: 1000, station: 70, speed: 36 },
    ]);
    const r = resampleByStation(jumpy, grid, L);
    // the bracket that contains the incredible step yields nothing
    expect(Number.isNaN(r.speedKph[at(30)])).toBe(true);
    // the credible brackets either side still draw
    expect(r.speedKph[at(5)]).toBeCloseTo(36, 4);
    expect(r.speedKph[at(65)]).toBeCloseTo(36, 4);
  });
});

/* ----------------------------------------------------------------- delta --- */

describe("cumulativeDelta", () => {
  const L = 100;
  const grid = stationGrid(L, 5);
  const at = (station: number) => Math.round((station / L) * grid.length);

  it("is positive where A is behind, and zeroed at the first station both laps cover", () => {
    const slow = evenLap(10, 10, 10, 0, L); // 10 m/s
    const fast = evenLap(10, 10, 20, 0, L); // 20 m/s
    const d = cumulativeDelta(slow, fast, grid, L);

    expect(d[at(0)]).toBeCloseTo(0, 4);
    // 50 m in: 5.0 s for A, 2.5 s for B
    expect(d[at(50)]).toBeCloseTo(2.5, 3);
    expect(d[at(80)]).toBeCloseTo(4, 3);
    // strictly opening, because A is slower everywhere
    expect(d[at(80)]).toBeGreaterThan(d[at(50)]);
  });

  it("is the negation of itself with the drivers swapped", () => {
    const a = evenLap(10, 10, 10, 0, L);
    const b = evenLap(10, 10, 20, 0, L);
    const ab = cumulativeDelta(a, b, grid, L);
    const ba = cumulativeDelta(b, a, grid, L);
    expect(ba[at(50)]).toBeCloseTo(-ab[at(50)], 4);
  });

  it("is NaN where either lap has no measurement, never a flat hold", () => {
    const full = evenLap(10, 10, 10, 0, L);
    const short = evenLap(5, 10, 10, 0, L); // stops at station 40
    const d = cumulativeDelta(full, short, grid, L);

    expect(Number.isFinite(d[at(20)])).toBe(true);
    expect(Number.isNaN(d[at(70)])).toBe(true);
  });
});
