import { describe, expect, it } from "vitest";
import { CarSim, defaultExitDistanceM, type CarState } from "./car";
import { CAR, CAR_RENDER, DRIVE, DRIVER, G, SIM, THERMAL, TIMELINE, YAW } from "./constants";

const REL = SIM.burnoutDurationS; // sim time of brake release
const SEED = 42;

/** Step a fresh sim for `seconds`, calling `probe` after each substep. */
function run(seed: number, seconds: number, probe: (s: CarState) => void, opts?: { burnoutDurationS?: number }) {
  const sim = new CarSim(seed, opts);
  const n = Math.round(seconds / SIM.dt);
  for (let i = 0; i < n; i++) {
    sim.step(SIM.dt);
    probe(sim.state);
  }
  return sim;
}

function assertFinite(s: CarState) {
  const values = [
    s.t, s.launchT, s.x, s.y, s.v, s.a, s.vy, s.yaw, s.yawRate, s.steer, s.throttle,
    s.fzRear, s.fzFront, s.frontSurfaceSpeed, s.jitterYaw,
    ...s.rear.flatMap((r) => [r.omega, r.vsx, r.slip, r.mu, r.fx, r.slipPowerW, r.tempC, r.smokeGain, r.markGain]),
  ];
  for (const k of values) expect(Number.isFinite(k)).toBe(true);
}

describe("defaultExitDistanceM", () => {
  it("places the rear axle at exitFractionOfWidth for the width clamp regime of each viewport", () => {
    for (const w of [360, 1440, 2560]) {
      const carPx = Math.min(Math.max(CAR_RENDER.widthFracOfViewport * w, CAR_RENDER.minWidthPx), CAR_RENDER.maxWidthPx);
      const pxPerM = carPx / CAR.lengthM;
      const d = defaultExitDistanceM(w);
      const rearAxlePx = w / 2 + (d - CAR.cgToRearAxleM) * pxPerM;
      expect(rearAxlePx).toBeCloseTo(TIMELINE.exitFractionOfWidth * w, 9);
    }
    expect(defaultExitDistanceM(360)).toBeCloseTo(3.94, 2);
    expect(defaultExitDistanceM(1440)).toBeCloseTo(6.32, 2);
    expect(defaultExitDistanceM(2560)).toBeCloseTo(9.513, 2);
  });
});

describe("CarSim burnout", () => {
  it("keeps the car pinned while the rears spin up to the slip target, with zero weight transfer", () => {
    let peakOmega = 0;
    const staticRear = CAR.rearStaticLoadFrac * CAR.massKg * G;
    run(SEED, REL, (s) => {
      expect(s.phase).toBe("burnout");
      expect(Math.abs(s.x)).toBeLessThan(1e-6);
      expect(s.v).toBe(0);
      expect(s.a).toBe(0);
      expect(s.yaw).toBe(0);
      expect(s.frontSurfaceSpeed).toBe(0);
      expect(s.fzRear).toBeCloseTo(staticRear, 9);
      expect(s.launchT).toBe(0);
      peakOmega = Math.max(peakOmega, s.rear[0].omega, s.rear[1].omega);
    });
    // rev-hold targets DRIVER.burnoutSlipTargetMps of slip velocity, i.e. ~23 rad/s plus spin-up overshoot
    expect(peakOmega).toBeGreaterThan(DRIVER.burnoutSlipTargetMps / CAR.wheelRadiusM);
  });

  it("ramps the throttle over burnoutThrottleRampS and never exceeds the rev limiter", () => {
    let maxOmega = 0;
    run(SEED, REL, (s) => {
      if (s.t <= DRIVER.burnoutThrottleRampS) expect(s.throttle).toBeLessThanOrEqual(s.t / DRIVER.burnoutThrottleRampS + 1e-9);
      maxOmega = Math.max(maxOmega, s.rear[0].omega, s.rear[1].omega);
    });
    // Slip-velocity target is 30 m/s ≈ 85 rad/s; the lagged P-loop overshoots but the soft limiter holds it under omegaLimit.
    expect(maxOmega).toBeGreaterThan(DRIVER.burnoutSlipTargetMps / CAR.wheelRadiusM);
    expect(maxOmega).toBeLessThan(DRIVE.omegaLimitRadS);
  });

  it("chassis jitter stays within burnoutJitterRad and escalates, then dies after release", () => {
    let peakEarly = 0;
    let peakLate = 0;
    let atRelease = 0;
    run(SEED, REL + 0.3, (s) => {
      expect(Math.abs(s.jitterYaw)).toBeLessThanOrEqual(DRIVER.burnoutJitterRad + 1e-12);
      if (s.t < 0.2 * REL) peakEarly = Math.max(peakEarly, Math.abs(s.jitterYaw));
      if (s.t > 0.6 * REL && s.t <= REL) peakLate = Math.max(peakLate, Math.abs(s.jitterYaw));
      if (s.phase === "release" && atRelease === 0) atRelease = Math.abs(s.jitterYaw);
      if (s.t > REL + 0.25) expect(Math.abs(s.jitterYaw)).toBeLessThan(0.01 * DRIVER.burnoutJitterRad);
    });
    expect(peakLate).toBeGreaterThan(peakEarly);
    expect(peakLate).toBeGreaterThan(0.3 * DRIVER.burnoutJitterRad);
  });

  it("forceRelease ends the burnout at the current sim time", () => {
    const sim = run(SEED, 0.3, () => undefined);
    sim.forceRelease();
    sim.step(SIM.dt);
    expect(sim.state.phase).toBe("release");
    expect(sim.state.launchT).toBeCloseTo(0, 6);
    for (let i = 0; i < 240; i++) sim.step(SIM.dt);
    assertFinite(sim.state);
    expect(sim.state.phase).toBe("launch");
    expect(sim.state.v).toBeGreaterThan(1);
    // Calling it again after release changes nothing.
    const t = sim.state.launchT;
    sim.forceRelease();
    sim.step(SIM.dt);
    expect(sim.state.launchT).toBeCloseTo(t + SIM.dt, 9);
  });

  it("honours a custom burnout duration", () => {
    const sim = run(SEED, 0.3, (s) => expect(s.phase).toBe("burnout"), { burnoutDurationS: 0.3 });
    sim.step(SIM.dt);
    expect(sim.state.phase).toBe("release");
    expect(sim.state.launchT).toBeCloseTo(0, 6);
  });
});

describe("CarSim launch", () => {
  it("after release v is monotone non-decreasing and the phases run burnout → release → launch", () => {
    let prevV = 0;
    const phases: string[] = [];
    run(SEED, 3, (s) => {
      if (!phases.includes(s.phase)) phases.push(s.phase);
      if (s.t > REL) {
        // a momentary lift while the spinning rears hook up may cost a few mm/s per substep; never more
        expect(s.v).toBeGreaterThanOrEqual(prevV - 0.01);
        expect(s.frontSurfaceSpeed).toBe(s.v);
        expect(s.launchT).toBeCloseTo(s.t - SIM.dt - REL, 9);
      }
      prevV = s.v;
    });
    expect(phases).toEqual(["burnout", "release", "launch"]);
  });

  it("reaches the rear-axle exit point for 360 / 1440 / 2560 px viewports", () => {
    const widths = [360, 1440, 2560];
    const exitAt: Record<number, number> = {};
    run(SEED, REL + 2, (s) => {
      for (const w of widths) {
        if (exitAt[w] === undefined && s.x >= defaultExitDistanceM(w)) exitAt[w] = s.t - REL;
      }
    });
    expect(exitAt[360]).toBeLessThanOrEqual(1.6);
    expect(exitAt[1440]).toBeLessThanOrEqual(1.6);
    // 2560 px needs 9.5 m of CG travel, and the clutch dump spends its first ~0.42 s spinning at
    // ~4.8 m/s^2 rather than the traction-limited ~10, which puts this case near 1.8 s. The runtime
    // forces the exit at 2.9 s wall (3.2 s sim) regardless, so 1.9 s is the honest bound.
    expect(exitAt[2560]).toBeLessThanOrEqual(1.9);
  });

  it("dumps the clutch at lights out: flat throttle, then the slip spikes well past the grid hold", () => {
    let holdEndSlip = 0;
    let dumpThrottleMin = 1;
    let peakLaunchSlip = 0;
    run(SEED, REL + 1.2, (s) => {
      if (s.phase === "burnout") {
        if (s.t > REL - 0.2) holdEndSlip = Math.max(holdEndSlip, s.rear[0].vsx, s.rear[1].vsx);
      } else {
        peakLaunchSlip = Math.max(peakLaunchSlip, s.rear[0].vsx, s.rear[1].vsx);
        // sample after the foot lag has caught up, before the controller takes back over
        if (s.launchT > 0.15 && s.launchT < DRIVER.releaseLiftS + DRIVER.launchDumpS) {
          dumpThrottleMin = Math.min(dumpThrottleMin, s.throttle);
        }
      }
    });
    expect(holdEndSlip).toBeLessThan(12);
    expect(dumpThrottleMin).toBeGreaterThan(0.9);
    expect(peakLaunchSlip).toBeGreaterThan(25);
  });

  it("does 0–100 km/h in 2.4–3.4 s after brake release", () => {
    let t100 = -1;
    run(SEED, REL + 3.5, (s) => {
      if (t100 < 0 && s.v >= 100 / 3.6) t100 = s.t - REL;
    });
    expect(t100).toBeGreaterThan(2.4);
    expect(t100).toBeLessThan(3.4);
  });

  it("only wisps on the grid, smokes hard on the launch, and clears within 1.5 s of release", () => {
    let peakHoldSmoke = 0;
    let peakLaunchSmoke = 0;
    run(SEED, REL + 1.5, (s) => {
      const g = Math.max(s.rear[0].smokeGain, s.rear[1].smokeGain);
      if (s.phase === "burnout") peakHoldSmoke = Math.max(peakHoldSmoke, g);
      else peakLaunchSmoke = Math.max(peakLaunchSmoke, g);
    }).state.rear.forEach((r) => {
      expect(r.smokeGain).toBeLessThan(0.1);
      expect(r.tempC).toBeGreaterThan(THERMAL.ambientC - 1);
    });
    // held on the grid the tread only warms — wisps, never a cloud
    expect(peakHoldSmoke).toBeGreaterThan(0.1);
    expect(peakHoldSmoke).toBeLessThan(0.5);
    // the clutch dump is where the smoke actually is
    expect(peakLaunchSmoke).toBeGreaterThan(0.8);
  });

  it("keeps |yaw| within maxBodyYawRad and actually yaws a little from the mu asymmetry", () => {
    let peakYaw = 0;
    run(SEED, REL + 2, (s) => {
      expect(Math.abs(s.yaw)).toBeLessThanOrEqual(YAW.maxBodyYawRad);
      expect(Math.abs(s.steer)).toBeLessThanOrEqual(DRIVER.steerMaxRad + 1e-12);
      peakYaw = Math.max(peakYaw, Math.abs(s.yaw));
    });
    expect(peakYaw).toBeGreaterThan(0.001); // ≈ 0.06°; measured 0.3–0.6° across seeds
  });

  it("applies rear weight transfer only once the brakes are off", () => {
    const staticRear = CAR.rearStaticLoadFrac * CAR.massKg * G;
    run(SEED, REL + 1, (s) => {
      if (s.phase === "launch" && s.a > 5) expect(s.fzRear).toBeGreaterThan(staticRear + 200);
      expect(s.fzFront).toBeGreaterThan(0);
      expect(s.fzRear + s.fzFront).toBeGreaterThanOrEqual(CAR.massKg * G - 1e-6);
    });
  });
});

describe("CarSim robustness", () => {
  it("never produces NaN/Infinity over 6 s for a spread of seeds", () => {
    for (const seed of [0, 1, 7, 12345, 0xdeadbeef]) run(seed, 6, assertFinite);
  });

  it("is deterministic for a given seed and differs between seeds", () => {
    const trace = (seed: number) => {
      const out: number[] = [];
      run(seed, REL + 1.5, (s) => {
        out.push(s.x, s.y, s.v, s.yaw, s.rear[0].omega, s.rear[1].omega, s.rear[0].tempC, s.jitterYaw);
      });
      return out;
    };
    expect(trace(SEED)).toEqual(trace(SEED));
    expect(trace(SEED)).not.toEqual(trace(SEED + 1));
  });

  it("mutates the same state and tyre objects in place", () => {
    const sim = new CarSim(SEED);
    const { state } = sim;
    const [l, r] = state.rear;
    for (let i = 0; i < 100; i++) sim.step(SIM.dt);
    expect(sim.state).toBe(state);
    expect(state.rear[0]).toBe(l);
    expect(state.rear[1]).toBe(r);
  });
});

describe("CarSim geometry", () => {
  it("places the rear patches behind the CG and the fronts ahead, left on the y<0 side", () => {
    const sim = new CarSim(SEED);
    const rear = sim.rearContactPatches();
    const front = sim.frontWheelCentres();
    expect(rear.left.x).toBeCloseTo(-CAR.cgToRearAxleM, 12);
    expect(rear.right.x).toBeCloseTo(-CAR.cgToRearAxleM, 12);
    expect(rear.left.y).toBeCloseTo(-CAR.rearTrackM / 2, 12);
    expect(rear.right.y).toBeCloseTo(CAR.rearTrackM / 2, 12);
    expect(front.left.x).toBeCloseTo(CAR.cgToFrontAxleM, 12);
    expect(front.left.y).toBeCloseTo(-CAR.frontTrackM / 2, 12);
    expect(front.right.y).toBeCloseTo(CAR.frontTrackM / 2, 12);
  });

  it("rotates contact points by yaw + jitterYaw about the CG (clockwise-positive, y down)", () => {
    const sim = new CarSim(SEED);
    const s = sim.state;
    s.x = 10;
    s.y = 2;
    s.yaw = 0.05;
    s.jitterYaw = 0.01;
    const th = 0.06;
    const rear = sim.rearContactPatches();
    const bx = -CAR.cgToRearAxleM;
    const by = CAR.rearTrackM / 2;
    expect(rear.right.x).toBeCloseTo(10 + bx * Math.cos(th) - by * Math.sin(th), 12);
    expect(rear.right.y).toBeCloseTo(2 + bx * Math.sin(th) + by * Math.cos(th), 12);
    // Nose yawed clockwise (screen-down) → the rear axle swings screen-up.
    expect(rear.left.y + rear.right.y).toBeLessThan(2 * 2);
  });
});

describe("CarSim yaw sign chain (mutation-resistant)", () => {
  // The bicycle model must actually be closed-loop stable and the driver must counter-steer:
  // a flipped counter-steer / rear slip-angle / yaw-moment sign pins yaw at the clamp, which
  // the original bounds-only test could not distinguish from a healthy launch.
  it("never comes near the safety clamp and counter-steers against the body yaw", () => {
    let peakYaw = 0;
    let opposed = 0;
    let samples = 0;
    let rateReversals = 0;
    let lastRateSign = 0;
    run(SEED, 3, (s) => {
      if (s.phase !== "launch") return;
      peakYaw = Math.max(peakYaw, Math.abs(s.yaw));
      if (Math.abs(s.yaw) > 0.0005) {
        samples++;
        if (Math.sign(s.steer) === -Math.sign(s.yaw)) opposed++;
      }
      // the tail wags: yaw rate must reverse (a PD driver leaves a small biased heading, so yaw itself need not cross zero)
      if (Math.abs(s.yawRate) > 0.005) {
        const rs = Math.sign(s.yawRate);
        if (lastRateSign !== 0 && rs !== lastRateSign) rateReversals++;
        lastRateSign = rs;
      }
    });
    expect(peakYaw).toBeLessThan(0.5 * YAW.maxBodyYawRad); // clamp must never engage
    expect(samples).toBeGreaterThan(50);
    expect(opposed / samples).toBeGreaterThan(0.7); // steer opposes yaw in the vast majority of substeps
    expect(rateReversals).toBeGreaterThanOrEqual(2); // it must actually fishtail (overshoot and recover)
  });
});
