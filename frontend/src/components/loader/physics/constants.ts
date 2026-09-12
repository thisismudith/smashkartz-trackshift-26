/**
 * Shared constants for the intro-animation burnout/launch model.
 *
 * SCOPE: this is the page-load INTRO ANIMATION only — a plausible-looking 2026-style F1 launch —
 * not the race simulator and not a validated vehicle model. Values are engineering ballparks
 * chosen so the motion reads right on screen; nothing here is an FIA rule or measured team data.
 *
 * Conventions (ground frame = screen frame):
 *   x  forward / screen-right (m)      y  screen-down (m)      angles clockwise-positive (rad)
 *   Car nose points +x. "Left" tyre = screen-up side (y < 0), "right" tyre = screen-down side (y > 0).
 *   Slip velocity v_sx = omega*r - v  (> 0 = wheel spinning faster than ground).
 */

export const G = 9.81; // m/s^2
export const RHO_AIR = 1.2; // kg/m^3
export const DEG = Math.PI / 180;

/** 2026-regulation-shaped geometry. Distances "from rear" are measured from the rearmost point of the car. */
export const CAR = {
  massKg: 800,
  lengthM: 5.6,
  widthM: 2.0,
  wheelbaseM: 3.4,
  cgToFrontAxleM: 1.98, // a
  cgToRearAxleM: 1.42, // b  (CG 55 % rearward over the wheelbase)
  cgHeightM: 0.3,
  rearStaticLoadFrac: 0.55,
  rearTrackM: 1.45,
  frontTrackM: 1.55,
  wheelRadiusM: 0.355,
  rearTyreWidthM: 0.375,
  frontTyreWidthM: 0.27,
  rearOverhangM: 0.6, // rear axle centre, measured from the rearmost point
  cdA: 1.2, // m^2
  clA: 3.5, // m^2 (downforce)
  rearDownforceFrac: 0.5,
  crr: 0.015,
  yawInertiaKgM2: 650,
  corneringStiffFrontNPerRad: 80_000,
  corneringStiffRearNPerRad: 100_000,
} as const;

/** Derived longitudinal stations (m from the rearmost point). */
export const CAR_STATIONS = {
  rearAxleX: CAR.rearOverhangM, // 0.60
  frontAxleX: CAR.rearOverhangM + CAR.wheelbaseM, // 4.00
  cgX: CAR.rearOverhangM + CAR.wheelbaseM - CAR.cgToFrontAxleM, // 2.02
  noseX: CAR.lengthM, // 5.60
} as const;

/**
 * Drawing layout for the top-view car, in SVG viewBox units: 100 units per metre,
 * viewBox "0 0 560 200", rear of car at x=0, nose at x=560, centreline at y=100.
 * Wheel rectangles are rendered as sibling DOM elements over the SVG (see HaasCarTop.tsx) so the
 * runtime can animate tread without re-rasterising the SVG. Everything must agree with these numbers.
 */
export const VB_PER_M = 100;
export const CAR_VIEWBOX = { w: 560, h: 200 } as const;
export const WHEEL_VB = {
  diameter: 2 * CAR.wheelRadiusM * VB_PER_M, // 71
  rear: {
    cx: CAR_STATIONS.rearAxleX * VB_PER_M, // 60
    halfTrack: (CAR.rearTrackM / 2) * VB_PER_M, // 72.5
    width: CAR.rearTyreWidthM * VB_PER_M, // 37.5 (lateral extent)
  },
  front: {
    cx: CAR_STATIONS.frontAxleX * VB_PER_M, // 400
    halfTrack: (CAR.frontTrackM / 2) * VB_PER_M, // 77.5
    width: CAR.frontTyreWidthM * VB_PER_M, // 27
  },
  cgX: CAR_STATIONS.cgX * VB_PER_M, // 202  -> transform-origin x = 202/560 = 36.07 %
} as const;

/** First gear only. The traction limit (~2.6 kN m) is below the torque cap, so the cap alone spins the rears. */
export const DRIVE = {
  maxAxleTorqueNm: 4500,
  powerW: 750_000, // never binds in 1st gear; kept for completeness
  omegaLimitRadS: 100, // ~36 m/s wheel surface speed
  limiterSoftnessRadS: 8,
  axleInertiaKgM2: 6, // both rears + reflected driveline through 1st gear; never below 3
  powerOmegaFloorRadS: 10, // T_avail = min(cap, powerW / max(omega, floor)) — keeps the power curve finite at stall
  diffCouplingNmPerRadS: 60, // viscous limited-slip diff: torque moved from the faster rear to the slower one per rad/s of speed difference
} as const;

/** Pacejka magic formula, longitudinal. mu(0.12)=1.70 peak, mu(0.35)=1.45, mu(1)=1.14, mu(inf)=0.89. */
export const TYRE = {
  B: 12,
  C: 1.65,
  D: 1.7,
  E: 0.3,
  slipRefFloorMps: 2.5, // s = v_sx / max(|v|, floor)
} as const;

/** One surface-layer temperature per rear tyre. Smoke needs a hot tyre; grip falls off above optimum. */
export const THERMAL = {
  heatCapJPerK: 110, // thin tread surface layer: must reach smokeFullC within a 0.65 s burnout at ~55 kW slip power per tyre
  slipToHeatFrac: 0.5,
  h0WPerK: 30,
  h1WsPerMK: 20, // convective term: tread must drop back below smokeOnC within ~1.5 s of the launch
  startC: 88, // out of blankets (warm enough that the tread smokes within ~0.25 s of the burnout)
  ambientC: 30,
  smokeOnC: 95, // tread starts to smoke here — low enough that the rev-hold shows wisps, not a cloud
  smokeFullC: 165,
  gripOptC: 105,
  gripCurvPerK2: 1.0e-5, // ±100 K off optimum costs 10 % grip; the post-burnout tread peaks ~210 C
  gripMin: 0.8,
} as const;

/** Driver model: throttle on a slip-velocity target, counter-steer PD on yaw. */
export const DRIVER = {
  /** Rev-hold: the rears only creep and heat. The real cloud belongs to the launch, not before it. */
  burnoutSlipTargetMps: 8,
  burnoutThrottleRampS: 0.15,
  /** Clutch dump: flat out for this long after release, controller bypassed — this is the wheelspin payoff. */
  launchDumpS: 0.32,
  launchSlipBase: 0.1,
  launchSlipExtra: 0.3,
  launchSlipDecayS: 0.35,
  throttleGainSPerM: 0.5, // driver lifts fully within ~2 m/s of excess slip velocity; below ~0.15 the P-loop parks the rears on the post-peak flank
  throttleBase: 0.35, // burnout feed-forward: ~r*mu(high slip)*fzRear / maxAxleTorque
  launchThrottleBase: 0.65, // launch feed-forward: ~r*mu_peak*fzRear / maxAxleTorque, so the P term only trims about the grip peak
  footLagS: 0.06,
  releaseLiftS: 0.1,
  steerKpRadPerRad: 3.0, // aggressive enough (with the lag) to overshoot and recover: that is the fishtail
  steerKdS: 0.05,
  steerLagS: 0.06,
  steerMaxRad: 8 * DEG,
  muAsymMin: 0.1, // L/R grip difference during the launch (one rear on the painted box line / dust)
  muAsymMax: 0.2,
  muAsymNoise: 0.1, // lets the asymmetry moment reverse sign during the launch
  muAsymNoiseTauS: 0.3,
  burnoutJitterRad: 0.7 * DEG,
  burnoutJitterHz: 25,
  burnoutJitterDecayS: 0.05, // jitterYaw relaxes to 0 with this time constant once the brakes come off
} as const;

export const YAW = {
  /**
   * Tyre lateral force builds with distance rolled (relaxation length), so at launch speeds the
   * lateral forces lag by tau = L / u. That lag is what lets the asymmetric drive moment rotate the
   * car before the front tyre can resist it — the fishtail — and it also bounds the yaw loop's
   * effective stiffness, so no artificial speed floor is needed for stability.
   */
  relaxationLengthM: 0.8,
  /** Rear lateral stiffness collapses under combined slip: C_eff = C / (1 + (s / ref)^2). */
  combinedSlipRef: 0.15,
  speedFloorMps: 0.5, // only guards the slip-angle division at a standstill
  maxBodyYawRad: 6 * DEG, // safety clamp, should never engage
} as const;

/**
 * F1 standing-start signal. Five columns come on one at a time, all go out together.
 * Real F1 lights come on roughly 1 s apart, then hold ALL RED for a deliberately unpredictable
 * 0.2-3 s (the FIA varies it every race precisely so drivers cannot anticipate lights-out and jump
 * the start). intervalS is compressed from the real 1 s so the intro stays a few seconds, but the
 * hold keeps that real unpredictability: it is drawn fresh from the seeded RNG on every load,
 * between holdMinS and holdMaxS, so lights-out never lands on the same beat twice.
 */
export const LIGHTS = {
  count: 5,
  firstAtS: 0.15,
  intervalS: 0.42,
  /** All-red hold before lights out — randomised per load between these bounds, see above. */
  holdMinS: 0.45,
  holdMaxS: 1.0,
  /** The physics starts (engine spools, rears begin to turn) when this light index comes on. */
  simStartLightIndex: 2,
  /** Gantry + grid markings fade over this long once the car launches. */
  fadeOutS: 0.45,
} as const;

/** Derived light-sequence instants, wall seconds from loop start. outS is the AVERAGE hold case —
 * the runtime draws the real, randomised value at load time and drives the loop off that instead. */
export const LIGHTS_AT = {
  allRedS: LIGHTS.firstAtS + (LIGHTS.count - 1) * LIGHTS.intervalS, // 1.83
  simStartS: LIGHTS.firstAtS + LIGHTS.simStartLightIndex * LIGHTS.intervalS, // 0.99
  outS: LIGHTS.firstAtS + (LIGHTS.count - 1) * LIGHTS.intervalS + (LIGHTS.holdMinS + LIGHTS.holdMaxS) / 2, // 2.56
} as const;

/**
 * Grid slot paint, car-frame metres (CG at origin, nose +x, screen-up = -y).
 * A real F1 grid slot is an open L — a lateral line the front wheels stop behind plus one
 * longitudinal line down a single side — not a closed box. Drawn white because the palette has
 * no yellow, and F1 uses white for the start/finish line anyway.
 */
export const GRID = {
  thicknessM: 0.12,
  frontLineXM: 3.8, // just ahead of the nose tip (3.58 m ahead of the CG)
  frontLineHalfSpanM: 1.3,
  sideLineYM: -1.15, // the left of the car is screen-up; keeps the tagline side clear
  sideLineBackXM: -3.2,
  alpha: 0.28,
} as const;

/**
 * "Engine on the limiter" is a PRESENTATION signal, not a physics claim: a real start holds the
 * revs behind a slipping clutch, and this model has no clutch (wheel speed is engine speed).
 * So the rev is shown through shudder and exhaust glow, never through fake wheel speed.
 */
export const REV = {
  flutterHz: 11,
  flutterDepth: 0.3,
  shakeMinPx: 0.8,
  shakeMaxPx: 3.2,
  exhaustMaxAlpha: 0.35,
  exhaustRadiusM: 0.45,
} as const;

export const SIM = {
  dt: 1 / 240,
  maxFrameDtS: 0.05, // background-tab catch-up clamp
  timeScale: 1.1, // sim seconds per wall second (never above 1.3)
  burnoutDurationS: 0.7375, // sim seconds ~ (lightsOut - simStart) * timeScale, rounded to 177 substeps
} as const;

/** Smoke emission (per rear tyre) and particle behaviour, in metres / seconds. */
export const SMOKE = {
  ratePerSPerTyre: 400, // many small puffs read as a cloud; a few big ones read as cotton balls
  slipPowerRefW: 40_000,
  lifeMinS: 1.2,
  lifeMaxS: 1.9,
  // Of v_sx. A spinning tyre throws its smoke REARWARD off the contact patch, so this has to
  // dominate the lateral/random terms — otherwise puffs scatter sideways off the top of the tyre
  // instead of trailing behind it.
  rearwardVelFrac: 0.18,
  lateralOutwardMps: 0.5,
  isotropicSpreadFrac: 0.35,
  dragTauS: 0.25,
  radius0M: 0.1,
  radiusGrowthM: 0.6, // r = r0 + growth*sqrt(age/life)
  alpha0: 0.2, // alpha = alpha0*(1-age/life)^1.5
  turbulenceMpsPerSqrtS: 1.2,
  capDesktop: 560,
  capLow: 280,
  hotCoreMaxAlpha: 0.25,
} as const;

/** Rubber deposition: two regimes blended on ds / patch length (see effects/marks.ts). */
export const MARKS = {
  patchLengthM: 0.12,
  qRefJPerM: 4000,
  /** Moving regime: alpha = movingGain * sqrt(q / qRef), capped. A single pass has to read on ~#3A tarmac. */
  movingGain: 2.0,
  movingAlphaCap: 0.85,
  staticEnergyJ: 12_000, // the rev-hold deposits less than a burnout did; keep the anchor patches readable
  featherWidthFrac: 1.6,
  featherAlphaFrac: 0.4,
  glossWidthFrac: 0.08,
  glossAlphaMax: 0.12, // specular streak: a hint, never brighter than the core is dark
  glossOffsetFrac: 0.15,
  haloWidthM: 0.5,
  haloAlphaPerFrame: 0.01, // per 60 Hz-frame-equivalent; scaled by the real frame duration (refresh-rate independent)
} as const;

/** Rendered car width (CSS px) vs viewport width W: clamp(minWidthPx, widthFracOfViewport*W, maxWidthPx); pxPerM = width / CAR.lengthM. */
export const CAR_RENDER = {
  minWidthPx: 280,
  widthFracOfViewport: 0.4,
  maxWidthPx: 620,
} as const;

/** Wall-clock choreography (seconds from loop start unless noted). */
export const TIMELINE = {
  // slower light cadence + a randomised (up to 1 s) all-red hold push lights-out to ~1.8-2.8 s now;
  // this is a safety-net fallback only and should not normally fire
  exitForcedWallS: 5.0,
  exitFractionOfWidth: 0.85, // rear axle x >= this * viewport width => 'exit'
  letterLagM: 0.4, // letter ignites when rear axle passes letter centre + lag
  letterRevealMs: 120,
  taglineAfterExitS: 0.1,
  // the finished lockup (name + tagline) now gets a real ~1.3 s hold before the fade starts
  fadeAfterExitS: 1.75,
  fadeDurationS: 0.4,
  failSafeMs: 7000,
  reducedMotionFadeS: 0.9,
  reducedMotionFailSafeMs: 1500,
  stagePushInScale: 1.015,
  launchKickPx: -5,
  launchKickTauS: 0.09,
  launchScaleRelaxS: 0.5,
} as const;
