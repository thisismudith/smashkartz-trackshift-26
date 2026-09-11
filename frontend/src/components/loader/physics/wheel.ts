/**
 * One driven wheel: rotational ODE  I*dω/dt = T − r*Fx(ω)  with the tyre force from the magic
 * formula. The ODE is stiff (Fx changes by ~B*C*D*fz over a few percent of slip), so the tyre
 * force is linearised and its POSITIVE-stiffness part is taken implicitly (backward Euler):
 *   dω = dt*(T − r*F) / (I + dt*r²*fz*K/u),  K = max(dmu/ds, 0), u = max(|v|, floor).
 * This is unconditionally stable on the rising flank and never oscillates or blows up at
 * animation step sizes; the negative-slope (post-peak) region is handled explicitly.
 */

import { CAR, DRIVE, TYRE } from "./constants";
import { magicFormula, magicFormulaSlope } from "./tyre";

/** Per-tyre effective inertia: both rears share the axle/driveline inertia. */
export const TYRE_INERTIA_KG_M2 = DRIVE.axleInertiaKgM2 / 2;

/** Result of one wheel substep. Units: rad/s, m/s, -, -, N. */
export interface WheelStepResult {
  omega: number;
  vsx: number;
  slip: number;
  mu: number;
  fx: number;
}

/**
 * Allocation-free variant of {@link stepWheelOmega}: writes the result into `out` and returns it.
 * `out` may carry extra fields (e.g. a car TyreState); only the five WheelStepResult keys change.
 */
export function stepWheelOmegaInto<T extends WheelStepResult>(
  out: T,
  omega: number,
  v: number,
  torqueNm: number,
  fzN: number,
  D: number,
  dt: number,
  inertiaKgM2: number = TYRE_INERTIA_KG_M2,
): T {
  const r = CAR.wheelRadiusM;
  const u = Math.max(Math.abs(v), TYRE.slipRefFloorMps);

  // Linearise the tyre force about the current slip.
  const vsx0 = omega * r - v;
  const s0 = vsx0 / u;
  const F0 = magicFormula(s0, D) * fzN;
  const K = Math.max(magicFormulaSlope(s0, D), 0); // only the stabilising part goes implicit

  const dOmega = (dt * (torqueNm - r * F0)) / (inertiaKgM2 + (dt * r * r * fzN * K) / u);
  const omega1 = Math.max(0, omega + dOmega);

  // Report the tyre force consistent with the UPDATED wheel speed.
  const vsx1 = omega1 * r - v;
  const s1 = vsx1 / u;
  const mu1 = magicFormula(s1, D);

  out.omega = omega1;
  out.vsx = vsx1;
  out.slip = s1;
  out.mu = mu1;
  out.fx = mu1 * fzN;
  return out;
}

/**
 * Advance one driven wheel by dt.
 * @param omega    wheel angular speed (rad/s), ≥ 0
 * @param v        ground speed of the hub along the heading (m/s)
 * @param torqueNm drive torque applied to THIS tyre (N m)
 * @param fzN      vertical load on THIS tyre (N)
 * @param D        magic-formula peak mu for this tyre (already scaled by temperature/asymmetry)
 * @param dt       substep (s)
 * @param inertiaKgM2 effective inertia of this tyre + its driveline share (default DRIVE.axleInertiaKgM2/2)
 */
export function stepWheelOmega(
  omega: number,
  v: number,
  torqueNm: number,
  fzN: number,
  D: number,
  dt: number,
  inertiaKgM2: number = TYRE_INERTIA_KG_M2,
): WheelStepResult {
  return stepWheelOmegaInto(
    { omega: 0, vsx: 0, slip: 0, mu: 0, fx: 0 },
    omega,
    v,
    torqueNm,
    fzN,
    D,
    dt,
    inertiaKgM2,
  );
}
