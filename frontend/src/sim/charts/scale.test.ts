import { describe, it, expect } from "vitest";
import {
  linearScale,
  bandScale,
  niceTicks,
  extent,
  decimate,
  linePath,
  stepPath,
  bandPath,
  resolveBrush,
  resolveMargins,
} from "./scale";

describe("linearScale", () => {
  it("maps domain ends onto range ends", () => {
    const s = linearScale([0, 100], [0, 500]);
    expect(s(0)).toBe(0);
    expect(s(100)).toBe(500);
    expect(s(50)).toBe(250);
  });

  it("handles an inverted range (SVG y grows downward)", () => {
    const s = linearScale([0, 10], [200, 0]);
    expect(s(0)).toBe(200);
    expect(s(10)).toBe(0);
    expect(s(5)).toBe(100);
  });

  it("round-trips through invert", () => {
    const s = linearScale([12.5, 87.5], [40, 960]);
    for (const v of [12.5, 30, 50.25, 87.5]) {
      expect(s.invert(s(v))).toBeCloseTo(v, 9);
    }
  });

  it("maps a zero-width domain to the range midpoint instead of dividing by zero", () => {
    const s = linearScale([7, 7], [0, 100]);
    expect(s(7)).toBe(50);
    expect(Number.isFinite(s(7))).toBe(true);
  });
});

describe("bandScale", () => {
  it("spaces centres evenly and leaves padding between bands", () => {
    const b = bandScale(4, [0, 400], 0.2);
    expect(b.step).toBe(100);
    expect(b.centre(0)).toBe(50);
    expect(b.centre(3)).toBe(350);
    expect(b.bandWidth).toBeCloseTo(80, 9);
    // the gap between band i's end and band i+1's start is the padding
    expect(b.start(1) - (b.start(0) + b.bandWidth)).toBeCloseTo(20, 9);
  });

  it("does not divide by zero on an empty band set", () => {
    const b = bandScale(0, [0, 100]);
    expect(Number.isFinite(b.step)).toBe(true);
  });
});

describe("niceTicks", () => {
  it("lands on 1/2/5 boundaries and covers the data", () => {
    const { ticks, niceMin, niceMax } = niceTicks(0, 97, 5);
    expect(niceMin).toBeLessThanOrEqual(0);
    expect(niceMax).toBeGreaterThanOrEqual(97);
    const step = ticks[1] - ticks[0];
    const mantissa = step / Math.pow(10, Math.floor(Math.log10(step)));
    expect([1, 2, 5, 10]).toContain(Math.round(mantissa));
  });

  it("does not drift on float steps", () => {
    // step lands on 0.1 here; repeated addition would end at 0.9999999999999999
    const { ticks, niceMax } = niceTicks(0, 1, 20);
    const step = ticks[1] - ticks[0];
    for (let i = 1; i < ticks.length; i++) {
      expect(ticks[i] - ticks[i - 1]).toBeCloseTo(step, 12);
    }
    expect(ticks[ticks.length - 1]).toBeCloseTo(niceMax, 12);
  });

  it("pads a flat domain rather than emitting a zero-width axis", () => {
    const { niceMin, niceMax } = niceTicks(5, 5);
    expect(niceMax).toBeGreaterThan(niceMin);
  });

  it("returns nothing for non-finite input", () => {
    expect(niceTicks(NaN, 10).ticks).toEqual([]);
  });
});

describe("extent", () => {
  it("ignores non-finite values", () => {
    expect(extent([3, NaN, 1, Infinity, 9])).toEqual([1, 9]);
  });

  it("returns null when nothing is finite, rather than a default domain", () => {
    expect(extent([NaN, NaN])).toBeNull();
    expect(extent([])).toBeNull();
  });
});

describe("decimate", () => {
  const n = 2000;
  const xs = Array.from({ length: n }, (_, i) => i);
  const ys = Array.from({ length: n }, (_, i) => Math.sin(i / 40) * 100);

  it("returns the input untouched when already under threshold", () => {
    const out = decimate([1, 2, 3], [4, 5, 6], 100);
    expect(out.x).toEqual([1, 2, 3]);
  });

  it("hits the requested point count and keeps the endpoints", () => {
    const out = decimate(xs, ys, 200);
    expect(out.x).toHaveLength(200);
    expect(out.x[0]).toBe(xs[0]);
    expect(out.x[out.x.length - 1]).toBe(xs[n - 1]);
  });

  it("preserves extrema that stride sampling would miss", () => {
    // a single sharp braking spike buried between samples
    const flat = Array.from({ length: 1000 }, () => 300);
    flat[501] = 60; // the braking point
    const sx = Array.from({ length: 1000 }, (_, i) => i);
    const out = decimate(sx, flat, 100);
    expect(Math.min(...out.y)).toBe(60);
  });

  it("drops non-finite samples instead of plotting them as zero", () => {
    const out = decimate([0, 1, 2, 3], [10, NaN, 30, 40], 10);
    expect(out.y).toEqual([10, 30, 40]);
  });

  it("is monotone in x for monotone input", () => {
    const out = decimate(xs, ys, 137);
    for (let i = 1; i < out.x.length; i++) expect(out.x[i]).toBeGreaterThan(out.x[i - 1]);
  });
});

describe("path builders", () => {
  it("linePath breaks the line at a gap rather than bridging it", () => {
    const d = linePath([0, 1, 2, 3], [0, NaN, 2, 3]);
    // two subpaths => two moves
    expect(d.match(/M/g)).toHaveLength(2);
  });

  it("stepPath holds the previous value until the next x", () => {
    const d = stepPath([0, 10], [5, 8]);
    expect(d).toBe("M0.00 5.00L10.00 5.00L10.00 8.00");
  });

  it("bandPath closes the ribbon and skips incomplete columns", () => {
    const d = bandPath([0, 1, 2], [0, NaN, 2], [10, 11, 12]);
    expect(d.startsWith("M")).toBe(true);
    expect(d.endsWith("Z")).toBe(true);
    // the middle column is dropped from both edges, so 2 forward + 2 back
    expect(d.match(/L/g)).toHaveLength(3);
  });

  it("bandPath returns empty string when no column is complete", () => {
    expect(bandPath([0, 1], [NaN, NaN], [1, 2])).toBe("");
  });
});

describe("resolveBrush", () => {
  const domain = [0, 100] as const;
  const toPx = (v: number) => v * 5; // 5 px per unit

  it("returns the ordered range for a real drag", () => {
    expect(resolveBrush(70, 20, domain, toPx)).toEqual([20, 70]);
  });

  it("treats a too-short drag as a reset, not a sliver zoom", () => {
    expect(resolveBrush(40, 41, domain, toPx)).toBeNull(); // 5px < 8px threshold
    expect(resolveBrush(40, 40, domain, toPx)).toBeNull();
  });

  it("clamps an overshoot to the domain rather than selecting past the data", () => {
    expect(resolveBrush(-50, 150, domain, toPx)).toEqual([0, 100]);
  });

  it("honours an inverted domain", () => {
    expect(resolveBrush(10, 80, [100, 0], toPx)).toEqual([10, 80]);
  });

  it("returns null for non-finite input rather than an invalid range", () => {
    expect(resolveBrush(NaN, 50, domain, toPx)).toBeNull();
  });
});

describe("resolveMargins", () => {
  /** What the ranked circuit chart asks for: 104px to seat "Australian" left of its row. */
  const banded = { left: 104, right: 76, top: 8, bottom: 38 };

  it("leaves an explicit label gutter intact at a normal width", () => {
    expect(resolveMargins(720, banded)).toEqual({ left: 104, right: 76, top: 8, bottom: 38 });
  });

  it("keeps the label gutter on a narrow chart", () => {
    // The regression this guards: a fixed 44px cap below a 520px breakpoint threw the gutter
    // away, and every circuit name was drawn from x = 37 outwards, spilling out of the plot.
    const m = resolveMargins(480, banded);
    expect(m.left).toBe(104);
    expect(m.right).toBe(76);
  });

  it("still caps a gutter that would leave no plotting area", () => {
    const m = resolveMargins(200, banded);
    expect(m.left).toBeLessThan(104);
    expect(200 - m.left - m.right).toBeGreaterThan(200 * 0.34);
  });

  it("applies the documented defaults when no margin is given", () => {
    expect(resolveMargins(720)).toEqual({ left: 48, right: 16, top: 12, bottom: 34 });
  });

  it("falls back to the pre-measure width rather than producing a negative gutter", () => {
    expect(resolveMargins(0, banded).left).toBe(104);
    expect(resolveMargins(Number.NaN, banded).left).toBe(104);
  });

  it("never returns a gutter wider than the chart", () => {
    for (const w of [120, 200, 320, 480, 720, 1200]) {
      const m = resolveMargins(w, banded);
      expect(m.left + m.right).toBeLessThan(w);
    }
  });
});
