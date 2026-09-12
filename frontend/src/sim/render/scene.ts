"use client";

import * as THREE from "three";
import type { TrackModel } from "../contract/types";
import { CAR } from "@/components/loader/physics/constants";
import { HAAS } from "@/lib/palette";
import { halfWidthAt, surfaceAt } from "../data/manifest";
import { POSE_FLOATS_PER_CAR, POSE_STATUS } from "../worker/protocol";
import {
  carInstanceY, FOCUS_OUTLINE_SCALE, focusGhostY, PRESENTATION_SCALE,
} from "./presentation";
import { buildF1CarGeometry } from "./carGeometry";
import { buildDriverLabels, type DriverLabels } from "./driverLabels";
import {
  applyEnvironmentMaterials, environmentForTrack, sameEnvironment,
  type ResolvedEnvironment,
} from "./environments";
import {
  applyShadowPriceOverlay, buildPitLaneMesh, buildTrackMesh, buildTrackOutline,
  carOrientation, fromRenderFrame, renderForward, toRenderFrame,
} from "./trackMesh";

export type CameraMode = "broadcast" | "orbit" | "onboard" | "helicopter";

export type GpuPreference = "high-performance" | "low-power" | "default";

export interface GpuInfo {
  renderer: string;
  vendor: string;
  isSoftware: boolean;
  isIntegrated: boolean;
  maxTextureSize: number;
}

export interface PerfStats {
  fps: number;
  frameMs: number;
  qualityTier: 0 | 1 | 2;
  /** Panel refresh measured from rAF; null until enough frames have been timed. */
  refreshHz: number | null;
  /** Cars not drawn this frame because their position was withdrawn upstream. A car
   * that vanishes should be a number someone can read, not a mystery. */
  carsHidden: number;
}

const SOFTWARE_MARKERS = ["swiftshader", "llvmpipe", "software", "microsoft basic render"];
/** Integrated-GPU families. Used only to tell the user the discrete GPU did not get
 * picked; it is never used to change behaviour. */
const INTEGRATED_MARKERS = ["intel", "uhd graphics", "iris", "radeon(tm) graphics", "vega 8"];

/** WEBGL_debug_renderer_info exposes the actual GPU string behind ANGLE, which is
 * the only reliable way from JS to tell "rendering on the GPU" from "rendering in a
 * software fallback" -- the extension can itself be unavailable (locked down by some
 * browsers/policies), in which case this reports "unknown" rather than guessing. */
export function readGpuInfo(gl: WebGLRenderingContext | WebGL2RenderingContext): GpuInfo {
  const ext = gl.getExtension("WEBGL_debug_renderer_info");
  const renderer = ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : "unknown";
  const vendor = ext ? String(gl.getParameter(ext.UNMASKED_VENDOR_WEBGL)) : "unknown";
  const lower = renderer.toLowerCase();
  return {
    renderer, vendor,
    isSoftware: SOFTWARE_MARKERS.some((m) => lower.includes(m)),
    isIntegrated: INTEGRATED_MARKERS.some((m) => lower.includes(m)),
    maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE) as number,
  };
}

interface PoseFrame {
  floats: Float32Array;
  sessionTime: number;
  carCount: number;
  arrivedMs: number;
}

interface PoseSource {
  getLatestPose(): PoseFrame | null;
  getPrevPose(): PoseFrame | null;
}

const LOW_END = (w: number, h: number) => w * h < 500_000 || (navigator.hardwareConcurrency ?? 4) <= 4;
/** Fallback "this frame was slow" threshold, used only until the real display
 * cadence has been measured. A fixed value cannot serve every panel: 34 ms is two
 * missed frames at 60 Hz but nearly six at 165 Hz, so a 165 Hz display could sit at
 * 50 fps and never trigger a single quality step. The threshold becomes a multiple of
 * the MEASURED refresh period instead (see measureRefresh). */
const LONG_FRAME_FALLBACK_MS = 34;
/** How far into lap 1 the field still counts as "starting" for lane fanning. */
const START_FAN_LAP_FRACTION = 0.15;
/** How many refresh periods a frame may take before it counts as slow. */
const LONG_FRAME_PERIODS = 2.5;
/** rAF intervals sampled before trusting the measurement. */
const REFRESH_SAMPLES = 40;

/** Driver tags hold this on-screen height in device pixels. The constant-pixel formula
 * does the real work; the clamps are only safety rails against a degenerate camera
 * distance. Note which rail bites where: a NEAR car needs a SMALL world height, so a
 * large minimum is what inflates close-up tags (0.9 m floored them at ~70 px). A FAR car
 * needs a large one, so the maximum is what bounds a tag on the far side of the
 * circuit. */
const LABEL_PX = 22;
const LABEL_MIN_WORLD_M = 0.2;
const LABEL_MAX_WORLD_M = 8;

/** How much of a car's own colour survives when the field is dimmed (the rest is
 * blended toward the background). 0.4 keeps the team colour identifiable while the
 * focused car clearly reads as the bright one. */
const DIM_MIX = 0.4;

/** Chase-camera rig, metres: how far behind the car the eye sits, how far above it,
 * and how far ahead of the car it looks. Broadcast looks AT the car; onboard looks
 * down the road. Every direction is taken from renderForward, never from a
 * hand-written sin/cos triple -- see chaseCameraPose.
 *
 * `headingRelative` says whether the rig turns with the car. Both ground rigs do: the
 * eye belongs behind the NOSE, wherever the nose is pointing. The helicopter's map view
 * deliberately does not, so the circuit does not spin under the viewer every time the
 * car turns a corner.
 *
 * The helicopter's 0.01 m is NOT a rig dimension. It is the nudge that keeps the view
 * matrix from collapsing: a camera looking exactly along its own up axis has no basis.
 * It used to be applied as a raw +0.01 on world Z after the rig was built; expressing it
 * as "0.01 m behind the car" puts it through the same renderForward path as everything
 * else and leaves the measured 45 m height untouched.
 */
const CHASE_RIG = {
  broadcast: { backM: 15, upM: 6, aheadM: 0, headingRelative: true },
  onboard: { backM: 9, upM: 3.2, aheadM: 20, headingRelative: true },
  helicopter: { backM: 0.01, upM: 45, aheadM: 0, headingRelative: false },
} as const;

/** Every camera mode that hangs off a car. Orbit is the odd one out: it looks at the
 * circuit, so it has no focused car to hang off and takes the free-camera path. */
export type ChaseMode = Exclude<CameraMode, "orbit">;

export interface ChaseRigGeometry {
  /** Distance from the anchor to the eye, metres. */
  distM: number;
  /**
   * Height of the eye above the horizontal, RADIANS FROM THE GROUND PLANE.
   *
   * NOT the same quantity as `orbitPitch`, which orbitEye measures from the UP axis.
   * The two are complementary, and mixing them silently lays the camera on its side, so
   * they are clamped by different functions (clampRigElevation here, MIN/MAX_ORBIT_POLAR
   * there) and are never assigned to one another.
   */
  elevation: number;
  aheadM: number;
  headingRelative: boolean;
}

function rigGeometryOf(mode: ChaseMode): ChaseRigGeometry {
  const r = CHASE_RIG[mode];
  return {
    distM: Math.hypot(r.backM, r.upM),
    elevation: Math.atan2(r.upM, r.backM),
    aheadM: r.aheadM,
    headingRelative: r.headingRelative,
  };
}

/**
 * The same three rigs, in the polar form the camera is actually driven in.
 *
 * This is a CHANGE OF COORDINATES, not a change of rig: dist = hypot(back, up) and
 * elevation = atan2(up, back) put the eye exactly where the old back/up arithmetic put
 * it (the test pins all three modes against that arithmetic, to 1e-9 m). Polar is the
 * form a user can be handed a mouse in -- yaw and elevation swing the eye around the
 * car, zoom scales the distance -- and the neutral rig is still the shot the camera has
 * always taken, so nothing had to be re-tuned to make the camera drivable.
 */
const CHASE_GEOMETRY: Record<ChaseMode, ChaseRigGeometry> = {
  broadcast: rigGeometryOf("broadcast"),
  onboard: rigGeometryOf("onboard"),
  helicopter: rigGeometryOf("helicopter"),
};

/** The rig a chase mode is built on, in polar form. Read-only: it is the shipped shot. */
export function chaseRigGeometry(mode: ChaseMode): Readonly<ChaseRigGeometry> {
  return CHASE_GEOMETRY[mode];
}

/** Render-frame up. One shared vector: the camera maths needs it several times a frame
 * and nothing ever writes to it. */
const RENDER_UP = new THREE.Vector3(0, 1, 0);

/** How low and how high the user may tilt a chase rig, radians above the ground plane.
 * The floor keeps the eye out of the scenery; the ceiling stops one notch short of
 * straight down, where the view direction is parallel to the camera's own up axis and
 * the view matrix has no solution. */
export const MIN_RIG_ELEVATION = 0.03;
export const MAX_RIG_ELEVATION = Math.PI / 2 - 1e-4;
/** How far in and out a chase rig may be pushed, as a multiple of its own distance. */
export const MIN_RIG_ZOOM = 0.2;
export const MAX_RIG_ZOOM = 8;
/** How far the rig may be slid off the car it is following, metres. A cap, not a
 * behaviour: without it a leant-on key flies the camera to another county while the
 * "locked" badge still claims it is following someone. */
export const MAX_RIG_PAN_M = 500;

export function clampRigElevation(elevation: number): number {
  return Math.max(MIN_RIG_ELEVATION, Math.min(MAX_RIG_ELEVATION, elevation));
}

/** Polar-angle limits for the free/orbit camera, measured from the UP axis (see
 * ChaseRigGeometry.elevation for why the two must not be interchanged). Both were
 * already in the pointer handler as bare numbers. */
export const MIN_ORBIT_POLAR = 0.05;
export const MAX_ORBIT_POLAR = 1.5;
export const MIN_ORBIT_DIST_M = 12;
export const MAX_ORBIT_DIST_M = 8000;

/**
 * The user's nudge to a chase camera, held PER MODE so a helicopter flown out over the
 * infield does not also drag the onboard shot with it.
 *
 * It is an OFFSET from the mode's own rig, never a replacement for it, which is what
 * keeps "locked" meaning locked: the camera still follows the focused car around the
 * lap, it just follows it from where the user put it. An all-zero rig with zoom 1 is
 * exactly the shipped shot.
 *
 * yaw:   swings the eye about the anchor, radians. 0 = directly behind the car (or,
 *        for the world-fixed helicopter, along render-frame +X).
 * pitch: ADDED to the mode's base elevation. The TOTAL is clamped, not this.
 * zoom:  multiplies the mode's base distance.
 * pan:   slides the whole rig -- eye AND look-at -- in the rig's own basis
 *        (x = right of travel, y = world up, z = along travel). Held in the rig basis
 *        rather than in world metres so it turns with the car instead of sliding off
 *        the road at the next corner.
 */
export interface CameraRig {
  yaw: number;
  pitch: number;
  zoom: number;
  pan: THREE.Vector3;
}

export function makeCameraRig(): CameraRig {
  return { yaw: 0, pitch: 0, zoom: 1, pan: new THREE.Vector3() };
}

/** Puts a rig back to the shipped shot, in place. */
export function resetRig(rig: CameraRig): CameraRig {
  rig.yaw = 0;
  rig.pitch = 0;
  rig.zoom = 1;
  rig.pan.set(0, 0, 0);
  return rig;
}

/** Whether the user has moved this rig at all, so a HUD can offer a reset only when
 * there is something to reset. */
export function rigIsNeutral(rig: CameraRig): boolean {
  return rig.yaw === 0 && rig.pitch === 0 && rig.zoom === 1 && rig.pan.lengthSq() === 0;
}

/** The untouched rig, shared and frozen: chaseCameraPose only ever reads it, and a
 * module is strict-mode, so an accidental write throws here instead of quietly
 * re-aiming every camera in the app. */
const NEUTRAL_RIG: CameraRig = Object.freeze(makeCameraRig()) as CameraRig;

/** Input rates, all previously bare numbers in the pointer handlers. A drag is an
 * ANGLE per pixel; a pan and a zoom are a fraction of the current view distance per
 * pixel, so the same gesture covers the same share of the screen whether the camera is
 * 9 m off a car or 2 km above the circuit. */
const LOOK_RAD_PER_PX = 0.005;
const PAN_M_PER_PX_PER_M = 0.0016;
const ZOOM_PER_WHEEL_UNIT = 0.0012;

/** How fast the camera closes on its goal, in e-foldings per second (see dampFactor).
 * 14 Hz settles ~75% of the remaining distance in 0.1 s: smooth, not laggy. */
const CAMERA_DAMP_HZ = 14;

/** Past this much error the camera is placed rather than eased -- see updateCamera for
 * the two situations (first frame, and 20x playback under a slow frame) where a bounded
 * per-frame lerp was measured to diverge instead of converge. */
const SNAP_DISTANCE_M = 60;

/**
 * Ring tangent across the segment [i0, i1], widening the chord SYMMETRICALLY about the
 * same midpoint for as long as the chord has no length.
 *
 * `Math.atan2(0, 0)` is 0 -- "due +x" in the track frame -- which is a FABRICATED
 * bearing, and this function's result steers both the car's yaw and the lateral offset
 * that decides which side of the road it is drawn on, as well as the chase camera.
 * Measured on the shipped models: exactly one collapsed segment at
 * hungarian-grand-prix (station 4358.5) and one at monaco-grand-prix (station 13.5 --
 * 13 m past the start/finish line, so crossed by every car on every one of its 78
 * laps), where the fabricated 0 rad is 26.55 deg and 45.18 deg away from the real
 * tangent. The other eleven rings have no collapsed segment and are bit-identical
 * under this function.
 *
 * This is the same rule manifest.ts's `trackPointAt` already applies, so the heading
 * the renderer draws and the `headingRad` the producer publishes are once again the
 * same quantity -- which carRenderPos's comment has always claimed and which stopped
 * being true when trackPointAt was hardened and this copy was not.
 */
function ringHeading(track: Pick<TrackModel, "x" | "y">, i0: number, i1: number): number {
  const n = track.x.length;
  let a = i0, b = i1;
  for (let k = 0; k < 8; k++) {
    const dx = track.x[b] - track.x[a];
    const dy = track.y[b] - track.y[a];
    if (dx !== 0 || dy !== 0) return Math.atan2(dy, dx);
    a = (a - 1 + n) % n;
    b = (b + 1) % n;
  }
  // Eight collapsed vertices in a row: no bearing exists. Unreachable on all 13
  // shipped rings and on all 13 post-campaign rings (measured minimum vertex step
  // 0.374 m, at Barcelona). Kept only because a NaN heading propagates into the
  // instance matrix and deletes the car from the scene without saying why; the ring
  // itself is what has to be repaired if this is ever hit.
  return 0;
}

/**
 * Where a car at (stationM, lateralM) is DRAWN, in render-frame world coordinates.
 *
 * ONE function for both the draw loop and the camera. Before this existed the two
 * carried separate copies of the same centreline interpolation and they had drifted
 * apart: the camera's copy left out PRESENTATION_SCALE and the ride-height lift, so it
 * aimed at a point on the road rather than at the car standing on it, and the two
 * would have parted company entirely the moment PRESENTATION_SCALE stopped being 1.
 *
 * Heading is the ring tangent at this station, which is the producer's own definition
 * of headingRad (replay/timeline.ts and engine/generatedTimeline.ts both set it to
 * trackPointAt(track, stationM).heading). Taking it here rather than from pose[3]
 * means it is the tangent at the BLENDED station -- the station the car is actually
 * drawn at -- so the nose can never point somewhere the car is not. Nothing is derived
 * here that Python does not already own.
 *
 * Allocates nothing beyond toRenderFrame's tuple: the caller owns `out`.
 */
export function carRenderPos(
  track: TrackModel, stationM: number, lateralM: number, geometryMinY: number,
  out: THREE.Vector3, headingOut?: { value: number },
): THREE.Vector3 {
  const n = track.x.length;
  const ds = track.lengthMetres / n;
  const s = ((stationM % track.lengthMetres) + track.lengthMetres) % track.lengthMetres;
  const f = s / ds;
  const i0 = Math.floor(f) % n;
  const i1 = (i0 + 1) % n;
  const frac = f - Math.floor(f);
  const cx = track.x[i0] + (track.x[i1] - track.x[i0]) * frac;
  const cy = track.y[i0] + (track.y[i1] - track.y[i0]) * frac;
  const cz = track.z[i0] + (track.z[i1] - track.z[i0]) * frac;
  const heading = ringHeading(track, i0, i1);
  const nx = -Math.sin(heading), ny = Math.cos(heading);
  // lateral is scaled with the road (presentation.ts), so a car sitting halfway to the
  // kerb in the data still sits halfway to the kerb on the widened ribbon
  const lat = lateralM * PRESENTATION_SCALE;
  const [rx, ry, rz] = toRenderFrame(cx + nx * lat, cy + ny * lat, cz);
  if (headingOut) headingOut.value = heading;
  // The geometry's origin is NOT its lowest point (the wheels hang below it), so
  // placing the origin on the road buries them. carInstanceY lifts by the geometry's
  // own measured extent plus a ride height, so the contact patch sits on the ribbon.
  // The telemetry point itself is the car's reference position, treated as its centre
  // in plan view.
  return out.set(rx, carInstanceY(ry, geometryMinY), rz);
}

/* Scratch for the per-frame camera maths. Module-scope and reused, the same rule
 * carGeometry/trackMesh already follow (ORIENT_SCRATCH): chaseCameraPose runs once per
 * frame and must allocate nothing. */
const RIG_RIGHT_SCRATCH = new THREE.Vector3();
const RIG_ANCHOR_SCRATCH = new THREE.Vector3();
const RIG_VIEW_SCRATCH = new THREE.Vector3();

/**
 * Eye and look-at for a camera following a car at `focusPos` with track-frame heading
 * `heading`, as adjusted by the user's `rig`. `focusPos` is the position the car was
 * actually DRAWN at, which is what makes the camera centre the car the viewer can see.
 *
 * The rig is polar about the car: yaw swings the eye around it, elevation raises it,
 * zoom pushes in and out, and pan slides the whole thing -- eye and look-at together --
 * off the car without letting go of it. With a neutral rig this reproduces the shipped
 * shot for all three modes to within floating point.
 *
 * EVERY VECTOR COMES FROM renderForward, the one place the track-frame -> render-frame
 * convention lives. Hand-written sin/cos triples here were what made flipping
 * toRenderFrame on its own point the cameras backwards. The world-fixed helicopter is
 * no exception: renderForward(0) IS the render-frame +X axis, so its basis comes from
 * the same function rather than from a second, hand-written one.
 */
export function chaseCameraPose(
  mode: ChaseMode,
  focusPos: THREE.Vector3, heading: number,
  eye: THREE.Vector3, lookAt: THREE.Vector3,
  fwd: THREE.Vector3 = new THREE.Vector3(),
  rig: CameraRig = NEUTRAL_RIG,
): void {
  const geom = CHASE_GEOMETRY[mode];
  renderForward(geom.headingRelative ? heading : 0, fwd);
  // forward x up = right of travel. Derived, not retyped: the cross product carries the
  // handedness of whatever renderForward just produced.
  const right = RIG_RIGHT_SCRATCH.copy(fwd).cross(RENDER_UP);
  const elevation = clampRigElevation(geom.elevation + rig.pitch);
  const cy = Math.cos(rig.yaw), sy = Math.sin(rig.yaw);
  const ce = Math.cos(elevation), se = Math.sin(elevation);

  // The rig hangs off the car, displaced by the user's pan. Eye and look-at take the
  // SAME displacement, so panning slides the whole rig rather than swinging the camera
  // off its subject. With a neutral rig every term is exactly zero, so the anchor is
  // bit-for-bit the drawn position.
  const anchor = RIG_ANCHOR_SCRATCH.copy(focusPos)
    .addScaledVector(right, rig.pan.x)
    .addScaledVector(RENDER_UP, rig.pan.y)
    .addScaledVector(fwd, rig.pan.z);

  const dist = geom.distM * rig.zoom;
  eye.copy(anchor)
    .addScaledVector(fwd, -dist * ce * cy)
    .addScaledVector(right, dist * ce * sy)
    .addScaledVector(RENDER_UP, dist * se);

  lookAt.copy(anchor);
  if (geom.aheadM !== 0) {
    // onboard looks down the road, and the road it looks down turns with the user's
    // yaw, so a mouse-look really does look where it is aimed instead of the eye
    // sliding sideways while the view stays pinned on the apex ahead.
    const viewFwd = RIG_VIEW_SCRATCH.copy(fwd).multiplyScalar(cy).addScaledVector(right, -sy);
    lookAt.addScaledVector(viewFwd, geom.aheadM);
  }
}

/**
 * A movement the user asked for IN THE VIEW -- (right, up, forward) metres as they read
 * on screen -- expressed in the rig's own basis, which is where the pan offset lives.
 *
 * The two frames differ by exactly the rig yaw about the up axis, so this is one 2-D
 * rotation and not a second camera basis to keep in step with the first. At yaw 0 it is
 * the identity, which is why a camera that has never been swung pans exactly along the
 * car's own axes.
 */
export function viewToRigPan(
  yaw: number, right: number, up: number, forward: number, out: THREE.Vector3,
): THREE.Vector3 {
  const c = Math.cos(yaw), s = Math.sin(yaw);
  return out.set(right * c - forward * s, up, right * s + forward * c);
}

/**
 * Eye position for an orbit camera: a point on a sphere of radius `dist` about
 * `centre`, at azimuth `yaw` and polar angle `pitch` from the render up axis.
 */
export function orbitEye(
  centre: THREE.Vector3, yaw: number, pitch: number, dist: number, out: THREE.Vector3,
): THREE.Vector3 {
  const r = dist * Math.sin(pitch);
  return out.set(
    centre.x + r * Math.cos(yaw),
    centre.y + dist * Math.cos(pitch),
    centre.z + r * Math.sin(yaw),
  );
}

/**
 * Screen-aligned ground basis for the orbit camera, so a drag moves the world with the
 * pointer: `right` is the camera's own +X (screen right) and `fwd` its own +Z (back
 * toward the viewer), both flattened onto the ground plane.
 *
 * This is deliberately NOT renderForward, and the two must not be merged. renderForward
 * maps a TRACK-frame heading through toRenderFrame and so carries the [x, z, -y]
 * convention with it: renderForward(a) = (cos a, 0, -sin a). orbitYaw is a free camera
 * parameter that is already expressed in render space and needs (cos a, 0, +sin a).
 * Routing the pan through renderForward after the handedness fix would have inverted
 * the vertical drag. The test pins both vectors against a real camera's view matrix.
 */
export function orbitPanBasis(yaw: number, right: THREE.Vector3, fwd: THREE.Vector3): void {
  right.set(Math.sin(yaw), 0, -Math.cos(yaw));
  fwd.set(Math.cos(yaw), 0, Math.sin(yaw));
}

export interface OrbitState { yaw: number; pitch: number; dist: number }

/**
 * The exact inverse of orbitEye: the yaw, polar pitch and distance that put an eye
 * where it already is.
 *
 * This is what makes unlocking seamless. The free camera used to keep whatever orbit
 * angles it was last left with, so taking manual control jumped the view somewhere else
 * before the user had touched anything; reading the angles back off the live camera
 * hands the user the picture they were already looking at.
 */
export function orbitFromEye(
  eye: THREE.Vector3, centre: THREE.Vector3, out: OrbitState,
): OrbitState {
  const dx = eye.x - centre.x, dy = eye.y - centre.y, dz = eye.z - centre.z;
  const dist = Math.hypot(dx, dy, dz);
  out.dist = dist;
  // atan2 of (z, x) inverts orbitEye's (cos yaw, sin yaw); at dist 0 there is no
  // direction to report, and 0 is the same answer orbitEye would have been given.
  out.yaw = dist > 0 ? Math.atan2(dz, dx) : 0;
  out.pitch = dist > 0 ? Math.acos(Math.max(-1, Math.min(1, dy / dist))) : 0;
  return out;
}

/**
 * Frame-rate-independent smoothing weight: the share of the remaining distance to cover
 * in `dt` seconds at rate `hz`.
 *
 * 1 - exp(-dt*k) is the exact solution of x' = k(goal - x), so ten 10 ms steps land in
 * exactly the same place as one 100 ms step -- exp(-k*a)*exp(-k*b) = exp(-k*(a+b)) --
 * and a dropped frame cannot make the camera lurch. A raw per-frame lerp cannot say
 * that: its settling time is whatever the frame rate happened to be.
 */
export function dampFactor(dtSeconds: number, hz: number): number {
  if (!(dtSeconds > 0) || !(hz > 0)) return 0;
  return 1 - Math.exp(-dtSeconds * hz);
}

/**
 * Keys the camera listens to, by physical `code` rather than `key`, so the WASD block
 * stays the WASD block on an AZERTY or QWERTZ keyboard.
 */
export const CAMERA_KEYS = {
  forward: ["KeyW", "ArrowUp"],
  back: ["KeyS", "ArrowDown"],
  left: ["KeyA", "ArrowLeft"],
  right: ["KeyD", "ArrowRight"],
  up: ["KeyE", "KeyR", "PageUp"],
  down: ["KeyQ", "KeyF", "PageDown"],
  faster: ["ShiftLeft", "ShiftRight"],
  reset: ["KeyC"],
} as const;

const CAMERA_KEY_CODE_SET = new Set<string>();
for (const group of Object.values(CAMERA_KEYS)) {
  for (const code of group) CAMERA_KEY_CODE_SET.add(code);
}
/** Every code above, so a key the camera does not use is left alone for the page. */
export const CAMERA_KEY_CODES: ReadonlySet<string> = CAMERA_KEY_CODE_SET;

/** How much faster shift makes the camera fly. */
export const KEY_BOOST = 4;

/** How the printed help spells a key code. */
export function keyLabel(code: string): string {
  if (code.startsWith("Key")) return code.slice(3);
  if (code.startsWith("Shift")) return "Shift";
  switch (code) {
    case "ArrowUp": return "↑";
    case "ArrowDown": return "↓";
    case "ArrowLeft": return "←";
    case "ArrowRight": return "→";
    case "PageUp": return "PgUp";
    case "PageDown": return "PgDn";
    default: return code;
  }
}

/**
 * The direction the held keys are asking for, in VIEW axes (x right, y up, z forward),
 * unit length or exactly zero.
 *
 * Normalised, so holding two keys does not make a diagonal 1.41x faster than a straight
 * line, and opposite keys cancel to a dead stop rather than fighting.
 */
export function keyMoveVector(held: ReadonlySet<string>, out: THREE.Vector3): THREE.Vector3 {
  const axis = (pos: readonly string[], neg: readonly string[]) =>
    (pos.some((c) => held.has(c)) ? 1 : 0) - (neg.some((c) => held.has(c)) ? 1 : 0);
  out.set(
    axis(CAMERA_KEYS.right, CAMERA_KEYS.left),
    axis(CAMERA_KEYS.up, CAMERA_KEYS.down),
    axis(CAMERA_KEYS.forward, CAMERA_KEYS.back),
  );
  if (out.lengthSq() > 0) out.normalize();
  return out;
}

export function keyBoost(held: ReadonlySet<string>): number {
  return CAMERA_KEYS.faster.some((c) => held.has(c)) ? KEY_BOOST : 1;
}

/** Flying speed as a fraction of the current view distance, per second, and the floor
 * under it. Tied to the view distance because the same key has to feel right in an
 * onboard rig 9 m off the car and in an orbit 2 km above the circuit. */
export const MOVE_PER_DISTANCE_HZ = 0.7;
export const MIN_MOVE_MPS = 3;

export function cameraMoveSpeed(distM: number, boost: number): number {
  return Math.max(MIN_MOVE_MPS, Math.abs(distM) * MOVE_PER_DISTANCE_HZ) * boost;
}

/** Closest ring vertex to a render-frame (x, z), by PLAN distance -- height ignored. */
export function nearestRingIndex(
  track: Pick<TrackModel, "x" | "y">, x: number, z: number,
): number {
  const [tx, ty] = fromRenderFrame(x, 0, z);
  const n = track.x.length;
  let best = 0, bestD = Infinity;
  for (let i = 0; i < n; i++) {
    const dx = track.x[i] - tx, dy = track.y[i] - ty;
    const d = dx * dx + dy * dy;
    if (d < bestD) { bestD = d; best = i; }
  }
  return best;
}

/**
 * How far either side of the nearest vertex still counts as "here" when deciding which
 * piece of road an eye is standing on. A little wider than any circuit's tarmac
 * (Silverstone's full ribbon is 12.2-15.0 m), so the band always contains the road the
 * eye is over, and short enough that it cannot reach a different part of the lap.
 */
export const GROUND_BAND_M = 25;

/**
 * Height of the road under an eye at render-frame (x, z, eyeY), in render-frame metres.
 *
 * Reads the BAKED surface through surfaceAt wherever the circuit carries one
 * (Silverstone today) -- that is the height the real model draws at, and the ribbon's
 * own z sits up to 0.110 m off it (measured across the shipped model) -- and falls back
 * to the ring everywhere else: every station of the other twelve circuits, and every
 * Silverstone station the raycast missed. It never invents a height; absence resolves to
 * the ring, not to zero.
 *
 * THE EYE'S OWN HEIGHT IS AN INPUT because a ring can pass over itself. Suzuka's
 * crossover carries two pieces of road 13.9 m apart in plan and 5.41 m apart in height
 * (measured 13 Sep 2026 from the shipped model; no other shipped circuit has a crossing
 * pair at all). Taking the nearest vertex in plan there would hand a camera under the
 * bridge the height of the bridge and shove it 3.4 m upward, twice a lap, in onboard.
 * So the road is chosen from those in the band that are NOT ABOVE the eye -- the one it
 * is standing on -- and only an eye below every one of them falls back to the nearest in
 * plan, which is the case the clamp exists to fix anyway.
 */
export function groundHeightAt(
  track: TrackModel, x: number, z: number, eyeY: number,
): number {
  const [tx, ty] = fromRenderFrame(x, 0, z);
  const n = track.x.length;
  let nearest = 0, nearestD2 = Infinity;
  for (let i = 0; i < n; i++) {
    const dx = track.x[i] - tx, dy = track.y[i] - ty;
    const d2 = dx * dx + dy * dy;
    if (d2 < nearestD2) { nearestD2 = d2; nearest = i; }
  }

  const band = Math.sqrt(nearestD2) + GROUND_BAND_M;
  const band2 = band * band;
  let under = -1, underD2 = Infinity;
  for (let i = 0; i < n; i++) {
    if (track.z[i] > eyeY) continue;
    const dx = track.x[i] - tx, dy = track.y[i] - ty;
    const d2 = dx * dx + dy * dy;
    if (d2 <= band2 && d2 < underD2) { underD2 = d2; under = i; }
  }

  // The band is walked on the RING's own z rather than on the baked surface: the two are
  // 0.110 m apart at worst, which cannot confuse two roads 5.41 m apart, and it keeps
  // this to one surfaceAt call instead of one per candidate.
  const chosen = under >= 0 ? under : nearest;
  const sample = surfaceAt(track, chosen * (track.lengthMetres / n));
  return toRenderFrame(0, 0, sample ? sample.zM : track.z[chosen])[1];
}

/** Metres of air kept between the eye and the road. About head height: enough that the
 * near clip plane (1 m) never eats the tarmac and the camera never ends up inside it. */
export const MIN_EYE_ABOVE_GROUND_M = 1.2;

/** Lifts an eye that has been driven below the road back onto it. Returns whether it
 * had to, so a caller can report it rather than guess. */
export function clampEyeAboveGround(
  eye: THREE.Vector3, groundY: number, marginM: number = MIN_EYE_ABOVE_GROUND_M,
): boolean {
  const floor = groundY + marginM;
  if (!(eye.y < floor)) return false;   // NaN-safe: an unknown height leaves the eye alone
  eye.y = floor;
  return true;
}

/**
 * What the camera controls are, for a HUD to print. Kept beside the bindings so the two
 * cannot drift apart: the test asserts that every key the camera binds is named here,
 * in the spelling keyLabel gives it.
 */
export const CAMERA_CONTROL_HELP: readonly { keys: string; action: string }[] = [
  { keys: "drag", action: "look around -- swings the camera about the car" },
  { keys: "right-drag", action: "slide the view left/right and up/down" },
  { keys: "scroll", action: "push in / pull out" },
  { keys: "W A S D  or  ← ↑ ↓ →", action: "fly across the ground" },
  { keys: "E R / PgUp  and  Q F / PgDn", action: "fly up / down" },
  { keys: "Shift", action: "move 4x faster" },
  { keys: "C", action: "reset this camera" },
  { keys: "middle-click", action: "follow the car / free the camera" },
];

/**
 * Assigns each car a lane offset so cars running within a rendered car-length of each
 * other never draw on top of one another. Rewrites `lateral` in place, in REAL metres
 * -- the render scale is applied later, once, in carRenderPos. An earlier version mixed
 * the two and multiplied the lane offsets twice, fanning cars up to ~90 m off the road.
 *
 * This is a RENDER-ONLY nudge: real telemetry snaps every car to nearly one line
 * (audit: p98 lateral spread 0.3-0.6 m, far smaller than a car's own 2 m width), so two
 * battling or lapping cars can be recorded at almost the same (station, lateral) even
 * though they are plainly not occupying the same patch of track. It never touches gaps,
 * lap counts, or any other reported value -- only where the car is drawn. Because it
 * does move the car, the camera must follow the DRAWN position, not the pose.
 *
 * The earlier pairwise "push both apart" version oscillated badly: on the grid, 20 cars
 * sit single file ~8 m apart, every car is inside its neighbours' window, and each
 * frame's pairwise passes resolved to a different answer -- which is exactly the cars
 * darting left and right. This version is stable instead: cars are grouped into
 * clusters by station, and within a cluster each car gets a FIXED lane index derived
 * from its running order, so the same situation always yields the same lanes. The
 * result is then eased over time (see updateCars) so even a genuine lane change slides
 * rather than snaps.
 *
 * `order` is a caller-owned scratch array, reused so the hot path allocates nothing.
 */
export function declutterLanes(
  n: number, pose: Float32Array, station: Float32Array, lateral: Float32Array,
  track: TrackModel, order: number[],
): void {
  const trackLen = track.lengthMetres;
  const windowM = CAR.lengthM * 1.15;
  const laneStepM = CAR.widthM * 1.15;

  order.length = 0;
  for (let i = 0; i < n; i++) {
    // Grid, pit-lane, finished and retired cars are all placed deliberately (a
    // staggered grid, the real pit-lane geometry, a parking queue). Shoving them
    // sideways is what produced the fan of cars sitting off the circuit.
    if (pose[i * POSE_FLOATS_PER_CAR + 12] !== POSE_STATUS.track) continue;
    order.push(i);
  }
  // station order makes clusters contiguous; position breaks ties identically every
  // frame so the lane assignment is stable rather than oscillating
  order.sort((a, b) => (station[a] - station[b])
    || (pose[a * POSE_FLOATS_PER_CAR + 10] - pose[b * POSE_FLOATS_PER_CAR + 10]));

  let clusterStart = 0;
  for (let k = 1; k <= order.length; k++) {
    const prev = order[k - 1];
    const cur = k < order.length ? order[k] : -1;
    let gap = Infinity;
    if (cur >= 0) {
      gap = Math.abs(station[cur] - station[prev]);
      if (gap > trackLen / 2) gap = trackLen - gap;
    }
    if (gap < windowM) continue; // still inside the same cluster

    const size = k - clusterStart;
    if (size > 1) {
      let mean = 0;
      for (let m = clusterStart; m < k; m++) mean += lateral[order[m]];
      mean /= size;
      // Spread the pack evenly ACROSS the available width rather than clamping to it:
      // clamping made every car past the last fitting lane land on the same edge
      // value, so they stacked exactly on top of each other -- the overlap this pass
      // exists to prevent. Squeezing the spacing keeps every car distinct and on the
      // road, which is what a tight pack really looks like.
      const halfW = halfWidthAt(track, station[order[clusterStart]]);
      // Normally a car's whole body stays on the road, so its centre can reach at most
      // half a width from the edge. In the opening moments of a race that is too
      // strict: the field starts two-wide and fans across the FULL road (and over the
      // painted edge) into the first corner, which is the one situation where a pack
      // legitimately needs every centimetre. Allowing the centre out to the edge there
      // buys ~1 m a side and is what actually happens.
      let opening = true;
      for (let m = clusterStart; m < k && opening; m++) {
        const o = order[m] * POSE_FLOATS_PER_CAR;
        opening = pose[o + 8] === 0 && pose[o + 9] < START_FAN_LAP_FRACTION;
      }
      const maxOffset = Math.max(0, opening ? halfW : halfW - CAR.widthM / 2);
      const spacing = Math.min(laneStepM, (2 * maxOffset) / (size - 1));
      for (let m = 0; m < size; m++) {
        lateral[order[clusterStart + m]] = mean + (m - (size - 1) / 2) * spacing;
      }
    }
    clusterStart = k;
  }
}

/* ==========================================================================
 * Track layers: what setTrack installs, and the one path that takes it down.
 * ======================================================================== */

/** Material properties the ribbon is put back to when it is not sitting under a GLB.
 * Snapshotted from the material three actually built, rather than retyped, so this
 * cannot drift from buildTrackMesh. */
interface RibbonMaterialState {
  transparent: boolean;
  opacity: number;
  depthWrite: boolean;
  polygonOffset: boolean;
  polygonOffsetFactor: number;
  polygonOffsetUnits: number;
}

/** Everything one circuit contributes to the scene, held together so a re-roll or a
 * session switch takes down exactly what it put up -- no more (the shared GLB) and no
 * less (the pit group, which used to be added and never removed). */
export interface TrackLayers {
  surface: THREE.Mesh;
  outline: THREE.LineSegments;
  pit: THREE.Group | null;
  ribbonDefaults: RibbonMaterialState;
}

/**
 * Every texture slot a material in this scene can own.
 *
 * `material.map` alone is not enough. The procedural ribbon has no textures at all, so
 * that was harmless until a real GLB arrived -- a glTF material routinely carries
 * normal, roughness, metalness, AO and emissive maps as well, and each is an
 * independent GPU allocation. Disposing the material without them leaks every one.
 */
const TEXTURE_SLOTS = [
  "map", "normalMap", "roughnessMap", "metalnessMap", "aoMap", "emissiveMap",
  "alphaMap", "bumpMap", "displacementMap", "lightMap", "specularMap", "envMap",
] as const;

function disposeMaterial(mat: THREE.Material): void {
  const slots = mat as unknown as Record<string, unknown>;
  for (const slot of TEXTURE_SLOTS) {
    const tex = slots[slot];
    if (tex instanceof THREE.Texture) tex.dispose();
  }
  mat.dispose();
}

/**
 * Frees one renderable object's GPU resources. Safe on anything: an object with no
 * geometry (a Group, a Sprite, a Light) is left alone.
 *
 * The type test covers `THREE.Line` rather than `THREE.LineSegments` because a GLB can
 * contribute plain `Line` and `Points` primitives -- glTF has both as primitive modes
 * -- and LineSegments extends Line, so the wider test still catches the track outline.
 * A missed type is not a crash; it is a silent leak, which is why it is worth being
 * wider than the scene's own contents.
 */
export function disposeRenderObject(obj: THREE.Object3D): void {
  if (!(obj instanceof THREE.Mesh || obj instanceof THREE.Line || obj instanceof THREE.Points)) {
    return;
  }
  obj.geometry.dispose();
  const mat = obj.material;
  for (const m of Array.isArray(mat) ? mat : [mat]) {
    if (m) disposeMaterial(m);
  }
}

/** Removes a circuit's layers from the scene and frees them. Idempotent: a null
 * `layers` is a no-op, which is what the first setTrack call passes. */
export function disposeTrackLayers(scene: THREE.Scene, layers: TrackLayers | null): void {
  if (!layers) return;
  for (const obj of [layers.surface, layers.outline, layers.pit]) {
    if (!obj) continue;
    scene.remove(obj);
    obj.traverse(disposeRenderObject);
  }
}

/** Builds and adds one circuit's ribbon, outline and pit lane. */
export function installTrackLayers(scene: THREE.Scene, track: TrackModel): TrackLayers {
  const surface = buildTrackMesh(track);
  const outline = buildTrackOutline(track);
  const pit = buildPitLaneMesh(track);
  scene.add(surface, outline);
  if (pit) scene.add(pit);
  const mat = surface.material as THREE.MeshStandardMaterial;
  return {
    surface, outline, pit,
    ribbonDefaults: {
      transparent: mat.transparent, opacity: mat.opacity, depthWrite: mat.depthWrite,
      polygonOffset: mat.polygonOffset,
      polygonOffsetFactor: mat.polygonOffsetFactor,
      polygonOffsetUnits: mat.polygonOffsetUnits,
    },
  };
}

/**
 * Puts the ribbon material into (or back out of) the state it needs when a real circuit
 * model is drawn underneath it.
 *
 * The ribbon is a flat strip through the middle of the real road surface, so the two
 * are close to coplanar and z-fight badly. Pulling it forward with a polygon offset,
 * dropping its depth writes and taking it to partial opacity makes it read as an
 * overlay ON the road instead of a second road. This only matters while something --
 * today, the shadow-price colouring -- asks for the ribbon over a GLB; the ordinary
 * "environment loaded" case just hides it.
 */
export function setRibbonOverEnvironment(layers: TrackLayers, over: boolean): void {
  const mat = layers.surface.material as THREE.MeshStandardMaterial;
  const d = layers.ribbonDefaults;
  mat.polygonOffset = over || d.polygonOffset;
  mat.polygonOffsetFactor = over ? -2 : d.polygonOffsetFactor;
  mat.polygonOffsetUnits = over ? -2 : d.polygonOffsetUnits;
  mat.depthWrite = over ? false : d.depthWrite;
  mat.transparent = over || d.transparent;
  mat.opacity = over ? 0.55 : d.opacity;
  mat.needsUpdate = true;
}

/* ==========================================================================
 * The circuit model: one fetch per URL, for the life of the page.
 * ======================================================================== */

/**
 * Parsed circuit models, keyed by served URL, at MODULE SCOPE.
 *
 * This has to outlive the renderer. Replay disposes the whole SimRenderer and builds a
 * new one on every session change (SimCanvas.tsx), so an instance-level cache would
 * re-fetch and re-parse a 158 MB asset every time the user picks a different session at
 * the same circuit. Keyed by URL, which is content-hashed, so a rebuilt asset is a new
 * key rather than a stale hit.
 *
 * The Promise is cached rather than the result, so two overlapping requests for the same
 * circuit share one fetch. A REJECTED promise is evicted (see below) so a transient
 * network failure does not permanently blacklist the circuit for the rest of the page's
 * life -- but the failure is only logged once per URL, which is the "log once" rule.
 */
const GLB_CACHE = new Map<string, Promise<THREE.Group>>();
const GLB_LOGGED_FAILURES = new Set<string>();

/** Whether this URL's model is already loaded or in flight. Diagnostics and tests: the
 * renderer itself never needs to ask, because loadEnvironmentGlb dedupes. */
export function environmentGlbCached(url: string): boolean {
  return GLB_CACHE.has(url);
}

/**
 * Fetches and parses a circuit model, once per URL per page.
 *
 * GLTFLoader is imported dynamically for two reasons: it keeps a loader that only one
 * circuit in thirteen needs out of the initial bundle, and it keeps a DOM-dependent
 * addon out of the node test environment. It resolves through `three/addons/*`, which
 * is already in node_modules -- no new npm dependency.
 */
export function loadEnvironmentGlb(
  url: string, onProgress?: (loaded: number, total: number) => void,
): Promise<THREE.Group> {
  const hit = GLB_CACHE.get(url);
  if (hit) return hit;
  const pending = (async () => {
    const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
    const gltf = await new GLTFLoader().loadAsync(url, (e) => {
      if (onProgress && e.total > 0) onProgress(e.loaded, e.total);
    });
    return gltf.scene;
  })().catch((err) => {
    // Evict, so the next session switch may retry; log once, so a repeatedly failing
    // URL cannot spam the console every time the user changes session.
    GLB_CACHE.delete(url);
    if (!GLB_LOGGED_FAILURES.has(url)) {
      GLB_LOGGED_FAILURES.add(url);
      console.warn(`[sim] circuit model ${url} could not be loaded; keeping the ` +
        `procedural ribbon.`, err);
    }
    throw err;
  });
  GLB_CACHE.set(url, pending);
  return pending;
}

/**
 * Imperative three.js scene: one instanced mesh for every car (one draw call), the
 * track ribbon built once from the track model, and a small set of camera modes. This
 * is the ONLY module that touches both a per-frame clock and WebGL, mirroring the
 * loader's runtime.ts seam rule.
 */
export class SimRenderer {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private cars: THREE.InstancedMesh | null = null;
  /** Black outline drawn around whichever car is focused (a slightly larger
   * back-faced copy of the car box, so it reads as a border on the car itself). */
  private focusOutline: THREE.Mesh | null = null;
  private labels: DriverLabels | null = null;
  private showLabels = true;
  /** The ribbon, outline and pit lane of the circuit currently installed. */
  private layers: TrackLayers | null = null;
  /** The circuit model currently being drawn, and the definition it came from. The
   * root is SHARED, module-scope state (see GLB_CACHE): this renderer may add and
   * remove it from its own scene, and must never dispose it. */
  private env: ResolvedEnvironment | null = null;
  private envRoot: THREE.Group | null = null;
  /** Bumped by every setEnvironment call, so a load that finishes after the user has
   * moved to another circuit is discarded instead of attaching the wrong model. */
  private envToken = 0;
  /** Hemisphere fill added only while a circuit model is drawn. */
  private envHemi: THREE.HemisphereLight | null = null;
  /** The scene's own lights, kept so the environment's lighting profile can be applied
   * and then exactly undone. */
  private ambient: THREE.AmbientLight;
  private sun: THREE.DirectionalLight;
  private baseLighting: { ambient: number; sun: number; sunPos: THREE.Vector3 };
  /** True while setShadowPrice is painting the ribbon, which is the one thing that
   * brings the ribbon back over a circuit model. */
  private shadowPriceActive = false;
  private track: TrackModel | null = null;
  private driverCount = 0;
  private driverNames: string[] = [];
  private cameraMode: CameraMode = "broadcast";
  /** -1 = auto-follow whichever car is currently in position 1 (the default: the
   * camera should always be watching the race, not a fixed driver-list array slot).
   * Set to a car index to lock focus onto one driver instead. */
  private focusIndex = -1;
  private rafId = 0;
  private alive = true;
  private longFrames = 0;
  /** Measured display refresh, Hz. Null until enough frames have been timed. */
  private refreshHz: number | null = null;
  private refreshSamples: number[] = [];
  private longFrameMs = LONG_FRAME_FALLBACK_MS;
  private qualityTier: 0 | 1 | 2 = 0; // 0 = full, 1 = no AA, 2 = capped DPR too
  private lastNow = 0;
  private cameraTarget = new THREE.Vector3();
  /** Centre of the circuit in render space; what the orbit camera looks at. */
  private orbitCentre = new THREE.Vector3();
  private cameraPos = new THREE.Vector3();
  private orbitYaw = Math.PI / 4;
  private orbitPitch = 0.6;
  private orbitDist = 400;
  /** Distance the free camera is easing toward, so a scroll dollies instead of cutting. */
  private easedOrbitDist = 400;
  /** The framing distance setTrack computed for this circuit, so a reset has somewhere
   * to go back to. */
  private orbitHomeDist = 400;
  /** The user's nudge to each chase mode, kept per mode: a helicopter flown out over
   * the infield must not also drag the onboard shot with it. */
  private rigs: Record<ChaseMode, CameraRig> = {
    broadcast: makeCameraRig(), onboard: makeCameraRig(), helicopter: makeCameraRig(),
  };
  /** Key codes held right now. Cleared on window blur, because a key held while the
   * window loses focus never sends its keyup. */
  private heldKeys = new Set<string>();
  /** false = the camera is free (user driven); true = it follows the focused car. */
  private cameraLocked = true;
  private onLockChange: ((locked: boolean) => void) | null = null;
  private dragButton: number | null = null;
  private panOffset = new THREE.Vector3();
  private lastPointer = { x: 0, y: 0 };
  private matrixScratch = new THREE.Matrix4();
  private quatScratch = new THREE.Quaternion();
  private posScratch = new THREE.Vector3();
  private scaleScratch = new THREE.Vector3(1, 1, 1);
  /** Reused every frame so the render loop allocates nothing: the camera eye and
   * look-at it is easing toward, the focused car's position, the heading out-param,
   * the orbit pan basis and the orbit target. */
  private camEye = new THREE.Vector3();
  private camLook = new THREE.Vector3();
  private focusPosScratch = new THREE.Vector3();
  private headingBox = { value: 0 };
  private fwdScratch = new THREE.Vector3();
  private panRight = new THREE.Vector3();
  private panFwd = new THREE.Vector3();
  private orbitTargetScratch = new THREE.Vector3();
  private moveScratch = new THREE.Vector3();
  private panStepScratch = new THREE.Vector3();
  private orbitStateScratch: OrbitState = { yaw: 0, pitch: 0, dist: 0 };
  /** Car indices in station order, reused by declutterLanes. */
  private declutterOrder: number[] = [];
  private raycaster = new THREE.Raycaster();
  private groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
  private fpsEma = 60;
  private onPerfSample: ((p: PerfStats) => void) | null = null;
  private perfSampleAcc = 0;
  private stationScratch: Float32Array | null = null;
  private lateralScratch: Float32Array | null = null;
  /** Lowest point of the car geometry in its own local frame, metres. Measured from
   * the geometry that was actually built rather than retyped as a constant, so the
   * cars cannot drift off the road again when the model changes. */
  private carGeomMinY = 0;
  /** Where the focused car was actually DRAWN this frame, and the heading it was drawn
   * with, so the camera frames the car the viewer can see rather than the raw pose. */
  private focusDrawnPos = new THREE.Vector3();
  private focusDrawnHeading = 0;
  /** Car index focusDrawnPos belongs to; -1 when no car has been drawn yet. */
  private focusDrawnIndex = -1;
  /** Eased lateral actually drawn, so a lane change slides instead of snapping. */
  private smoothedLateral: Float32Array | null = null;
  private smoothedValid = false;
  /** Pose interpolated between the last two sim frames, so the picture updates at the
   * display's refresh rate rather than the sim's fixed 60 Hz tick. */
  private blendScratch: Float32Array | null = null;
  /** Cars hidden this frame because their position was withdrawn upstream. Counted so
   * "the car vanished" is a reportable number rather than a mystery. */
  private hiddenThisFrame = 0;
  /** Team colour per instance as set up, so dimming can be undone exactly. */
  private baseColours: Float32Array | null = null;
  /** Fade the field so the focused car stands out. Implemented as a colour blend
   * toward the scene background rather than real per-instance alpha: the cars are one
   * InstancedMesh sharing one opaque material, and making instances transparent needs
   * a custom shader attribute plus back-to-front sorting that an instanced draw cannot
   * do. Blending toward the background is visually the same against this dark track,
   * costs one buffer upload when the focus changes, and keeps the single draw call. */
  private dimUnfocused = false;
  /** Focus index the current instanceColor buffer was written for; -2 forces a rewrite. */
  private dimAppliedFocus = -2;
  private dimAppliedOn = false;
  /** Focus index from the most recent pose, so a toggle can recolour while paused. */
  private lastFocusIdx = -1;

  constructor(
    private canvas: HTMLCanvasElement,
    private poseSource: PoseSource,
    powerPreference: GpuPreference = "high-performance",
  ) {
    // A HINT ONLY. On a hybrid-graphics laptop "high-performance" asks the browser
    // for the discrete GPU, but the browser's GPU process, the Windows per-app
    // graphics preference and the NVIDIA control panel all outrank it -- WebGL has
    // no API to pick a physical adapter. getGpuInfo() reports what was actually
    // handed over, so the UI can show the truth rather than the request.
    this.renderer = new THREE.WebGLRenderer({
      canvas, antialias: true, alpha: false, powerPreference,
    });
    this.renderer.setClearColor(new THREE.Color(HAAS.black), 1);
    this.camera = new THREE.PerspectiveCamera(55, 1, 1, 20000);

    this.ambient = new THREE.AmbientLight(0xffffff, 0.7);
    this.sun = new THREE.DirectionalLight(0xffffff, 0.8);
    this.sun.position.set(1, 3, 1);
    this.scene.add(this.ambient, this.sun);
    // snapshotted so a circuit model's own lighting profile can be undone exactly,
    // rather than restored to numbers retyped somewhere else
    this.baseLighting = {
      ambient: this.ambient.intensity,
      sun: this.sun.intensity,
      sunPos: this.sun.position.clone(),
    };

    canvas.addEventListener("pointerdown", this.onPointerDown);
    window.addEventListener("pointermove", this.onPointerMove);
    window.addEventListener("pointerup", this.onPointerUp);
    canvas.addEventListener("wheel", this.onWheel, { passive: false });
    canvas.addEventListener("contextmenu", this.onContextMenu);
    // keys on the window: the canvas is not focusable, so a keydown never reaches it
    window.addEventListener("keydown", this.onKeyDown);
    window.addEventListener("keyup", this.onKeyUp);
    window.addEventListener("blur", this.onWindowBlur);
    canvas.addEventListener("auxclick", (e) => { if (e.button === 1) e.preventDefault(); });

    this.resize();
  }

  /**
   * Installs a circuit.
   *
   * NewRaceCanvas re-calls this on every re-roll, on the SAME renderer, so it has to
   * take down the previous circuit's layers before building the next -- otherwise every
   * re-roll stacks another ribbon, outline and pit group on the scene and leaks their
   * geometry. The environment hook lives inside here, on the slug this already receives,
   * which is why no call site changed to gain a 3D circuit.
   */
  setTrack(track: TrackModel) {
    this.track = track;
    disposeTrackLayers(this.scene, this.layers);
    this.layers = installTrackLayers(this.scene, track);
    // Frame the orbit view on the CIRCUIT's own centre, not the telemetry origin:
    // the coordinate origin is an arbitrary point in the feed's frame and can sit
    // well outside the track, which left the circuit off-centre and clipped.
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity, sumZ = 0;
    for (let i = 0; i < track.x.length; i++) {
      if (track.x[i] < minX) minX = track.x[i];
      if (track.x[i] > maxX) maxX = track.x[i];
      if (track.y[i] < minY) minY = track.y[i];
      if (track.y[i] > maxY) maxY = track.y[i];
      sumZ += track.z[i];
    }
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    const radius = Math.max(maxX - minX, maxY - minY) / 2;
    this.orbitCentre.set(...toRenderFrame(cx, cy, sumZ / track.x.length));
    this.orbitDist = radius * 2.2;
    this.orbitHomeDist = this.orbitDist;
    this.easedOrbitDist = this.orbitDist;
    this.cameraTarget.copy(this.orbitCentre);

    // Null for twelve of thirteen circuits and for every artifact built before the
    // surface block existed, which is why this reads as one line and not as a branch.
    this.setEnvironment(environmentForTrack(track));
  }

  /**
   * Draws this circuit's real model instead of the procedural ribbon, or -- given null
   * -- goes back to the ribbon.
   *
   * NOTHING WAITS ON THE DOWNLOAD. The ribbon is already in the scene and the race is
   * already running by the time this is called; the model is fetched in the background
   * and swapped in only if and when it arrives. A failed fetch leaves the circuit
   * exactly as it is today.
   *
   * IDEMPOTENT, because NewRaceCanvas re-calls setTrack on every re-roll: asking for the
   * environment that is already installed (or already in flight) re-applies the ribbon
   * state -- the ribbon is a NEW object after a re-roll and needs hiding again -- and
   * does nothing else. It does not restart the fetch and does not re-add the model.
   */
  setEnvironment(env: ResolvedEnvironment | null) {
    if (sameEnvironment(env, this.env)) {
      this.applyRibbonForEnvironment();
      return;
    }
    const token = ++this.envToken;
    this.detachEnvironment();
    this.env = env;
    this.applyRibbonForEnvironment();
    if (!env) return;

    void loadEnvironmentGlb(env.assetUrl).then((root) => {
      // discarded if the renderer is gone, or the user moved on while this was in
      // flight; the model itself stays in the module cache either way
      if (!this.alive || token !== this.envToken) return;
      this.attachEnvironment(root, env);
    }).catch(() => {
      // already logged once by loadEnvironmentGlb. The ribbon is still there: this is
      // the third of the three fallbacks (no entry / failed gate / failed fetch), and
      // all three have to leave the circuit byte-for-byte as it is without a model.
    });
  }

  /**
   * Hangs the loaded model on the placement derived from the artifact's own fit.
   *
   * The placement assumes the model's WORLD coordinates are the frame the Python fit
   * targeted, which holds because GLTFLoader applies the same node matrices Python
   * composes (scripts/simdata/glb_surface.py: both Sketchfab chains compose to
   * world = (raw_x, -raw_z, raw_y)). Neither side re-orients the model by hand.
   */
  private attachEnvironment(root: THREE.Group, env: ResolvedEnvironment) {
    const { position, rotationY, scale } = env.placement;
    root.position.set(position[0], position[1], position[2]);
    root.rotation.set(0, rotationY, 0);
    root.scale.setScalar(scale);
    applyEnvironmentMaterials(root, env.def, this.renderer.capabilities.getMaxAnisotropy());
    // NoToneMapping at exposure 1: ACES rendered these editor-authored assets far
    // darker than their own source render. Both are three's defaults, set explicitly
    // because the model's appearance depends on them.
    this.renderer.toneMapping = THREE.NoToneMapping;
    this.renderer.toneMappingExposure = 1;
    this.scene.add(root);
    this.envRoot = root;
    this.applyEnvironmentLighting(env);
    this.applyRibbonForEnvironment();
  }

  /**
   * Takes the circuit model out of this scene WITHOUT disposing it.
   *
   * The root, its geometry and its textures are shared module-scope state, cached so
   * that switching session does not re-download 158 MB. Disposing it here would free
   * the GPU resources of an object the cache still hands out, and the next session at
   * the same circuit would render an empty husk.
   */
  private detachEnvironment() {
    if (this.envRoot) {
      this.scene.remove(this.envRoot);
      this.envRoot = null;
    }
    this.restoreLighting();
    this.env = null;
  }

  /** The lighting the model was authored against (env_sim.md Part B). Applied to the
   * scene's existing lights so the cars stay lit by the same rig as the circuit. */
  private applyEnvironmentLighting(env: ResolvedEnvironment) {
    const r = env.def.render;
    this.ambient.intensity = r.ambientIntensity;
    this.sun.intensity = r.directional.intensity;
    this.sun.position.set(...r.directional.position);
    if (!this.envHemi) {
      this.envHemi = new THREE.HemisphereLight(
        r.hemisphere.skyHex, r.hemisphere.groundHex, r.hemisphere.intensity,
      );
      this.scene.add(this.envHemi);
    } else {
      this.envHemi.intensity = r.hemisphere.intensity;
    }
  }

  private restoreLighting() {
    this.ambient.intensity = this.baseLighting.ambient;
    this.sun.intensity = this.baseLighting.sun;
    this.sun.position.copy(this.baseLighting.sunPos);
    if (this.envHemi) {
      this.scene.remove(this.envHemi);
      this.envHemi.dispose();
      this.envHemi = null;
    }
  }

  /**
   * Ribbon visibility, given what is under it.
   *
   * HIDDEN, NEVER DELETED. setShadowPrice has to be able to bring the ribbon back to
   * paint the energy shadow price over the real circuit, and a deleted ribbon cannot
   * come back; a hidden one is one boolean away. The outline and the pit ribbon go with
   * it -- they are schematic stand-ins for geometry the model actually has, and drawing
   * them over it reads as double vision.
   */
  private applyRibbonForEnvironment() {
    const layers = this.layers;
    if (!layers) return;
    const onModel = this.envRoot !== null;
    layers.surface.visible = !onModel || this.shadowPriceActive;
    layers.outline.visible = !onModel;
    if (layers.pit) layers.pit.visible = !onModel;
    setRibbonOverEnvironment(layers, onModel);
  }

  setDrivers(driverCount: number, teamColours: (string | null)[], names: string[] = []) {
    this.driverCount = driverCount;
    this.driverNames = names;
    if (this.cars) {
      this.scene.remove(this.cars);
      this.cars.geometry.dispose();
      (this.cars.material as THREE.Material).dispose();
    }
    const geom = buildF1CarGeometry();
    // Ask the geometry where its wheels are instead of assuming a box: the lift applied
    // in updateCars is -minY, so the contact patch lands on the ribbon whatever shape
    // buildF1CarGeometry returns.
    geom.computeBoundingBox();
    this.carGeomMinY = geom.boundingBox?.min.y ?? 0;
    this.focusDrawnIndex = -1;
    // vertexColors ON so the geometry's own dark tyres/wings survive, multiplied by
    // the per-instance team colour -- still one geometry, one material, one draw call
    const mat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.55, metalness: 0.25 });
    const mesh = new THREE.InstancedMesh(geom, mat, Math.max(1, driverCount));
    // InstancedMesh computes its frustum-culling bounding sphere from the LOCAL
    // geometry only (one car-sized shape near the origin) -- it is never expanded
    // to cover the per-instance world matrices. Left at the default, the whole mesh
    // was silently culled every frame the camera did not happen to look at world
    // origin (0,0,0), which is every frame once the camera follows a car around a
    // 5+ km track. Confirmed by instrumenting car matrices: positions were correct,
    // nothing rendered. Disabling frustum culling is the standard fix for an
    // instanced mesh whose instances move across an area much larger than the
    // source geometry.
    mesh.frustumCulled = false;
    mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(driverCount * 3), 3);
    this.baseColours = new Float32Array(driverCount * 3);
    for (let i = 0; i < driverCount; i++) {
      const hex = teamColours[i];
      const c = new THREE.Color(hex ?? HAAS.grey);
      mesh.setColorAt(i, c);
      this.baseColours[i * 3] = c.r;
      this.baseColours[i * 3 + 1] = c.g;
      this.baseColours[i * 3 + 2] = c.b;
    }
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    // force a recolour on the next frame so a dim toggle survives a session switch
    this.dimAppliedFocus = -2;
    this.cars = mesh;
    this.scene.add(mesh);

    this.labels?.dispose();
    if (this.labels) this.scene.remove(this.labels.group);
    this.labels = buildDriverLabels(
      this.driverNames.length === driverCount
        ? this.driverNames
        : Array.from({ length: driverCount }, (_, i) => `#${i + 1}`),
      teamColours,
      1.5,
    );
    this.labels.group.visible = this.showLabels;
    this.scene.add(this.labels.group);

    if (this.focusOutline) {
      this.scene.remove(this.focusOutline);
      this.focusOutline.geometry.dispose();
      (this.focusOutline.material as THREE.Material).dispose();
    }
    {
      // A soft white ghost of the SAME car shape, a hair larger and drawn without
      // depth testing. It hugs the real silhouette, so there is no padded box or
      // wireframe cage around the car, and the team colour underneath still reads.
      // depthTest stays ON so the car's own bodywork occludes the middle of the
      // ghost: only the part standing proud of the silhouette survives, which is a
      // rim around the car rather than a white wash over it.
      const glowMat = new THREE.MeshBasicMaterial({
        color: new THREE.Color(HAAS.white), transparent: true, opacity: 0.6,
        depthTest: true, side: THREE.BackSide,
      });
      this.focusOutline = new THREE.Mesh(buildF1CarGeometry(), glowMat);
      this.focusOutline.frustumCulled = false;
      this.focusOutline.visible = false;
      this.focusOutline.renderOrder = 6;
      this.scene.add(this.focusOutline);
    }
  }

  setCameraMode(mode: CameraMode) { this.cameraMode = mode; }
  setLabelsVisible(v: boolean) {
    this.showLabels = v;
    if (this.labels) this.labels.group.visible = v;
  }
  setFocusIndex(i: number) { this.focusIndex = i; }

  /** Fade every car except the focused one, so it can be picked out of a pack.
   * Applied immediately rather than on the next pose: the toggle has to work while the
   * race is PAUSED, and updateCars (which resolves the focus) only runs when a new pose
   * frame arrives. */
  setDimUnfocused(v: boolean) {
    this.dimUnfocused = v;
    this.applyDim(this.lastFocusIdx);
  }

  /** Rewrites instanceColor only when the toggle or the focused car actually changes,
   * so the common case costs nothing. `focus` is the resolved index, which moves on its
   * own in auto-follow mode as the lead changes. */
  private applyDim(focus: number) {
    const mesh = this.cars;
    const base = this.baseColours;
    if (!mesh || !base || !mesh.instanceColor) return;
    if (this.dimAppliedOn === this.dimUnfocused && this.dimAppliedFocus === focus) return;
    const bg = new THREE.Color(HAAS.black);
    const arr = mesh.instanceColor.array as Float32Array;
    for (let i = 0; i < this.driverCount; i++) {
      const dim = this.dimUnfocused && i !== focus;
      const k = dim ? DIM_MIX : 1;
      arr[i * 3] = bg.r + (base[i * 3] - bg.r) * k;
      arr[i * 3 + 1] = bg.g + (base[i * 3 + 1] - bg.g) * k;
      arr[i * 3 + 2] = bg.b + (base[i * 3 + 2] - bg.b) * k;
    }
    mesh.instanceColor.needsUpdate = true;
    this.dimAppliedOn = this.dimUnfocused;
    this.dimAppliedFocus = focus;
    // tags follow the cars: a dimmed field with full-strength name plates is worse
    if (this.labels) {
      for (let i = 0; i < this.labels.sprites.length; i++) {
        const mat = this.labels.sprites[i].material as THREE.SpriteMaterial;
        mat.opacity = this.dimUnfocused && i !== focus ? DIM_MIX : 1;
      }
    }
  }

  /** E-Delta hook (plan section 12/Phase 4): paints the track surface by an external
   * per-station scalar in [0, 1], e.g. the energy shadow price from the Python
   * planner. Pass null to restore the plain surface. A no-op before setTrack(). */
  setShadowPrice(values: Float32Array | null) {
    if (!this.layers) return;
    applyShadowPriceOverlay(this.layers.surface, values);
    // The ribbon is the only thing that can carry this colouring, so asking for it
    // brings the ribbon back even over a real circuit model -- as a translucent,
    // depth-offset overlay rather than a second road (setRibbonOverEnvironment).
    this.shadowPriceActive = values !== null;
    this.applyRibbonForEnvironment();
  }

  resize() {
    const w = this.canvas.clientWidth || 1;
    const h = this.canvas.clientHeight || 1;
    // three steps that each do something (the qualityTier==1 step used to be a no-op:
    // WebGL antialiasing cannot be toggled without recreating the GL context, so DPR
    // is the lever at every tier instead of only the last one).
    const dprCap = this.qualityTier === 0 ? 1.5 : this.qualityTier === 1 ? 1.15 : 1;
    const dpr = Math.min(window.devicePixelRatio || 1, dprCap);
    this.renderer.setPixelRatio(dpr);
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  getGpuInfo(): GpuInfo {
    return readGpuInfo(this.renderer.getContext());
  }

  onPerf(cb: (p: PerfStats) => void) { this.onPerfSample = cb; }

  /**
   * True when the camera is flying free rather than hanging off a car.
   *
   * Orbit counts as free whatever the lock says: it looks at the CIRCUIT, so there is no
   * car for a rig to hang off, and it would otherwise be the one mode that ignored every
   * key and every drag. Everywhere else this is false exactly when a chase rig is live,
   * which is what makes the `as ChaseMode` casts below sound.
   */
  private isFreeCamera(): boolean {
    return !this.cameraLocked || this.cameraMode === "orbit";
  }

  /** The rig for the mode currently on screen. Only ever called when isFreeCamera() is
   * false, i.e. when the mode is one of the three chase modes. */
  private rig(): CameraRig {
    return this.rigs[this.cameraMode as ChaseMode];
  }

  private onPointerDown = (e: PointerEvent) => {
    if (e.button === 1) {
      // middle click toggles follow/free, the shortcut asked for
      e.preventDefault();
      this.setCameraLocked(!this.cameraLocked);
      return;
    }
    this.dragButton = e.button;
    this.lastPointer = { x: e.clientX, y: e.clientY };
  };

  /**
   * Left-drag looks, right-drag (or shift-left) slides the view.
   *
   * Both work in EVERY mode now, not only in the free camera. In a chase mode they move
   * the RIG the camera hangs off, so the camera keeps following the focused car -- from
   * wherever the user put it. A followed camera used to refuse the mouse outright.
   *
   * The two conventions the shipped free camera already used are kept, and matched here:
   * a drag TURNS the camera (the world swings the other way, as in any mouse-look) and a
   * pan DRAGS THE WORLD with the pointer. Note the yaw signs differ between the two
   * paths on purpose: orbitYaw and rig yaw are angles in frames of opposite handedness
   * (see orbitPanBasis), and both signs below are chosen so that dragging right turns
   * the camera right.
   */
  private onPointerMove = (e: PointerEvent) => {
    if (this.dragButton === null) return;
    const dx = e.clientX - this.lastPointer.x;
    const dy = e.clientY - this.lastPointer.y;
    this.lastPointer = { x: e.clientX, y: e.clientY };
    const look = this.dragButton === 0 && !e.shiftKey;

    if (this.isFreeCamera()) {
      if (look) {
        this.orbitYaw -= dx * LOOK_RAD_PER_PX;
        this.orbitPitch = Math.max(MIN_ORBIT_POLAR,
          Math.min(MAX_ORBIT_POLAR, this.orbitPitch - dy * LOOK_RAD_PER_PX));
        return;
      }
      // pan across the ground, scaled by how far out we are. The basis is the camera's
      // OWN screen axes (see orbitPanBasis), so the world travels with the pointer in
      // both axes.
      const k = this.orbitDist * PAN_M_PER_PX_PER_M;
      orbitPanBasis(this.orbitYaw, this.panRight, this.panFwd);
      this.panOffset.addScaledVector(this.panRight, -dx * k).addScaledVector(this.panFwd, -dy * k);
      return;
    }

    const rig = this.rig();
    const geom = chaseRigGeometry(this.cameraMode as ChaseMode);
    if (look) {
      rig.yaw += dx * LOOK_RAD_PER_PX;
      // The OFFSET is clamped against its own mode's base elevation, not the total.
      // Clamping only the total lets a drag that has run into the limit wind the offset
      // up indefinitely, and then the drag back does nothing until it has unwound.
      rig.pitch = Math.max(MIN_RIG_ELEVATION - geom.elevation,
        Math.min(MAX_RIG_ELEVATION - geom.elevation, rig.pitch + dy * LOOK_RAD_PER_PX));
      return;
    }
    // Vertical drag moves the rig UP AND DOWN rather than along the ground: "in
    // helicopter I can move the camera up down left right with the mouse" is the ask,
    // and the ground-plane version of the same motion is on W/S.
    const k = geom.distM * rig.zoom * PAN_M_PER_PX_PER_M;
    viewToRigPan(rig.yaw, -dx * k, dy * k, 0, this.panStepScratch);
    rig.pan.add(this.panStepScratch).clampLength(0, MAX_RIG_PAN_M);
  };

  private onPointerUp = () => { this.dragButton = null; };

  private onContextMenu = (e: Event) => {
    e.preventDefault(); // right-drag is a pan in every mode now, not a menu
  };

  /**
   * Scroll dollies: in a chase mode it pushes the rig in and out along its own axis, and
   * in the free camera it zooms toward wherever the cursor is -- the point under the
   * pointer is projected onto the ground plane and the orbit target eased toward it as
   * the distance shrinks.
   */
  private onWheel = (e: WheelEvent) => {
    e.preventDefault();
    const factor = Math.exp(e.deltaY * ZOOM_PER_WHEEL_UNIT);

    if (!this.isFreeCamera()) {
      const rig = this.rig();
      rig.zoom = Math.max(MIN_RIG_ZOOM, Math.min(MAX_RIG_ZOOM, rig.zoom * factor));
      return;
    }

    const rect = this.canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1,
    );
    const before = this.orbitDist;
    this.orbitDist = Math.max(MIN_ORBIT_DIST_M, Math.min(MAX_ORBIT_DIST_M, this.orbitDist * factor));

    this.raycaster.setFromCamera(ndc, this.camera);
    const hit = new THREE.Vector3();
    const target = this.orbitCentre.clone().add(this.panOffset);
    this.groundPlane.constant = -target.y;
    if (this.raycaster.ray.intersectPlane(this.groundPlane, hit)) {
      // move the target a share of the way to the cursor, proportional to the zoom
      const t = 1 - this.orbitDist / before;
      this.panOffset.addScaledVector(hit.sub(target), Math.max(-0.6, Math.min(0.6, t)));
    }
  };

  /** Keys are taken on the window, not the canvas: the canvas is not focusable, and the
   * user has just clicked a HUD button as often as not. A key the camera does not bind
   * is left entirely alone, so the page keeps its own shortcuts (SimCanvas owns Space). */
  private onKeyDown = (e: KeyboardEvent) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (!CAMERA_KEY_CODES.has(e.code)) return;
    const el = e.target as HTMLElement | null;
    // typing in a field is typing, never flying. BUTTON is deliberately NOT in this
    // list: clicking "helicopter" leaves that button focused, and WASD has to keep
    // working afterwards.
    if (el && (/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName) || el.isContentEditable)) return;
    if ((CAMERA_KEYS.reset as readonly string[]).includes(e.code)) {
      this.resetCamera();
      return;
    }
    this.heldKeys.add(e.code);
    // arrows and PgUp/PgDn scroll the page otherwise, which is the one thing a viewport
    // must not do while someone is flying a camera with them
    if (e.code.startsWith("Arrow") || e.code.startsWith("Page")) e.preventDefault();
  };

  private onKeyUp = (e: KeyboardEvent) => { this.heldKeys.delete(e.code); };

  /** A key held while the window loses focus never sends its keyup, and the camera would
   * fly off in that direction for as long as the tab stayed open. */
  private onWindowBlur = () => { this.heldKeys.clear(); };

  /** Applies whatever keys are held this frame. Metres, not pixels: the step is
   * dt-scaled, so the camera covers the same ground per second at 30 fps and at 165. */
  private applyKeyboardMotion(dt: number) {
    if (this.heldKeys.size === 0) return;
    const dir = keyMoveVector(this.heldKeys, this.moveScratch);
    if (dir.lengthSq() === 0) return;
    const boost = keyBoost(this.heldKeys);

    if (this.isFreeCamera()) {
      const step = cameraMoveSpeed(this.orbitDist, boost) * dt;
      // the pan basis IS the camera's own screen axes, so the keys move the view the way
      // it is facing; panFwd points BACK toward the viewer, hence the minus on forward
      orbitPanBasis(this.orbitYaw, this.panRight, this.panFwd);
      this.panOffset
        .addScaledVector(this.panRight, dir.x * step)
        .addScaledVector(RENDER_UP, dir.y * step)
        .addScaledVector(this.panFwd, -dir.z * step);
      return;
    }

    const rig = this.rig();
    const geom = chaseRigGeometry(this.cameraMode as ChaseMode);
    const step = cameraMoveSpeed(geom.distM * rig.zoom, boost) * dt;
    viewToRigPan(rig.yaw, dir.x * step, dir.y * step, dir.z * step, this.panStepScratch);
    rig.pan.add(this.panStepScratch).clampLength(0, MAX_RIG_PAN_M);
  }

  /** Keeps the eye out of the tarmac. The baked surface where the circuit has one
   * (Silverstone), the ring's own elevation everywhere else -- never a made-up height. */
  private clampCameraToGround(eye: THREE.Vector3) {
    if (!this.track) return;
    clampEyeAboveGround(eye, groundHeightAt(this.track, eye.x, eye.z, eye.y));
  }

  setCameraLocked(locked: boolean) {
    this.cameraLocked = locked;
    this.panOffset.set(0, 0, 0);
    if (!locked) {
      // Take the free camera over EXACTLY where the chase camera was: centre on what it
      // was looking at, and read back the yaw, polar pitch and distance that reproduce
      // its eye (orbitFromEye is the exact inverse of orbitEye). Before this, unlocking
      // resumed whatever orbit angles the camera was last left with and the view jumped
      // before the user had touched anything.
      this.orbitCentre.copy(this.cameraTarget);
      const s = orbitFromEye(this.camera.position, this.orbitCentre, this.orbitStateScratch);
      this.orbitYaw = s.yaw;
      this.orbitPitch = Math.max(MIN_ORBIT_POLAR, Math.min(MAX_ORBIT_POLAR, s.pitch));
      this.orbitDist = Math.max(MIN_ORBIT_DIST_M, Math.min(MAX_ORBIT_DIST_M, s.dist));
      this.easedOrbitDist = this.orbitDist;
    }
    this.onLockChange?.(locked);
  }

  isCameraLocked() { return this.cameraLocked; }
  onCameraLockChange(cb: (locked: boolean) => void) { this.onLockChange = cb; }

  /**
   * Puts the camera on screen back where it started: the shipped rig for a chase mode,
   * the circuit framing for the free/orbit camera. Bound to C, and exposed so the HUD
   * can offer a button (see isCameraNudged).
   */
  resetCamera() {
    if (this.isFreeCamera()) {
      this.panOffset.set(0, 0, 0);
      this.orbitDist = this.orbitHomeDist;
      return;
    }
    resetRig(this.rig());
  }

  /** Whether the user has moved the camera on screen off its default. */
  isCameraNudged(): boolean {
    if (this.isFreeCamera()) {
      return this.panOffset.lengthSq() > 0 || this.orbitDist !== this.orbitHomeDist;
    }
    return !rigIsNeutral(this.rig());
  }

  /**
   * Fallback world position for a car straight from the pose, used only before any
   * frame has been drawn. The drawn position (which carries the declutter offset and
   * its easing) is what the camera normally follows -- see focusDrawnPos.
   *
   * Routed through the SAME carRenderPos the draw loop uses, so the fallback and the
   * drawn position cannot disagree about the render scale or the ride height.
   */
  private carWorldPos(pose: Float32Array, i: number, out: THREE.Vector3, headingOut?: { value: number }) {
    if (!this.track) return;
    const o = i * POSE_FLOATS_PER_CAR;
    carRenderPos(this.track, pose[o + 0], pose[o + 1], this.carGeomMinY, out, headingOut);
  }

  private updateCars(pose: Float32Array, dtWall: number) {
    if (!this.cars || !this.track) return;
    const n = this.driverCount;
    if (!this.stationScratch || this.stationScratch.length !== n) {
      this.stationScratch = new Float32Array(n);
      this.lateralScratch = new Float32Array(n);
      this.smoothedLateral = new Float32Array(n);
      this.smoothedValid = false;
    }
    const station = this.stationScratch, lateral = this.lateralScratch!;
    const smoothed = this.smoothedLateral!;
    for (let i = 0; i < n; i++) {
      const o = i * POSE_FLOATS_PER_CAR;
      station[i] = pose[o + 0];
      lateral[i] = pose[o + 1];
    }
    // finished/retired cars are already queued nose-to-tail along the station axis
    // (see timeline.ts's parkedStation), well outside this window, so no separate
    // "is this car still racing" check is needed here.
    declutterLanes(n, pose, station, lateral, this.track, this.declutterOrder);

    // ease toward the assigned lane; the first frame snaps so cars do not fly in
    const k = this.smoothedValid ? 1 - Math.exp(-dtWall * 6) : 1;
    for (let i = 0; i < n; i++) {
      smoothed[i] += (lateral[i] - smoothed[i]) * k;
      lateral[i] = smoothed[i];
    }
    this.smoothedValid = true;

    // Resolved before the draw loop: the camera follows the position this loop
    // actually writes, so it has to know which car to record on the way past.
    const focusIdx = this.resolveFocusIndex(pose);
    this.focusDrawnIndex = -1;

    const track = this.track;
    this.hiddenThisFrame = 0;
    for (let i = 0; i < n; i++) {
      // The DECLUTTERED, EASED lateral -- not pose[o + 1]. This is exactly why the
      // camera has to follow focusDrawnPos: the car is drawn up to a full lane away
      // from its recorded lateral.
      // A car whose position was WITHDRAWN upstream arrives here as NaN -- the encoder
      // writes NaN for a position it refuses to invent, and timeline.ts now passes that
      // through instead of substituting the start/finish line. Composing a matrix from
      // NaN puts the whole instance in an undefined state (three.js does not validate),
      // and a single NaN vertex can drop the entire InstancedMesh from the draw. So an
      // unplaceable car is HIDDEN, by zero scale, rather than drawn somewhere wrong.
      // This is the last line of defence, not the fix: the fix is that nothing upstream
      // fabricates a position. Both exist because the whole class of bug here was a
      // number standing in for "unknown".
      if (!Number.isFinite(station[i]) || !Number.isFinite(lateral[i])) {
        this.matrixScratch.makeScale(0, 0, 0);
        this.cars.setMatrixAt(i, this.matrixScratch);
        this.hiddenThisFrame++;
        if (this.labels) this.labels.sprites[i].visible = false;
        continue;
      }
      if (this.labels && this.showLabels) this.labels.sprites[i].visible = true;
      carRenderPos(track, station[i], lateral[i], this.carGeomMinY,
        this.posScratch, this.headingBox);
      carOrientation(this.headingBox.value, this.quatScratch);
      this.matrixScratch.compose(this.posScratch, this.quatScratch, this.scaleScratch);
      this.cars.setMatrixAt(i, this.matrixScratch);
      if (i === focusIdx) {
        this.focusDrawnPos.copy(this.posScratch);
        this.focusDrawnHeading = this.headingBox.value;
        this.focusDrawnIndex = i;
      }
    }
    this.cars.instanceMatrix.needsUpdate = true;

    if (this.labels && this.labels.group.visible) {
      // A sprite sized in world metres grows without bound as the camera closes in --
      // at broadcast range the plates covered half the screen. Rescale each one per
      // frame so it holds a roughly constant PIXEL height instead: the world height
      // that subtends LABEL_PX pixels is (2 * dist * tan(fov/2)) * LABEL_PX / viewportPx.
      // Clamped so a distant car's tag stays legible and a near one stays modest.
      const cam = this.camera;
      const viewportPx = this.renderer.domElement.height || 1;
      const tanHalfFov = Math.tan((cam.fov * Math.PI) / 360);
      for (let i = 0; i < n && i < this.labels.sprites.length; i++) {
        this.cars.getMatrixAt(i, this.matrixScratch);
        this.matrixScratch.decompose(this.posScratch, this.quatScratch, this.scaleScratch);
        const sprite = this.labels.sprites[i];
        const dist = cam.position.distanceTo(this.posScratch);
        const hM = Math.min(
          LABEL_MAX_WORLD_M,
          Math.max(LABEL_MIN_WORLD_M, (2 * dist * tanHalfFov * LABEL_PX) / viewportPx),
        );
        sprite.scale.set(hM * sprite.userData.aspect, hM, 1);
        // sit the plate just above the roll hoop, in world metres so it does not
        // drift into the bodywork as the plate itself rescales
        sprite.position.set(
          this.posScratch.x, this.posScratch.y + 1.6 + hM * 0.5, this.posScratch.z,
        );
      }
    }

    // focusIdx was resolved before the draw loop: in auto-follow mode it moves as the
    // lead changes, and the draw loop, the dim pass, the focus outline and the camera
    // must all agree on which car is the focused one.
    this.lastFocusIdx = focusIdx;
    this.applyDim(focusIdx);

    if (this.focusOutline) {
      const fi = focusIdx;
      if (fi >= 0 && fi < n) {
        // reuse the exact matrix already written for that instance, then grow it
        // slightly so the rim peeks out evenly on every side
        this.cars.getMatrixAt(fi, this.matrixScratch);
        this.matrixScratch.decompose(this.posScratch, this.quatScratch, this.scaleScratch);
        this.focusOutline.position.copy(this.posScratch);
        // Scaling happens about the GEOMETRY's origin, which is -carGeomMinY above the
        // contact patch, so a uniform 1.07 would push the ghost's underside
        // -carGeomMinY * 0.07 = 0.021 m into the tarmac. focusGhostY undoes exactly
        // that, for any geometry and any scale.
        this.focusOutline.position.y = focusGhostY(this.posScratch.y, this.carGeomMinY);
        this.focusOutline.quaternion.copy(this.quatScratch);
        this.focusOutline.scale.setScalar(FOCUS_OUTLINE_SCALE); // just proud of the bodywork
        this.focusOutline.visible = true;
      } else {
        this.focusOutline.visible = false;
      }
    }
  }

  /**
   * Linear blend between the previous and latest sim frames, by wall clock.
   *
   * The worker ticks at a fixed 60 Hz. Drawing its newest frame directly means a
   * 165 Hz display shows the same car positions for two or three consecutive frames
   * and motion judders. Interpolating costs one sim tick of latency (~17 ms) and in
   * exchange the motion is continuous at any refresh rate. Station is blended the
   * short way around the lap so a car crossing the line does not sweep backwards.
   *
   * Heading (index 3) is deliberately NOT blended: updateCars takes it from the ring
   * tangent at the blended station instead, so it cannot disagree with where the car
   * is drawn. Blending an angle linearly would also sweep the long way round at the
   * +-pi wrap.
   */
  private blendPose(latest: PoseFrame, prev: PoseFrame | null, nowMs: number): Float32Array {
    if (!prev || prev.carCount !== latest.carCount || !this.track) return latest.floats;
    const span = latest.arrivedMs - prev.arrivedMs;
    if (span <= 0) return latest.floats;
    // render one tick behind, so the value asked for always lies between the two
    const alpha = Math.max(0, Math.min(1, (nowMs - latest.arrivedMs) / span));
    const n = latest.floats.length;
    if (!this.blendScratch || this.blendScratch.length !== n) this.blendScratch = new Float32Array(n);
    const out = this.blendScratch;
    const trackLen = this.track.lengthMetres;

    out.set(latest.floats);
    for (let i = 0; i < latest.carCount; i++) {
      const o = i * POSE_FLOATS_PER_CAR;
      const a = prev.floats[o], b = latest.floats[o];
      let d = b - a;
      if (d > trackLen / 2) d -= trackLen;
      if (d < -trackLen / 2) d += trackLen;
      out[o] = ((a + d * alpha) % trackLen + trackLen) % trackLen;
      out[o + 1] = prev.floats[o + 1] + (latest.floats[o + 1] - prev.floats[o + 1]) * alpha;
      out[o + 4] = prev.floats[o + 4] + (latest.floats[o + 4] - prev.floats[o + 4]) * alpha;
    }
    return out;
  }

  /** Learns the panel's real cadence from rAF itself, then scales the slow-frame
   * threshold to it, so adaptive quality behaves the same on a 60 Hz laptop screen and
   * a 165 Hz one. The median is used because the first frames after load are noisy. */
  private measureRefresh(frameMs: number) {
    if (this.refreshHz !== null) return;
    if (frameMs > 0 && frameMs < 100) this.refreshSamples.push(frameMs);
    if (this.refreshSamples.length < REFRESH_SAMPLES) return;
    const sorted = [...this.refreshSamples].sort((a, b) => a - b);
    const median = sorted[sorted.length >> 1];
    this.refreshHz = Math.round(1000 / median);
    this.longFrameMs = Math.max(median * LONG_FRAME_PERIODS, 12);
    this.refreshSamples.length = 0;
  }

  getRefreshHz() { return this.refreshHz; }

  private resolveFocusIndex(pose: Float32Array): number {
    if (this.focusIndex >= 0) return Math.min(this.focusIndex, this.driverCount - 1);
    // auto-follow: scan the pose buffer for whichever car currently holds position 1
    for (let i = 0; i < this.driverCount; i++) {
      if (pose[i * POSE_FLOATS_PER_CAR + 10] === 1) return i;
    }
    return 0;
  }

  private updateCamera(pose: Float32Array, dtWall: number) {
    if (!this.track) return;
    const focusPos = this.focusPosScratch.set(0, 0, 0);
    const headingBox = this.headingBox;
    headingBox.value = 0;
    if (this.driverCount > 0) {
      const idx = this.resolveFocusIndex(pose);
      if (this.focusDrawnIndex === idx) {
        // Follow the car where it was DRAWN. The pose's lateral is the raw recorded
        // one; declutterLanes moves a car up to a full lane off it and updateCars then
        // eases toward that over ~0.17 s, so aiming at the pose left the camera centred
        // on a patch of road beside the car it was meant to be following.
        focusPos.copy(this.focusDrawnPos);
        headingBox.value = this.focusDrawnHeading;
      } else {
        // no car drawn yet this session (first frame, or no InstancedMesh)
        this.carWorldPos(pose, idx, focusPos, headingBox);
      }
    }

    this.applyKeyboardMotion(dtWall);

    const desired = this.camEye;
    const lookAt = this.camLook;
    // Tracking has to hold at 20x playback, where the car covers ~20 m between
    // frames. At the old rate the camera settled hundreds of metres behind and the
    // car shrank to a speck on the horizon; this keeps it framed while still easing.
    // dampFactor, not a bare lerp weight: the settling time is then a property of the
    // camera and not of whatever frame rate the machine happened to deliver.
    const lag = dampFactor(dtWall, CAMERA_DAMP_HZ);

    if (this.isFreeCamera()) {
      // The user drives the GOAL; the camera eases onto it. Both the look-at and the
      // distance are damped, so a pan glides and a scroll dollies instead of teleporting
      // -- the free camera used to be placed exactly on its goal every frame.
      const goal = this.orbitTargetScratch.copy(this.orbitCentre).add(this.panOffset);
      if (this.cameraTarget.distanceTo(goal) > SNAP_DISTANCE_M) {
        this.cameraTarget.copy(goal);
      } else {
        this.cameraTarget.lerp(goal, lag);
      }
      this.easedOrbitDist += (this.orbitDist - this.easedOrbitDist) * lag;
      orbitEye(this.cameraTarget, this.orbitYaw, this.orbitPitch, this.easedOrbitDist, desired);
      this.clampCameraToGround(desired);
      this.camera.position.copy(desired);
      this.camera.lookAt(this.cameraTarget);
      // Keep the chase camera's easing state on the camera the viewer can actually
      // see. Without this, re-locking resumed the lerp from wherever cameraPos was
      // left before the user took over, so the view jumped before it eased.
      this.cameraPos.copy(desired);
      return;
    }

    // Orbit never reaches here: isFreeCamera() is true for it whatever the lock says,
    // which is what makes this cast sound.
    chaseCameraPose(
      this.cameraMode as ChaseMode, focusPos, headingBox.value,
      desired, lookAt, this.fwdScratch, this.rig(),
    );
    // A pure per-frame lerp assumes small, regular frame times. It breaks in two
    // real situations: the very first frame (camera starts at the scene origin,
    // potentially hundreds of metres from any car) and a stalled/slow frame or a
    // high playback multiplier (the target can move faster, between renders, than
    // a bounded lerp can ever catch up to -- it was measured to DIVERGE, not
    // converge, at 20x speed under a slow frame rate). Snapping past a distance
    // threshold makes both cases instant instead of a multi-second crawl.
    if (this.cameraPos.distanceTo(desired) > SNAP_DISTANCE_M) {
      this.cameraPos.copy(desired);
    } else {
      this.cameraPos.lerp(desired, lag);
    }
    // Clamp the EASED position, not just the copy handed to three.js: clamping only the
    // copy would leave the eased state underground and the camera would fight the floor
    // every frame instead of resting on it.
    this.clampCameraToGround(this.cameraPos);
    this.camera.position.copy(this.cameraPos);
    if (this.cameraTarget.distanceTo(lookAt) > SNAP_DISTANCE_M) {
      this.cameraTarget.copy(lookAt);
    } else {
      this.cameraTarget.lerp(lookAt, lag);
    }
    this.camera.lookAt(this.cameraTarget);
  }

  start() {
    this.lastNow = performance.now();
    const frame = (now: number) => {
      if (!this.alive) return;
      this.rafId = requestAnimationFrame(frame);
      const dtWall = Math.min((now - this.lastNow) / 1000, 0.1);
      const frameMs = now - this.lastNow;
      this.lastNow = now;

      // exponential moving average, not the instantaneous 1/dt: a single frame's
      // fps is too noisy for a UI readout to be useful
      const instFps = frameMs > 0 ? 1000 / frameMs : 60;
      this.fpsEma += (instFps - this.fpsEma) * 0.1;

      this.measureRefresh(frameMs);

      if (frameMs > this.longFrameMs) {
        if (++this.longFrames >= 3 && this.qualityTier < 2) {
          this.qualityTier = (this.qualityTier + 1) as 0 | 1 | 2;
          this.resize();
        }
      } else {
        this.longFrames = 0;
      }

      const pose = this.poseSource.getLatestPose();
      if (pose) {
        const floats = this.blendPose(pose, this.poseSource.getPrevPose(), now);
        this.updateCars(floats, dtWall);
        this.updateCamera(floats, dtWall);
      }
      this.renderer.render(this.scene, this.camera);

      this.perfSampleAcc += dtWall;
      if (this.onPerfSample && this.perfSampleAcc >= 0.5) {
        this.perfSampleAcc = 0;
        this.onPerfSample({
          fps: Math.round(this.fpsEma), frameMs, qualityTier: this.qualityTier,
          refreshHz: this.refreshHz, carsHidden: this.hiddenThisFrame,
        });
      }
    };
    this.rafId = requestAnimationFrame(frame);
  }

  dispose() {
    this.alive = false;
    this.labels?.dispose();
    cancelAnimationFrame(this.rafId);
    this.canvas.removeEventListener("pointerdown", this.onPointerDown);
    window.removeEventListener("pointermove", this.onPointerMove);
    window.removeEventListener("pointerup", this.onPointerUp);
    this.canvas.removeEventListener("wheel", this.onWheel);
    window.removeEventListener("keydown", this.onKeyDown);
    window.removeEventListener("keyup", this.onKeyUp);
    window.removeEventListener("blur", this.onWindowBlur);
    this.canvas.removeEventListener("contextmenu", this.onContextMenu);
    // BEFORE the traverse, not after. The circuit model is shared, module-scope state
    // cached across renderer lifetimes, and this traverse frees the geometry and
    // textures of everything it can still reach -- so leaving the model in the scene
    // here would destroy the cached copy that the NEXT session is about to be handed.
    // Replay disposes and rebuilds the whole renderer on every session change, so this
    // is not a rare path: it is every session change.
    this.detachEnvironment();
    this.scene.traverse(disposeRenderObject);
    this.layers = null;
    this.renderer.dispose();
  }
}

export function lowEndHint(): boolean {
  return LOW_END(window.innerWidth, window.innerHeight);
}
