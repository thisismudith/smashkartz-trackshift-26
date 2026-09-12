import { describe, expect, it } from "vitest";
import { decodeLap, LATERAL_ABSENT_CM, sampleLap, SAMPLE_BYTES } from "./codec";

/**
 * End-to-end guard for the ABSENCE contract across the Python -> browser seam.
 *
 * scripts/simdata/replay.py withdraws a position it cannot place, writing
 * stationM = NaN and lateralCm = INT16_MIN, and its module docstring says outright:
 * "The frontend must skip such a sample, never draw it."
 *
 * This is a seam, and a seam is where a contract with no consumer hides. The first
 * version of that change shipped with a producer and no consumer at all: codec.ts still
 * computed `latCm / 100`, so INT16_MIN decoded to -327.68 m and the renderer drew cars
 * 327 metres off the circuit -- the exact defect the change was made to remove, moved
 * one module downstream. These tests exist so that cannot happen silently again.
 */

/** Build a replay blob by hand, in the exact wire layout replay.py writes. */
function blob(samples: {
  dtMs: number; station: number; latCm: number; speed?: number;
}[]): ArrayBuffer {
  const buf = new ArrayBuffer(samples.length * SAMPLE_BYTES);
  const view = new DataView(buf);
  samples.forEach((s, i) => {
    const o = i * SAMPLE_BYTES;
    view.setUint16(o, s.dtMs, true);
    view.setFloat32(o + 2, s.station, true);
    view.setInt16(o + 6, s.latCm, true);
    view.setUint16(o + 8, s.speed ?? 200, true);
    view.setUint8(o + 10, 5);
    view.setUint8(o + 11, 80);
  });
  return buf;
}

const L = 5000;

describe("absent positions survive the Python -> browser seam as absent", () => {
  it("decodes the absence sentinel as NaN, never as -327.68 m", () => {
    const lap = decodeLap(blob([
      { dtMs: 0, station: 100, latCm: 120 },
      { dtMs: 250, station: NaN, latCm: LATERAL_ABSENT_CM },
      { dtMs: 250, station: 130, latCm: 140 },
    ]), 0, 3);

    expect(lap.lateralM[0]).toBeCloseTo(1.2, 6);
    expect(lap.lateralM[2]).toBeCloseTo(1.4, 6);
    // the specific wrong answer this guards against
    expect(lap.lateralM[1]).not.toBeCloseTo(-327.68, 2);
    expect(Number.isFinite(lap.lateralM[1])).toBe(false);
    expect(Number.isFinite(lap.stationM[1])).toBe(false);
  });

  it("does not interpolate an absent sample away", () => {
    // A car sampled BETWEEN a good position and an absent one has no known position.
    // Blending would produce a plausible number for a place nothing measured.
    const lap = decodeLap(blob([
      { dtMs: 0, station: 100, latCm: 120 },
      { dtMs: 250, station: NaN, latCm: LATERAL_ABSENT_CM },
    ]), 0, 2);

    const mid = sampleLap(lap, 0.125, L);
    expect(mid).not.toBeNull();
    expect(Number.isFinite(mid!.stationM)).toBe(false);
    expect(Number.isFinite(mid!.lateralM)).toBe(false);
  });

  it("keeps measured channels usable on an absent-position sample", () => {
    // Speed, gear and throttle are measured independently of the positioning system.
    // Withdrawing the POSITION must not withdraw the telemetry: at the sentinel the
    // feed is still reporting a real 79.6 km/h pit-limit speed.
    const lap = decodeLap(blob([
      { dtMs: 0, station: NaN, latCm: LATERAL_ABSENT_CM, speed: 80 },
      { dtMs: 250, station: NaN, latCm: LATERAL_ABSENT_CM, speed: 80 },
    ]), 0, 2);
    const s = sampleLap(lap, 0.125, L);
    expect(s!.speedKph).toBeCloseTo(80, 3);
    expect(s!.gear).toBe(5);
    expect(s!.throttlePct).toBeCloseTo(80, 3);
  });

  it("leaves a fully measured lap untouched", () => {
    const lap = decodeLap(blob([
      { dtMs: 0, station: 100, latCm: -250 },
      { dtMs: 250, station: 120, latCm: 250 },
    ]), 0, 2);
    const s = sampleLap(lap, 0.125, L);
    expect(s!.stationM).toBeCloseTo(110, 3);
    expect(s!.lateralM).toBeCloseTo(0, 3);
    expect(Number.isFinite(s!.stationM)).toBe(true);
  });
});
