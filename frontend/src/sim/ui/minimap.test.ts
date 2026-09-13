import { describe, expect, it } from "vitest";
import { ringPath, ringProjection } from "./Minimap";

/** A circle of radius r centred on (cx, cy), as a ring would be. */
function ring(n = 200, r = 500, cx = 0, cy = 0) {
  const x = new Float32Array(n);
  const y = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const t = (i / n) * Math.PI * 2;
    x[i] = cx + Math.cos(t) * r;
    y[i] = cy + Math.sin(t) * r;
  }
  return { x, y };
}

describe("ringProjection", () => {
  it("fits the circuit inside the 200-unit box with padding on every side", () => {
    const track = ring();
    const p = ringProjection(track)!;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (let i = 0; i < track.x.length; i++) {
      const sx = p.toX(track.x[i]);
      const sy = p.toY(track.y[i]);
      minX = Math.min(minX, sx); maxX = Math.max(maxX, sx);
      minY = Math.min(minY, sy); maxY = Math.max(maxY, sy);
    }
    expect(minX).toBeGreaterThanOrEqual(9.99);
    expect(minY).toBeGreaterThanOrEqual(9.99);
    expect(maxX).toBeLessThanOrEqual(190.01);
    expect(maxY).toBeLessThanOrEqual(190.01);
  });

  it("is translation-invariant: the same circuit anywhere in the feed's frame draws the same", () => {
    // The telemetry origin is an arbitrary point and differs per circuit, so a
    // projection anchored to it would put some circuits off the edge entirely.
    const a = ringProjection(ring(200, 500, 0, 0))!;
    const b = ringProjection(ring(200, 500, 12345, -9876))!;
    expect(a.toX(0)).toBeCloseTo(b.toX(12345), 4);
    expect(a.toY(0)).toBeCloseTo(b.toY(-9876), 4);
  });

  it("keeps aspect ratio, so a long thin circuit is not stretched round", () => {
    // A 2:1 layout must stay 2:1 on the plan.
    const n = 100;
    const x = new Float32Array(n), y = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const t = (i / n) * Math.PI * 2;
      x[i] = Math.cos(t) * 1000;
      y[i] = Math.sin(t) * 500;
    }
    const p = ringProjection({ x, y })!;
    const width = p.toX(1000) - p.toX(-1000);
    const height = Math.abs(p.toY(500) - p.toY(-500));
    expect(width / height).toBeCloseTo(2, 1);
  });

  it("flips y, because SVG grows downward and the track frame grows upward", () => {
    const p = ringProjection(ring())!;
    expect(p.toY(500)).toBeLessThan(p.toY(-500));
  });

  it("is null for a degenerate ring rather than dividing by zero", () => {
    expect(ringProjection({ x: new Float32Array(0), y: new Float32Array(0) })).toBeNull();
    const flat = { x: new Float32Array([5, 5, 5]), y: new Float32Array([5, 5, 5]) };
    expect(ringProjection(flat)).toBeNull();
  });
});

describe("ringPath", () => {
  it("is a closed path starting with a move", () => {
    const track = ring();
    const d = ringPath(track, ringProjection(track)!);
    expect(d.startsWith("M")).toBe(true);
    expect(d.trimEnd().endsWith("Z")).toBe(true);
  });

  it("subsamples a dense ring rather than emitting every vertex", () => {
    // Silverstone ships 5832 stations; drawing all of them is thousands of DOM
    // path segments for a 200px box nobody can see the difference in.
    const track = ring(6000);
    const d = ringPath(track, ringProjection(track)!, 320);
    const points = (d.match(/[ML]/g) ?? []).length;
    expect(points).toBeLessThanOrEqual(322);
    expect(points).toBeGreaterThan(100);
  });

  it("is empty for a ring with no vertices", () => {
    const empty = { x: new Float32Array(0), y: new Float32Array(0) };
    expect(ringPath(empty, { toX: (v) => v, toY: (v) => v })).toBe("");
  });
});
