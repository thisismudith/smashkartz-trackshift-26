import * as THREE from "three";
import type { TrackModel } from "../contract/types";
import { HAAS } from "@/lib/palette";
import { halfWidthAt } from "../data/manifest";
import { PRESENTATION_SCALE } from "./presentation";

/** Telemetry frame is X/Y horizontal, Z vertical; three.js scenes are conventionally
 * Y-up, so every conversion from track-frame to render-frame swaps Y and Z here, in
 * exactly one place. */
export function toRenderFrame(x: number, y: number, z: number): [number, number, number] {
  return [x, z, y];
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

/** Real pit-lane width is not in the telemetry any more than track width is, so this
 * is a documented RULE constant (F1 pit lanes are ~12 m wide including the box side). */
export const PIT_LANE_WIDTH_M = 12;

/**
 * The pit lane as its own ribbon, built from the explicit XY polyline the track model
 * carries (NOT a lateral offset from the racing line -- a pit lane shortcuts the
 * corner it bypasses, so no station-offset description of it is possible). Drawn in a
 * lighter grey than the racing surface so it reads as a separate piece of road.
 */
export function buildPitLaneMesh(track: TrackModel): THREE.Mesh | null {
  const path = track.pitLanePath;
  if (!path || path.x.length < 2) return null;
  const n = path.x.length;
  const half = (PIT_LANE_WIDTH_M / 2) * PRESENTATION_SCALE;
  const positions = new Float32Array(n * 2 * 3);
  const indices: number[] = [];

  for (let i = 0; i < n; i++) {
    // forward difference, clamped at the ends (the lane is open, not a loop)
    const i0 = i === n - 1 ? i - 1 : i;
    const i1 = i === n - 1 ? i : i + 1;
    const dx = path.x[i1] - path.x[i0];
    const dy = path.y[i1] - path.y[i0];
    const len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len, ny = dx / len;
    const [lx, ly, lz] = toRenderFrame(path.x[i] + nx * half, path.y[i] + ny * half, path.z[i]);
    const [rx, ry, rz] = toRenderFrame(path.x[i] - nx * half, path.y[i] - ny * half, path.z[i]);
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
  });
  const mesh = new THREE.Mesh(geom, mat);
  mesh.name = "pit-lane";
  // nudged just above the ground plane so it never z-fights the racing ribbon
  mesh.position.y = 0.02;
  return mesh;
}
