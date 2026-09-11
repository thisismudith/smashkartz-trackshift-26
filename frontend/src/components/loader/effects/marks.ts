/**
 * Rubber-mark deposition maths and the persistent-canvas stroke passes.
 *
 * depositAlpha() is pure and unit-tested. The draw helpers only touch the 2D context API
 * (strokeStyle / lineWidth / lineCap / lineJoin / globalAlpha / beginPath / moveTo / lineTo /
 * stroke) so they can be exercised with a hand-written fake context in node.
 *
 * Every draw call is exactly 4 strokes: halo → feather → core → gloss. No save/restore; state
 * (cap, join, alpha) is set explicitly in each call and globalAlpha is left at 1.
 */
import { HAAS } from "@/lib/palette";
import { MARKS } from "../physics/constants";

const clamp01 = (v: number): number => (v < 0 ? 0 : v > 1 ? 1 : v);

/**
 * Rubber alpha to deposit for one contact-patch step.
 *   slipEnergyJ  slip energy dissipated this step (fx·|vsx|·dt)
 *   distanceM    ground distance the patch travelled this step
 *   markGain     0.5..1 thermal gain from the tyre model
 * Two regimes blended on w = distance / patch length: a moving regime where deposit scales with
 * sqrt(energy per metre) and is capped, and a static regime (burnout on the spot) where the
 * patch simply darkens with total energy.
 */
export function depositAlpha(slipEnergyJ: number, distanceM: number, markGain: number): number {
  const d = distanceM > 0 ? distanceM : 0;
  const w = Math.min(1, d / MARKS.patchLengthM);
  const E = Math.max(0, slipEnergyJ) * markGain;
  const qJPerM = E / Math.max(d, 1e-4);
  const aMoving = Math.min(MARKS.movingAlphaCap, MARKS.movingGain * Math.sqrt(qJPerM / MARKS.qRefJPerM));
  const aStatic = E / MARKS.staticEnergyJ;
  return clamp01(w * aMoving + (1 - w) * aStatic);
}

export interface MarkSegment {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/** Subset of CanvasRenderingContext2D the mark passes use (lets tests pass a fake). */
export type MarkStrokeContext = Pick<
  CanvasRenderingContext2D,
  | "strokeStyle"
  | "lineWidth"
  | "lineCap"
  | "lineJoin"
  | "globalAlpha"
  | "beginPath"
  | "moveTo"
  | "lineTo"
  | "stroke"
>;

/** Zero-length strokes are not reliably rendered with round caps everywhere; nudge by this. */
const DOT_EPS_PX = 0.01;

function strokeLine(
  ctx: MarkStrokeContext,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  colour: string,
  widthPx: number,
  alpha: number,
  cap: CanvasLineCap,
): void {
  ctx.strokeStyle = colour;
  ctx.lineWidth = widthPx;
  ctx.lineCap = cap;
  ctx.lineJoin = "round";
  ctx.globalAlpha = alpha;
  ctx.beginPath();
  ctx.moveTo(x0, y0);
  ctx.lineTo(x1, y1);
  ctx.stroke();
}

/**
 * Passes shared by segment and dot: halo → feather → core → gloss. Gloss is offset by (gx, gy) px.
 * `haloScale` is the render frame's duration in 60 Hz frames, so the accumulating halo is
 * refresh-rate independent; pass 0 to skip the halo (and gloss) — used for the stationary patch,
 * where a wide grey bloom around a small dark disc reads as a ring, not rubber.
 */
function markPasses(
  ctx: MarkStrokeContext,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  widthPx: number,
  haloWidthPx: number,
  alpha: number,
  gx: number,
  gy: number,
  cap: CanvasLineCap,
  haloScale: number,
): void {
  const withHalo = haloScale > 0;
  // (1) halo: faint grey dust bloom beside a moving stroke, accumulating per unit time
  if (withHalo) {
    const haloAlpha = MARKS.haloAlphaPerFrame * haloScale * Math.min(1, alpha / 0.05);
    strokeLine(ctx, x0, y0, x1, y1, HAAS.grey, haloWidthPx, haloAlpha, cap);
  }
  // (2) feather: wider, weaker black under-stroke softens the edge
  strokeLine(
    ctx,
    x0,
    y0,
    x1,
    y1,
    HAAS.black,
    widthPx * MARKS.featherWidthFrac,
    alpha * MARKS.featherAlphaFrac,
    cap,
  );
  // (3) core: the rubber itself
  strokeLine(ctx, x0, y0, x1, y1, HAAS.black, widthPx, alpha, cap);
  // (4) gloss: thin white sheen offset toward screen-up (moving strokes only)
  if (withHalo) {
    strokeLine(
      ctx,
      x0 + gx,
      y0 + gy,
      x1 + gx,
      y1 + gy,
      HAAS.white,
      widthPx * MARKS.glossWidthFrac,
      alpha * MARKS.glossAlphaMax,
      cap,
    );
  }
  ctx.globalAlpha = 1;
}

/**
 * Stroke one moving rubber segment (butt caps so consecutive segments tile without bulges).
 * widthPx = tyre width in px, haloWidthPx = MARKS.haloWidthM * pxPerM, alpha from depositAlpha().
 * The gloss pass is shifted perpendicular to the segment, toward screen-up (-y).
 */
export function drawMarkSegment(
  ctx: MarkStrokeContext,
  seg: MarkSegment,
  widthPx: number,
  haloWidthPx: number,
  alpha: number,
  frameScale = 1,
): void {
  if (alpha <= 0 || widthPx <= 0) return;
  const { x0, y0, x1, y1 } = seg;
  const dx = x1 - x0;
  const dy = y1 - y0;
  const len = Math.hypot(dx, dy);
  // Unit normal; pick the sign with n.y < 0 (screen-up). Degenerate segment → straight up.
  let nx = 0;
  let ny = -1;
  if (len > 1e-9) {
    nx = -dy / len;
    ny = dx / len;
    if (ny > 0 || (ny === 0 && nx < 0)) {
      nx = -nx;
      ny = -ny;
    }
  }
  const off = widthPx * MARKS.glossOffsetFrac;
  markPasses(ctx, x0, y0, x1, y1, widthPx, haloWidthPx, alpha, nx * off, ny * off, "butt", Math.max(0.01, frameScale));
}

/**
 * Stationary patch (burnout on the spot): a (near-)zero-length round-capped stroke, so the
 * rubber builds up as a rounded blob the width of the tyre. Gloss offset straight up (-y).
 */
export function drawMarkDot(
  ctx: MarkStrokeContext,
  x: number,
  y: number,
  widthPx: number,
  haloWidthPx: number,
  alpha: number,
): void {
  if (alpha <= 0 || widthPx <= 0) return;
  // no halo / gloss for the stationary patch: it should build into a plain dark rubber disc
  markPasses(ctx, x, y, x + DOT_EPS_PX, y, widthPx, haloWidthPx, alpha, 0, 0, "round", 0);
}
