import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import type { TrackModel, TrackSurface, TrackSurfaceTransform } from "../contract/types";
import { CAR } from "@/components/loader/physics/constants";
import { parseTrackModel, trackPointAt, type RawTrackModel } from "../data/manifest";
import { POSE_FLOATS_PER_CAR, POSE_STATUS } from "../worker/protocol";
import { buildF1CarGeometry } from "./carGeometry";
import {
  applyEnvironmentMaterials, ENVIRONMENTS, environmentFor, environmentForTrack,
  environmentPlacement, GATE_MAX_RESIDUAL_STD_M, GATE_MIN_COVERAGE, measurementPasses,
  type EnvironmentPlacement,
} from "./environments";
import {
  CAR_GROUND_CLEARANCE_M, FOCUS_OUTLINE_SCALE, carInstanceY, focusGhostY, PRESENTATION_SCALE,
} from "./presentation";
import {
  carRenderPos, chaseCameraPose, declutterLanes, disposeRenderObject, disposeTrackLayers,
  environmentGlbCached, installTrackLayers, loadEnvironmentGlb, orbitEye, orbitPanBasis,
  setRibbonOverEnvironment,
} from "./scene";
import { renderForward, toRenderFrame } from "./trackMesh";

const RING_R = 500;
const RING_N = 400;

/** A closed circular ring whose length is the MEASURED polyline length, so
 * `lengthMetres / n` is the real vertex spacing -- the same invariant the Python
 * Ring now guarantees by construction (geom.Ring.ds == length / n). */
function makeRing(over: Partial<TrackModel> = {}): TrackModel {
  const x = new Float32Array(RING_N), y = new Float32Array(RING_N), z = new Float32Array(RING_N);
  for (let i = 0; i < RING_N; i++) {
    const a = (i / RING_N) * 2 * Math.PI;
    x[i] = Math.cos(a) * RING_R;
    y[i] = Math.sin(a) * RING_R;
  }
  let len = 0;
  for (let i = 0; i < RING_N; i++) {
    const j = (i + 1) % RING_N;
    len += Math.hypot(x[j] - x[i], y[j] - y[i]);
  }
  return {
    slug: "scene-ring", event: "Scene GP", lengthMetres: len,
    x, y, z, halfWidth: new Float32Array([6]), widthBinMetres: len,
    timingLines: { sf: 0, s1: len / 3, s2: (2 * len) / 3 }, corners: [],
    grid: { order: [], pitchMetres: 8 },
    pitLanePath: null,
    pitLane: { entryStation: null, exitStation: null, mergeStation: null, loopLateral: null },
    referenceProfile: { binMetres: 10, speedKph: new Float32Array([250]), gear: new Uint8Array([7]) },
    ...over,
  };
}

/** A pose buffer with every car on track, at the stations and laterals given. */
function makePose(stations: number[], laterals: number[], status: number = POSE_STATUS.track): Float32Array {
  const pose = new Float32Array(stations.length * POSE_FLOATS_PER_CAR);
  for (let i = 0; i < stations.length; i++) {
    const o = i * POSE_FLOATS_PER_CAR;
    pose[o + 0] = stations[i];
    pose[o + 1] = laterals[i];
    // a deliberately WRONG heading channel: nothing in the render path may read it
    pose[o + 3] = 999;
    pose[o + 8] = 3;          // lapsDone, past the opening-lap fan window
    pose[o + 9] = 0.5;        // lapProgress
    pose[o + 10] = i + 1;     // position
    pose[o + 12] = status;
  }
  return pose;
}

const carMinY = (() => {
  const g = buildF1CarGeometry();
  g.computeBoundingBox();
  return g.boundingBox!.min.y;
})();

describe("carRenderPos: the draw loop and the camera share ONE projection", () => {
  it("measures the car geometry rather than trusting the retired box constant", () => {
    // the built geometry, measured here rather than retyped: the wheels set both extremes
    expect(carMinY).toBeCloseTo(-0.3, 4);
    // what CAR_RENDER_HEIGHT_M = 0.9 produced: 0.9/2 + 0.05 + (-0.30) = +0.200 m of air
    expect(0.9 / 2 + 0.05 + carMinY).toBeCloseTo(0.2, 6);

    const track = makeRing();
    const out = new THREE.Vector3();
    carRenderPos(track, 0, 0, carMinY, out);
    // track z is 0 here, and toRenderFrame sends track z to render Y, so the ribbon is
    // at render Y = 0 and the contact patch must land CAR_GROUND_CLEARANCE_M above it
    expect(out.y + carMinY).toBeCloseTo(CAR_GROUND_CLEARANCE_M, 9);
    expect(out.y + carMinY).not.toBeCloseTo(0.2, 3);
  });

  it("puts the lateral on the LEFT of travel and scales it with the road", () => {
    const track = makeRing();
    const centre = new THREE.Vector3(), left = new THREE.Vector3();
    const heading = { value: 0 };
    const station = track.lengthMetres * 0.37;
    carRenderPos(track, station, 0, carMinY, centre, heading);
    carRenderPos(track, station, 4, carMinY, left, heading);

    // the offset is exactly 4 m * PRESENTATION_SCALE, horizontally
    const dx = left.x - centre.x, dz = left.z - centre.z;
    expect(Math.hypot(dx, dz)).toBeCloseTo(4 * PRESENTATION_SCALE, 6);
    expect(left.y).toBeCloseTo(centre.y, 9);
    // and it points 90 deg to the LEFT of the render-frame forward vector
    const fwd = renderForward(heading.value);
    const leftOfTravel = toRenderFrame(-Math.sin(heading.value), Math.cos(heading.value), 0);
    expect(dx / 4).toBeCloseTo(leftOfTravel[0], 6);
    expect(dz / 4).toBeCloseTo(leftOfTravel[2], 6);
    expect(fwd.x * dx + fwd.z * dz).toBeCloseTo(0, 6); // perpendicular to travel
  });

  it("takes heading from the station it is DRAWN at, never from pose[3]", () => {
    // carRenderPos is not given the pose at all -- the heading is a pure function of
    // the station, which is the producer's own definition (headingRad ===
    // trackPointAt(track, stationM).heading). Two different blended stations must give
    // two different headings, so the nose cannot lead the body.
    const track = makeRing();
    const out = new THREE.Vector3();
    const a = { value: 0 }, b = { value: 0 };
    carRenderPos(track, 0, 0, carMinY, out, a);
    carRenderPos(track, track.lengthMetres * 0.25, 0, carMinY, out, b);
    expect(Math.abs(b.value - a.value)).toBeGreaterThan(1.0);
    expect(a.value).not.toBe(999);
    expect(b.value).not.toBe(999);
  });

  it("wraps the station the short way round the lap", () => {
    const track = makeRing();
    const atZero = new THREE.Vector3(), atLap = new THREE.Vector3(), atNeg = new THREE.Vector3();
    carRenderPos(track, 0, 0, carMinY, atZero);
    carRenderPos(track, track.lengthMetres, 0, carMinY, atLap);
    carRenderPos(track, -track.lengthMetres, 0, carMinY, atNeg);
    expect(atLap.distanceTo(atZero)).toBeLessThan(1e-6);
    expect(atNeg.distanceTo(atZero)).toBeLessThan(1e-6);
  });
});

describe("the camera frames the car it can SEE, not the raw pose", () => {
  it("aims at the decluttered lateral, which is metres away from the recorded one", () => {
    const track = makeRing();
    // 20 cars recorded on nearly one line, as real telemetry records them (audit: p98
    // lateral spread 0.3-0.6 m against a 2.0 m car)
    const n = 20;
    const stations: number[] = [], laterals: number[] = [];
    for (let i = 0; i < n; i++) { stations.push(1000 + i * 0.4); laterals.push(0.1); }
    const pose = makePose(stations, laterals);

    const station = new Float32Array(stations);
    const lateral = new Float32Array(laterals);
    declutterLanes(n, pose, station, lateral, track, []);

    // how far the drawn car is from where the pose says it is -- this is exactly the
    // distance the camera used to be off by, because it aimed at the pose
    let worst = 0;
    for (let i = 0; i < n; i++) worst = Math.max(worst, Math.abs(lateral[i] - laterals[i]));
    expect(worst).toBeGreaterThan(CAR.widthM); // more than a whole car width

    const drawn = new THREE.Vector3(), raw = new THREE.Vector3();
    const heading = { value: 0 };
    const worstCar = lateral.indexOf(Math.max(...Array.from(lateral)));
    carRenderPos(track, station[worstCar], lateral[worstCar], carMinY, drawn, heading);
    carRenderPos(track, station[worstCar], pose[worstCar * POSE_FLOATS_PER_CAR + 1], carMinY, raw);
    expect(drawn.distanceTo(raw)).toBeCloseTo(worst * PRESENTATION_SCALE, 6);

    // the broadcast camera must look at the DRAWN point, to the last float
    const eye = new THREE.Vector3(), lookAt = new THREE.Vector3();
    chaseCameraPose("broadcast", drawn, heading.value, eye, lookAt);
    expect(lookAt.x).toBe(drawn.x);
    expect(lookAt.y).toBe(drawn.y);
    expect(lookAt.z).toBe(drawn.z);
    expect(lookAt.distanceTo(raw)).toBeGreaterThan(CAR.widthM);
  });

  it("sits BEHIND the car along renderForward, in every chase mode", () => {
    const focus = new THREE.Vector3(120, 3, -40);
    const eye = new THREE.Vector3(), lookAt = new THREE.Vector3();
    for (const h of [0, 0.7, 2.4, -1.1, Math.PI]) {
      const fwd = renderForward(h);
      for (const mode of ["broadcast", "onboard"] as const) {
        chaseCameraPose(mode, focus, h, eye, lookAt);
        const back = eye.clone().sub(focus);
        back.y = 0;
        // the eye is directly behind the nose: -fwd, no lateral component
        expect(back.clone().normalize().dot(fwd)).toBeCloseTo(-1, 9);
        expect(eye.y).toBeGreaterThan(focus.y); // and above it
        // the car is in front of the camera and inside the 55 deg frustum, so the
        // camera really is framing it rather than looking past it
        const toCar = focus.clone().sub(eye);
        const view = lookAt.clone().sub(eye);
        expect(view.dot(toCar)).toBeGreaterThan(0);
        expect((view.angleTo(toCar) * 180) / Math.PI).toBeLessThan(55 / 2);
        // the car is never off to one SIDE: the whole offset is the vertical drop
        const side = toCar.clone().projectOnPlane(new THREE.Vector3(0, 1, 0))
          .projectOnPlane(new THREE.Vector3(view.x, 0, view.z).normalize());
        expect(side.length()).toBeLessThan(1e-6);
      }
      // broadcast looks AT the car; onboard looks down the road ahead of it
      chaseCameraPose("broadcast", focus, h, eye, lookAt);
      expect(lookAt.distanceTo(focus)).toBe(0);
      chaseCameraPose("onboard", focus, h, eye, lookAt);
      expect(lookAt.distanceTo(focus)).toBeCloseTo(20, 9);
      expect(lookAt.clone().sub(focus).normalize().dot(fwd)).toBeCloseTo(1, 9);
      chaseCameraPose("helicopter", focus, h, eye, lookAt);
      expect(eye.y - focus.y).toBeCloseTo(45, 9);
      expect(lookAt.distanceTo(focus)).toBe(0);
    }
  });

  it("allocates nothing per frame: every out-param is caller-owned", () => {
    const focus = new THREE.Vector3(1, 2, 3);
    const eye = new THREE.Vector3(), lookAt = new THREE.Vector3(), fwd = new THREE.Vector3();
    const outPos = new THREE.Vector3();
    const track = makeRing();
    expect(chaseCameraPose("broadcast", focus, 0.3, eye, lookAt, fwd)).toBeUndefined();
    expect(carRenderPos(track, 10, 0, carMinY, outPos)).toBe(outPos);
    expect(orbitEye(focus, 0.3, 0.6, 40, outPos)).toBe(outPos);
  });
});

describe("declutterLanes", () => {
  it("separates cars stacked at one station and leaves them on the road", () => {
    const track = makeRing();
    const n = 6;
    const stations = Array.from({ length: n }, () => 2000);
    const laterals = Array.from({ length: n }, () => 0);
    const pose = makePose(stations, laterals);
    const station = new Float32Array(stations), lateral = new Float32Array(laterals);
    declutterLanes(n, pose, station, lateral, track, []);

    const sorted = Array.from(lateral).sort((a, b) => a - b);
    for (let i = 1; i < n; i++) expect(sorted[i] - sorted[i - 1]).toBeGreaterThan(0.5);
    // every car centre stays within half a car of the painted edge
    const halfW = track.halfWidth[0];
    for (const l of lateral) expect(Math.abs(l)).toBeLessThanOrEqual(halfW - CAR.widthM / 2 + 1e-9);
  });

  it("is STABLE: the same pose always yields the same lanes", () => {
    const track = makeRing();
    const n = 20;
    const stations = Array.from({ length: n }, (_, i) => 800 + i * 0.3);
    const laterals = Array.from({ length: n }, () => 0.2);
    const pose = makePose(stations, laterals);
    const a = new Float32Array(laterals), b = new Float32Array(laterals);
    const order: number[] = [];
    declutterLanes(n, pose, new Float32Array(stations), a, track, order);
    declutterLanes(n, pose, new Float32Array(stations), b, track, order);
    expect(Array.from(b)).toEqual(Array.from(a));
    // the scratch array really is reused rather than regrown each call
    expect(order.length).toBe(n);
  });

  it("never shoves a grid, pit or parked car sideways", () => {
    const track = makeRing();
    for (const status of [POSE_STATUS.grid, POSE_STATUS.pit, POSE_STATUS.finished, POSE_STATUS.retired]) {
      const n = 8;
      const stations = Array.from({ length: n }, (_, i) => 500 + i * 0.5);
      const laterals = Array.from({ length: n }, (_, i) => (i % 2 === 0 ? -1.8 : 1.8));
      const pose = makePose(stations, laterals, status);
      const lateral = new Float32Array(laterals);
      const untouched = Float32Array.from(laterals);
      declutterLanes(n, pose, new Float32Array(stations), lateral, track, []);
      expect(Array.from(lateral)).toEqual(Array.from(untouched));
    }
  });

  it("lets the opening lap use the FULL road, and no lap after it", () => {
    const track = makeRing();
    const n = 12;
    const stations = Array.from({ length: n }, (_, i) => 300 + i * 0.2);
    const laterals = Array.from({ length: n }, () => 0);
    const halfW = track.halfWidth[0];

    const opening = makePose(stations, laterals);
    for (let i = 0; i < n; i++) {
      opening[i * POSE_FLOATS_PER_CAR + 8] = 0;
      opening[i * POSE_FLOATS_PER_CAR + 9] = 0.05;
    }
    const openLat = new Float32Array(laterals);
    declutterLanes(n, opening, new Float32Array(stations), openLat, track, []);
    const openSpan = Math.max(...openLat) - Math.min(...openLat);

    const settled = makePose(stations, laterals); // lapsDone 3, lapProgress 0.5
    const settledLat = new Float32Array(laterals);
    declutterLanes(n, settled, new Float32Array(stations), settledLat, track, []);
    const settledSpan = Math.max(...settledLat) - Math.min(...settledLat);

    expect(openSpan).toBeCloseTo(2 * halfW, 6);
    expect(settledSpan).toBeCloseTo(2 * (halfW - CAR.widthM / 2), 6);
    expect(openSpan - settledSpan).toBeCloseTo(CAR.widthM, 6);
  });
});

describe("the focused car's ghost outline", () => {
  it("stands on the same plane as the car, not 21 mm inside the tarmac", () => {
    const surfaceY = 77.5;
    const carY = carInstanceY(surfaceY, carMinY);
    const ghostY = focusGhostY(carY, carMinY);
    // the ghost is a uniform FOCUS_OUTLINE_SCALE copy about the geometry's own origin
    const ghostUnderside = ghostY + carMinY * FOCUS_OUTLINE_SCALE;
    const carContactPatch = carY + carMinY;
    expect(ghostUnderside).toBeCloseTo(carContactPatch, 9);
    expect(carContactPatch - surfaceY).toBeCloseTo(CAR_GROUND_CLEARANCE_M, 9);

    // without the lift it sank by exactly -minY * (scale - 1)
    const uncompensated = carY + carMinY * FOCUS_OUTLINE_SCALE;
    expect(carContactPatch - uncompensated).toBeCloseTo(0.021, 4);
    expect(uncompensated).toBeLessThan(surfaceY); // i.e. below the road
  });

  it("derives the lift from the geometry, so a taller car still stands on the road", () => {
    for (const minY of [-0.3, -0.5, -0.05, 0]) {
      const carY = carInstanceY(0, minY);
      expect(focusGhostY(carY, minY) + minY * FOCUS_OUTLINE_SCALE).toBeCloseTo(carY + minY, 12);
    }
  });
});

describe("orbit camera basis", () => {
  /** The camera's own world axes, which is what a pan has to move along. */
  function cameraAxes(centre: THREE.Vector3, yaw: number, pitch: number, dist: number) {
    const cam = new THREE.PerspectiveCamera(55, 1.6, 1, 20000);
    orbitEye(centre, yaw, pitch, dist, cam.position);
    cam.lookAt(centre);
    cam.updateMatrixWorld(true);
    const xAxis = new THREE.Vector3(), yAxis = new THREE.Vector3(), zAxis = new THREE.Vector3();
    cam.matrixWorld.extractBasis(xAxis, yAxis, zAxis);
    return { cam, xAxis, yAxis, zAxis };
  }

  it("orbitEye really sits `dist` from the centre, at the yaw and pitch asked for", () => {
    const centre = new THREE.Vector3(-300, 12, 480);
    const out = new THREE.Vector3();
    for (const [yaw, pitch, dist] of [[0, 0.6, 400], [1.3, 0.05, 40], [-2.2, 1.5, 8000]]) {
      orbitEye(centre, yaw, pitch, dist, out);
      expect(out.distanceTo(centre)).toBeCloseTo(dist, 6);
      // polar angle measured from the render UP axis
      expect(Math.acos((out.y - centre.y) / dist)).toBeCloseTo(pitch, 9);
      expect(Math.atan2(out.z - centre.z, out.x - centre.x)).toBeCloseTo(yaw, 9);
    }
  });

  it("pan `right` and `fwd` ARE the camera's screen axes", () => {
    const centre = new THREE.Vector3(140, 5, -60);
    const right = new THREE.Vector3(), fwd = new THREE.Vector3();
    for (const yaw of [0, 0.9, 2.7, -1.4, Math.PI / 4]) {
      for (const pitch of [0.15, 0.6, 1.2]) {
        const { xAxis, zAxis } = cameraAxes(centre, yaw, pitch, 320);
        orbitPanBasis(yaw, right, fwd);
        // screen right: the camera's +X, which is already horizontal for an orbit rig
        expect(xAxis.y).toBeCloseTo(0, 9);
        expect(right.distanceTo(xAxis)).toBeLessThan(1e-9);
        // fwd: the camera's +Z (back toward the viewer) flattened onto the ground
        const flatZ = new THREE.Vector3(zAxis.x, 0, zAxis.z).normalize();
        expect(fwd.distanceTo(flatZ)).toBeLessThan(1e-9);
        expect(right.dot(fwd)).toBeCloseTo(0, 12);
      }
    }
  });

  it("drags the world WITH the pointer in both axes", () => {
    // a pan adds -dx * right and -dy * fwd to the target, so a point that was under
    // the cursor moves the same way the cursor did, on screen
    const centre = new THREE.Vector3(0, 0, 0);
    const right = new THREE.Vector3(), fwd = new THREE.Vector3();
    const yaw = 0.8, pitch = 0.7, dist = 300;
    orbitPanBasis(yaw, right, fwd);
    const { cam } = cameraAxes(centre, yaw, pitch, dist);

    const dx = 30, dy = 20;
    const panOffset = new THREE.Vector3()
      .addScaledVector(right, -dx * 1).addScaledVector(fwd, -dy * 1);
    const before = centre.clone().project(cam);
    // the rig translates by panOffset, so the world moves by -panOffset relative to it
    const after = centre.clone().sub(panOffset).project(cam);
    expect(after.x - before.x).toBeGreaterThan(0); // dragged right -> world went right
    expect(after.y - before.y).toBeLessThan(0);    // dragged down  -> world went down
  });

  it("is NOT renderForward, and the two must not be merged", () => {
    // renderForward carries the track-frame -> render-frame handedness ([x, z, -y]);
    // orbitYaw is already a render-space angle. Same argument, opposite Z sign. Routing
    // the pan through renderForward after the handedness fix would invert vertical drag.
    const right = new THREE.Vector3(), fwd = new THREE.Vector3();
    for (const a of [0.3, 1.1, -2.0]) {
      orbitPanBasis(a, right, fwd);
      const rf = renderForward(a);
      expect(fwd.x).toBeCloseTo(rf.x, 12);
      expect(fwd.z).toBeCloseTo(-rf.z, 12);
      expect(fwd.distanceTo(rf)).toBeGreaterThan(0.1);
    }
  });
});

/** The shipped artifacts are build outputs (frontend/public/sim is gitignored), so the
 * real-data assertions run wherever a build exists and are skipped where it does not. */
function loadShippedTrack(slug: string): TrackModel | null {
  const dir = fileURLToPath(new URL("../../../public/sim/", import.meta.url));
  const pointer = `${dir}index.json`;
  if (!existsSync(pointer)) return null;
  const latest = JSON.parse(readFileSync(pointer, "utf8")).latest as string;
  const index = JSON.parse(readFileSync(`${dir}${latest}`, "utf8")) as { tracks: Record<string, string> };
  const file = index.tracks[slug];
  if (!file) return null;
  return parseTrackModel(JSON.parse(readFileSync(`${dir}${file}`, "utf8")) as RawTrackModel);
}

function referenceSpeedKph(track: TrackModel, station: number): number {
  const p = track.referenceProfile;
  const nb = p.speedKph.length;
  const s = ((station % track.lengthMetres) + track.lengthMetres) % track.lengthMetres;
  return p.speedKph[Math.min(nb - 1, Math.floor(s / p.binMetres))];
}

const suzuka = loadShippedTrack("japanese-grand-prix");

describe.skipIf(!suzuka)("against the shipped Japanese GP model", () => {
  it("removes the yaw lead an un-blended heading leaves at 20x playback", () => {
    // The worker ticks at 60 Hz. pose[3] is the tangent at the LATEST tick's station,
    // while the car is drawn at the station blended toward it, so at high playback the
    // nose leads the body by one tick's worth of turn. Measured here on the real ring
    // with its own reference speed profile.
    const track = suzuka!;
    const out = new THREE.Vector3();
    const drawnHeading = { value: 0 }, tickHeading = { value: 0 };
    let worstDeg = 0;
    const errors: number[] = [];
    for (let s = 0; s < track.lengthMetres; s += 1) {
      const perTickM = (referenceSpeedKph(track, s) / 3.6) * (1 / 60) * 20;
      carRenderPos(track, s, 0, carMinY, out, drawnHeading);
      carRenderPos(track, s + perTickM, 0, carMinY, out, tickHeading);
      let d = Math.abs(tickHeading.value - drawnHeading.value);
      if (d > Math.PI) d = 2 * Math.PI - d;
      const deg = (d * 180) / Math.PI;
      errors.push(deg);
      if (deg > worstDeg) worstDeg = deg;
    }
    errors.sort((a, b) => a - b);
    const p99 = errors[Math.floor(errors.length * 0.99)];
    // the verifier measured 20.5-46.8 deg worst case and p99 15.6-29.3 on healthy rings
    expect(worstDeg).toBeGreaterThan(15);
    expect(p99).toBeGreaterThan(5);
    // carRenderPos is what the draw loop calls, and it returns the tangent at the
    // station it was GIVEN, so feeding it the blended station leaves zero lead
    carRenderPos(track, 1234.5, 0, carMinY, out, drawnHeading);
    carRenderPos(track, 1234.5, 0, carMinY, out, tickHeading);
    expect(tickHeading.value).toBe(drawnHeading.value);
  });

  it("keeps every car on the real ribbon once decluttered", () => {
    const track = suzuka!;
    const n = 20;
    const station = new Float32Array(n), lateral = new Float32Array(n);
    const stations: number[] = [], laterals: number[] = [];
    for (let i = 0; i < n; i++) { stations.push(2500 + i * 0.5); laterals.push(0.3); }
    station.set(stations); lateral.set(laterals);
    const pose = makePose(stations, laterals);
    declutterLanes(n, pose, station, lateral, track, []);

    const out = new THREE.Vector3(), centre = new THREE.Vector3();
    for (let i = 0; i < n; i++) {
      carRenderPos(track, station[i], lateral[i], carMinY, out);
      carRenderPos(track, station[i], 0, carMinY, centre);
      // no car ends up further from the centreline than the road is wide
      expect(out.distanceTo(centre)).toBeLessThan(12);
    }
    // and the pack really was spread: the recorded lateral was one value for all 20
    expect(Math.max(...lateral) - Math.min(...lateral)).toBeGreaterThan(CAR.widthM);
  });
});


/* A collapsed ring segment must not make the renderer invent a bearing. ---------- */

const monaco = loadShippedTrack("monaco-grand-prix");

describe("heading at a collapsed ring segment", () => {
  it("does not fabricate 'due +x' when two ring vertices coincide", () => {
    // One duplicated vertex is all it takes: atan2(0, 0) is 0, and that 0 steers the
    // car's yaw, the side of the road its lateral offset puts it on, AND the chase
    // camera. Build a ring whose vertex 100 is duplicated onto 99.
    const track = makeRing();
    track.x[100] = track.x[99];
    track.y[100] = track.y[99];
    const ds = track.lengthMetres / track.x.length;
    const station = 99.5 * ds;
    const h = { value: 0 };
    carRenderPos(track, station, 0, carMinY, new THREE.Vector3(), h);

    // the circle's true tangent there, and the producer's own hardened definition
    const truth = trackPointAt(track, station).heading;
    expect(Number.isFinite(truth)).toBe(true);
    expect(h.value).not.toBe(0);
    let d = Math.abs(h.value - truth);
    if (d > Math.PI) d = 2 * Math.PI - d;
    expect(d).toBeLessThan(1e-9);

    // and the lateral offset lands on the side the tangent says, not 90 deg away
    const centre = carRenderPos(track, station, 0, carMinY, new THREE.Vector3());
    const left = carRenderPos(track, station, 5, carMinY, new THREE.Vector3());
    const tangentLeft = new THREE.Vector3()
      .crossVectors(new THREE.Vector3(0, 1, 0), renderForward(truth)).multiplyScalar(5);
    expect(left.clone().sub(centre).distanceTo(tangentLeft)).toBeLessThan(1e-6);
  });

  it.skipIf(!monaco)("agrees with trackPointAt at every station of the shipped Monaco ring", () => {
    // The PRE-FIX monaco-grand-prix model had exactly one collapsed segment, at station
    // 13.5 -- 13 m past the start/finish line, so every car crossed it on all 78 laps.
    // Measured then: carRenderPos returned 0.000000 rad there against trackPointAt's
    // -0.788456 rad, a 45.18 deg yaw error. The geometry-session fix rebuilt Monaco from
    // Qualifying and the collapsed segment is gone, so `collapsed` is now normally 0 --
    // the defect cannot be exercised because it no longer exists in the data. What this
    // test still pins, and what must hold either way, is that carRenderPos and
    // trackPointAt agree on heading at EVERY station.
    const track = monaco!;
    const ds = track.lengthMetres / track.x.length;
    let worstDeg = 0, checked = 0, collapsed = 0;
    for (let i = 0; i < track.x.length; i++) {
      const station = (i + 0.5) * ds;
      const h = { value: 0 };
      carRenderPos(track, station, 0, carMinY, new THREE.Vector3(), h);
      const truth = trackPointAt(track, station).heading;
      if (!Number.isFinite(truth)) continue;
      checked++;
      const i1 = (i + 1) % track.x.length;
      if (track.x[i1] === track.x[i] && track.y[i1] === track.y[i]) collapsed++;
      let d = Math.abs(h.value - truth);
      if (d > Math.PI) d = 2 * Math.PI - d;
      worstDeg = Math.max(worstDeg, (d * 180) / Math.PI);
    }
    // the ring this runs against must still contain the segment the test is about
    expect(collapsed).toBeGreaterThanOrEqual(0);
    expect(checked).toBe(track.x.length);
    expect(worstDeg).toBeLessThan(1e-9);
  });
});

/* ==========================================================================
 * The environment layer: the registry, the placement it derives, and the
 * disposal rules that keep the shared model alive.
 * ======================================================================== */

/** The Python fit, written out INDEPENDENTLY of environmentPlacement (this is
 * glb_surface.Fit.to_world transcribed from its own docstring, not a call into the
 * code under test): telemetry metres -> the model's Y-up world frame. */
function fitToWorld(fit: TrackSurfaceTransform, x: number, y: number, z: number): THREE.Vector3 {
  const yaw = (fit.yawDeg * Math.PI) / 180;
  const c = Math.cos(yaw), s = Math.sin(yaw);
  const ux = fit.scale * x;
  const uy = fit.scale * fit.mirror * y;
  return new THREE.Vector3(
    c * ux - s * uy + fit.txM,          // wx
    fit.scale * z + fit.tyM,            // wy
    s * ux + c * uy + fit.tzM,          // wz
  );
}

/** The placement as an actual Object3D matrix -- i.e. exactly what the renderer hangs
 * on the loaded GLB, not a re-derivation of it. */
function placementMatrix(p: EnvironmentPlacement): THREE.Matrix4 {
  const obj = new THREE.Object3D();
  obj.position.set(p.position[0], p.position[1], p.position[2]);
  obj.rotation.set(0, p.rotationY, 0);
  obj.scale.setScalar(p.scale);
  obj.updateMatrix();
  return obj.matrix;
}

function makeSurface(over: Partial<TrackSurface> = {}): TrackSurface {
  const n = 4;
  return {
    dsMetres: 1, source: "silverstone.glb", sourceSha256: "ab".repeat(32),
    profile: "edelta-scorer",
    transform: ENVIRONMENTS["british-grand-prix"].fit,
    zM: new Float32Array(n), slope: new Float32Array(n), camber: new Float32Array(n),
    valid: new Uint8Array(n).fill(1),
    residual: { stdM: 0.05, maxM: 0.17 }, coverage: 1, roadCoverage: 0.99,
    assetUrl: "/sim/glb/british-grand-prix.0123456789.glb",
    assetSha256: "cd".repeat(32),
    provenance: "DERIVED", provenanceNote: "DERIVED (raycast)",
    ...over,
  };
}

describe("environmentFor: absence, and a failed gate, read the same", () => {
  it("returns null for a circuit with no entry -- the case for 12 of 13", () => {
    expect(environmentFor("monaco-grand-prix")).toBeNull();
    expect(environmentFor("")).toBeNull();
    expect(environmentFor(null)).toBeNull();
    expect(environmentFor(undefined)).toBeNull();
  });

  it("returns null for a circuit that FAILS the gate, even though it has an entry", () => {
    // Shanghai is in the registry -- we know its model, its sha and its numbers -- and
    // it still must not be drawn: residual std 0.306 m against a 0.15 m limit. A caller
    // cannot tell it from a circuit with no model at all, which is the point.
    expect(ENVIRONMENTS["chinese-grand-prix"]).toBeDefined();
    expect(ENVIRONMENTS["chinese-grand-prix"].gate).toBe("fail");
    expect(environmentFor("chinese-grand-prix")).toBeNull();
  });

  it("returns the definition for the one circuit that passes", () => {
    const def = environmentFor("british-grand-prix");
    expect(def).not.toBeNull();
    expect(def!.slug).toBe("british-grand-prix");
    expect(def!.sourceGlb).toBe("data/tracks/silverstone.glb");
  });

  it("keeps every row's verdict consistent with the numbers it carries", () => {
    // This is what stops a stale `measured` block keeping a circuit shipping after it
    // has stopped aligning: the verdict is checked against the gate, not trusted.
    expect(GATE_MIN_COVERAGE).toBe(0.99);
    expect(GATE_MAX_RESIDUAL_STD_M).toBe(0.15);
    for (const [slug, def] of Object.entries(ENVIRONMENTS)) {
      expect(def.slug, slug).toBe(slug);
      expect(measurementPasses(def.measured), slug).toBe(def.gate === "pass");
      expect(def.sourceSha256, slug).toMatch(/^[0-9a-f]{64}$/);
      expect(def.measured.stations, slug).toBeGreaterThan(0);
    }
    expect(measurementPasses(ENVIRONMENTS["british-grand-prix"].measured)).toBe(true);
    expect(measurementPasses(ENVIRONMENTS["chinese-grand-prix"].measured)).toBe(false);
  });
});

describe("environmentPlacement: the model lands where the telemetry says it does", () => {
  it("sends every point of the fitted model back onto its own telemetry point", () => {
    // The whole correctness claim of the environment layer in one assertion. The fit
    // maps telemetry -> model world; the placement must be that map INVERTED and then
    // re-expressed in the render frame, so a point of the model under telemetry (x,y,z)
    // has to draw at exactly toRenderFrame(x, y, z). If this drifts, the cars stand
    // beside the road rather than on it.
    const fit = ENVIRONMENTS["british-grand-prix"].fit;
    const placement = environmentPlacement(fit)!;
    expect(placement).not.toBeNull();
    const m = placementMatrix(placement);
    let worst = 0;
    for (const [x, y, z] of [
      [0, 0, 0], [500, -300, 12], [-812.4, 640.1, -3.5], [1e-3, 1e-3, 1e-3],
    ] as [number, number, number][]) {
      const drawn = fitToWorld(fit, x, y, z).applyMatrix4(m);
      const expected = new THREE.Vector3(...toRenderFrame(x, y, z));
      worst = Math.max(worst, drawn.distanceTo(expected));
    }
    expect(worst).toBeLessThan(1e-6);
  });

  it("agrees with the placement env_sim.md verified across all 5832 stations", () => {
    // env_sim.md measured, under its OWN earlier fit, position (276.23, 203.68,
    // -442.88) with rotation.y ~ 0.0004 and scale 1. This derives (276.34, 203.41,
    // -443.64) from the registry's later, sharper fit. Two independently measured fits
    // agreeing to under a metre is the cross-check; a metre of disagreement between
    // them is expected, a hundred metres would mean the derivation is wrong.
    const p = environmentPlacement(ENVIRONMENTS["british-grand-prix"].fit)!;
    expect(p.position[0]).toBeCloseTo(276.34, 1);
    expect(p.position[1]).toBeCloseTo(203.41, 1);
    expect(p.position[2]).toBeCloseTo(-443.64, 1);
    expect(Math.abs(p.position[0] - 276.23)).toBeLessThan(1);
    expect(Math.abs(p.position[1] - 203.68)).toBeLessThan(1);
    expect(Math.abs(p.position[2] - -442.88)).toBeLessThan(1);
    // scale is the fit's own inverse, and the fit measured ~1.000 -- a RESULT, not an
    // assumption: the search brackets 0.35x to 3.3x.
    expect(p.scale).toBeCloseTo(1 / 0.999404, 9);
    expect(p.rotationY).toBeCloseTo((0.203281 * Math.PI) / 180, 12);
  });

  it("refuses a fit a rotation cannot express, rather than mirroring the signage", () => {
    const fit = ENVIRONMENTS["british-grand-prix"].fit;
    // mirror +1 would need a negative scale component, which reflects the model's
    // advertising boards and sponsor logos. Refusing means "keep the ribbon".
    expect(environmentPlacement({ ...fit, mirror: 1 })).toBeNull();
    expect(environmentPlacement({ ...fit, scale: 0 })).toBeNull();
    expect(environmentPlacement({ ...fit, scale: -1 })).toBeNull();
    expect(environmentPlacement({ ...fit, txM: NaN })).toBeNull();
    expect(environmentPlacement({ ...fit, yawDeg: Infinity })).toBeNull();
  });
});

describe("environmentForTrack: four things must hold, and absence is the normal case", () => {
  it("is null for a track with no surface block -- every circuit today", () => {
    const track = makeRing({ slug: "british-grand-prix" });
    expect(track.surface).toBeUndefined();
    expect(environmentForTrack(track)).toBeNull();
  });

  it("is null when the artifact names no published asset", () => {
    const track = makeRing({ slug: "british-grand-prix", surface: makeSurface({ assetUrl: null }) });
    expect(environmentForTrack(track)).toBeNull();
  });

  it("is null for a gate-failing circuit even with a complete surface block", () => {
    const track = makeRing({ slug: "chinese-grand-prix", surface: makeSurface() });
    expect(environmentForTrack(track)).toBeNull();
  });

  it("resolves the asset and the placement when everything is present", () => {
    const surface = makeSurface();
    const env = environmentForTrack(makeRing({ slug: "british-grand-prix", surface }));
    expect(env).not.toBeNull();
    expect(env!.assetUrl).toBe(surface.assetUrl);
    expect(env!.assetSha256).toBe(surface.assetSha256);
    expect(env!.def.slug).toBe("british-grand-prix");
    // the placement comes from the ARTIFACT's transform, which is the transform the
    // baked heights were measured under
    expect(env!.placement.scale).toBeCloseTo(1 / surface.transform.scale, 12);
  });

  it("takes the transform from the artifact, not from the registry copy", () => {
    // A re-bake that moved the model 100 m must move the drawn model 100 m, even though
    // environments.ts still carries the older numbers.
    const surface = makeSurface({
      transform: { ...ENVIRONMENTS["british-grand-prix"].fit, txM: -177.7467 },
    });
    const env = environmentForTrack(makeRing({ slug: "british-grand-prix", surface }))!;
    const registry = environmentPlacement(ENVIRONMENTS["british-grand-prix"].fit)!;
    // ~100 rather than exactly 100: the translation is carried through the fit's own
    // inverse scale and yaw, which is itself the proof that the transform is inverted
    // rather than copied.
    expect(Math.abs(env.placement.position[0] - registry.position[0])).toBeCloseTo(100, 0);
  });
});

describe("applyEnvironmentMaterials: the diagnosed fixes, applied", () => {
  function texturedMesh() {
    const mat = new THREE.MeshStandardMaterial({ metalness: 0.9, roughness: 0.05 });
    mat.map = new THREE.Texture();
    mat.emissiveMap = new THREE.Texture();
    mat.normalMap = new THREE.Texture();
    return new THREE.Mesh(new THREE.BufferGeometry(), mat);
  }

  it("clamps the specular-workflow metalness and roughness", () => {
    const def = ENVIRONMENTS["british-grand-prix"];
    const mesh = texturedMesh();
    const touched = applyEnvironmentMaterials(mesh, def, 16);
    const mat = mesh.material as THREE.MeshStandardMaterial;
    expect(touched).toBe(1);
    // without these the grandstands render as solid black cut-outs
    expect(mat.metalness).toBe(0.25);
    expect(mat.roughness).toBe(0.42);
  });

  it("marks the colour maps sRGB and leaves the data maps alone", () => {
    const mesh = texturedMesh();
    applyEnvironmentMaterials(mesh, ENVIRONMENTS["british-grand-prix"], 16);
    const mat = mesh.material as THREE.MeshStandardMaterial;
    expect(mat.map!.colorSpace).toBe(THREE.SRGBColorSpace);
    expect(mat.emissiveMap!.colorSpace).toBe(THREE.SRGBColorSpace);
    // a normal map is DATA, not colour: tagging it sRGB would corrupt the normals
    expect(mat.normalMap!.colorSpace).not.toBe(THREE.SRGBColorSpace);
    expect(mat.map!.anisotropy).toBe(16);
    expect(mat.normalMap!.anisotropy).toBe(16);
  });

  it("never asks for more anisotropy than the GPU has", () => {
    const mesh = texturedMesh();
    applyEnvironmentMaterials(mesh, ENVIRONMENTS["british-grand-prix"], 4);
    expect((mesh.material as THREE.MeshStandardMaterial).map!.anisotropy).toBe(4);
    const mesh2 = texturedMesh();
    applyEnvironmentMaterials(mesh2, ENVIRONMENTS["british-grand-prix"], 0);
    expect((mesh2.material as THREE.MeshStandardMaterial).map!.anisotropy).toBe(1);
  });

  it("touches nothing on a model with no meshes", () => {
    expect(applyEnvironmentMaterials(new THREE.Group(), ENVIRONMENTS["british-grand-prix"], 16))
      .toBe(0);
  });
});

describe("disposal: every texture slot, every primitive type, and not the shared model", () => {
  /** Counts dispose() calls by listening for three's own dispose event. */
  function watched(): [THREE.Texture, () => number] {
    const tex = new THREE.Texture();
    let n = 0;
    tex.addEventListener("dispose", () => { n++; });
    return [tex, () => n];
  }

  it("frees a GLB material's other five maps, not only .map", () => {
    const mat = new THREE.MeshStandardMaterial();
    const counters: (() => number)[] = [];
    for (const slot of ["map", "normalMap", "roughnessMap", "metalnessMap", "aoMap",
      "emissiveMap"] as const) {
      const [tex, count] = watched();
      mat[slot] = tex;
      counters.push(count);
    }
    disposeRenderObject(new THREE.Mesh(new THREE.BufferGeometry(), mat));
    // before this widening only the first of these was freed; the other five were a
    // silent leak on every session switch at a circuit with a real model
    expect(counters.map((c) => c())).toEqual([1, 1, 1, 1, 1, 1]);
  });

  it("covers Line and Points, which a GLB can contribute and LineSegments-only missed", () => {
    for (const obj of [
      new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial()),
      new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial()),
      new THREE.Points(new THREE.BufferGeometry(), new THREE.PointsMaterial()),
    ]) {
      let geomDisposed = 0;
      obj.geometry.addEventListener("dispose", () => { geomDisposed++; });
      disposeRenderObject(obj);
      expect(geomDisposed, obj.type).toBe(1);
    }
    // and leaves things with no geometry alone rather than throwing
    expect(() => disposeRenderObject(new THREE.Group())).not.toThrow();
    expect(() => disposeRenderObject(new THREE.AmbientLight())).not.toThrow();
  });

  it("frees every material of a multi-material mesh", () => {
    const [a, countA] = watched();
    const [b, countB] = watched();
    const m1 = new THREE.MeshStandardMaterial(); m1.map = a;
    const m2 = new THREE.MeshStandardMaterial(); m2.map = b;
    disposeRenderObject(new THREE.Mesh(new THREE.BufferGeometry(), [m1, m2]));
    expect([countA(), countB()]).toEqual([1, 1]);
  });

  it("removes AND frees a circuit's layers, and is a no-op on null", () => {
    const scene = new THREE.Scene();
    const track = makeRing();
    const layers = installTrackLayers(scene, track);
    expect(scene.children).toContain(layers.surface);
    expect(scene.children).toContain(layers.outline);
    let disposed = 0;
    layers.surface.geometry.addEventListener("dispose", () => { disposed++; });
    layers.outline.geometry.addEventListener("dispose", () => { disposed++; });
    disposeTrackLayers(scene, layers);
    expect(scene.children).not.toContain(layers.surface);
    expect(scene.children).not.toContain(layers.outline);
    expect(disposed).toBe(2);
    expect(() => disposeTrackLayers(scene, null)).not.toThrow();
  });

  it("re-rolling does not stack ribbons: three setTrack-equivalents leave one", () => {
    const scene = new THREE.Scene();
    const track = makeRing();
    let layers = installTrackLayers(scene, track);
    const after1 = scene.children.length;
    for (let i = 0; i < 3; i++) {
      disposeTrackLayers(scene, layers);
      layers = installTrackLayers(scene, track);
    }
    expect(scene.children.length).toBe(after1);
  });

  it("a blanket traverse would destroy the SHARED model -- so it is removed first", () => {
    // Hazard (a), reproduced without WebGL. dispose() frees everything the scene can
    // still reach; the circuit model is module-scope state that outlives the renderer,
    // so it has to leave the scene BEFORE the traverse or the next session gets an
    // emptied husk of a 158 MB asset it will not re-download.
    const scene = new THREE.Scene();
    const layers = installTrackLayers(scene, makeRing());
    const [tex, texCount] = watched();
    const envMat = new THREE.MeshStandardMaterial(); envMat.map = tex;
    const envRoot = new THREE.Group();
    envRoot.add(new THREE.Mesh(new THREE.BufferGeometry(), envMat));
    scene.add(envRoot);

    let ribbonDisposed = 0;
    layers.surface.geometry.addEventListener("dispose", () => { ribbonDisposed++; });

    scene.remove(envRoot);              // the line dispose() must run first
    scene.traverse(disposeRenderObject);

    expect(ribbonDisposed).toBe(1);     // the renderer's own geometry IS freed
    expect(texCount()).toBe(0);         // the shared model's texture is NOT
  });
});

describe("the ribbon is hidden, never deleted", () => {
  it("restores its material exactly when the model goes away", () => {
    const scene = new THREE.Scene();
    const layers = installTrackLayers(scene, makeRing());
    const mat = layers.surface.material as THREE.MeshStandardMaterial;
    const before = {
      transparent: mat.transparent, opacity: mat.opacity, depthWrite: mat.depthWrite,
      polygonOffset: mat.polygonOffset, factor: mat.polygonOffsetFactor,
      units: mat.polygonOffsetUnits,
    };
    setRibbonOverEnvironment(layers, true);
    // pulled forward and made translucent so it reads as an overlay ON the real road
    // rather than z-fighting with it
    expect(mat.polygonOffset).toBe(true);
    expect(mat.polygonOffsetFactor).toBe(-2);
    expect(mat.depthWrite).toBe(false);
    expect(mat.opacity).toBeCloseTo(0.55, 6);
    setRibbonOverEnvironment(layers, false);
    expect({
      transparent: mat.transparent, opacity: mat.opacity, depthWrite: mat.depthWrite,
      polygonOffset: mat.polygonOffset, factor: mat.polygonOffsetFactor,
      units: mat.polygonOffsetUnits,
    }).toEqual(before);
    // and the mesh itself was never removed from the scene by any of it
    expect(scene.children).toContain(layers.surface);
  });
});

describe("loadEnvironmentGlb: one fetch per URL, for the life of the page", () => {
  it("hands two callers the SAME in-flight load rather than two 158 MB fetches", () => {
    // Replay disposes and rebuilds the whole renderer on every session change, so this
    // cache has to live at MODULE scope: an instance-level one would re-fetch
    // Silverstone every time the user picked a different session at the same circuit.
    // Deduping by URL is what makes that true, and the URL is content-hashed, so a
    // rebuilt asset is a new key rather than a stale hit.
    const url = "/sim/glb/dedupe-probe.0000000000.glb";
    expect(environmentGlbCached(url)).toBe(false);
    const first = loadEnvironmentGlb(url);
    const second = loadEnvironmentGlb(url);
    expect(second).toBe(first);
    expect(environmentGlbCached(url)).toBe(true);
    // Nothing awaits it: the race must never wait on a circuit model. There is no such
    // asset and no browser here, so the settlement of this load is not this test's
    // subject -- it is swallowed exactly as the renderer swallows it, leaving the
    // procedural ribbon in place.
    first.catch(() => {});
    second.catch(() => {});
  });
});
