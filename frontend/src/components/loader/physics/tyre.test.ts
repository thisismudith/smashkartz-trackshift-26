import { describe, expect, it } from "vitest";
import { THERMAL, TYRE } from "./constants";
import {
  gripTempFactor,
  magicFormula,
  magicFormulaSlope,
  markGain,
  slipRatio,
  smokeGain,
} from "./tyre";

describe("magicFormula", () => {
  it("matches the documented anchor values", () => {
    expect(magicFormula(0.12)).toBeCloseTo(1.7, 1); // ±0.05 > contract ±0.02 tolerance below
    expect(Math.abs(magicFormula(0.12) - 1.7)).toBeLessThan(0.02);
    expect(Math.abs(magicFormula(0.35) - 1.45)).toBeLessThan(0.05);
    expect(Math.abs(magicFormula(1.0) - 1.14)).toBeLessThan(0.05);
    expect(Math.abs(magicFormula(1e6) - 0.89)).toBeLessThan(0.05);
  });

  it("is odd in slip and scales with D", () => {
    for (const s of [0.01, 0.12, 0.5, 3]) {
      expect(magicFormula(-s)).toBeCloseTo(-magicFormula(s), 12);
      expect(magicFormula(s, 2 * TYRE.D)).toBeCloseTo(2 * magicFormula(s), 12);
    }
    expect(magicFormula(0)).toBe(0);
  });
});

describe("magicFormulaSlope", () => {
  it("matches a finite difference of magicFormula", () => {
    const h = 1e-6;
    for (const s of [-2, -0.3, -0.05, 0, 0.05, 0.12, 0.3, 1, 5]) {
      const fd = (magicFormula(s + h) - magicFormula(s - h)) / (2 * h);
      expect(magicFormulaSlope(s)).toBeCloseTo(fd, 5);
    }
  });

  it("changes sign exactly once on s > 0, near the peak", () => {
    let changes = 0;
    let peakS = 0;
    let prev = magicFormulaSlope(1e-4);
    expect(prev).toBeGreaterThan(0);
    for (let s = 1e-3; s <= 20; s *= 1.01) {
      const k = magicFormulaSlope(s);
      if (Math.sign(k) !== Math.sign(prev) && k !== 0) {
        changes += 1;
        peakS = s;
      }
      prev = k;
    }
    expect(changes).toBe(1);
    expect(peakS).toBeGreaterThan(0.08);
    expect(peakS).toBeLessThan(0.16);
  });
});

describe("slipRatio", () => {
  it("floors the reference speed", () => {
    expect(slipRatio(5, 0)).toBeCloseTo(5 / TYRE.slipRefFloorMps, 12);
    expect(slipRatio(5, -1)).toBeCloseTo(5 / TYRE.slipRefFloorMps, 12);
    expect(slipRatio(5, 20)).toBeCloseTo(0.25, 12);
    expect(slipRatio(-5, 20)).toBeCloseTo(-0.25, 12);
  });
});

describe("thermal gains", () => {
  it("smokeGain is a smoothstep between smokeOnC and smokeFullC", () => {
    expect(smokeGain(THERMAL.smokeOnC - 50)).toBe(0);
    expect(smokeGain(THERMAL.smokeOnC)).toBe(0);
    expect(smokeGain((THERMAL.smokeOnC + THERMAL.smokeFullC) / 2)).toBeCloseTo(0.5, 12);
    expect(smokeGain(THERMAL.smokeFullC)).toBe(1);
    expect(smokeGain(THERMAL.smokeFullC + 200)).toBe(1);
    let prev = -1;
    for (let t = 0; t <= 300; t += 1) {
      const g = smokeGain(t);
      expect(g).toBeGreaterThanOrEqual(prev);
      prev = g;
    }
  });

  it("gripTempFactor peaks at the optimum and floors at gripMin", () => {
    expect(gripTempFactor(THERMAL.gripOptC)).toBe(1);
    expect(gripTempFactor(THERMAL.gripOptC + 50)).toBeLessThan(1);
    expect(gripTempFactor(THERMAL.gripOptC + 50)).toBeCloseTo(gripTempFactor(THERMAL.gripOptC - 50), 12);
    expect(gripTempFactor(1000)).toBe(THERMAL.gripMin);
    expect(gripTempFactor(-1000)).toBe(THERMAL.gripMin);
  });

  it("markGain runs from 0.5 (cold) to 1 (smoking)", () => {
    expect(markGain(20)).toBe(0.5);
    expect(markGain(THERMAL.smokeFullC + 10)).toBe(1);
    expect(markGain((THERMAL.smokeOnC + THERMAL.smokeFullC) / 2)).toBeCloseTo(0.75, 12);
  });
});
