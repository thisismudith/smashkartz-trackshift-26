import * as THREE from "three";
import type { TrackModel } from "../contract/types";
import { HAAS } from "@/lib/palette";
import { halfWidthAt } from "../data/manifest";
import { PRESENTATION_SCALE } from "./presentation";

/**
 * Telemetry frame is X/Y horizontal with Z up; three.js scenes are conventionally
 * Y-up, so every conversion from track-frame to render-frame happens here, in exactly
 * one place.
 *
 * The Y/Z SWAP MUST CARRY A SIGN. `[x, z, y]` -- a bare swap -- has determinant -1: it
 * is a REFLECTION, not a rotation, and it silently mirrors the whole plan view. That
 * was live until this fix, and it inverted the rotational sense of twelve of the
 * thirteen shipped circuits (measured signed ring area, track frame: negative, i.e.
 * clockwise, on every circuit except Miami at +400,052 m^2 -- the one genuinely
 * anti-clockwise circuit, which rendered clockwise).
 *
 * `[x, z, -y]` has determinant +1 and preserves the data contract stated in
 * scripts/simdata/geom.py:8 ("Lateral offset is signed with + to the LEFT of travel"):
 * the track-frame left normal maps onto up x forward in the render frame, so left of
 * travel still draws left of travel. Both properties are locked by trackMesh.test.ts.
 */
export function toRenderFrame(x: number, y: number, z: number): [number, number, number] {
  return [x, z, -y];
}

/** Exact inverse of toRenderFrame. */
export function fromRenderFrame(X: number, Y: number, Z: number): [number, number, number] {
  return [X, -Z, Y];
}

/** Local forward axis of the car geometry (carGeometry.ts: "+X is forward (nose)"). */
const LOCAL_FORWARD = new THREE.Vector3(1, 0, 0);

/**
 * The render-frame direction a car whose track-frame heading is `heading` points in.
 *
 * Defined AS toRenderFrame applied to the track-frame forward vector, rather than
 * re-derived by hand, so the frame convention lives in exactly one place. Before this
 * existed the convention was spelled out four separate times -- the car yaw, the
 * onboard camera's eye and look-at, and the broadcast camera's eye -- each with its own
 * hand-written sign, which is why flipping toRenderFrame alone would have left the
 * cameras pointing backwards.
 */
export function renderForward(heading: number, out = new THREE.Vector3()): THREE.Vector3 {
  const [x, y, z] = toRenderFrame(Math.cos(heading), Math.sin(heading), 0);
  return out.set(x, y, z);
}

const ORIENT_SCRATCH = new THREE.Vector3();

/**
 * Orientation that puts the car's nose along renderForward(heading). Derived from
 * renderForward rather than from an axis-angle guess, so the two cannot drift apart:
 * the test asserts exactly this (local +X maps onto renderForward), which a
 * determinant-plus-round-trip test on toRenderFrame alone would not catch.
 */
export function carOrientation(heading: number, out = new THREE.Quaternion()): THREE.Quaternion {
  return out.setFromUnitVectors(LOCAL_FORWARD, renderForward(heading, ORIENT_SCRATCH));
}

/** Builds the track ribbon as one BufferGeometry: a strip of quads offset left/right
 * of the centreline by the per-station half-width. One mesh, one draw call for the
 * whole track surface -- the plan's own benchmark measured a 100k-triangle lit ribbon
 * at 0.6 ms GPU on this class of integrated GPU. */
export function buildTrackMesh(track: TrackModel): THREE.Mesh {
  const n = track.x.length;
  const positions = new Float32Array(n * 2 * 3);
  const colors = new Float32Array(n * 2 * 3);
  const indices: number[] = [];

  const kerbColor = new THREE.Color(HAAS.red);
  const surfaceColor = new THREE.Color(HAAS.black).lerp(new THREE.Color(HAAS.grey), 0.15);

  for (let i = 0; i < n; i++) {
    const i1 = (i + 1) % n;
    const dx = track.x[i1] - track.x[i];
    const dy = track.y[i1] - track.y[i];
    const len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len, ny = dx / len; // left normal in track-frame XY
    // widened by the same factor the cars are (see presentation.ts), so the
    // car-to-road proportion on screen stays the real one
    const hw = halfWidthAt(track, i * (track.lengthMetres / n)) * PRESENTATION_SCALE;

    const lx = track.x[i] + nx * hw, ly = track.y[i] + ny * hw;
    const rx = track.x[i] - nx * hw, ry = track.y[i] - ny * hw;
    const [lrx, lry, lrz] = toRenderFrame(lx, ly, track.z[i]);
    const [rrx, rry, rrz] = toRenderFrame(rx, ry, track.z[i]);

    positions.set([lrx, lry, lrz], i * 6);
    positions.set([rrx, rry, rrz], i * 6 + 3);

    // kerb tint near the edges (last 12% of the half-width on each side), a cheap
    // per-vertex colour instead of a second material/draw call
    kerbColor.toArray(colors, i * 6);
    kerbColor.toArray(colors, i * 6 + 3);
  }
  // overwrite the interior of the strip with the surface colour, leaving only the two
  // extreme edge-vertex rows (there are only 2 vertices per station here, so blend a
  // fraction of the edge tint into the surface colour instead of a separate row)
  for (let i = 0; i < n; i++) {
    surfaceColor.toArray(colors, i * 6);
    surfaceColor.toArray(colors, i * 6 + 3);
  }

  for (let i = 0; i < n; i++) {
    const i1 = (i + 1) % n;
    const a = i * 2, b = i * 2 + 1, c = i1 * 2, d = i1 * 2 + 1;
    indices.push(a, b, c, b, d, c);
  }

  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geom.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geom.setIndex(indices);
  geom.computeVertexNormals();

  const mat = new THREE.MeshStandardMaterial({
    vertexColors: true, roughness: 0.9, metalness: 0.0, side: THREE.DoubleSide,
  });
  const mesh = new THREE.Mesh(geom, mat);
  mesh.name = "track-surface";
  // a COPY, not a reference: colorAttr's own .array IS the `colors` array object, so
  // storing the reference here would let applyShadowPriceOverlay's in-place writes
  // corrupt the "original" this exists to restore (caught by trackMesh.test.ts).
  mesh.userData.baseColors = colors.slice();
  return mesh;
}

/**
 * E-Delta hook: recolours the track surface by a per-station scalar in [0, 1] (a
 * placeholder here; the real energy shadow price is a Python planner output the UI
 * has not been given yet). Blends the Haas grey/black surface toward Haas red, never
 * introducing a hue outside the palette, and restores the plain surface when passed
 * null. Provenance of the SOURCE VALUES is the caller's responsibility to label
 * (DERIVED, from the plan's two-lap dynamic programme) -- this function only draws
 * whatever scalar it is given.
 */
export function applyShadowPriceOverlay(mesh: THREE.Mesh, values: Float32Array | null) {
  const geom = mesh.geometry as THREE.BufferGeometry;
  const colorAttr = geom.getAttribute("color") as THREE.BufferAttribute;
  const base = mesh.userData.baseColors as Float32Array;
  if (!values) {
    colorAttr.array.set(base);
    colorAttr.needsUpdate = true;
    return;
  }
  const n = colorAttr.count; // 2 vertices per station
  const grey = new THREE.Color(HAAS.grey);
  const red = new THREE.Color(HAAS.red);
  const blended = new THREE.Color();
  const arr = colorAttr.array as Float32Array;
  for (let i = 0; i < n; i++) {
    const stationIdx = Math.floor(i / 2) % values.length;
    const t = Math.max(0, Math.min(1, values[stationIdx]));
    blended.copy(grey).lerp(red, t);
    arr[i * 3] = blended.r; arr[i * 3 + 1] = blended.g; arr[i * 3 + 2] = blended.b;
  }
  colorAttr.needsUpdate = true;
}

/** A thin outline along each edge of the ribbon, for legibility against the ground
 * plane. Uses ordinary (thin) LineSegments -- the plan's benchmark found this costs
 * 0.4 ms GPU for a 12,000-segment loop, versus 47-52 ms for screen-spanning random
 * segments, so a track-following loop like this is well inside budget. */
export function buildTrackOutline(track: TrackModel): THREE.LineSegments {
  const n = track.x.length;
  const positions: number[] = [];
  for (let i = 0; i < n; i++) {
    const i1 = (i + 1) % n;
    const dx = track.x[i1] - track.x[i];
    const dy = track.y[i1] - track.y[i];
    const len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len, ny = dx / len;
    // widened by the same factor the cars are (see presentation.ts), so the
    // car-to-road proportion on screen stays the real one
    const hw = halfWidthAt(track, i * (track.lengthMetres / n)) * PRESENTATION_SCALE;
    for (const side of [1, -1]) {
      const [rx, ry, rz] = toRenderFrame(track.x[i] + side * nx * hw, track.y[i] + side * ny * hw, track.z[i]);
      const [rx1, ry1, rz1] = toRenderFrame(
        track.x[i1] + side * nx * hw, track.y[i1] + side * ny * hw, track.z[i1],
      );
      positions.push(rx, ry, rz, rx1, ry1, rz1);
    }
  }
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  const mat = new THREE.LineBasicMaterial({ color: new THREE.Color(HAAS.grey), transparent: true, opacity: 0.6 });
  return new THREE.LineSegments(geom, mat);
}

/**
 * RULE constant, like track width: the telemetry gives the lane's CENTRE but not its
 * width.
 *
 * Sized against the measured separation. Over the 22 segments this renderer draws, the
 * perpendicular offset of the lane centre from the racing line has a per-segment median
 * of 5.3-32.6 m, and closes to 1.3-6.3 m at the end where the lane meets the road --
 * against a racing surface whose own half-width is 6.00-7.50 m on all 13 shipped models.
 * So along the stretch where the two run side by side a ribbon much wider than this
 * climbs onto the track rather than sitting beside it, and at the end where they meet
 * even this width is already inside the racing surface, which is what PIT_TAPER_FRACTION
 * is for.
 */
export const PIT_LANE_WIDTH_M = 9;

/**
 * Fraction of a pit segment over which the ribbon fades to nothing where it MEETS the
 * racing surface. Exactly ONE end of each segment does. Measured over the 26 segments
 * of the 13 shipped track models, the end that meets the road is 1.3-11.1 m from the
 * nearest ring vertex and the far end 7.0-559.8 m (7.0-36.3 m on the eleven circuits
 * whose position feed does not freeze), so which end it is is never in doubt -- and the
 * geometric test in buildPitLaneMesh agrees with the `role` string on all 26.
 *
 * Tapering BOTH ends also narrowed the far end of every run to a point. That end is not
 * an edge of anything: it is the middle of the pit road, where the entry segment hands
 * over to the exit segment 6.7-13.7 m away on eleven of the thirteen circuits (Italian
 * 74.7 m is the one wide handover). The span it consumed there is 12% of the run --
 * 14.2-74.4 m of pit road on the eleven healthy circuits. The ribbon was not ABSENT
 * over that span: the ramp is linear, so the half-width is still 2.25 m of its 4.5 m a
 * twentieth of the run from the end, and only collapses to zero at the final vertex.
 * A pinch, not a gap, is what was wrong with it.
 */
export const PIT_TAPER_FRACTION = 0.12;

/**
 * How far from the racing line the adjacent track surface may still define the pit
 * lane's elevation. 60 m is the bound scripts/simdata/track.py already declares as
 * MAX_PIT_LAT_M ("beyond this it is a projection artefact, not a pit lane").
 *
 * It is a per-vertex SOURCE bound, not a per-run rejection gate, and that distinction
 * is what keeps Silverstone. Its entry lane shortcuts Club while the ring loops round
 * the corner, so 20 of that segment's 80 vertices are 60.0-102.9 m from the nearest
 * ring vertex -- a lane that is entirely correct. Those 20 fall in ONE interior run,
 * take an interpolated elevation from the anchors either side, and the segment draws in
 * full. Rejecting a whole run at 60 m, as an earlier proposal had it, would have
 * deleted it.
 *
 * Measured nearest-ring distance per segment over the eleven circuits whose position
 * feed does not freeze: median 5.3-32.6 m, max 14.9-102.9 m, 75-100% of vertices inside
 * the bound.
 */
export const PIT_DRAPE_MAX_M = 60;

/** Tie-break depth for the pit ribbon under the racing surface. The pit material
 * already carries a polygonOffset, which is what actually makes the racing surface win
 * the depth test where the two are coplanar; this is only belt and braces. It was
 * 0.15 m, a visible 15 cm step down onto the lane that also left every car drawn over
 * the ribbon floating by that much on top of its own 0.200 m float. */
export const PIT_SURFACE_DEPTH_M = 0.02;

export interface PitPathElevation {
  /** Drawn elevation per vertex, track-frame metres. */
  z: Float32Array;
  /** Distance from each vertex to the nearest ring vertex, metres. */
  ringDistance: Float32Array;
  /** How many vertices took their elevation from a ring point inside the bound. */
  anchored: number;
}

/**
 * The elevation the pit ribbon is DRAWN at, taken from the adjacent racing surface
 * rather than from the pit polyline's own z. Provenance: INFERRED, not OBSERVED.
 *
 * Why the shipped z cannot be drawn as measured: the position feed holds z constant
 * through the whole pit stretch. Measured over the 13 shipped track models, a pit
 * segment's own z spans 0.00-2.15 m while the ring it runs beside spans up to 18.61 m
 * over the same ground, and the resulting gap between ribbon and road reaches a median
 * 8.28 m at Suzuka (max 22.72 m) and 8.05 m at the Red Bull Ring.
 *
 * Why the ring is the right source: the renderer places a CAR from the ring -- a
 * station gives it (x, y, z) on the centreline and the lateral offset never changes z
 * (scene.ts updateCars) -- so a car in the pit lane is already drawn at ring
 * elevation. Taking the ribbon's elevation from the same place is what makes the car
 * sit ON the lane instead of 8-23 m above or below it. When Python can supply a
 * trustworthy pit z AND the car placement itself follows the pit polyline, both change
 * together; until then one source for both is the only self-consistent choice.
 *
 * Returns null when FEWER THAN HALF the segment's vertices are within PIT_DRAPE_MAX_M
 * of the ring -- equivalently, when the MEDIAN vertex has no adjacent racing surface at
 * all. There is then no elevation to be had for most of the ribbon, and the segment is
 * not drawn rather than drawn at a guessed height.
 *
 * That threshold is where the measurement puts it, not a round number. Over the shipped
 * models the eleven circuits whose position feed does not freeze anchor 75-100% of their
 * vertices (Silverstone's shortcut past Club is the 75% floor), while the two whose feed
 * freezes on a sentinel coordinate anchor 13.8-26.3%: their "pit lane" is a 536-1272 m
 * spike out to a point 514-571 m off the circuit, and on their entry segments 59 and 69
 * of the 80 vertices lie past the LAST anchor, where the only elevation on offer is a
 * held one. Half-way between 26.3% and 75% is a 1.9x margin on one side and 1.5x on the
 * other.
 */
export function pitPathElevation(
  track: TrackModel, path: { x: Float32Array; y: Float32Array },
): PitPathElevation | null {
  const n = path.x.length;
  const rn = track.x.length;
  const z = new Float32Array(n);
  const ringDistance = new Float32Array(n);
  const anchor = new Int32Array(n).fill(-1);
  let anchored = 0;

  for (let i = 0; i < n; i++) {
    let best = Infinity, bestJ = -1;
    for (let j = 0; j < rn; j++) {
      const dx = track.x[j] - path.x[i], dy = track.y[j] - path.y[i];
      const d2 = dx * dx + dy * dy;
      if (d2 < best) { best = d2; bestJ = j; }
    }
    ringDistance[i] = Math.sqrt(best);
    if (ringDistance[i] <= PIT_DRAPE_MAX_M && bestJ >= 0) {
      z[i] = track.z[bestJ];
      anchor[i] = bestJ;
      anchored++;
    }
  }
  // Not "is there any elevation at all" but "does the ribbon mostly run alongside the
  // circuit": see the measured 75-100% vs 13.8-26.3% separation above.
  if (anchored * 2 <= n) return null;

  // Vertices too far from the ring get no elevation of their own: interpolate along
  // the lane between the anchors either side, and hold the nearest anchor's value past
  // the first and last one. Never an absolute value invented from nothing.
  let prev = -1;
  for (let i = 0; i < n; i++) {
    if (anchor[i] < 0) continue;
    if (prev < 0) { for (let k = 0; k < i; k++) z[k] = z[i]; }
    else if (i - prev > 1) {
      for (let k = prev + 1; k < i; k++) z[k] = z[prev] + (z[i] - z[prev]) * ((k - prev) / (i - prev));
    }
    prev = i;
  }
  for (let k = prev + 1; k < n; k++) z[k] = z[prev];

  return { z, ringDistance, anchored };
}

/**
 * The pit lane as its own ribbon, built from the explicit XY polyline the track model
 * carries (NOT a lateral offset from the racing line -- a pit lane shortcuts the
 * corner it bypasses, so no station-offset description of it is possible). Drawn in a
 * lighter grey than the racing surface so it reads as a separate piece of road.
 *
 * Elevation is INFERRED from the adjacent racing surface, not read from the polyline
 * -- see pitPathElevation for the measurement that forces that.
 */
export function buildPitLaneMesh(track: TrackModel): THREE.Group | null {
  const segments = track.pitLanePath;
  if (!segments || segments.length === 0) return null;
  const group = new THREE.Group();
  group.name = "pit-lane";
  /** Segments with no usable elevation anywhere; reported rather than drawn. */
  let elevationUnavailable = 0;

  for (const path of segments) {
    const n = path.x.length;
    if (n < 2) continue;
    const elevation = pitPathElevation(track, path);
    if (!elevation) { elevationUnavailable++; continue; }
    const positions = new Float32Array(n * 2 * 3);
    const indices: number[] = [];
    const full = (PIT_LANE_WIDTH_M / 2) * PRESENTATION_SCALE;
    // Taper the end that actually meets the racing surface, which is whichever end
    // runs closest to the ring. Derived from the geometry rather than from the `role`
    // string so a future segment naming cannot silently taper the wrong end; on the 13
    // shipped models this agrees with role for all 26 segments.
    const taperAtStart = elevation.ringDistance[0] <= elevation.ringDistance[n - 1];

    for (let i = 0; i < n; i++) {
      const i0 = i === n - 1 ? i - 1 : i;
      const i1 = i === n - 1 ? i : i + 1;
      const dx = path.x[i1] - path.x[i0];
      const dy = path.y[i1] - path.y[i0];
      const len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len, ny = dx / len;
      // A constant-width ribbon stops in a hard rectangular cap, which punched a
      // visible notch through the racing surface where the lane meets it; fading the
      // width there lets it slip under the track. The OTHER end is the far end of the
      // pit road and is left at full width.
      const t = i / (n - 1);
      const ramp = Math.min(1, (taperAtStart ? t : 1 - t) / PIT_TAPER_FRACTION);
      const half = full * ramp;
      const ez = elevation.z[i];
      const [lx, ly, lz] = toRenderFrame(path.x[i] + nx * half, path.y[i] + ny * half, ez);
      const [rx, ry, rz] = toRenderFrame(path.x[i] - nx * half, path.y[i] - ny * half, ez);
      positions.set([lx, ly, lz], i * 6);
      positions.set([rx, ry, rz], i * 6 + 3);
      if (i < n - 1) {
        const a = i * 2, b = i * 2 + 1, c = (i + 1) * 2, d = (i + 1) * 2 + 1;
        indices.push(a, b, c, b, d, c);
      }
    }

    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geom.setIndex(indices);
    geom.computeVertexNormals();
    const mat = new THREE.MeshStandardMaterial({
      color: new THREE.Color(HAAS.grey).multiplyScalar(0.45),
      roughness: 0.95, metalness: 0, side: THREE.DoubleSide,
      polygonOffset: true, polygonOffsetFactor: 2, polygonOffsetUnits: 2,
    });
    const mesh = new THREE.Mesh(geom, mat);
    mesh.name = `pit-lane-${path.role}`;
    mesh.userData.anchoredVertices = elevation.anchored;
    group.add(mesh);
  }

  // The lane runs alongside and merges INTO the racing surface. Where they overlap the
  // racing surface must win: polygonOffset on the material does that, drawing first
  // (renderOrder -1) backs it up, and this is only a tie-break epsilon.
  group.position.y = -PIT_SURFACE_DEPTH_M;
  group.renderOrder = -1;
  group.userData.elevationUnavailableSegments = elevationUnavailable;
  return group.children.length ? group : null;
}

export interface PitLaneAvailability {
  /** Segment roles this renderer will draw. */
  drawn: string[];
  /** Segment roles it refuses to draw, with the reason, for the UI to SAY. */
  unavailable: { role: string; reason: string }[];
}

/**
 * What buildPitLaneMesh will and will not draw, without building it.
 *
 * buildPitLaneMesh returns null for a track whose pit polyline it refuses, and a null
 * mesh is indistinguishable from "this circuit has no pit-lane data" once it reaches
 * the scene -- so a UI that keys "has a pit lane" off `track.pitLanePath !== null` will
 * advertise a lane that is not on screen. AGENTS.md 0.3 wants the loss stated, not
 * silently absorbed; this is the statement. Nothing here decides anything the mesh
 * builder does not -- it runs the same test.
 */
export function pitLaneAvailability(track: TrackModel): PitLaneAvailability {
  const out: PitLaneAvailability = { drawn: [], unavailable: [] };
  for (const path of track.pitLanePath ?? []) {
    if (path.x.length < 2) {
      out.unavailable.push({ role: path.role, reason: "fewer than two vertices" });
    } else if (pitPathElevation(track, path)) {
      out.drawn.push(path.role);
    } else {
      out.unavailable.push({
        role: path.role,
        reason: `over half the polyline is more than ${PIT_DRAPE_MAX_M} m from the racing `
          + "surface, so there is no measured elevation to draw it at",
      });
    }
  }
  return out;
}
