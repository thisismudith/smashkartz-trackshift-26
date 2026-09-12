import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { CAR } from "@/components/loader/physics/constants";
import { buildF1CarGeometry } from "./carGeometry";
import {
  CAR_GROUND_CLEARANCE_M, CAR_RENDER_LENGTH_M, CAR_RENDER_WIDTH_M, CAR_VISUAL_SCALE,
  carInstanceY, FOCUS_OUTLINE_SCALE, focusGhostY,
} from "./presentation";

/**
 * The car is DRAWN bigger than it is. These tests pin the two halves of that split:
 *
 *   1. the drawn geometry really is CAR_VISUAL_SCALE times the true car, uniformly;
 *   2. nothing that reasons about SPACE (declutter lanes, the grid fit-on-road clamp,
 *      the launch non-interpenetration gap) sees the inflated number;
 *   3. the contact patch still sits CAR_GROUND_CLEARANCE_M above the road, because the
 *      lift is measured from the geometry and therefore scales with it.
 *
 * (3) is the one that silently breaks: a lift computed from the UNSCALED geometry would
 * bury a 1.3x car 0.090 m into the tarmac and nothing but the picture would complain.
 */

/** Axis-aligned bounds of the built geometry, measured from its vertices. */
function bounds(geom: THREE.BufferGeometry) {
  geom.computeBoundingBox();
  const b = geom.boundingBox!;
  return {
    min: b.min.clone(), max: b.max.clone(),
    length: b.max.x - b.min.x,   // +X is forward (nose)
    width: b.max.z - b.min.z,    // +Z is to the side
    height: b.max.y - b.min.y,
  };
}

const trueSize = bounds(buildF1CarGeometry(1));
const drawn = bounds(buildF1CarGeometry());

describe("CAR_VISUAL_SCALE: drawn size and true size are separate numbers", () => {
  it("inflates the DRAWN geometry and nothing else", () => {
    // the scale is a presentation choice, but it is not a licence to draw a bus
    expect(CAR_VISUAL_SCALE).toBeGreaterThan(1);
    expect(CAR_VISUAL_SCALE).toBeLessThanOrEqual(1.5);

    // the drawn car is bigger than the true car, in both plan dimensions
    expect(drawn.length).toBeGreaterThan(CAR_RENDER_LENGTH_M);
    expect(drawn.width).toBeGreaterThan(CAR_RENDER_WIDTH_M);
    expect(drawn.length).toBeCloseTo(CAR.lengthM * CAR_VISUAL_SCALE, 6);
    expect(drawn.width).toBeCloseTo(CAR.widthM * CAR_VISUAL_SCALE, 6);
  });

  it("leaves the TRUE metres alone, so spacing logic is unchanged", () => {
    // raceEngine's launch non-interpenetration gap and generatedTimeline's lap-1 wrap
    // check read CAR_RENDER_LENGTH_M; timeline's columnLateral fit-on-road clamp reads
    // CAR_RENDER_WIDTH_M. All three must keep seeing a real F1 car.
    expect(CAR_RENDER_LENGTH_M).toBe(CAR.lengthM);
    expect(CAR_RENDER_WIDTH_M).toBe(CAR.widthM);
    expect(CAR_RENDER_LENGTH_M).toBeCloseTo(5.6, 12);
    expect(CAR_RENDER_WIDTH_M).toBeCloseTo(2.0, 12);
    // and they must NOT have been quietly set to the drawn size
    expect(CAR_RENDER_LENGTH_M).not.toBeCloseTo(drawn.length, 3);
    expect(CAR_RENDER_WIDTH_M).not.toBeCloseTo(drawn.width, 3);
  });

  it("scales UNIFORMLY: the silhouette is the same shape, just larger", () => {
    // the unscaled build is the historical geometry, measured here rather than retyped
    expect(trueSize.length).toBeCloseTo(CAR.lengthM, 6);
    expect(trueSize.width).toBeCloseTo(CAR.widthM, 6);
    expect(trueSize.min.y).toBeCloseTo(-0.3, 4);   // wheel bottoms
    expect(trueSize.max.y).toBeCloseTo(0.41, 4);   // wheel tops

    for (const axis of ["x", "y", "z"] as const) {
      expect(drawn.min[axis]).toBeCloseTo(trueSize.min[axis] * CAR_VISUAL_SCALE, 6);
      expect(drawn.max[axis]).toBeCloseTo(trueSize.max[axis] * CAR_VISUAL_SCALE, 6);
    }
    // aspect ratios preserved. Positions live in a Float32Array, so the comparison is
    // good to ~1e-7 relative and no further; anything non-uniform would be orders larger.
    expect(drawn.length / drawn.width).toBeCloseTo(trueSize.length / trueSize.width, 6);
    expect(drawn.height / drawn.width).toBeCloseTo(trueSize.height / trueSize.width, 6);
  });

  it("keeps the normals unit-length and the vertex colours untouched", () => {
    // a uniform scale leaves normals alone; if it ever stopped being uniform, lighting
    // would go wrong before anyone noticed the shape
    const g = buildF1CarGeometry();
    const n = g.getAttribute("normal");
    for (let i = 0; i < n.count; i++) {
      expect(Math.hypot(n.getX(i), n.getY(i), n.getZ(i))).toBeCloseTo(1, 5);
    }
    // dark tyres/wings survive the scale: the colour channel is not a position channel
    const a = buildF1CarGeometry(1).getAttribute("color").array as Float32Array;
    const b = g.getAttribute("color").array as Float32Array;
    expect(b.length).toBe(a.length);
    expect(Array.from(b.slice(0, 300))).toEqual(Array.from(a.slice(0, 300)));
  });
});

describe("the contact patch stays on the road at any visual scale", () => {
  it("puts the tyres CAR_GROUND_CLEARANCE_M above the surface, scaled or not", () => {
    for (const scale of [1, CAR_VISUAL_SCALE, 2]) {
      const minY = bounds(buildF1CarGeometry(scale)).min.y;
      for (const surfaceY of [0, 123.45, -37.5]) {
        const y = carInstanceY(surfaceY, minY);
        // lowest drawn vertex minus the road it is standing on
        expect(y + minY - surfaceY).toBeCloseTo(CAR_GROUND_CLEARANCE_M, 12);
        expect(y + minY - surfaceY).toBeGreaterThan(0);     // never sunk into the tarmac
        expect(y + minY - surfaceY).toBeLessThan(0.05);     // never floating
      }
    }
  });

  it("catches the sink a stale, unscaled lift would cause", () => {
    // the failure mode this test exists for: lift the car by the TRUE-SIZE geometry's
    // extent while drawing the SCALED geometry
    const staleLift = carInstanceY(0, trueSize.min.y);
    const sunkBy = staleLift + drawn.min.y;     // where the scaled tyres would end up
    expect(sunkBy).toBeCloseTo(               // float32 vertices: 6 places is the floor
      CAR_GROUND_CLEARANCE_M - (CAR_VISUAL_SCALE - 1) * -trueSize.min.y, 6,
    );
    expect(sunkBy).toBeLessThan(0);             // i.e. underground

    // what the render path actually does instead
    expect(carInstanceY(0, drawn.min.y) + drawn.min.y).toBeCloseTo(CAR_GROUND_CLEARANCE_M, 12);
  });

  it("keeps the focus ghost's underside on the car's contact patch", () => {
    const surfaceY = 12.5;
    const carY = carInstanceY(surfaceY, drawn.min.y);
    const ghostY = focusGhostY(carY, drawn.min.y);
    // the ghost is a uniform FOCUS_OUTLINE_SCALE copy about the geometry's own origin
    const ghostUnderside = ghostY + drawn.min.y * FOCUS_OUTLINE_SCALE;
    expect(ghostUnderside).toBeCloseTo(carY + drawn.min.y, 12);
    expect(ghostUnderside - surfaceY).toBeCloseTo(CAR_GROUND_CLEARANCE_M, 12);
    // without the compensation the bigger ghost digs in further than it used to
    const uncompensated = carY + drawn.min.y * FOCUS_OUTLINE_SCALE;
    expect(uncompensated - surfaceY).toBeLessThan(0);
  });
});
