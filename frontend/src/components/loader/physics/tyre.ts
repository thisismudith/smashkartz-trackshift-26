/**
 * Longitudinal tyre model: Pacejka magic formula mu(slip) plus the thermal helpers that turn a
 * rear-tyre surface temperature into smoke / rubber-mark / grip factors.
 * Slip ratio s = v_sx / max(|v|, floor), where v_sx = omega*r - v (> 0 = wheel over-spinning).
 */

import { THERMAL, TYRE } from "./constants";

const { B, C, E } = TYRE;

/** Clamp x to [lo, hi]. */
function clamp(x: number, lo: number, hi: number): number {
  return x < lo ? lo : x > hi ? hi : x;
}

/** Hermite smoothstep of x between edges e0 and e1 (0 below e0, 1 above e1). */
function smoothstep(e0: number, e1: number, x: number): number {
  const t = clamp((x - e0) / (e1 - e0), 0, 1);
  return t * t * (3 - 2 * t);
}

/**
 * Friction coefficient mu(s) — magic formula, odd in s.
 * y = B*s - E*(B*s - atan(B*s)); mu = D*sin(C*atan(y)).
 * With the default D: mu(0.12) ≈ 1.70 (peak), mu(0.35) ≈ 1.45, mu(1) ≈ 1.14, mu(∞) ≈ 0.89.
 */
export function magicFormula(slip: number, D: number = TYRE.D): number {
  const bs = B * slip;
  const y = bs - E * (bs - Math.atan(bs));
  return D * Math.sin(C * Math.atan(y));
}

/**
 * Analytic slope dmu/ds (even in s). Positive on the rising flank, negative past the peak.
 * dy/ds = B*(1 - E + E/(1 + B²s²));  dmu/ds = D*C*cos(C*atan(y))/(1 + y²) * dy/ds.
 */
export function magicFormulaSlope(slip: number, D: number = TYRE.D): number {
  const bs = B * slip;
  const y = bs - E * (bs - Math.atan(bs));
  const dyds = B * (1 - E + E / (1 + bs * bs));
  return ((D * C * Math.cos(C * Math.atan(y))) / (1 + y * y)) * dyds;
}

/** Slip ratio with a floor on the reference speed so a stationary spinning wheel stays finite. */
export function slipRatio(vsx: number, v: number): number {
  return vsx / Math.max(Math.abs(v), TYRE.slipRefFloorMps);
}

/** 0 below smokeOnC, 1 above smokeFullC, smoothstep between — drives smoke emission rate. */
export function smokeGain(tempC: number): number {
  return smoothstep(THERMAL.smokeOnC, THERMAL.smokeFullC, tempC);
}

/** Grip multiplier on D: a parabola about the optimum temperature, floored at gripMin. */
export function gripTempFactor(tempC: number): number {
  const d = tempC - THERMAL.gripOptC;
  return clamp(1 - THERMAL.gripCurvPerK2 * d * d, THERMAL.gripMin, 1);
}

/** Rubber-deposition multiplier: cold tyres still leave half-strength marks, hot ones full. */
export function markGain(tempC: number): number {
  return 0.5 + 0.5 * smokeGain(tempC);
}
