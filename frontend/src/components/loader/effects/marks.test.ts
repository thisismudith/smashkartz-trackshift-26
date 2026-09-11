import { describe, expect, it } from "vitest";
import { HAAS } from "@/lib/palette";
import { MARKS } from "../physics/constants";
import { depositAlpha, drawMarkDot, drawMarkSegment, type MarkStrokeContext } from "./marks";

describe("depositAlpha", () => {
  it("is monotone non-decreasing in slip energy in both regimes", () => {
    for (const d of [0, 0.02, MARKS.patchLengthM, 0.5]) {
      let prev = -1;
      for (let E = 0; E <= 60_000; E += 500) {
        const a = depositAlpha(E, d, 1);
        expect(a).toBeGreaterThanOrEqual(prev);
        expect(a).toBeLessThanOrEqual(1);
        prev = a;
      }
    }
  });

  it("static regime (no travel) is E*markGain / staticEnergyJ", () => {
    expect(depositAlpha(3_000, 0, 1)).toBeCloseTo(3_000 / MARKS.staticEnergyJ, 9);
    expect(depositAlpha(3_000, 0, 0.5)).toBeCloseTo(1_500 / MARKS.staticEnergyJ, 9);
    expect(depositAlpha(10 * MARKS.staticEnergyJ, 0, 1)).toBe(1); // clamped
  });

  it("moving regime follows movingGain*sqrt(q/qRef) and is capped at movingAlphaCap", () => {
    const d = 1.0; // w = 1
    const q = 250; // J/m, well below qRef (and below the cap once the gain is applied)
    expect(depositAlpha(q * d, d, 1)).toBeCloseTo(MARKS.movingGain * Math.sqrt(q / MARKS.qRefJPerM), 9);
    expect(depositAlpha(1e9, d, 1)).toBeCloseTo(MARKS.movingAlphaCap, 9);
    expect(depositAlpha(1e9, 3, 1)).toBeLessThanOrEqual(MARKS.movingAlphaCap);
  });

  it("blend is continuous across w = 1", () => {
    const E = 400;
    const L = MARKS.patchLengthM;
    const below = depositAlpha(E, L * (1 - 1e-6), 1);
    const at = depositAlpha(E, L, 1);
    const above = depositAlpha(E, L * (1 + 1e-6), 1);
    expect(Math.abs(at - below)).toBeLessThan(1e-5);
    expect(Math.abs(above - at)).toBeLessThan(1e-5);
  });

  it("handles zero and negative inputs without NaN", () => {
    expect(depositAlpha(0, 0, 1)).toBe(0);
    expect(depositAlpha(-5, -1, 1)).toBe(0);
    expect(Number.isNaN(depositAlpha(100, 1e-12, 1))).toBe(false);
  });
});

interface Stroke {
  colour: string;
  width: number;
  alpha: number;
  cap: string;
  join: string;
  from: [number, number];
  to: [number, number];
}

/** Hand-written fake context recording each stroke's state at the moment stroke() is called. */
function fakeCtx(): { ctx: MarkStrokeContext; strokes: Stroke[] } {
  const strokes: Stroke[] = [];
  let from: [number, number] = [0, 0];
  let to: [number, number] = [0, 0];
  const ctx = {
    strokeStyle: "" as string | CanvasGradient | CanvasPattern,
    lineWidth: 1,
    lineCap: "butt" as CanvasLineCap,
    lineJoin: "miter" as CanvasLineJoin,
    globalAlpha: 1,
    beginPath() {},
    moveTo(x: number, y: number) {
      from = [x, y];
    },
    lineTo(x: number, y: number) {
      to = [x, y];
    },
    stroke() {
      strokes.push({
        colour: String(ctx.strokeStyle),
        width: ctx.lineWidth,
        alpha: ctx.globalAlpha,
        cap: ctx.lineCap,
        join: ctx.lineJoin,
        from,
        to,
      });
    },
  };
  return { ctx: ctx as unknown as MarkStrokeContext, strokes };
}

describe("drawMarkSegment", () => {
  it("strokes halo → feather → core → gloss with the contract widths/alphas, butt caps", () => {
    const { ctx, strokes } = fakeCtx();
    const width = 20;
    const halo = 36;
    const alpha = 0.3;
    drawMarkSegment(ctx, { x0: 0, y0: 50, x1: 10, y1: 50 }, width, halo, alpha);
    expect(strokes.length).toBe(4);
    const [h, f, c, g] = strokes;
    expect(h.colour).toBe(HAAS.grey);
    expect(h.width).toBe(halo);
    expect(h.alpha).toBeCloseTo(MARKS.haloAlphaPerFrame, 9); // alpha/0.05 ≥ 1
    expect(f.colour).toBe(HAAS.black);
    expect(f.width).toBeCloseTo(width * MARKS.featherWidthFrac, 9);
    expect(f.alpha).toBeCloseTo(alpha * MARKS.featherAlphaFrac, 9);
    expect(c.colour).toBe(HAAS.black);
    expect(c.width).toBe(width);
    expect(c.alpha).toBe(alpha);
    expect(g.colour).toBe(HAAS.white);
    expect(g.width).toBeCloseTo(width * MARKS.glossWidthFrac, 9);
    expect(g.alpha).toBeCloseTo(alpha * MARKS.glossAlphaMax, 9);
    for (const s of strokes) {
      expect(s.cap).toBe("butt");
      expect(s.join).toBe("round");
    }
    expect(ctx.globalAlpha).toBe(1);
  });

  it("halo alpha scales down for faint deposits", () => {
    const { ctx, strokes } = fakeCtx();
    drawMarkSegment(ctx, { x0: 0, y0: 0, x1: 1, y1: 0 }, 10, 20, 0.01);
    expect(strokes[0].alpha).toBeCloseTo(MARKS.haloAlphaPerFrame * 0.2, 9);
  });

  it("offsets the gloss perpendicular to the segment toward screen-up, either direction", () => {
    const width = 20;
    const off = width * MARKS.glossOffsetFrac;
    // left → right
    {
      const { ctx, strokes } = fakeCtx();
      drawMarkSegment(ctx, { x0: 0, y0: 50, x1: 10, y1: 50 }, width, 30, 0.5);
      const g = strokes[3];
      expect(g.from).toEqual([0, 50 - off]);
      expect(g.to).toEqual([10, 50 - off]);
    }
    // right → left must still go up, not down
    {
      const { ctx, strokes } = fakeCtx();
      drawMarkSegment(ctx, { x0: 10, y0: 50, x1: 0, y1: 50 }, width, 30, 0.5);
      expect(strokes[3].from[1]).toBeCloseTo(50 - off, 9);
    }
    // diagonal: normal has n.y < 0 and unit length
    {
      const { ctx, strokes } = fakeCtx();
      drawMarkSegment(ctx, { x0: 0, y0: 0, x1: 10, y1: 10 }, width, 30, 0.5);
      const g = strokes[3];
      const dx = g.from[0] - 0;
      const dy = g.from[1] - 0;
      expect(dy).toBeLessThan(0);
      expect(Math.hypot(dx, dy)).toBeCloseTo(off, 9);
      expect(dx * 10 + dy * 10).toBeCloseTo(0, 9); // perpendicular
    }
  });

  it("does nothing for zero alpha", () => {
    const { ctx, strokes } = fakeCtx();
    drawMarkSegment(ctx, { x0: 0, y0: 0, x1: 1, y1: 0 }, 10, 20, 0);
    expect(strokes.length).toBe(0);
  });
});

describe("drawMarkDot", () => {
  it("uses round caps, only the two dark passes (no halo ring, no gloss), and restores alpha", () => {
    const { ctx, strokes } = fakeCtx();
    const width = 16;
    drawMarkDot(ctx, 100, 200, width, 30, 0.4);
    // feather + core only: a grey halo wider than the dark disc reads as a ring, a white dot as a crosshair
    expect(strokes.length).toBe(2);
    for (const s of strokes) expect(s.cap).toBe("round");
    expect(strokes[0].width).toBeCloseTo(width * MARKS.featherWidthFrac, 9);
    expect(strokes[1].width).toBe(width);
    expect(strokes[1].alpha).toBe(0.4);
    // near-zero-length stroke at the dot position
    expect(Math.hypot(strokes[1].to[0] - strokes[1].from[0], strokes[1].to[1] - strokes[1].from[1])).toBeLessThan(0.1);
    expect(strokes[1].from[0]).toBe(100);
    expect(ctx.globalAlpha).toBe(1);
  });
});
