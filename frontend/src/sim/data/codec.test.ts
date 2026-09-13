import { describe, expect, it } from "vitest";
import { LATERAL_ABSENT_CM, SAMPLE_BYTES, decodeLap, sampleLap } from "./codec";

/** Encode a synthetic lap using the SAME layout as scripts/simdata/replay.py's
 * SAMPLE_STRUCT ("<HfhHBB"), so this test exercises the real wire format without
 * depending on generated data files. */
function encodeSynthetic(
  dtMsList: number[], stationM: number[], lateralCm: number[],
  speedKph: number[], gear: number[], brake: number[], throttle: number[],
): ArrayBuffer {
  const n = dtMsList.length;
  const buf = new ArrayBuffer(n * SAMPLE_BYTES);
  const view = new DataView(buf);
  for (let i = 0; i < n; i++) {
    const o = i * SAMPLE_BYTES;
    view.setUint16(o, dtMsList[i], true);
    view.setFloat32(o + 2, stationM[i], true);
    view.setInt16(o + 6, lateralCm[i], true);
    view.setUint16(o + 8, speedKph[i], true);
    view.setUint8(o + 10, (gear[i] & 0x7f) | (brake[i] << 7));
    view.setUint8(o + 11, throttle[i]);
  }
  return buf;
}

describe("codec", () => {
  it("round-trips dt, station, lateral, speed, gear, brake, throttle", () => {
    const buf = encodeSynthetic(
      [0, 130, 130, 130], // dtMs
      [0.5, 10.2, 20.1, 30.0], // station
      [12, -34, 0, 500], // lateral cm -> 0.12, -0.34, 0, 5.0 m
      [80, 150, 200, 300], // speed
      [3, 4, 5, 6], // gear
      [0, 0, 1, 0], // brake
      [50, 100, 100, 0], // throttle
    );
    const lap = decodeLap(buf, 0, 4);
    expect(lap.n).toBe(4);
    // absolute time reconstructed from cumulative dt (Float32Array, so compare loosely)
    const expected = [0, 0.13, 0.26, 0.39];
    Array.from(lap.tS).forEach((v, i) => expect(v).toBeCloseTo(expected[i], 4));
    expect(lap.stationM[1]).toBeCloseTo(10.2, 5);
    expect(lap.lateralM[1]).toBeCloseTo(-0.34, 5);
    expect(lap.speedKph[2]).toBe(200);
    expect(lap.gear[3]).toBe(6);
    expect(lap.brake[2]).toBe(1);
    expect(lap.brake[0]).toBe(0);
    expect(lap.throttlePct[0]).toBe(50);
  });

  it("interpolates linearly between samples and holds at the ends", () => {
    // constant 360 kph (100 m/s) over 1 s covers exactly the 100 m station delta below,
    // so this is a credible step -- see "rejects a step the speed channel contradicts".
    const buf = encodeSynthetic([0, 1000], [0, 100], [0, 0], [360, 360], [1, 1], [0, 0], [0, 100]);
    const lap = decodeLap(buf, 0, 2);
    const mid = sampleLap(lap, 0.5, 5000)!;
    expect(mid.stationM).toBeCloseTo(50, 5);
    expect(mid.speedKph).toBeCloseTo(360, 5);
    const before = sampleLap(lap, -1, 5000)!;
    expect(before.stationM).toBe(0);
    const after = sampleLap(lap, 5, 5000)!;
    expect(after.stationM).toBe(100);
  });

  it("wraps station interpolation the short way around the start/finish line", () => {
    // a car crossing the line: station goes 4990 -> 10 on a 5000 m ring
    const buf = encodeSynthetic([0, 200], [4990, 10], [0, 0], [300, 300], [8, 8], [0, 0], [100, 100]);
    const lap = decodeLap(buf, 0, 2);
    const mid = sampleLap(lap, 0.1, 5000)!;
    // the short way is +20 m from 4990, i.e. should land near 4990+10=5000 -> 0 (mod),
    // NOT near the midpoint of the naive (4990+10)/2 = 2500
    expect(mid.stationM).toBeGreaterThan(4990);
  });

  it("returns null for an empty lap", () => {
    const buf = encodeSynthetic([], [], [], [], [], [], []);
    const lap = decodeLap(buf, 0, 0);
    expect(sampleLap(lap, 0, 5000)).toBeNull();
  });

  /**
   * Measured live, 2026 Australian GP Race, car index 0: two adjacent DECODED samples
   * 0.30 s apart landed 50 m apart even though both read a plausible ~250 kph -- an
   * implied ~600 kph, faster than any 2026 car reaches. sampleLap blended it as a
   * normal step, which is exactly the sweep-backward-then-teleport the fix log
   * describes. The station channel disagreeing with the speed channel by more than
   * physics allows is not a real position change.
   */
  it("rejects a step the speed channel contradicts, holding at the prior station", () => {
    const buf = encodeSynthetic(
      [0, 300], [100, 180], [0, 0], [250, 250], [7, 7], [0, 0], [100, 100],
    );
    const lap = decodeLap(buf, 0, 2);
    const mid = sampleLap(lap, 0.15, 5000)!;
    expect(mid.stationM).toBeCloseTo(100, 5);
    // the non-positional channels are still real measurements and stay interpolated
    expect(mid.speedKph).toBeCloseTo(250, 5);
  });

  it("still accepts a large but speed-credible step", () => {
    // 300 kph (83.3 m/s) for 0.3 s covers 25 m -- comfortably explains this step
    const buf = encodeSynthetic(
      [0, 300], [100, 125], [0, 0], [300, 300], [7, 7], [0, 0], [100, 100],
    );
    const lap = decodeLap(buf, 0, 2);
    const mid = sampleLap(lap, 0.15, 5000)!;
    expect(mid.stationM).toBeCloseTo(112.5, 5);
  });
});

/**
 * The ABSENCE contract at the producer/consumer seam.
 *
 * scripts/simdata/replay.py stopped clipping an unusable projection to +-327 m and
 * now writes an explicit absence instead: stationM = NaN together with
 * lateralCm = LATERAL_ABSENT_CM (INT16_MIN). Decoding that marker arithmetically
 * reads it back as a real -327.68 m position, which is the very number the producer
 * removed. Measured on a freshly built Monaco 2026 Race pack driven through
 * ReplayTimeline: 440 car-instants at 1 s sampling carried |lateral| > 300 m before
 * this was handled and 0 after, and a linear blend across the marker produced
 * fabricated intermediates such as -181.72 m.
 */
describe("absent positions", () => {
  const buf = encodeSynthetic(
    [0, 100, 100],
    [100, NaN, 300],
    [150, LATERAL_ABSENT_CM, -120],
    [200, 200, 200], [5, 5, 5], [0, 0, 0], [90, 90, 90],
  );
  const lap = decodeLap(buf, 0, 3);

  it("decodes the absence marker as absent, not as -327.68 m", () => {
    expect(lap.lateralM[0]).toBeCloseTo(1.5, 6);
    expect(Number.isNaN(lap.lateralM[1])).toBe(true);
    expect(lap.lateralM[2]).toBeCloseTo(-1.2, 6);
    expect(Number.isNaN(lap.stationM[1])).toBe(true);
  });

  it("never blends an absent sample into a neighbouring interval", () => {
    // both intervals touch the absent sample, so neither has a position
    for (const t of [0.05, 0.1, 0.15]) {
      const s = sampleLap(lap, t, 5000)!;
      expect(Number.isNaN(s.stationM), `station at t=${t}`).toBe(true);
      expect(Number.isNaN(s.lateralM), `lateral at t=${t}`).toBe(true);
      // the non-positional channels are still measurements and stay usable
      expect(s.speedKph).toBe(200);
      expect(s.gear).toBe(5);
    }
  });

  it("leaves an interval clear of the marker untouched", () => {
    // constant 200 kph (55.56 m/s) covers the 100 m station delta in 1.8 s, so this is a
    // credible step; 1.8 s in is this interval's midpoint (was 0.05 s of a 0.1 s interval).
    const clean = decodeLap(encodeSynthetic(
      [0, 1800], [100, 200], [150, -120], [200, 200], [5, 5], [0, 0], [90, 90],
    ), 0, 2);
    const s = sampleLap(clean, 0.9, 5000)!;
    expect(s.stationM).toBeCloseTo(150, 4);
    expect(s.lateralM).toBeCloseTo(0.15, 4);
  });
});
