/**
 * Integration seam for the F1 standing-start intro animation.
 *
 * Wires the pure physics model (physics/car.ts) to the canvas effects (effects/*) and the DOM
 * (start lights, grid paint, car transform, tread offsets, wordmark letters, camera classes).
 * This is the only file in the loader that touches both a `dt` and the DOM; keep it that way so
 * the physics stays testable.
 *
 * Units: physics in metres / seconds (ground frame = screen frame, x right, y down, angles
 * clockwise-positive); everything drawn is converted with pxPerM = carRenderedWidthPx / 5.6.
 */

import { CarSim, defaultExitDistanceM } from "./physics/car";
import { CAR, CAR_STATIONS, LIGHTS, LIGHTS_AT, MARKS, REV, SIM, SMOKE, TIMELINE } from "./physics/constants";
import { mulberry32, randRange } from "./physics/prng";
import { ParticlePool, emissionCount, smokeInitialVelocity } from "./effects/particles";
import { depositAlpha, drawMarkDot, drawMarkSegment } from "./effects/marks";
import { makeSprites } from "./effects/sprites";
import { paintAsphalt } from "./effects/asphalt";

export interface LoaderElements {
  overlay: HTMLElement;
  stage: HTMLElement;
  marks: HTMLCanvasElement;
  smoke: HTMLCanvasElement;
  car: HTMLElement;
  /** One element per wordmark letter, in reading order. */
  letters: HTMLElement[];
  tagline: HTMLElement;
  /** Grid-slot paint wrapper (fades out at lights out). */
  grid: HTMLElement;
  /** Start-light gantry wrapper (fades out with the grid). */
  gantry: HTMLElement;
  /** One element per light column, left to right. */
  lightColumns: HTMLElement[];
}

export interface LoaderClasses {
  pushIn: string;
  kick: string;
  lit: string;
  shown: string;
  fading: string;
  gone: string;
  dimmed: string;
  clearing: string;
}

export interface LoaderOptions {
  classes: LoaderClasses;
  /** Called once the overlay's exit fade has ended (the component unmounts). */
  onDone: () => void;
  /** PRNG seed; defaults to a time-derived value so each load differs slightly. */
  seed?: number;
}

const TWO_PI = Math.PI * 2;
const TREAD_PITCH_PX = 10; // must match the gradient period in car-top.module.css
/** Stripes alias above ~4 px/frame; above this surface speed the tread cross-fades to a blur band. */
const TREAD_BLUR_START_MPS = 1.5;
const TREAD_BLUR_FULL_MPS = 5.5;
/** Under the blur band the stripes only crawl (px/s), so they never strobe. */
const TREAD_MAX_CRAWL_PX_PER_S = 150;
const HEAT_POWER_REF_W = 50_000; // the launch peaks around 60 kW of slip power per tyre
const HEAT_MAX_ALPHA = 0.45;
/** Slip energy over a render frame below which no rubber is deposited. */
const MIN_SLIP_ENERGY_J = 30;
/** Deposit alpha is low-passed this heavily, so throttle hunting cannot print a barcode. */
const MARK_ALPHA_SMOOTHING = 0.75;
/** Rubber fades out over the last stretch instead of hitting the screen edge like a scan line. */
const MARK_TAPER_START_FRAC = 0.6;
const MARK_TAPER_SPAN_FRAC = 0.45;
const MAX_SUBSTEPS = 13; // 50 ms of wall time at 240 Hz * 1.1
const LONG_FRAME_MS = 34;
/**
 * Presentation gain on body yaw / steer. The bicycle model is physically stiff at launch speeds
 * (peak body yaw ~1°), which is barely visible at this scale. The body is exaggerated more than the
 * contact patches on purpose: the tyres cover the patches while the car is over them, so the
 * mismatch is invisible, and a fully-exaggerated patch path would braid the stripes into wire.
 */
const VISUAL_YAW_GAIN = 4;
const VISUAL_PATCH_YAW_GAIN = 1.5;
const VISUAL_STEER_GAIN = 4;

interface WheelEls {
  outer: HTMLElement;
  tread: HTMLElement | null;
  blur: HTMLElement | null;
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

function treadBlur(surfaceMps: number): number {
  return clamp(
    (Math.abs(surfaceMps) - TREAD_BLUR_START_MPS) / (TREAD_BLUR_FULL_MPS - TREAD_BLUR_START_MPS),
    0,
    1,
  );
}

function queryWheels(car: HTMLElement): Record<"rl" | "rr" | "fl" | "fr", WheelEls | undefined> {
  const out: Partial<Record<"rl" | "rr" | "fl" | "fr", WheelEls>> = {};
  car.querySelectorAll<HTMLElement>("[data-wheel]").forEach((outer) => {
    const key = outer.dataset.wheel as "rl" | "rr" | "fl" | "fr" | undefined;
    if (!key) return;
    out[key] = {
      outer,
      tread: outer.querySelector<HTMLElement>("[data-tread]"),
      blur: outer.querySelector<HTMLElement>("[data-blur]"),
    };
  });
  return out as Record<"rl" | "rr" | "fl" | "fr", WheelEls | undefined>;
}

/**
 * Start the animation. Returns a dispose function that stops the loop, removes listeners, undoes
 * the DOM writes and releases canvas memory, so a re-run (React StrictMode, Fast Refresh) starts clean.
 */
export function runLoader(els: LoaderElements, opts: LoaderOptions): () => void {
  const { classes, onDone } = opts;
  let alive = true;

  // ---------- one-time layout reads (overlay box, not innerWidth: excludes any scrollbar) ----------
  const W = els.overlay.clientWidth || window.innerWidth;
  const H = els.overlay.clientHeight || window.innerHeight;
  const carPx = els.car.offsetWidth || Math.min(Math.max(0.4 * W, 280), 620);
  const pxPerM = carPx / CAR.lengthM;
  const cx0 = W / 2; // CG start (px)
  const cy0 = H / 2;
  const exitDistanceM = defaultExitDistanceM(W);
  const letterCentresPx = els.letters.map((el) => {
    const r = el.getBoundingClientRect();
    return r.left + r.width / 2;
  });
  // Ignition x for each letter: when the rear axle passes it (+ lag). Letters that start behind the
  // axle are staggered by travelled distance instead, so the car pulls the name out of its wake
  // rather than lighting them all on the first moving frame.
  const rearAxleStartPx = cx0 - CAR.cgToRearAxleM * pxPerM;
  const letterStaggerPx = 0.35 * pxPerM;
  const ignitePx = letterCentresPx.map((cx, i) =>
    Math.max(cx + TIMELINE.letterLagM * pxPerM, rearAxleStartPx + (i + 1) * letterStaggerPx),
  );

  // ---------- canvases ----------
  const marksDpr = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(6e6 / (W * H)));
  const smokeDpr = Math.min(1, Math.sqrt(2.5e6 / (W * H)));
  const marksCtx = setupCanvas(els.marks, W, H, marksDpr, false);
  const smokeCtx = setupCanvas(els.smoke, W, H, smokeDpr, true);
  if (!marksCtx || !smokeCtx) {
    // Canvas unavailable: skip straight to the brand moment.
    return runFallback(els, opts);
  }

  // ---------- model + effects ----------
  const seed = opts.seed ?? ((performance.now() * 1000) | 0) ^ 0x5eed2026;
  const rng = mulberry32(seed);
  // Real F1 holds the all-red for an unpredictable 0.2-3 s specifically so drivers cannot anticipate
  // lights-out; draw that hold fresh every load instead of using a fixed beat.
  const lightsOutS = LIGHTS_AT.allRedS + randRange(rng, LIGHTS.holdMinS, LIGHTS.holdMaxS);
  // The physics only runs from the moment the engine spools (light 3) to lights out.
  const burnoutDurationS = (lightsOutS - LIGHTS_AT.simStartS) * SIM.timeScale;
  const sim = new CarSim(seed, { burnoutDurationS });

  paintAsphalt(marksCtx, W, H, { centreX: cx0, centreY: cy0, rng });
  const sprites = makeSprites();
  const lowEnd = W * H < 500_000 || (navigator.hardwareConcurrency ?? 4) <= 4;
  const pool = new ParticlePool(lowEnd ? SMOKE.capLow : SMOKE.capDesktop, rng);
  // worst case (pool full of grown particles) ~ 560 x (2*72)^2 ~ 11.6 Mpx per frame on desktop
  const maxParticleRadiusPx = lowEnd ? Math.min(48, 0.5 * pxPerM) : Math.min(72, 0.7 * pxPerM);

  /** Rear contact patches in ground metres using the PRESENTATION yaw, so marks match the drawn body. */
  const patchL = { x: 0, y: 0 };
  const patchR = { x: 0, y: 0 };
  const patches = [patchL, patchR] as const;
  const rearPatches = () => {
    const s = sim.state;
    const ang = s.yaw * VISUAL_PATCH_YAW_GAIN + s.jitterYaw;
    const c = Math.cos(ang);
    const sn = Math.sin(ang);
    const bx = -CAR.cgToRearAxleM; // rear axle behind the CG, car frame
    const half = CAR.rearTrackM / 2;
    // car frame (bx, ±half) rotated clockwise-positive into the screen frame, then translated by the CG
    patchL.x = s.x + bx * c - -half * sn;
    patchL.y = s.y + bx * sn + -half * c;
    patchR.x = s.x + bx * c - half * sn;
    patchR.y = s.y + bx * sn + half * c;
    return patches;
  };

  const wheels = queryWheels(els.car);
  const tyreWidthPx = CAR.rearTyreWidthM * pxPerM;
  const haloWidthPx = MARKS.haloWidthM * pxPerM;
  const heatRadiusPx = 1.2 * CAR.rearTyreWidthM * pxPerM;
  const hotCoreRadiusPx = 0.3 * pxPerM;
  const exhaustRadiusPx = REV.exhaustRadiusM * pxPerM;
  /** Exhaust/crash structure, car frame: the rearmost point is cgX behind the CG. */
  const exhaustXM = -(CAR_STATIONS.cgX - 0.15);

  // per-rear-tyre bookkeeping (index 0 = left / screen-up, 1 = right / screen-down)
  const emitAcc = [{ value: 0 }, { value: 0 }];
  const slipEnergyJ = [0, 0];
  const prevPatch = [
    { x: 0, y: 0, valid: false },
    { x: 0, y: 0, valid: false },
  ];
  const markAlphaLp = [0, 0];
  const v0 = { vx: 0, vy: 0 }; // scratch for smokeInitialVelocity
  /**
   * Tread travel (px) integrated per substep at the DISPLAY rate: the physical surface speed while
   * stripes are readable, clamped to a slow crawl once the blur band covers them. Integrating the
   * rate (not scaling the accumulated angle) is what keeps stripes from jumping when the blur changes.
   */
  const treadTravelPx = { rear: 0, front: 0 };

  // ---------- choreography state ----------
  let rafId = 0;
  let lastNow = 0;
  let wall = 0; // seconds since loop start
  let acc = 0; // sim-seconds owed to the integrator
  let litCount = 0;
  let revving = false; // engine spooling: the physics is running
  let launched = false; // lights out
  let revFrac = 0;
  let exitAt = -1;
  let taglineShown = false;
  let fadeStarted = false;
  let carGone = false;
  let emissionScale = 1;
  let longFrames = 0;
  const timers: number[] = [];

  const lightLetter = (i: number) => {
    const el = els.letters[i];
    if (el && !el.classList.contains(classes.lit)) el.classList.add(classes.lit);
  };

  const finish = () => {
    if (!alive) return;
    onDone();
  };

  const onOverlayTransitionEnd = (e: TransitionEvent) => {
    if (e.target === els.overlay && e.propertyName === "opacity") finish();
  };
  els.overlay.addEventListener("transitionend", onOverlayTransitionEnd);

  /** All five reds go out together: brakes off, camera recoil, and the grid is left behind. */
  const lightsOut = () => {
    if (launched) return;
    launched = true;
    for (const col of els.lightColumns) col.classList.remove(classes.lit);
    els.grid.classList.add(classes.gone);
    els.gantry.classList.add(classes.gone);
    els.stage.classList.remove(classes.pushIn);
    els.stage.classList.add(classes.kick);
    sim.forceRelease();
  };

  const triggerExit = () => {
    if (exitAt >= 0) return;
    exitAt = wall;
    lightsOut();
    for (let i = 0; i < els.letters.length; i++) lightLetter(i);
    // the rubber drops back to background texture and the haze clears, so the lockup reads clean
    els.marks.classList.add(classes.dimmed);
    els.smoke.classList.add(classes.clearing);
    timers.push(
      window.setTimeout(() => {
        if (!alive || taglineShown) return;
        taglineShown = true;
        els.tagline.classList.add(classes.shown);
      }, TIMELINE.taglineAfterExitS * 1000),
    );
    timers.push(
      window.setTimeout(() => {
        if (!alive || fadeStarted) return;
        fadeStarted = true;
        els.overlay.classList.add(classes.fading);
      }, TIMELINE.fadeAfterExitS * 1000),
    );
  };

  const onVisibility = () => {
    // Coming back from a hidden tab: do not "catch up" seconds of physics — jump to the brand moment.
    if (document.visibilityState === "visible" && alive) triggerExit();
  };
  document.addEventListener("visibilitychange", onVisibility);

  // ---------- per-substep work ----------
  const stepSim = (simDt: number) => {
    sim.step(simDt);
    const s = sim.state;
    const sides = rearPatches();
    for (let i = 0; i < 2; i++) {
      const tyre = s.rear[i];
      slipEnergyJ[i] += tyre.slipPowerW * simDt;
      if (exitAt < 0) {
        const rate =
          SMOKE.ratePerSPerTyre *
          emissionScale *
          tyre.smokeGain *
          Math.min(1, tyre.slipPowerW / SMOKE.slipPowerRefW);
        const n = emissionCount(emitAcc[i], rate, simDt);
        for (let k = 0; k < n; k++) {
          smokeInitialVelocity(rng, tyre.vsx, i === 0 ? -1 : 1, s.yaw + s.jitterYaw, v0);
          pool.emit(
            cx0 + sides[i].x * pxPerM,
            cy0 + sides[i].y * pxPerM,
            v0.vx * pxPerM,
            v0.vy * pxPerM,
            SMOKE.radius0M * pxPerM * randRange(rng, 0.8, 1.2),
            randRange(rng, SMOKE.lifeMinS, SMOKE.lifeMaxS),
            (rng() * 4) | 0,
            SMOKE.alpha0,
          );
        }
      }
    }
    // tread travel at the display rate (see treadTravelPx)
    const rearSurface = ((s.rear[0].omega + s.rear[1].omega) / 2) * CAR.wheelRadiusM; // m/s
    treadTravelPx.rear = advanceTread(treadTravelPx.rear, rearSurface, simDt);
    treadTravelPx.front = advanceTread(treadTravelPx.front, s.frontSurfaceSpeed, simDt);
  };

  const advanceTread = (travel: number, surfaceMps: number, dt: number): number => {
    const raw = surfaceMps * pxPerM; // px/s
    const rate = Math.sign(raw) * Math.min(Math.abs(raw), TREAD_MAX_CRAWL_PX_PER_S);
    const t = travel + rate * dt;
    return ((t % TREAD_PITCH_PX) + TREAD_PITCH_PX) % TREAD_PITCH_PX;
  };

  // ---------- per-frame work ----------
  const drawMarks = (dtWall: number) => {
    const s = sim.state;
    const sides = rearPatches();
    const frameScale = dtWall * 60; // halo accumulates per unit time, not per frame
    const taper = clamp(
      1 - (s.x - MARK_TAPER_START_FRAC * exitDistanceM) / (MARK_TAPER_SPAN_FRAC * exitDistanceM),
      0,
      1,
    );
    for (let i = 0; i < 2; i++) {
      const cx = cx0 + sides[i].x * pxPerM;
      const cy = cy0 + sides[i].y * pxPerM;
      const prev = prevPatch[i];
      const e = slipEnergyJ[i];
      slipEnergyJ[i] = 0;
      if (prev.valid && e > MIN_SLIP_ENERGY_J) {
        const dx = cx - prev.x;
        const dy = cy - prev.y;
        const dsPx = Math.hypot(dx, dy);
        const raw = depositAlpha(e, dsPx / pxPerM, s.rear[i].markGain) * taper;
        const alpha = (markAlphaLp[i] =
          MARK_ALPHA_SMOOTHING * markAlphaLp[i] + (1 - MARK_ALPHA_SMOOTHING) * raw);
        if (alpha > 0.002) {
          if (dsPx < 0.5) drawMarkDot(marksCtx, cx, cy, tyreWidthPx, haloWidthPx, alpha);
          else
            drawMarkSegment(
              marksCtx,
              { x0: prev.x, y0: prev.y, x1: cx, y1: cy },
              tyreWidthPx,
              haloWidthPx,
              alpha,
              frameScale,
            );
        }
      } else {
        markAlphaLp[i] = 0;
      }
      prev.x = cx;
      prev.y = cy;
      prev.valid = true;
    }
  };

  const drawSmoke = (simDtThisFrame: number, flutter: number) => {
    const s = sim.state;
    smokeCtx.clearRect(0, 0, W, H);
    if (!carGone) {
      const sides = rearPatches();
      // exhaust glow: the "engine on the limiter" cue while the car is held on the grid
      if (revFrac > 0.01) {
        const ang = s.yaw * VISUAL_YAW_GAIN + s.jitterYaw;
        const ex = cx0 + (s.x + exhaustXM * Math.cos(ang)) * pxPerM;
        const ey = cy0 + (s.y + exhaustXM * Math.sin(ang)) * pxPerM;
        smokeCtx.globalAlpha = REV.exhaustMaxAlpha * revFrac * (0.7 + 0.3 * flutter);
        smokeCtx.drawImage(
          sprites.heat,
          ex - exhaustRadiusPx,
          ey - exhaustRadiusPx,
          2 * exhaustRadiusPx,
          2 * exhaustRadiusPx,
        );
      }
      for (let i = 0; i < 2; i++) {
        const tyre = s.rear[i];
        const px = cx0 + sides[i].x * pxPerM;
        const py = cy0 + sides[i].y * pxPerM;
        const heat = HEAT_MAX_ALPHA * Math.min(1, tyre.slipPowerW / HEAT_POWER_REF_W);
        if (heat > 0.01) {
          smokeCtx.globalAlpha = heat;
          smokeCtx.drawImage(sprites.heat, px - heatRadiusPx, py - heatRadiusPx, 2 * heatRadiusPx, 2 * heatRadiusPx);
        }
        const core =
          SMOKE.hotCoreMaxAlpha * tyre.smokeGain * Math.min(1, tyre.slipPowerW / SMOKE.slipPowerRefW);
        if (core > 0.01) {
          smokeCtx.globalAlpha = core;
          smokeCtx.drawImage(
            sprites.hotCore,
            px - hotCoreRadiusPx,
            py - hotCoreRadiusPx,
            2 * hotCoreRadiusPx,
            2 * hotCoreRadiusPx,
          );
        }
      }
      smokeCtx.globalAlpha = 1;
    }
    pool.update(simDtThisFrame, pxPerM);
    pool.draw(smokeCtx, sprites.smoke, maxParticleRadiusPx);
  };

  const writeCar = (shakePx: number) => {
    const s = sim.state;
    // Chassis shudder while the engine is held on the limiter; on the car element alone, never the canvases.
    const jx = shakePx > 0 ? (rng() - 0.5) * shakePx : 0;
    const jy = shakePx > 0 ? (rng() - 0.5) * shakePx : 0;
    const tx = s.x * pxPerM + jx;
    const ty = s.y * pxPerM + jy;
    const bodyAngle = s.yaw * VISUAL_YAW_GAIN + s.jitterYaw;
    els.car.style.transform = `translate3d(${tx.toFixed(2)}px, ${ty.toFixed(2)}px, 0) rotate(${bodyAngle.toFixed(4)}rad)`;

    const rearSurface = ((s.rear[0].omega + s.rear[1].omega) / 2) * CAR.wheelRadiusM; // m/s
    const steer = s.steer * VISUAL_STEER_GAIN;
    writeWheel(wheels.rl, treadTravelPx.rear, rearSurface, 0);
    writeWheel(wheels.rr, treadTravelPx.rear, rearSurface, 0);
    writeWheel(wheels.fl, treadTravelPx.front, s.frontSurfaceSpeed, steer);
    writeWheel(wheels.fr, treadTravelPx.front, s.frontSurfaceSpeed, steer);
  };

  const writeWheel = (w: WheelEls | undefined, travelPx: number, surfaceMps: number, steerRad: number) => {
    if (!w) return;
    const blur = treadBlur(surfaceMps);
    // In plan view the visible (top) tread surface moves WITH the car, so stripes scroll +x.
    // Offset stays in (-pitch, 0] so the 200 %-wide tread never exposes an edge.
    const offset = travelPx - TREAD_PITCH_PX;
    if (w.tread) {
      w.tread.style.transform = `translate3d(${offset.toFixed(2)}px, 0, 0)`;
      w.tread.style.opacity = (1 - blur).toFixed(3);
    }
    if (w.blur) w.blur.style.opacity = blur.toFixed(3);
    if (steerRad !== 0 || w.outer.style.transform) {
      w.outer.style.transform = steerRad === 0 ? "" : `rotate(${steerRad.toFixed(4)}rad)`;
    }
  };

  const frame = (now: number) => {
    if (!alive) return;
    rafId = requestAnimationFrame(frame);
    const frameMs = lastNow ? now - lastNow : 16;
    lastNow = now;
    const dtWall = Math.min(frameMs / 1000, SIM.maxFrameDtS);
    wall += dtWall;

    // one-way quality degrade on sustained long frames
    if (frameMs > LONG_FRAME_MS) {
      if (++longFrames >= 2) emissionScale = 0.5;
    } else longFrames = 0;

    // ---- start-light sequence (wall-clock; the whole anticipation beat) ----
    while (litCount < LIGHTS.count && wall >= LIGHTS.firstAtS + litCount * LIGHTS.intervalS) {
      els.lightColumns[litCount]?.classList.add(classes.lit);
      litCount++;
    }
    if (!revving && wall >= LIGHTS_AT.simStartS) {
      revving = true;
      els.stage.classList.add(classes.pushIn);
    }
    if (!launched && wall >= lightsOutS) lightsOut();

    // Engine-on-the-limiter presentation signal: ramps in over lights 2→5, holds, dies at launch.
    // The model has no clutch, so revs are shown through shudder and exhaust glow, never wheel speed.
    const revTarget = launched
      ? 0
      : clamp(
          (wall - (LIGHTS.firstAtS + LIGHTS.intervalS)) / ((LIGHTS.count - 2) * LIGHTS.intervalS),
          0,
          1,
        );
    revFrac += (revTarget - revFrac) * Math.min(1, dtWall / 0.12);
    const flutter = 0.5 + 0.5 * Math.sin(TWO_PI * REV.flutterHz * wall);
    const revAmp = revFrac * (1 - REV.flutterDepth + REV.flutterDepth * flutter);
    const shakePx = revFrac > 0.01 ? REV.shakeMinPx + (REV.shakeMaxPx - REV.shakeMinPx) * revAmp : 0;

    // ---- physics: consume exactly the owed sim time in n equal substeps (h <= SIM.dt), so the
    //      rendered advance per frame is smooth instead of alternating 4/5 fixed steps ----
    let simDtThisFrame = 0;
    if (revving && !carGone) {
      acc += dtWall * SIM.timeScale;
      const n = Math.min(MAX_SUBSTEPS, Math.ceil(acc / SIM.dt));
      const h = n > 0 ? Math.min(SIM.dt, acc / n) : 0;
      for (let i = 0; i < n; i++) stepSim(h);
      simDtThisFrame = n * h;
      acc = 0; // any remainder beyond MAX_SUBSTEPS is dropped rather than spiralling
    } else {
      simDtThisFrame = dtWall * SIM.timeScale;
    }

    const s = sim.state;

    // ---- letters ignite as the rear axle passes them (never before lights out) ----
    const rearAxlePx = cx0 + (s.x - CAR.cgToRearAxleM) * pxPerM;
    if (exitAt < 0 && launched) {
      for (let i = 0; i < ignitePx.length; i++) {
        if (rearAxlePx >= ignitePx[i]) lightLetter(i);
      }
    }
    if (exitAt < 0 && (rearAxlePx >= TIMELINE.exitFractionOfWidth * W || wall >= TIMELINE.exitForcedWallS)) {
      triggerExit();
    }
    // stop stepping once the whole car is off the right edge (rear of car = rear axle - overhang)
    if (!carGone && rearAxlePx - CAR.rearOverhangM * pxPerM > W + 40) carGone = true;

    // ---- draw ----
    if (revving && !carGone) {
      drawMarks(dtWall);
      writeCar(shakePx);
    }
    drawSmoke(simDtThisFrame, flutter);
  };

  if (document.visibilityState === "hidden") {
    // Nothing will render; go straight to the exit beats so the fail-safe is not the only way out.
    triggerExit();
  }
  rafId = requestAnimationFrame((t) => {
    lastNow = t;
    frame(t);
  });

  return () => {
    alive = false;
    cancelAnimationFrame(rafId);
    for (const t of timers) window.clearTimeout(t);
    document.removeEventListener("visibilitychange", onVisibility);
    els.overlay.removeEventListener("transitionend", onOverlayTransitionEnd);
    // release backing stores (Safari keeps them until zeroed); also makes a re-mount start clean
    els.marks.width = els.marks.height = 0;
    els.smoke.width = els.smoke.height = 0;
    // undo every DOM write the loop made, so a re-run (StrictMode / Fast Refresh) replays from the SSR pose
    els.stage.classList.remove(classes.pushIn, classes.kick);
    els.overlay.classList.remove(classes.fading);
    els.tagline.classList.remove(classes.shown);
    els.marks.classList.remove(classes.dimmed);
    els.smoke.classList.remove(classes.clearing);
    els.grid.classList.remove(classes.gone);
    els.gantry.classList.remove(classes.gone);
    for (const col of els.lightColumns) col.classList.remove(classes.lit);
    for (const l of els.letters) l.classList.remove(classes.lit);
    els.car.style.transform = "";
    for (const w of [wheels.rl, wheels.rr, wheels.fl, wheels.fr]) {
      if (!w) continue;
      w.outer.style.transform = "";
      if (w.tread) {
        w.tread.style.transform = "";
        w.tread.style.opacity = "";
      }
      if (w.blur) w.blur.style.opacity = "";
    }
  };
}

function setupCanvas(
  canvas: HTMLCanvasElement,
  wCss: number,
  hCss: number,
  dpr: number,
  alpha: boolean,
): CanvasRenderingContext2D | null {
  canvas.width = Math.max(1, Math.round(wCss * dpr));
  canvas.height = Math.max(1, Math.round(hCss * dpr));
  const ctx = canvas.getContext("2d", { alpha });
  if (!ctx) return null;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}

/** No canvas (very old browser / blocked): show the name, fade, done. */
function runFallback(els: LoaderElements, opts: LoaderOptions): () => void {
  let alive = true;
  els.letters.forEach((l) => l.classList.add(opts.classes.lit));
  els.tagline.classList.add(opts.classes.shown);
  els.grid.classList.add(opts.classes.gone);
  els.gantry.classList.add(opts.classes.gone);
  const onEnd = (e: TransitionEvent) => {
    if (e.target === els.overlay && e.propertyName === "opacity" && alive) opts.onDone();
  };
  els.overlay.addEventListener("transitionend", onEnd);
  const t = window.setTimeout(() => els.overlay.classList.add(opts.classes.fading), 900);
  return () => {
    alive = false;
    window.clearTimeout(t);
    els.overlay.removeEventListener("transitionend", onEnd);
    els.overlay.classList.remove(opts.classes.fading);
    els.tagline.classList.remove(opts.classes.shown);
    els.grid.classList.remove(opts.classes.gone);
    els.gantry.classList.remove(opts.classes.gone);
    els.letters.forEach((l) => l.classList.remove(opts.classes.lit));
  };
}
