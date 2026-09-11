import { describe, expect, it } from "vitest";
import { CAR, DRIVE, G, TYRE } from "./constants";
import { magicFormula, slipRatio } from "./tyre";
import { stepWheelOmega, stepWheelOmegaInto, TYRE_INERTIA_KG_M2 } from "./wheel";

const FZ_TYRE = (CAR.rearStaticLoadFrac * CAR.massKg * G) / 2; // static per-tyre rear load (N)
const T_FULL = DRIVE.maxAxleTorqueNm / 2; // full drive torque on one tyre (N m)

describe("stepWheelOmega", () => {
  it.each([1 / 60, 1 / 120, 1 / 240])("never produces NaN over 5 s of full torque at dt=%f", (dt) => {
    for (const v of [0, 10]) {
      let omega = 0;
      let peak = 0;
      for (let t = 0; t < 5; t += dt) {
        const r = stepWheelOmega(omega, v, T_FULL, FZ_TYRE, TYRE.D, dt);
        for (const k of [r.omega, r.vsx, r.slip, r.mu, r.fx]) expect(Number.isFinite(k)).toBe(true);
        expect(r.omega).toBeGreaterThanOrEqual(0);
        omega = r.omega;
        peak = Math.max(peak, omega);
      }
      // Full torque exceeds what the tyre can react, so the wheel runs away — but only linearly in time.
      expect(peak).toBeGreaterThan(60);
      expect(peak).toBeLessThan(5 * ((T_FULL / TYRE_INERTIA_KG_M2) * 1.01));
    }
  });

  it("does not oscillate on the stiff rising flank (torque held below the peak)", () => {
    // Torque below the traction limit: the wheel must settle to a steady slip without ringing.
    const torque = 0.6 * CAR.wheelRadiusM * TYRE.D * FZ_TYRE;
    for (const dt of [1 / 60, 1 / 240]) {
      let omega = 30 / CAR.wheelRadiusM; // rolling at v = 30 m/s
      const v = 30;
      let prevSlip = 0;
      let reversals = 0;
      for (let i = 0; i < 2 / dt; i++) {
        const r = stepWheelOmega(omega, v, torque, FZ_TYRE, TYRE.D, dt);
        if (i > 0 && Math.sign(r.slip - prevSlip) < 0) reversals += 1;
        prevSlip = r.slip;
        omega = r.omega;
      }
      const final = stepWheelOmega(omega, v, torque, FZ_TYRE, TYRE.D, dt);
      expect(reversals).toBeLessThanOrEqual(1);
      expect(final.mu).toBeCloseTo(0.6 * TYRE.D, 2);
      expect(final.slip).toBeGreaterThan(0);
      expect(final.slip).toBeLessThan(0.12);
    }
  });

  it("reports vsx/slip/mu/fx consistent with the updated omega", () => {
    const r = stepWheelOmega(40, 3, T_FULL, FZ_TYRE, TYRE.D, 1 / 240);
    expect(r.vsx).toBeCloseTo(r.omega * CAR.wheelRadiusM - 3, 12);
    expect(r.slip).toBeCloseTo(slipRatio(r.vsx, 3), 12);
    expect(r.mu).toBeCloseTo(magicFormula(r.slip), 12);
    expect(r.fx).toBeCloseTo(r.mu * FZ_TYRE, 9);
  });

  it("gives a negative (braking) tyre force when the wheel is slower than the ground", () => {
    const r = stepWheelOmega(0, 10, 0, FZ_TYRE, TYRE.D, 1 / 240);
    expect(r.vsx).toBeLessThan(0);
    expect(r.fx).toBeLessThan(0);
    expect(r.omega).toBeGreaterThan(0); // dragged up to speed by the ground
  });

  it("stepWheelOmegaInto writes into the given object without touching extra fields", () => {
    const out = { omega: 0, vsx: 0, slip: 0, mu: 0, fx: 0, extra: 7 };
    const ret = stepWheelOmegaInto(out, 10, 0, T_FULL, FZ_TYRE, TYRE.D, 1 / 240);
    expect(ret).toBe(out);
    expect(out.extra).toBe(7);
    expect(out).toMatchObject(stepWheelOmega(10, 0, T_FULL, FZ_TYRE, TYRE.D, 1 / 240));
  });
});
