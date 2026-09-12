/**
 * One-off background paint for the persistent marks canvas: opaque asphalt, a tiled speckle,
 * and an elliptical "lift" so the car sits in a pool of light.
 *
 * DOM-only (creates an offscreen tile canvas); nothing here runs per frame, so save/restore and
 * gradients are fine. Only palette colours are used; alpha is expressed as rgba() of the palette
 * RGB values so no new hue is ever introduced.
 */
import { HAAS } from "@/lib/palette";

export interface AsphaltOptions {
  /** Car CG at start, CSS px. */
  centreX: number;
  centreY: number;
  rng: () => number;
}

/** Speckle tile: ~1 dot per 36 px² of mostly faint grey with a sprinkle of white. */
const SPECKLE = {
  tilePx: 512,
  areaPerDotPx2: 36,
  greyAlphaMin: 0.05,
  greyAlphaMax: 0.12,
  whiteAlpha: 0.04,
  whiteFrac: 1 / 12,
} as const;

/**
 * Elliptical lift ("one overhead light") biased toward the car's run so the tarmac under the
 * whole launch averages ~#3A3A3A; that headroom is what lets a single-pass rubber stroke read.
 * Alpha stops are of #AEAEAE.
 */
const LIFT = {
  /** Ellipse centre is shifted this fraction of the width toward the exit (car runs to +x). */
  centreShiftFracOfW: 0.15,
  rxFracOfW: 0.6,
  ryFracOfH: 0.38,
  alphaCentre: 0.26,
  alphaMid: 0.13,
  midStop: 0.55,
  endStop: 0.9,
} as const;

/** The tarmac is painted in these hues only — speckle and lighting, nothing chromatic. */
type TarmacHue = "white" | "grey" | "black";

const RGB: Record<TarmacHue, readonly [number, number, number]> = {
  white: [239, 239, 239],
  grey: [174, 174, 174],
  black: [17, 17, 17],
};

const rgba = (c: TarmacHue, a: number): string => `rgba(${RGB[c].join(",")},${a})`;

function makeSpeckleTile(rng: () => number): HTMLCanvasElement | null {
  const size = SPECKLE.tilePx;
  const tile = document.createElement("canvas");
  tile.width = size;
  tile.height = size;
  const tctx = tile.getContext("2d");
  if (!tctx) return null;
  const img = tctx.createImageData(size, size);
  const data = img.data;
  const dots = Math.floor((size * size) / SPECKLE.areaPerDotPx2);
  const [gr, gg, gb] = RGB.grey;
  const [wr, wg, wb] = RGB.white;
  for (let i = 0; i < dots; i++) {
    const px = Math.floor(rng() * size);
    const py = Math.floor(rng() * size);
    const p = (py * size + px) * 4;
    if (rng() < SPECKLE.whiteFrac) {
      data[p] = wr;
      data[p + 1] = wg;
      data[p + 2] = wb;
      data[p + 3] = Math.round(SPECKLE.whiteAlpha * 255);
    } else {
      const a = SPECKLE.greyAlphaMin + rng() * (SPECKLE.greyAlphaMax - SPECKLE.greyAlphaMin);
      data[p] = gr;
      data[p + 1] = gg;
      data[p + 2] = gb;
      data[p + 3] = Math.round(a * 255);
    }
  }
  tctx.putImageData(img, 0, 0);
  return tile;
}

/**
 * Paint the static asphalt into `ctx` (already scaled so 1 unit = 1 CSS px).
 * wCss/hCss are the canvas size in CSS px.
 */
export function paintAsphalt(
  ctx: CanvasRenderingContext2D,
  wCss: number,
  hCss: number,
  opts: AsphaltOptions,
): void {
  const { centreX, centreY, rng } = opts;

  // 1. Opaque base.
  ctx.globalAlpha = 1;
  ctx.fillStyle = HAAS.black;
  ctx.fillRect(0, 0, wCss, hCss);

  // 2. Speckle via a repeating pattern of one pre-rasterised tile.
  const tile = makeSpeckleTile(rng);
  if (tile) {
    const pattern = ctx.createPattern(tile, "repeat");
    if (pattern) {
      ctx.fillStyle = pattern;
      ctx.fillRect(0, 0, wCss, hCss);
    }
  }

  // 3. Elliptical lift: a unit radial gradient stretched by (rx, ry) through the transform.
  const rx = LIFT.rxFracOfW * wCss;
  const ry = LIFT.ryFracOfH * hCss;
  if (rx > 0 && ry > 0) {
    ctx.save();
    ctx.translate(centreX + LIFT.centreShiftFracOfW * wCss, centreY);
    ctx.scale(rx, ry);
    const g = ctx.createRadialGradient(0, 0, 0, 0, 0, 1);
    g.addColorStop(0, rgba("grey", LIFT.alphaCentre));
    g.addColorStop(LIFT.midStop, rgba("grey", LIFT.alphaMid));
    g.addColorStop(LIFT.endStop, rgba("grey", 0));
    g.addColorStop(1, rgba("grey", 0));
    ctx.fillStyle = g;
    ctx.fillRect(-1, -1, 2, 2);
    ctx.restore();
  }

}
