/**
 * Seeded PRNG for the intro animation. Everything random in the loader must come from here so a
 * given seed replays the identical burnout (tests rely on it, and SSR/client never disagree).
 */

/**
 * mulberry32 — small, fast 32-bit generator with a full 2^32 period.
 * @returns a function yielding uniform numbers in [0, 1).
 */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Uniform sample in [lo, hi) from a [0, 1) generator. */
export function randRange(rng: () => number, lo: number, hi: number): number {
  return lo + (hi - lo) * rng();
}
