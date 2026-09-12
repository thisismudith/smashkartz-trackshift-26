import { describe, expect, it } from "vitest";
import { SMOKE } from "../physics/constants";
import { ParticlePool, emissionCount, smokeInitialVelocity } from "./particles";

/** Local seeded PRNG so the test does not depend on physics/prng.ts landing first. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Minimal fake 2D context recording only what ParticlePool.draw is allowed to call. */
interface DrawCall {
  alpha: number;
  sprite: number;
  x: number;
  y: number;
  w: number;
  h: number;
}
function fakeCtx(sprites: readonly CanvasImageSource[]) {
  const calls: DrawCall[] = [];
  const ctx = {
    globalAlpha: 1,
    drawImage(img: CanvasImageSource, x: number, y: number, w: number, h: number) {
      calls.push({ alpha: this.globalAlpha, sprite: sprites.indexOf(img), x, y, w, h });
    },
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls };
}
const SPRITES = [{}, {}, {}, {}] as unknown as CanvasImageSource[];

describe("emissionCount", () => {
  it("accumulates fractional rate into whole particles without losing the remainder", () => {
    const acc = { value: 0 };
    let total = 0;
    for (let i = 0; i < 100; i++) total += emissionCount(acc, 130, 1 / 60);
    // 130/s over 100/60 s = 216.67 → 216 emitted, 0.67 carried
    expect(total).toBe(216);
    expect(acc.value).toBeCloseTo(216.6667 - 216, 3);
  });
  it("returns 0 and keeps accumulating below one particle", () => {
    const acc = { value: 0 };
    expect(emissionCount(acc, 10, 0.05)).toBe(0);
    expect(acc.value).toBeCloseTo(0.5, 9);
  });
});

describe("smokeInitialVelocity", () => {
  const zeroRng = () => 0; // spread magnitude sqrt(0) = 0 → deterministic part only
  it("yaw 0: rearward along -x, outward lateral by sideSign", () => {
    const v = smokeInitialVelocity(zeroRng, 30, 1, 0);
    expect(v.vx).toBeCloseTo(-SMOKE.rearwardVelFrac * 30, 9);
    expect(v.vy).toBeCloseTo(SMOKE.lateralOutwardMps, 9);
    const l = smokeInitialVelocity(zeroRng, 30, -1, 0);
    expect(l.vy).toBeCloseTo(-SMOKE.lateralOutwardMps, 9);
  });
  it("rotates with yaw (heading = (cos, sin), perp = (-sin, cos))", () => {
    const yaw = Math.PI / 2;
    const v = smokeInitialVelocity(zeroRng, 30, 1, yaw);
    // heading is +y, so rearward is -y; perp is -x
    expect(v.vx).toBeCloseTo(-SMOKE.lateralOutwardMps, 9);
    expect(v.vy).toBeCloseTo(-SMOKE.rearwardVelFrac * 30, 9);
  });
  it("isotropic spread never exceeds the spread fraction of the deterministic speed", () => {
    const rng = mulberry32(7);
    const base = smokeInitialVelocity(zeroRng, 30, 1, 0.1);
    const speed = Math.hypot(base.vx, base.vy);
    for (let i = 0; i < 200; i++) {
      const v = smokeInitialVelocity(rng, 30, 1, 0.1);
      const dev = Math.hypot(v.vx - base.vx, v.vy - base.vy);
      expect(dev).toBeLessThanOrEqual(SMOKE.isotropicSpreadFrac * speed + 1e-9);
    }
  });
  it("is deterministic for the same seed", () => {
    const a = smokeInitialVelocity(mulberry32(3), 20, -1, 0.05);
    const b = smokeInitialVelocity(mulberry32(3), 20, -1, 0.05);
    expect(a).toEqual(b);
  });
});

describe("ParticlePool", () => {
  it("counts emits up to cap and recycles the oldest when full", () => {
    const pool = new ParticlePool(3, mulberry32(1));
    expect(pool.cap).toBe(3);
    pool.emit(0, 0, 0, 0, 5, 1, 0, 0.5); // oldest
    pool.emit(1, 0, 0, 0, 5, 1, 1, 0.5);
    pool.emit(2, 0, 0, 0, 5, 1, 2, 0.5);
    expect(pool.count).toBe(3);
    pool.emit(3, 0, 0, 0, 5, 1, 3, 0.5); // must overwrite the x=0 particle
    expect(pool.count).toBe(3);
    const { ctx, calls } = fakeCtx(SPRITES);
    pool.draw(ctx, SPRITES, 100);
    const xs = calls.map((c) => c.x + c.w / 2).sort((a, b) => a - b);
    expect(xs).toEqual([1, 2, 3]);
  });

  it("ages, grows, fades, moves and kills at end of life without NaN", () => {
    const pool = new ParticlePool(8, mulberry32(11));
    const pxPerM = 60;
    pool.emit(100, 100, 30, 0, 10, 1.0, 0, SMOKE.alpha0);
    const dt = 1 / 120;
    const { ctx, calls } = fakeCtx(SPRITES);
    let lastAlpha = Infinity;
    let lastR = 0;
    let steps = 0;
    while (pool.count > 0 && steps < 1000) {
      pool.update(dt, pxPerM);
      steps++;
      calls.length = 0;
      pool.draw(ctx, SPRITES, 1e9);
      if (calls.length) {
        const c = calls[0];
        expect(Number.isFinite(c.x) && Number.isFinite(c.y)).toBe(true);
        expect(c.alpha).toBeLessThanOrEqual(lastAlpha);
        expect(c.w / 2).toBeGreaterThanOrEqual(lastR);
        lastAlpha = c.alpha;
        lastR = c.w / 2;
      }
    }
    expect(pool.count).toBe(0);
    // life 1.0 s at 120 Hz: dies on the 120th step (age >= life), ±1 for float32 age accumulation
    expect(steps).toBeGreaterThanOrEqual(119);
    expect(steps).toBeLessThanOrEqual(121);
    // radius grew toward r0 + growth*pxPerM
    expect(lastR).toBeGreaterThan(10 + 0.9 * SMOKE.radiusGrowthM * pxPerM);
  });

  it("applies exponential drag to the mean velocity", () => {
    // Turbulence is zero-mean; with pxPerM = 0 it vanishes entirely, isolating the drag.
    const pool = new ParticlePool(1, mulberry32(5));
    pool.emit(0, 0, 100, 0, 1, 10, 0, 1);
    const dt = 0.1;
    pool.update(dt, 0);
    const { ctx, calls } = fakeCtx(SPRITES);
    pool.draw(ctx, SPRITES, 1e9);
    const x = calls[0].x + calls[0].w / 2;
    expect(x).toBeCloseTo(100 * Math.exp(-dt / SMOKE.dragTauS) * dt, 6);
  });

  it("draw: one drawImage per visible particle, clamps radius, skips faint ones, resets alpha", () => {
    const pool = new ParticlePool(4, mulberry32(42));
    pool.emit(10, 10, 0, 0, 50, 2, 1, 0.8); // radius 50 > clamp 20
    pool.emit(20, 20, 0, 0, 5, 2, 2, 0.005); // alpha < 0.01 → skipped
    const { ctx, calls } = fakeCtx(SPRITES);
    ctx.globalAlpha = 0.37;
    pool.draw(ctx, SPRITES, 20);
    expect(calls.length).toBe(1);
    const c = calls[0];
    expect(c.alpha).toBeCloseTo(0.8, 6); // Float32 storage
    expect([c.sprite, c.x, c.y, c.w, c.h]).toEqual([1, -10, -10, 40, 40]);
    expect(ctx.globalAlpha).toBe(1);
  });

  it("swap-remove keeps every survivor after mixed lifetimes", () => {
    const pool = new ParticlePool(16, mulberry32(9));
    for (let i = 0; i < 10; i++) pool.emit(i, 0, 0, 0, 1, i % 2 ? 0.5 : 5, 0, 1);
    pool.update(0.6, 0); // odd-indexed die
    expect(pool.count).toBe(5);
    const { ctx, calls } = fakeCtx(SPRITES);
    pool.draw(ctx, SPRITES, 1e9);
    const xs = calls.map((c) => c.x + c.w / 2).sort((a, b) => a - b);
    expect(xs).toEqual([0, 2, 4, 6, 8]);
  });
});
