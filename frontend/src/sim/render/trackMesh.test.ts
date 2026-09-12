import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import type { TrackModel } from "../contract/types";
import { halfWidthAt, parseTrackModel, type RawTrackModel } from "../data/manifest";
import { buildF1CarGeometry } from "./carGeometry";
import { carInstanceY, CAR_GROUND_CLEARANCE_M } from "./presentation";
import {
  applyShadowPriceOverlay, buildPitLaneMesh, buildTrackMesh, carOrientation, fromRenderFrame,
  PIT_DRAPE_MAX_M, PIT_LANE_WIDTH_M, PIT_SURFACE_DEPTH_M, PIT_TAPER_FRACTION,
  pitLaneAvailability, pitPathElevation, renderForward, toRenderFrame,
} from "./trackMesh";

function makeTrack(over: Partial<TrackModel> = {}): TrackModel {
  const n = 100;
  const x = new Float32Array(n), y = new Float32Array(n), z = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const a = (i / n) * 2 * Math.PI;
    x[i] = Math.cos(a) * 500;
    y[i] = Math.sin(a) * 500;
  }
  return {
    slug: "mesh-track", event: "Mesh GP", lengthMetres: 3141,
    x, y, z, halfWidth: new Float32Array([6]), widthBinMetres: 3141,
    timingLines: { sf: 0, s1: 1000, s2: 2000 }, corners: [],
    grid: { order: [], pitchMetres: 8 },
    pitLanePath: null,
    pitLane: { entryStation: null, exitStation: null, mergeStation: null, loopLateral: null },
    referenceProfile: { binMetres: 10, speedKph: new Float32Array([250]), gear: new Uint8Array([7]) },
    ...over,
  };
}

/** Distance from (px, py) to the nearest ring VERTEX, with that vertex's elevation and
 * station -- the quantity pitPathElevation drapes from, recomputed independently here
 * rather than read back out of the thing under test. */
function nearestRing(
  track: TrackModel, px: number, py: number,
): { d: number; z: number; station: number } {
  let best = Infinity, z = NaN, j0 = -1;
  for (let j = 0; j < track.x.length; j++) {
    const dx = track.x[j] - px, dy = track.y[j] - py;
    const d2 = dx * dx + dy * dy;
    if (d2 < best) { best = d2; z = track.z[j]; j0 = j; }
  }
  return { d: Math.sqrt(best), z, station: j0 * (track.lengthMetres / track.x.length) };
}

/** Moves one vertex of the synthetic pit path to a known distance OUTSIDE the ring
 * (which is a circle of radius 500 in makeTrack), so a test can name the separation it
 * is exercising instead of nudging coordinates and hoping. */
function pushOffRing(path: { x: Float32Array; y: Float32Array }, i: number, metres: number) {
  const scale = (500 + metres) / Math.hypot(path.x[i], path.y[i]);
  path.x[i] *= scale;
  path.y[i] *= scale;
}

function median(v: number[]): number {
  return [...v].sort((a, b) => a - b)[v.length >> 1];
}

/** Signed area of a closed 2-D polygon; negative means clockwise. */
function signedArea(px: ArrayLike<number>, py: ArrayLike<number>): number {
  let a = 0;
  for (let i = 0; i < px.length; i++) {
    const j = (i + 1) % px.length;
    a += px[i] * py[j] - px[j] * py[i];
  }
  return a / 2;
}

describe("track frame -> render frame", () => {
  it("is a ROTATION, not a reflection: the 3x3 has determinant +1", () => {
    // columns are the images of the track-frame basis vectors
    const m = new THREE.Matrix3();
    const ex = toRenderFrame(1, 0, 0);
    const ey = toRenderFrame(0, 1, 0);
    const ez = toRenderFrame(0, 0, 1);
    m.set(
      ex[0], ey[0], ez[0],
      ex[1], ey[1], ez[1],
      ex[2], ey[2], ez[2],
    );
    expect(m.determinant()).toBeCloseTo(1, 12);
    // a bare Y/Z swap -- what this used to be -- is det -1
    const mirrored = new THREE.Matrix3().set(1, 0, 0, 0, 0, 1, 0, 1, 0);
    expect(mirrored.determinant()).toBeCloseTo(-1, 12);
  });

  it("round-trips exactly through fromRenderFrame", () => {
    for (const p of [[1, 2, 3], [-417.25, 908.5, 64.125], [0, 0, 0]]) {
      const [X, Y, Z] = toRenderFrame(p[0], p[1], p[2]);
      expect(fromRenderFrame(X, Y, Z)).toEqual([p[0], p[1], p[2]]);
    }
  });

  it("keeps + lateral on the LEFT of travel, as scripts/simdata/geom.py declares", () => {
    const up = new THREE.Vector3(0, 1, 0);
    for (const h of [0, 0.7, 2.3, -1.1, Math.PI]) {
      // track-frame left normal of a car heading h, mapped into the render frame
      const [lx, ly, lz] = toRenderFrame(-Math.sin(h), Math.cos(h), 0);
      // left of travel in a Y-up render frame is up x forward
      const expected = new THREE.Vector3().crossVectors(up, renderForward(h));
      expect(lx).toBeCloseTo(expected.x, 12);
      expect(ly).toBeCloseTo(expected.y, 12);
      expect(lz).toBeCloseTo(expected.z, 12);
    }
  });

  it("preserves a circuit's rotational sense (12 of 13 shipped rings are clockwise)", () => {
    const track = makeTrack();
    // the synthetic ring above is anti-clockwise in the track frame; reverse it so the
    // test runs on the sense 12 of the 13 real circuits actually have
    const cx = Float32Array.from(track.x).reverse();
    const cy = Float32Array.from(track.y).reverse();
    expect(signedArea(cx, cy)).toBeLessThan(0);

    // The render frame is Y-up: the plan view is the XZ plane seen from +Y looking
    // DOWN, so a clockwise loop in the track frame must stay clockwise on screen.
    // Looking down -Y flips the handedness of the XZ signed area, so a frame that
    // preserves the picture inverts that number -- and a MIRROR would not.
    const rx: number[] = [], rz: number[] = [];
    for (let i = 0; i < cx.length; i++) {
      const [X, , Z] = toRenderFrame(cx[i], cy[i], 0);
      rx.push(X); rz.push(Z);
    }
    expect(signedArea(rx, rz)).toBeGreaterThan(0);
    expect(signedArea(rx, rz)).toBeCloseTo(-signedArea(cx, cy), 3);
  });
});

describe("one forward convention", () => {
  it("renderForward(h) is exactly toRenderFrame of the track-frame forward vector", () => {
    for (const h of [0, 0.4, 1.9, -2.7, Math.PI / 2, Math.PI]) {
      const [x, y, z] = toRenderFrame(Math.cos(h), Math.sin(h), 0);
      const f = renderForward(h);
      expect(f.x).toBeCloseTo(x, 12);
      expect(f.y).toBeCloseTo(y, 12);
      expect(f.z).toBeCloseTo(z, 12);
      expect(f.length()).toBeCloseTo(1, 12);
    }
  });

  it("the car's orientation maps its local +X (the nose) onto renderForward(h)", () => {
    // This is the assertion a determinant-plus-round-trip test would pass straight
    // through: the yaw used to be hand-derived as -heading to compensate for the
    // mirror, and nothing tied it to the frame it was compensating for.
    for (const h of [0, 0.4, 1.9, -2.7, Math.PI / 2, Math.PI, -Math.PI / 3]) {
      const nose = new THREE.Vector3(1, 0, 0).applyQuaternion(carOrientation(h));
      const fwd = renderForward(h);
      expect(nose.x).toBeCloseTo(fwd.x, 10);
      expect(nose.y).toBeCloseTo(fwd.y, 10);
      expect(nose.z).toBeCloseTo(fwd.z, 10);
    }
  });

  it("keeps the car upright: the orientation is a pure yaw about the render up axis", () => {
    for (const h of [0.4, 1.9, -2.7]) {
      const up = new THREE.Vector3(0, 1, 0).applyQuaternion(carOrientation(h));
      expect(up.y).toBeCloseTo(1, 10);
    }
  });
});

describe("car ride height", () => {
  it("puts the contact patch on the road, not 0.200 m above it", () => {
    const geom = buildF1CarGeometry();
    geom.computeBoundingBox();
    const minY = geom.boundingBox!.min.y;
    // the built geometry, measured: wheels at the bottom, wheels at the top too
    expect(minY).toBeCloseTo(-0.3, 4);
    expect(geom.boundingBox!.max.y).toBeCloseTo(0.41, 4);

    // what the retired CAR_RENDER_HEIGHT_M = 0.9 box constant produced
    const staleLift = 0.9 / 2 + 0.05;
    expect(staleLift + minY).toBeCloseTo(0.2, 6);

    const surfaceY = 123.45;
    const drawn = carInstanceY(surfaceY, minY);
    expect(drawn + minY - surfaceY).toBeCloseTo(CAR_GROUND_CLEARANCE_M, 9);
    expect(drawn + minY - surfaceY).toBeLessThan(0.05);
    expect(drawn + minY - surfaceY).toBeGreaterThan(0); // never sunk into the tarmac
  });
});

/** A pit lane running beside the synthetic ring: it starts on the racing line and
 * peels away from it, with a z channel held flat the way the real feed holds it. */
function makePitTrack(): TrackModel {
  const track = makeTrack();
  // give the ring some elevation so a flat pit polyline is measurably wrong (Spa's
  // shipped ring spans 102 m, Suzuka's 40 m, so 100 m is a real-world figure)
  for (let i = 0; i < track.z.length; i++) track.z[i] = (i / track.z.length) * 100;
  const n = 40;
  const px = new Float32Array(n), py = new Float32Array(n), pz = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const a = (i / track.x.length) * 2 * Math.PI;
    const off = (i / (n - 1)) * 25; // 0 m from the ring at the start, 25 m at the end
    px[i] = Math.cos(a) * (500 + off);
    py[i] = Math.sin(a) * (500 + off);
    pz[i] = 4; // held flat, as the position feed holds it through the pit stretch
  }
  track.pitLanePath = [{ role: "entry", x: px, y: py, z: pz }];
  return track;
}

describe("pit-lane ribbon", () => {
  it("tapers ONLY the end that meets the racing surface", () => {
    const track = makePitTrack();
    const group = buildPitLaneMesh(track)!;
    expect(group).not.toBeNull();
    const mesh = group.children[0] as THREE.Mesh;
    const pos = mesh.geometry.getAttribute("position");
    const n = pos.count / 2;

    // positions are stored as Float32, so ~1e-5 m of storage error is expected
    const halfAt = (i: number) => {
      const l = new THREE.Vector3().fromBufferAttribute(pos as THREE.BufferAttribute, i * 2);
      const r = new THREE.Vector3().fromBufferAttribute(pos as THREE.BufferAttribute, i * 2 + 1);
      return l.distanceTo(r) / 2;
    };

    // start: on the racing line, so it fades to nothing there
    expect(halfAt(0)).toBeCloseTo(0, 6);
    // end: the far end of the pit road. It used to be pinched shut here too.
    expect(halfAt(n - 1)).toBeCloseTo(PIT_LANE_WIDTH_M / 2, 3);
    // and the taper is confined to its own end
    const firstFull = Math.ceil(PIT_TAPER_FRACTION * (n - 1));
    expect(halfAt(firstFull)).toBeCloseTo(PIT_LANE_WIDTH_M / 2, 3);
    expect(halfAt(Math.floor(n / 2))).toBeCloseTo(PIT_LANE_WIDTH_M / 2, 3);
  });

  it("draws the ribbon at the elevation the cars are drawn at, not the held pit z", () => {
    const track = makePitTrack();
    const path = track.pitLanePath![0];
    const elevation = pitPathElevation(track, path)!;
    expect(elevation).not.toBeNull();
    expect(elevation.anchored).toBe(path.x.length); // all within PIT_DRAPE_MAX_M here

    const group = buildPitLaneMesh(track)!;
    const pos = (group.children[0] as THREE.Mesh).geometry.getAttribute("position");

    let worstDraped = 0, worstHeld = 0;
    for (let i = 0; i < path.x.length; i++) {
      // nearest ring vertex: the elevation a car at this point would be drawn at
      let best = Infinity, bestZ = 0;
      for (let j = 0; j < track.x.length; j++) {
        const d = (track.x[j] - path.x[i]) ** 2 + (track.y[j] - path.y[i]) ** 2;
        if (d < best) { best = d; bestZ = track.z[j]; }
      }
      const ribbonY = pos.getY(i * 2); // render-frame Y is the track-frame z
      worstDraped = Math.max(worstDraped, Math.abs(ribbonY - bestZ));
      worstHeld = Math.max(worstHeld, Math.abs(path.z[i] - bestZ));
    }
    expect(worstDraped).toBeCloseTo(0, 4);
    // the defect this replaces: drawing the held z put the ribbon metres off the road
    expect(worstHeld).toBeGreaterThan(10);
  });

  it("reports the elevation as unavailable rather than guessing one", () => {
    const track = makePitTrack();
    const path = track.pitLanePath![0];
    // shove the whole lane clear of the circuit, the way the Chinese and Hungarian
    // position feeds do when they freeze on a sentinel coordinate (those land 190-560 m
    // from the ring; this goes further so no vertex can be within PIT_DRAPE_MAX_M)
    for (let i = 0; i < path.x.length; i++) path.x[i] += 100 * PIT_DRAPE_MAX_M;
    expect(pitPathElevation(track, path)).toBeNull();
    expect(buildPitLaneMesh(track)).toBeNull();
  });

  it("sits just under the racing surface instead of a 15 cm step below it", () => {
    const group = buildPitLaneMesh(makePitTrack())!;
    expect(group.position.y).toBeCloseTo(-PIT_SURFACE_DEPTH_M, 9);
    // a car's contact patch is CAR_GROUND_CLEARANCE_M above the road, so this is the
    // whole visible gap between a car in the pit lane and the lane itself
    expect(Math.abs(group.position.y) + CAR_GROUND_CLEARANCE_M).toBeLessThan(0.05);
  });

  it("keeps a lane that shortcuts a corner, interpolating across the gap", () => {
    // Silverstone's shape: the ring loops round Club while the lane goes straight, so a
    // run of interior vertices is 60-102.9 m from the nearest ring vertex. Those are a
    // correct pit lane and must still draw.
    const track = makePitTrack();
    const path = track.pitLanePath![0];
    const n = path.x.length;
    const lo = Math.floor(n * 0.4), hi = Math.floor(n * 0.65); // 25% of the run
    for (let i = lo; i < hi; i++) pushOffRing(path, i, 1.5 * PIT_DRAPE_MAX_M);

    const elevation = pitPathElevation(track, path)!;
    expect(elevation).not.toBeNull();
    expect(elevation.anchored).toBe(n - (hi - lo));
    expect(elevation.anchored / n).toBeGreaterThan(0.5);
    for (let i = lo; i < hi; i++) expect(elevation.ringDistance[i]).toBeGreaterThan(PIT_DRAPE_MAX_M);
    // the gap is bridged between the two anchors, not held flat and not invented
    const a = elevation.z[lo - 1], b = elevation.z[hi];
    for (let i = lo; i < hi; i++) {
      expect(elevation.z[i]).toBeGreaterThanOrEqual(Math.min(a, b) - 1e-4);
      expect(elevation.z[i]).toBeLessThanOrEqual(Math.max(a, b) + 1e-4);
    }
    expect(elevation.z[lo]).not.toBeCloseTo(a, 6); // actually interpolating, not holding
    expect(buildPitLaneMesh(track)).not.toBeNull();
    expect(pitLaneAvailability(track).drawn).toEqual(["entry"]);
  });

  it("refuses a polyline that is MOSTLY nowhere near the circuit", () => {
    // the Chinese/Hungarian shape: the feed freezes on a sentinel coordinate, so most of
    // the "lane" is a spike out to a point hundreds of metres off the circuit. A handful
    // of vertices near the racing line still anchor, so "any anchor at all" -- the test
    // this replaces -- passed it and drew a 9 m ribbon across the infield.
    const track = makePitTrack();
    const path = track.pitLanePath![0];
    const n = path.x.length;
    const keep = Math.floor(n * 0.26); // measured worst-case anchored share, 26.3%
    for (let i = keep; i < n; i++) pushOffRing(path, i, 8 * PIT_DRAPE_MAX_M);

    let anchored = 0;
    for (let i = 0; i < n; i++) if (nearestRing(track, path.x[i], path.y[i]).d <= PIT_DRAPE_MAX_M) anchored++;
    expect(anchored).toBe(keep);        // some DO anchor: the old `anchored === 0` gate let this through
    expect(anchored * 2).toBeLessThan(n);

    expect(pitPathElevation(track, path)).toBeNull();
    expect(buildPitLaneMesh(track)).toBeNull();
    const availability = pitLaneAvailability(track);
    expect(availability.drawn).toEqual([]);
    expect(availability.unavailable).toHaveLength(1);
    expect(availability.unavailable[0].role).toBe("entry");
  });

  it("still draws a lane anchored just over half the way along", () => {
    // the gate is a majority rule, so prove the majority side of it too
    const track = makePitTrack();
    const path = track.pitLanePath![0];
    const n = path.x.length;
    for (let i = Math.floor(n / 2) + 1; i < n; i++) pushOffRing(path, i, 8 * PIT_DRAPE_MAX_M);
    const elevation = pitPathElevation(track, path);
    expect(elevation).not.toBeNull();
    expect(elevation!.anchored * 2).toBeGreaterThan(n);
  });
});

/** The shipped artifacts are build outputs (frontend/public/sim is gitignored), so the
 * real-data assertions run wherever a build exists and are skipped where it does not. */
function shippedIndex(): { dir: string; tracks: Record<string, string> } | null {
  const dir = fileURLToPath(new URL("../../../public/sim/", import.meta.url));
  const pointer = `${dir}index.json`;
  if (!existsSync(pointer)) return null;
  const latest = JSON.parse(readFileSync(pointer, "utf8")).latest as string;
  const index = JSON.parse(readFileSync(`${dir}${latest}`, "utf8")) as { tracks: Record<string, string> };
  return { dir, tracks: index.tracks };
}

function loadShippedTrack(slug: string): TrackModel | null {
  const index = shippedIndex();
  const file = index?.tracks[slug];
  if (!index || !file) return null;
  try {
    return parseTrackModel(JSON.parse(readFileSync(`${index.dir}${file}`, "utf8")) as RawTrackModel);
  } catch {
    // A shipped model can be unreadable: the Chinese one carries a literal NaN, which
    // JSON.parse rejects (someone else's fix, tracked separately). Skip it rather than
    // take the whole suite down with it.
    return null;
  }
}

const shipped = Object.keys(shippedIndex()?.tracks ?? {})
  .map((slug) => [slug, loadShippedTrack(slug)] as const)
  .filter((e): e is readonly [string, TrackModel] => e[1] !== null);

const suzuka = shipped.find(([slug]) => slug === "japanese-grand-prix")?.[1] ?? null;

describe.skipIf(!suzuka)("against the shipped Japanese GP model", () => {
  it("its ring is clockwise, as the real circuit is", () => {
    expect(signedArea(suzuka!.x, suzuka!.y)).toBeLessThan(0);
  });

  it("cuts the pit ribbon's elevation error from a median 8.28 m to zero", () => {
    const segments = suzuka!.pitLanePath!;
    expect(segments.length).toBe(2);
    const held: number[] = [], draped: number[] = [];
    for (const path of segments) {
      const elevation = pitPathElevation(suzuka!, path)!;
      expect(elevation).not.toBeNull();
      expect(elevation.anchored).toBe(path.x.length);
      for (let i = 0; i < path.x.length; i++) {
        let best = Infinity, bestZ = 0;
        for (let j = 0; j < suzuka!.x.length; j++) {
          const d = (suzuka!.x[j] - path.x[i]) ** 2 + (suzuka!.y[j] - path.y[i]) ** 2;
          if (d < best) { best = d; bestZ = suzuka!.z[j]; }
        }
        held.push(Math.abs(path.z[i] - bestZ));
        draped.push(Math.abs(elevation.z[i] - bestZ));
      }
    }
    expect(median(held)).toBeGreaterThan(8); // measured 8.28 m, max 22.72 m
    expect(Math.max(...draped)).toBeCloseTo(0, 4);
  });

  it("tapers each pit segment at its racing-line end only", () => {
    const group = buildPitLaneMesh(suzuka!)!;
    expect(group.children.length).toBe(2);
    for (const child of group.children) {
      const mesh = child as THREE.Mesh;
      const pos = mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
      const n = pos.count / 2;
      const halfAt = (i: number) => new THREE.Vector3().fromBufferAttribute(pos, i * 2)
        .distanceTo(new THREE.Vector3().fromBufferAttribute(pos, i * 2 + 1)) / 2;
      const ends = [halfAt(0), halfAt(n - 1)];
      // exactly one end is closed off; the other is the open end of the pit road
      expect(ends.filter((h) => h < 1e-6)).toHaveLength(1);
      expect(ends.filter((h) => Math.abs(h - PIT_LANE_WIDTH_M / 2) < 1e-3)).toHaveLength(1);
      // and the closed end is the one that actually touches the circuit
      const elevation = pitPathElevation(suzuka!, suzuka!.pitLanePath!
        .find((p) => mesh.name.endsWith(p.role))!)!;
      const nearerEndIsStart = elevation.ringDistance[0] <= elevation.ringDistance[n - 1];
      expect(ends[nearerEndIsStart ? 0 : 1]).toBeLessThan(1e-6);
    }
  });

  it("draws the ribbon within PIT_SURFACE_DEPTH_M of the road where the two overlap", () => {
    // 21.3% of Suzuka's pit-ribbon vertices lie over the racing surface. The shipped pit
    // z put the ribbon a median 20.29 m (max 22.72 m) off the road at exactly those
    // vertices -- a ribbon cutting THROUGH the circuit, not one hidden under it, which is
    // why a 0.15 m group offset could never have been the answer to the overlap.
    const group = buildPitLaneMesh(suzuka!)!;
    const held: number[] = [], drawn: number[] = [];
    for (const child of group.children) {
      const mesh = child as THREE.Mesh;
      const path = suzuka!.pitLanePath!.find((seg) => mesh.name.endsWith(seg.role))!;
      const pos = mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
      for (let i = 0; i < path.x.length; i++) {
        const { d, z, station } = nearestRing(suzuka!, path.x[i], path.y[i]);
        if (d - PIT_LANE_WIDTH_M / 2 >= halfWidthAt(suzuka!, station)) continue; // no overlap
        held.push(Math.abs(path.z[i] - z));
        drawn.push(Math.abs(pos.getY(i * 2) + group.position.y - z));
      }
    }
    expect(drawn.length).toBeGreaterThan(10);
    expect(median(held)).toBeGreaterThan(8);
    expect(Math.max(...drawn)).toBeLessThanOrEqual(PIT_SURFACE_DEPTH_M + 1e-3);
  });

  it("has corner metadata that the renderer can check its own handedness against", () => {
    // markerLateral used to be dropped at parse time, which is why nothing on screen
    // ever contradicted the mirrored frame
    const withLateral = (suzuka!.corners as unknown as { markerLateral?: number | null }[])
      .filter((c) => typeof c.markerLateral === "number");
    expect(withLateral.length).toBe(suzuka!.corners.length);
    expect(suzuka!.corners.length).toBeGreaterThan(10);
  });
});

describe.skipIf(!shipped.length)("across every readable shipped track model", () => {
  it("draws a pit ribbon exactly where the polyline runs beside the circuit", () => {
    let drawnSegments = 0, refusedSegments = 0;
    for (const [, track] of shipped) {
      const segments = track.pitLanePath;
      if (!segments) continue;
      const availability = pitLaneAvailability(track);
      for (const path of segments) {
        const distances = Array.from(path.x, (_, i) => nearestRing(track, path.x[i], path.y[i]).d);
        const runsBesideTheRoad = median(distances) <= PIT_DRAPE_MAX_M;
        // the gate IS the median vertex, so this states the same decision twice: once by
        // the renderer, once straight from the shipped coordinates
        expect(availability.drawn.includes(path.role)).toBe(runsBesideTheRoad);
        if (runsBesideTheRoad) drawnSegments++; else refusedSegments++;
      }
      const group = buildPitLaneMesh(track);
      expect(group === null).toBe(availability.drawn.length === 0);
      if (group) expect(group.children.length).toBe(availability.drawn.length);
    }
    // Measured on the shipped build: 22 segments drawn (11 circuits, entry + exit, median
    // vertex 5.3-32.6 m from the ring) and 4 refused -- Chinese and Hungarian, median
    // 191.6-296.9 m, where the position feed freezes on a sentinel coordinate and the
    // "pit lane" is a 536-1272 m spike off the circuit. Floors, so that a repaired Python
    // feed can only move the numbers the right way.
    expect(drawnSegments).toBeGreaterThanOrEqual(22);
    expect(drawnSegments + refusedSegments).toBeGreaterThanOrEqual(22);
  });

  it("tapers every drawn segment at its racing-line end and nowhere else", () => {
    let checked = 0;
    for (const [, track] of shipped) {
      const group = buildPitLaneMesh(track);
      if (!group) continue;
      for (const child of group.children) {
        const mesh = child as THREE.Mesh;
        const path = track.pitLanePath!.find((seg) => mesh.name.endsWith(seg.role))!;
        const pos = mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
        const n = pos.count / 2;
        const halfAt = (i: number) => new THREE.Vector3().fromBufferAttribute(pos, i * 2)
          .distanceTo(new THREE.Vector3().fromBufferAttribute(pos, i * 2 + 1)) / 2;
        const d0 = nearestRing(track, path.x[0], path.y[0]).d;
        const dN = nearestRing(track, path.x[n - 1], path.y[n - 1]).d;
        // one end closed, one end open, and the closed one is the end that is on the road
        expect(halfAt(d0 <= dN ? 0 : n - 1)).toBeLessThan(1e-6);
        expect(halfAt(d0 <= dN ? n - 1 : 0)).toBeCloseTo(PIT_LANE_WIDTH_M / 2, 3);
        // the far end was pinched to a point too; the run is at full width well before it
        expect(halfAt(Math.round(n / 2))).toBeCloseTo(PIT_LANE_WIDTH_M / 2, 3);
        // and on all 22 drawn segments the geometry agrees with the role string
        expect(d0 <= dN).toBe(path.role === "entry");
        checked++;
      }
    }
    expect(checked).toBeGreaterThanOrEqual(22);
  });

  it("never draws a pit vertex at the polyline's own held elevation", () => {
    // The point of pitPathElevation: the drawn z comes from the racing surface, so a car
    // placed from the ring and the ribbon beneath it agree. Worst shipped gap between the
    // polyline's own z and the ring's, per circuit: 0.09 m (Dutch) to 22.72 m (Japanese).
    let worstHeld = 0, draped = 0;
    for (const [, track] of shipped) {
      for (const path of track.pitLanePath ?? []) {
        const elevation = pitPathElevation(track, path);
        if (!elevation) continue;
        for (let i = 0; i < path.x.length; i++) {
          const { d, z } = nearestRing(track, path.x[i], path.y[i]);
          if (d > PIT_DRAPE_MAX_M) continue;        // interpolated, not draped
          expect(elevation.z[i]).toBeCloseTo(z, 4); // draped from the racing surface
          worstHeld = Math.max(worstHeld, Math.abs(path.z[i] - z));
          draped++;
        }
      }
    }
    expect(draped).toBeGreaterThan(1000);
    expect(worstHeld).toBeGreaterThan(8); // 22.72 m at Suzuka, measured
  });
});

describe("track surface geometry", () => {
  it("builds a closed ribbon with two vertices per station and a valid index buffer", () => {
    const track = makeTrack();
    const mesh = buildTrackMesh(track);
    const posAttr = mesh.geometry.getAttribute("position");
    expect(posAttr.count).toBe(track.x.length * 2);
    const index = mesh.geometry.getIndex();
    expect(index).not.toBeNull();
    // 2 triangles (6 indices) per station going into the next one
    expect(index!.count).toBe(track.x.length * 6);
  });

  it("restores the plain surface colour when the shadow-price overlay is cleared", () => {
    const track = makeTrack();
    const mesh = buildTrackMesh(track);
    const colorAttr = mesh.geometry.getAttribute("color");
    const original = Float32Array.from(colorAttr.array as Float32Array);

    const values = new Float32Array(track.x.length).fill(1); // all "hot"
    applyShadowPriceOverlay(mesh, values);
    const painted = Float32Array.from(colorAttr.array as Float32Array);
    expect(painted).not.toEqual(original);

    applyShadowPriceOverlay(mesh, null);
    const restored = Float32Array.from(colorAttr.array as Float32Array);
    expect(restored).toEqual(original);
  });

  it("blends toward Haas red as the scalar rises, never leaving the palette hue range", () => {
    const track = makeTrack();
    const mesh = buildTrackMesh(track);
    const colorAttr = mesh.geometry.getAttribute("color");

    const low = new Float32Array(track.x.length).fill(0);
    applyShadowPriceOverlay(mesh, low);
    const lowColor = (colorAttr.array as Float32Array).slice(0, 3);

    const high = new Float32Array(track.x.length).fill(1);
    applyShadowPriceOverlay(mesh, high);
    const highColor = (colorAttr.array as Float32Array).slice(0, 3);

    // red channel should rise and green/blue fall as the scalar goes 0 -> 1
    expect(highColor[0]).toBeGreaterThan(lowColor[0]);
    expect(highColor[1]).toBeLessThanOrEqual(lowColor[1]);
  });
});
