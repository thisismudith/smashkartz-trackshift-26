/**
 * Pre-rasterised soft discs for the smoke canvas. Built once via ImageData (no per-particle
 * gradients at draw time). DOM-only: `document` is touched inside makeSprites() only, so the
 * module itself loads under node.
 *
 * Every sprite is a single palette colour with a radial alpha falloff (1-r^2)^k; the smoke
 * variants get 2–3 low-weight off-centre sub-blobs so overlapping particles do not read as
 * identical stamped circles.
 */
import { HAAS } from "@/lib/palette";

export interface LoaderSprites {
  /** 4 variants, 64×64, #AEAEAE, (1-r^2)^2 falloff with slight per-variant lumpiness. */
  smoke: HTMLCanvasElement[];
  /** 48×48, #EFEFEF, (1-r^2)^2 — bright core stamped under fresh smoke. */
  hotCore: HTMLCanvasElement;
  /** 96×96, #DA291C, (1-r^2)^2 — soft tyre-heat glow. */
  heat: HTMLCanvasElement;
}

export const SPRITE_SIZE = { smoke: 64, hotCore: 48, heat: 96 } as const;

interface SubBlob {
  /** Offset from centre, in units of the sprite radius. */
  ox: number;
  oy: number;
  /** Sub-blob radius, in units of the sprite radius. */
  rad: number;
  /** Peak alpha contribution, 0..1. */
  w: number;
}

/** Fixed per-variant lump layouts (deterministic; no PRNG needed for a one-off rasterisation). */
const SMOKE_LUMPS: readonly (readonly SubBlob[])[] = [
  [
    { ox: 0.32, oy: -0.18, rad: 0.5, w: 0.22 },
    { ox: -0.28, oy: 0.26, rad: 0.45, w: 0.18 },
  ],
  [
    { ox: -0.3, oy: -0.3, rad: 0.48, w: 0.2 },
    { ox: 0.22, oy: 0.3, rad: 0.42, w: 0.16 },
    { ox: 0.34, oy: -0.1, rad: 0.36, w: 0.12 },
  ],
  [
    { ox: 0.1, oy: -0.36, rad: 0.5, w: 0.2 },
    { ox: -0.36, oy: 0.06, rad: 0.44, w: 0.18 },
    { ox: 0.24, oy: 0.3, rad: 0.38, w: 0.12 },
  ],
  [
    { ox: -0.2, oy: -0.28, rad: 0.46, w: 0.18 },
    { ox: 0.36, oy: 0.14, rad: 0.5, w: 0.22 },
  ],
];

function hexToRgb(hex: string): [number, number, number] {
  const v = parseInt(hex.slice(1), 16);
  return [(v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff];
}

/** (1 - r^2)^k for r < 1, else 0. */
function falloff(r2: number, k: number): number {
  if (r2 >= 1) return 0;
  const b = 1 - r2;
  let v = b;
  for (let i = 1; i < k; i++) v *= b;
  return v;
}

/**
 * Rasterise one disc. Alpha channel carries the falloff; RGB is the flat palette colour so
 * blending never introduces a new hue.
 */
function makeDisc(sizePx: number, hex: string, k: number, lumps: readonly SubBlob[]): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = sizePx;
  canvas.height = sizePx;
  const ctx = canvas.getContext("2d");
  if (!ctx) return canvas; // headless / test-only environments: leave a blank canvas
  const img = ctx.createImageData(sizePx, sizePx);
  const data = img.data;
  const [cr, cg, cb] = hexToRgb(hex);
  const half = sizePx / 2;
  const inv = 1 / half;
  let p = 0;
  for (let py = 0; py < sizePx; py++) {
    const ny = (py + 0.5 - half) * inv; // -1..1
    for (let px = 0; px < sizePx; px++) {
      const nx = (px + 0.5 - half) * inv;
      const r2 = nx * nx + ny * ny;
      const main = falloff(r2, k);
      // Screen-combine sub-blobs, then envelope by the main disc so the edge still reaches 0.
      let lump = 0;
      for (let i = 0; i < lumps.length; i++) {
        const L = lumps[i];
        const dx = (nx - L.ox) / L.rad;
        const dy = (ny - L.oy) / L.rad;
        const s = falloff(dx * dx + dy * dy, 2) * L.w;
        lump = lump + s - lump * s;
      }
      const envelope = r2 >= 1 ? 0 : 1 - r2;
      const a = main + lump * envelope - main * lump * envelope; // screen blend
      data[p] = cr;
      data[p + 1] = cg;
      data[p + 2] = cb;
      data[p + 3] = Math.round(Math.min(1, a) * 255);
      p += 4;
    }
  }
  ctx.putImageData(img, 0, 0);
  return canvas;
}

/** Build all loader sprites. Call once, on the client, after mount. */
export function makeSprites(): LoaderSprites {
  return {
    smoke: SMOKE_LUMPS.map((lumps) => makeDisc(SPRITE_SIZE.smoke, HAAS.grey, 2, lumps)),
    hotCore: makeDisc(SPRITE_SIZE.hotCore, HAAS.white, 2, []),
    heat: makeDisc(SPRITE_SIZE.heat, HAAS.red, 2, []),
  };
}
