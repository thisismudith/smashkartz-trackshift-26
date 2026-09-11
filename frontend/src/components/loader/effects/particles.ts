/**
 * Smoke particle pool for the intro loader.
 *
 * Struct-of-arrays storage in CSS px, sized once at construction; `emit` / `update` / `draw`
 * never allocate. When the pool is full, `emit` recycles the OLDEST live particle (lowest birth
 * stamp). Dead particles are swap-removed, so iteration order is not age order — hence the
 * explicit birth counter.
 *
 * Units: positions px, velocities px/s, radii px, ages/lives s. Callers convert m/s → px/s with
 * pxPerM before calling `emit` (see `smokeInitialVelocity`, which returns m/s).
 */
import { SMOKE } from "../physics/constants";

/** Smoke sprite variant index. `emit` accepts any integer; draw() wraps it modulo the sprite count. */
export type SmokeVariant = 0 | 1 | 2 | 3;

/** Sum of three uniforms minus 1.5 has std 0.5; scale by 2 for a unit-variance kick. */
const APPROX_GAUSS_SCALE = 2;
const TWO_PI = Math.PI * 2;

export class ParticlePool {
  readonly cap: number;
  private n = 0;
  /** Monotonic birth stamp; the smallest live value is the oldest particle. */
  private births = 0;

  private readonly x: Float32Array;
  private readonly y: Float32Array;
  private readonly vx: Float32Array;
  private readonly vy: Float32Array;
  private readonly r0: Float32Array;
  private readonly r: Float32Array;
  private readonly age: Float32Array;
  private readonly life: Float32Array;
  private readonly alpha0: Float32Array;
  private readonly alpha: Float32Array;
  private readonly variant: Uint8Array;
  private readonly birth: Float64Array;
  private readonly rng: () => number;

  constructor(cap: number, rng: () => number) {
    this.cap = Math.max(1, Math.floor(cap));
    this.rng = rng;
    const c = this.cap;
    this.x = new Float32Array(c);
    this.y = new Float32Array(c);
    this.vx = new Float32Array(c);
    this.vy = new Float32Array(c);
    this.r0 = new Float32Array(c);
    this.r = new Float32Array(c);
    this.age = new Float32Array(c);
    this.life = new Float32Array(c);
    this.alpha0 = new Float32Array(c);
    this.alpha = new Float32Array(c);
    this.variant = new Uint8Array(c);
    this.birth = new Float64Array(c);
  }

  /** Number of live particles. */
  get count(): number {
    return this.n;
  }

  /**
   * Spawn a particle. x/y px, vx/vy px/s, r0 px, life s, variant = sprite index (0..3, any
   * integer is wrapped at draw time), alpha0 in [0,1]. If the pool is full the oldest live
   * particle is overwritten.
   */
  emit(
    x: number,
    y: number,
    vx: number,
    vy: number,
    r0: number,
    life: number,
    variant: SmokeVariant | number,
    alpha0: number,
  ): void {
    let i: number;
    if (this.n < this.cap) {
      i = this.n++;
    } else {
      // Full: linear scan for the oldest (cap ≤ a few hundred, cheaper than keeping a heap).
      i = 0;
      let oldest = this.birth[0];
      for (let k = 1; k < this.n; k++) {
        if (this.birth[k] < oldest) {
          oldest = this.birth[k];
          i = k;
        }
      }
    }
    this.x[i] = x;
    this.y[i] = y;
    this.vx[i] = vx;
    this.vy[i] = vy;
    this.r0[i] = r0;
    this.r[i] = r0;
    this.age[i] = 0;
    this.life[i] = life > 0 ? life : SMOKE.lifeMinS;
    this.alpha0[i] = alpha0;
    this.alpha[i] = alpha0;
    this.variant[i] = variant & 0xff;
    this.birth[i] = this.births++;
  }

  /**
   * Advance every particle by dt seconds. pxPerM converts the metre-based turbulence and
   * radius-growth constants into px. Kills (swap-removes) particles whose age reaches their life.
   */
  update(dt: number, pxPerM: number): void {
    if (dt <= 0) return;
    const drag = Math.exp(-dt / SMOKE.dragTauS);
    // Random-walk velocity kick: sigma grows with sqrt(dt) so the walk is step-size independent.
    const sigma = SMOKE.turbulenceMpsPerSqrtS * pxPerM * Math.sqrt(dt) * APPROX_GAUSS_SCALE;
    const growth = SMOKE.radiusGrowthM * pxPerM;
    const rng = this.rng;
    const { x, y, vx, vy, r0, r, age, life, alpha0, alpha, variant, birth } = this;

    let i = 0;
    while (i < this.n) {
      const a = age[i] + dt;
      const L = life[i];
      if (a >= L) {
        // Swap-remove: move the last live particle into this slot and re-examine the slot.
        const last = --this.n;
        if (i !== last) {
          x[i] = x[last];
          y[i] = y[last];
          vx[i] = vx[last];
          vy[i] = vy[last];
          r0[i] = r0[last];
          r[i] = r[last];
          age[i] = age[last];
          life[i] = life[last];
          alpha0[i] = alpha0[last];
          alpha[i] = alpha[last];
          variant[i] = variant[last];
          birth[i] = birth[last];
        }
        continue;
      }
      age[i] = a;
      const kx = (rng() + rng() + rng() - 1.5) * sigma;
      const ky = (rng() + rng() + rng() - 1.5) * sigma;
      const nvx = vx[i] * drag + kx;
      const nvy = vy[i] * drag + ky;
      vx[i] = nvx;
      vy[i] = nvy;
      x[i] += nvx * dt;
      y[i] += nvy * dt;
      const f = a / L; // 0..1 life fraction
      r[i] = r0[i] + growth * Math.sqrt(f);
      const rem = 1 - f;
      alpha[i] = alpha0[i] * rem * Math.sqrt(rem); // (1-f)^1.5
      i++;
    }
  }

  /**
   * Draw all live particles: exactly one globalAlpha write + one drawImage per visible particle,
   * no save/restore, no gradients. Particles with alpha < 0.01 are skipped. Radius is clamped to
   * maxRadiusPx so late-life particles do not blow the fill-rate budget. Leaves globalAlpha = 1.
   */
  draw(ctx: CanvasRenderingContext2D, sprites: readonly CanvasImageSource[], maxRadiusPx: number): void {
    const { x, y, r, alpha, variant } = this;
    const nSprites = sprites.length;
    if (nSprites === 0) return;
    for (let i = 0; i < this.n; i++) {
      const a = alpha[i];
      if (a < 0.01) continue;
      const rad = r[i] < maxRadiusPx ? r[i] : maxRadiusPx;
      ctx.globalAlpha = a;
      ctx.drawImage(sprites[variant[i] % nSprites], x[i] - rad, y[i] - rad, rad * 2, rad * 2);
    }
    ctx.globalAlpha = 1;
  }
}

/**
 * Fractional-rate emission helper: accumulates ratePerS*dt into `acc.value` and returns the
 * integer number of particles to spawn this step, keeping the remainder for the next call.
 */
export function emissionCount(acc: { value: number }, ratePerS: number, dt: number): number {
  acc.value += ratePerS * dt;
  if (acc.value < 1) return 0;
  const n = Math.floor(acc.value);
  acc.value -= n;
  return n;
}

/**
 * Initial smoke velocity in m/s (ground frame) for a particle leaving a spinning rear tyre.
 *   rearward: -SMOKE.rearwardVelFrac * vsx along the car heading (cos yaw, sin yaw)
 *   lateral:   SMOKE.lateralOutwardMps * sideSign along the heading's perpendicular (-sin yaw, cos yaw);
 *              sideSign +1 = screen-down (y>0, "right") tyre, -1 = screen-up ("left") tyre
 *   spread:    isotropic, magnitude up to SMOKE.isotropicSpreadFrac × |deterministic part|,
 *              uniform over the disc (sqrt-radius), random direction.
 */
export function smokeInitialVelocity(
  rng: () => number,
  vsx: number,
  sideSign: -1 | 1,
  yaw: number,
  out: { vx: number; vy: number } = { vx: 0, vy: 0 },
): { vx: number; vy: number } {
  const hx = Math.cos(yaw);
  const hy = Math.sin(yaw);
  const rear = -SMOKE.rearwardVelFrac * vsx;
  const lat = SMOKE.lateralOutwardMps * sideSign;
  const dvx = rear * hx + lat * -hy;
  const dvy = rear * hy + lat * hx;
  const speed = Math.hypot(dvx, dvy);
  const mag = SMOKE.isotropicSpreadFrac * speed * Math.sqrt(rng());
  const ang = rng() * TWO_PI;
  // `out` lets the hot emission path reuse one scratch object instead of allocating per particle
  out.vx = dvx + mag * Math.cos(ang);
  out.vy = dvy + mag * Math.sin(ang);
  return out;
}
