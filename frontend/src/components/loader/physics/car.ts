/**
 * Burnout → release → launch car model for the intro animation. Planar, rear-wheel drive:
 *   - two rear tyres with the magic-formula/implicit wheel step (wheel.ts) and their own surface temperature,
 *   - a driver that chases a slip-velocity target with the throttle and counter-steers yaw with a PD,
 *   - longitudinal body dynamics (drive − drag − rolling) once the front brakes release,
 *   - a bicycle-model yaw loop excited by the left/right drive-force asymmetry.
 * Ground frame = screen frame: x forward/right (m), y down (m), angles clockwise-positive (rad).
 * `step()` mutates `state` in place and allocates nothing.
 */

import {
  CAR,
  CAR_RENDER,
  DRIVE,
  DRIVER,
  G,
  RHO_AIR,
  SIM,
  THERMAL,
  TIMELINE,
  TYRE,
  YAW,
} from "./constants";
import { mulberry32, randRange } from "./prng";
import { gripTempFactor, markGain, smokeGain } from "./tyre";
import { stepWheelOmegaInto } from "./wheel";

export type Phase = "burnout" | "release" | "launch";

/** One rear tyre. omega rad/s, vsx m/s, slip -, mu -, fx N, slipPowerW W, tempC °C, gains 0..1. */
export interface TyreState {
  omega: number;
  vsx: number;
  slip: number;
  mu: number;
  fx: number;
  slipPowerW: number;
  tempC: number;
  smokeGain: number;
  markGain: number;
}

export interface CarState {
  /** Sim seconds since start. */
  t: number;
  phase: Phase;
  /** Seconds since brake release (0 while braked). */
  launchT: number;
  /** CG longitudinal travel along the heading (m). */
  x: number;
  /** CG lateral ground displacement (m, screen-down positive). */
  y: number;
  /** Longitudinal speed / acceleration (m/s, m/s²). */
  v: number;
  a: number;
  /** Body-frame lateral velocity (m/s). */
  vy: number;
  /** Body yaw / yaw rate (rad, rad/s) — burnout jitter is NOT included, see jitterYaw. */
  yaw: number;
  yawRate: number;
  /** Front steer angle (rad, clockwise-positive). */
  steer: number;
  /** Filtered throttle 0..1. */
  throttle: number;
  /** Axle vertical loads (N). */
  fzRear: number;
  fzFront: number;
  /** [left (y<0 / screen-up side), right (y>0 side)] */
  rear: [TyreState, TyreState];
  /** Tread surface speed for the front-wheel animation (m/s): 0 while braked, else v. */
  frontSurfaceSpeed: number;
  /** Chassis jitter during the burnout (rad); add to yaw for rendering and contact positions. */
  jitterYaw: number;
}

export interface CarSimOptions {
  /** Sim seconds of burnout before the brakes release (default SIM.burnoutDurationS). */
  burnoutDurationS?: number;
}

/** Clamp x to [lo, hi]. */
function clamp(x: number, lo: number, hi: number): number {
  return x < lo ? lo : x > hi ? hi : x;
}

/** Fraction a first-order low-pass moves toward its target over dt. */
function lowPassAlpha(dt: number, tauS: number): number {
  return 1 - Math.exp(-dt / tauS);
}

function makeTyre(): TyreState {
  return {
    omega: 0,
    vsx: 0,
    slip: 0,
    mu: 0,
    fx: 0,
    slipPowerW: 0,
    tempC: THERMAL.startC,
    smokeGain: smokeGain(THERMAL.startC),
    markGain: markGain(THERMAL.startC),
  };
}

/** Screen scale used by the runtime: rendered car width clamp / car length. */
function defaultPxPerM(viewportWidthPx: number): number {
  const widthPx = clamp(
    CAR_RENDER.widthFracOfViewport * viewportWidthPx,
    CAR_RENDER.minWidthPx,
    CAR_RENDER.maxWidthPx,
  );
  return widthPx / CAR.lengthM;
}

/**
 * CG travel (m, from the start position at the viewport centre) at which the REAR AXLE reaches
 * TIMELINE.exitFractionOfWidth of the viewport width — the runtime's "car has exited" trigger.
 */
export function defaultExitDistanceM(viewportWidthPx: number): number {
  const pxPerM = defaultPxPerM(viewportWidthPx);
  const rearAxleTravelM = ((TIMELINE.exitFractionOfWidth - 0.5) * viewportWidthPx) / pxPerM;
  return rearAxleTravelM + CAR.cgToRearAxleM;
}

export class CarSim {
  readonly state: CarState;

  private readonly rng: () => number;
  /** Sim seconds of burnout before the brakes release; shortened in place by {@link forceRelease}. */
  private burnoutDurationS: number;
  /** Fixed left/right peak-mu asymmetry (signed fraction of D), drawn once per seed. */
  private readonly muAsymEps: number;
  /** Low-passed asymmetry noise (fraction of D). */
  private muAsymNoise = 0;
  /** Burnout jitter: sample-and-hold target, its low-passed value, and the resample clock. */
  private jitterTarget = 0;
  private jitterRaw = 0;
  private jitterClockS = 0;
  /** Previous substep's acceleration — drives the (lagged) longitudinal weight transfer. */
  private aPrev = 0;
  /** Relaxed lateral tyre forces (N), front / rear axle — they lag the slip angle by L_relax / u. */
  private fyF = 0;
  private fyR = 0;

  constructor(seed: number, opts?: CarSimOptions) {
    this.rng = mulberry32(seed);
    this.burnoutDurationS = opts?.burnoutDurationS ?? SIM.burnoutDurationS;
    const mag = randRange(this.rng, DRIVER.muAsymMin, DRIVER.muAsymMax);
    this.muAsymEps = this.rng() < 0.5 ? -mag : mag;

    const staticRearN = CAR.rearStaticLoadFrac * CAR.massKg * G;
    this.state = {
      t: 0,
      phase: "burnout",
      launchT: 0,
      x: 0,
      y: 0,
      v: 0,
      a: 0,
      vy: 0,
      yaw: 0,
      yawRate: 0,
      steer: 0,
      throttle: 0,
      fzRear: staticRearN,
      fzFront: CAR.massKg * G - staticRearN,
      rear: [makeTyre(), makeTyre()],
      frontSurfaceSpeed: 0,
      jitterYaw: 0,
    };
  }

  /**
   * Release the front brakes now (wall-clock burnout cap): the burnout ends at the current sim time,
   * so the next step() runs the release phase. No-op once the brakes are already off.
   */
  forceRelease(): void {
    this.burnoutDurationS = Math.min(this.burnoutDurationS, this.state.t);
  }

  /** Advance the model by one fixed substep (SIM.dt). Order: loads → wheels → body → yaw → thermal → derived. */
  step(dt: number): void {
    const s = this.state;
    const [L, R] = s.rear;
    const t = s.t;
    const m = CAR.massKg;

    // ---- phase (for the interval [t, t+dt)) ----
    const bd = this.burnoutDurationS;
    const phase: Phase = t < bd ? "burnout" : t < bd + DRIVER.releaseLiftS ? "release" : "launch";
    const braked = phase === "burnout";
    s.phase = phase;
    s.launchT = braked ? 0 : t - bd;
    const v = s.v;

    // ---- loads: static + lagged longitudinal transfer (zero while the front brakes react the drive) + aero ----
    const transferN = braked ? 0 : (m * this.aPrev * CAR.cgHeightM) / CAR.wheelbaseM;
    const aeroN = 0.5 * RHO_AIR * CAR.clA * v * v;
    const staticRearN = CAR.rearStaticLoadFrac * m * G;
    s.fzRear = Math.max(0, staticRearN + transferN + aeroN * CAR.rearDownforceFrac);
    s.fzFront = Math.max(0, m * G - staticRearN - transferN + aeroN * (1 - CAR.rearDownforceFrac));
    const fzTyre = 0.5 * s.fzRear;

    // ---- driver throttle: slip-velocity target controller, first-order foot lag ----
    const vsxMean = 0.5 * (L.vsx + R.vsx);
    let cmd = 0;
    if (braked) {
      const ramp = Math.min(1, t / DRIVER.burnoutThrottleRampS);
      cmd =
        ramp *
        clamp(
          DRIVER.throttleBase + DRIVER.throttleGainSPerM * (DRIVER.burnoutSlipTargetMps - vsxMean),
          0,
          1,
        );
    } else if (s.launchT < DRIVER.releaseLiftS + DRIVER.launchDumpS) {
      // Clutch dump: flat out from the instant the brakes come off, slip controller bypassed. The
      // rears spin up to the limiter — this is where the launch smoke and the long stripes come from.
      cmd = 1;
    } else {
      const slipTarget =
        DRIVER.launchSlipBase + DRIVER.launchSlipExtra * Math.exp(-s.launchT / DRIVER.launchSlipDecayS);
      const vsxTarget = slipTarget * Math.max(v, TYRE.slipRefFloorMps);
      cmd = clamp(DRIVER.launchThrottleBase + DRIVER.throttleGainSPerM * (vsxTarget - vsxMean), 0, 1);
    }
    s.throttle += (cmd - s.throttle) * lowPassAlpha(dt, DRIVER.footLagS);

    // ---- available axle torque: power curve, torque cap, soft rev limiter; split equally ----
    const omegaMean = 0.5 * (L.omega + R.omega);
    const tAvailNm =
      Math.min(DRIVE.maxAxleTorqueNm, DRIVE.powerW / Math.max(omegaMean, DRIVE.powerOmegaFloorRadS)) *
      clamp((DRIVE.omegaLimitRadS - omegaMean) / DRIVE.limiterSoftnessRadS, 0, 1);
    const tTyreNm = 0.5 * s.throttle * tAvailNm;
    // Viscous limited-slip diff: shifts torque from the faster rear to the slower one so a low-grip
    // side cannot peel away on its own (which an equal split would allow) and the left/right drive
    // forces genuinely differ — that difference is what excites the yaw loop below.
    const tCoupleNm = DRIVE.diffCouplingNmPerRadS * (L.omega - R.omega);

    // ---- per-tyre peak mu: fixed asymmetry + low-passed noise, scaled by surface temperature ----
    this.muAsymNoise +=
      (randRange(this.rng, -DRIVER.muAsymNoise, DRIVER.muAsymNoise) - this.muAsymNoise) *
      lowPassAlpha(dt, DRIVER.muAsymNoiseTauS);
    const eps = this.muAsymEps + this.muAsymNoise;
    const dLeft = TYRE.D * (1 + eps) * gripTempFactor(L.tempC);
    const dRight = TYRE.D * (1 - eps) * gripTempFactor(R.tempC);

    // ---- wheels ----
    stepWheelOmegaInto(L, L.omega, v, tTyreNm - tCoupleNm, fzTyre, dLeft, dt);
    stepWheelOmegaInto(R, R.omega, v, tTyreNm + tCoupleNm, fzTyre, dRight, dt);

    // ---- body longitudinal (semi-implicit Euler); pinned by the front brakes during burnout ----
    let a = 0;
    let vNew = 0;
    if (!braked) {
      const dragN = 0.5 * RHO_AIR * CAR.cdA * v * v;
      const rollN = v > 0 ? CAR.crr * (s.fzRear + s.fzFront) : 0;
      a = (L.fx + R.fx - dragN - rollN) / m;
      vNew = Math.max(0, v + a * dt);
      s.x += vNew * dt;
    }
    s.a = a;
    this.aPrev = a;
    s.v = vNew;

    // ---- yaw ----
    if (braked) {
      s.yaw = 0;
      s.yawRate = 0;
      s.vy = 0;
      s.steer = 0;
      this.fyF = 0;
      this.fyR = 0;
      // Chassis shudder: sample-and-hold noise at ~burnoutJitterHz, low-passed, amplitude ramping up over the burnout.
      const periodS = 1 / DRIVER.burnoutJitterHz;
      this.jitterClockS += dt;
      if (this.jitterClockS >= periodS) {
        this.jitterClockS -= periodS;
        this.jitterTarget = randRange(this.rng, -DRIVER.burnoutJitterRad, DRIVER.burnoutJitterRad);
      }
      this.jitterRaw += (this.jitterTarget - this.jitterRaw) * lowPassAlpha(dt, periodS / (2 * Math.PI));
      s.jitterYaw = this.jitterRaw * Math.min(1, t / bd);
    } else {
      // Bicycle model with two tyre effects that matter at launch speeds:
      //  (1) combined slip — a spinning rear tyre has (almost) no lateral stiffness left, so while the
      //      rears spin the asymmetric drive moment is resisted by the front tyre alone;
      //  (2) relaxation length — lateral force builds with distance rolled, tau = L_relax / u, so at
      //      1-3 m/s the front cannot respond for a few tenths of a second. The car yaws first and the
      //      (lagged) counter-steer then overshoots: that is the fishtail, and it comes out of the model.
      const u = Math.max(vNew, YAW.speedFloorMps);
      const fzRef = Math.max(fzTyre, 1);
      const combL = 1 / (1 + (L.slip / YAW.combinedSlipRef) ** 2);
      const combR = 1 / (1 + (R.slip / YAW.combinedSlipRef) ** 2);
      const circL = Math.sqrt(Math.max(0, 1 - (L.fx / (dLeft * fzRef)) ** 2));
      const circR = Math.sqrt(Math.max(0, 1 - (R.fx / (dRight * fzRef)) ** 2));
      const cRear = CAR.corneringStiffRearNPerRad * 0.5 * (combL + combR);
      const cFront = CAR.corneringStiffFrontNPerRad;
      const aF = CAR.cgToFrontAxleM;
      const bR = CAR.cgToRearAxleM;
      const alphaF = s.steer - (s.vy + aF * s.yawRate) / u;
      const alphaR = -(s.vy - bR * s.yawRate) / u;
      // friction-circle caps on the steady-state lateral forces
      const capF = TYRE.D * s.fzFront;
      const capR = 0.5 * (dLeft * circL + dRight * circR) * fzRef * 2;
      const fyFTarget = clamp(cFront * alphaF, -capF, capF);
      const fyRTarget = clamp(cRear * alphaR, -capR, capR);
      // relaxation toward the target at rate u / L_relax (implicit, unconditionally stable)
      const kRelax = (u * dt) / YAW.relaxationLengthM;
      this.fyF = (this.fyF + kRelax * fyFTarget) / (1 + kRelax);
      this.fyR = (this.fyR + kRelax * fyRTarget) / (1 + kRelax);
      const fyF = this.fyF;
      const fyR = this.fyR;
      const mAsymNm = (L.fx - R.fx) * (CAR.rearTrackM / 2);
      const yawAcc = (aF * fyF - bR * fyR + mAsymNm) / CAR.yawInertiaKgM2;
      s.yawRate += yawAcc * dt;
      s.vy += ((fyF + fyR) / m - u * s.yawRate) * dt;
      s.yaw += s.yawRate * dt;
      if (s.yaw > YAW.maxBodyYawRad) {
        s.yaw = YAW.maxBodyYawRad;
        s.yawRate = Math.min(0, s.yawRate);
      } else if (s.yaw < -YAW.maxBodyYawRad) {
        s.yaw = -YAW.maxBodyYawRad;
        s.yawRate = Math.max(0, s.yawRate);
      }
      // Driver counter-steer: PD on yaw through a first-order arm lag.
      const steerCmd = clamp(
        -(DRIVER.steerKpRadPerRad * s.yaw + DRIVER.steerKdS * s.yawRate),
        -DRIVER.steerMaxRad,
        DRIVER.steerMaxRad,
      );
      s.steer += (steerCmd - s.steer) * lowPassAlpha(dt, DRIVER.steerLagS);
      // Lateral ground displacement from heading and body side-slip.
      s.y += (vNew * Math.sin(s.yaw) + s.vy * Math.cos(s.yaw)) * dt;
      // Shudder dies as soon as the brakes come off.
      s.jitterYaw *= Math.exp(-dt / DRIVER.burnoutJitterDecayS);
    }

    // ---- thermal + derived, per rear tyre (implicit in the cooling term) ----
    const hWPerK = THERMAL.h0WPerK + THERMAL.h1WsPerMK * vNew;
    this.finishTyre(L, hWPerK, dt);
    this.finishTyre(R, hWPerK, dt);

    s.frontSurfaceSpeed = braked ? 0 : vNew;
    s.t = t + dt;
  }

  /** C*dT/dt = eta*P_slip − h*(T − T_amb) with P_slip = |fx*vsx|; then the render gains. */
  private finishTyre(tyre: TyreState, hWPerK: number, dt: number): void {
    const pSlipW = Math.abs(tyre.fx * tyre.vsx);
    const c = THERMAL.heatCapJPerK;
    tyre.tempC =
      (tyre.tempC + (dt / c) * (THERMAL.slipToHeatFrac * pSlipW + hWPerK * THERMAL.ambientC)) /
      (1 + (dt * hWPerK) / c);
    tyre.slipPowerW = pSlipW;
    tyre.smokeGain = smokeGain(tyre.tempC);
    tyre.markGain = markGain(tyre.tempC);
  }

  /** Ground position (m) of a body-frame point, rotated by yaw + jitterYaw about the CG. */
  private bodyToGround(bx: number, by: number): { x: number; y: number } {
    const th = this.state.yaw + this.state.jitterYaw;
    const c = Math.cos(th);
    const sn = Math.sin(th);
    return { x: this.state.x + bx * c - by * sn, y: this.state.y + bx * sn + by * c };
  }

  /** Rear tyre contact patch centres (ground metres). */
  rearContactPatches(): { left: { x: number; y: number }; right: { x: number; y: number } } {
    const half = CAR.rearTrackM / 2;
    return {
      left: this.bodyToGround(-CAR.cgToRearAxleM, -half),
      right: this.bodyToGround(-CAR.cgToRearAxleM, half),
    };
  }

  /** Front wheel centres (ground metres). */
  frontWheelCentres(): { left: { x: number; y: number }; right: { x: number; y: number } } {
    const half = CAR.frontTrackM / 2;
    return {
      left: this.bodyToGround(CAR.cgToFrontAxleM, -half),
      right: this.bodyToGround(CAR.cgToFrontAxleM, half),
    };
  }
}
