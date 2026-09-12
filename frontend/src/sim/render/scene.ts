"use client";

import * as THREE from "three";
import type { TrackModel } from "../contract/types";
import { CAR } from "@/components/loader/physics/constants";
import { HAAS } from "@/lib/palette";
import { halfWidthAt } from "../data/manifest";
import { POSE_FLOATS_PER_CAR, POSE_STATUS } from "../worker/protocol";
import {
  CAR_RENDER_HEIGHT_M, CAR_RENDER_LENGTH_M, CAR_RENDER_WIDTH_M, PRESENTATION_SCALE,
} from "./presentation";
import {
  applyShadowPriceOverlay, buildPitLaneMesh, buildTrackMesh, buildTrackOutline, toRenderFrame,
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

interface PoseSource {
  getLatestPose(): { floats: Float32Array; sessionTime: number; carCount: number } | null;
}

const LOW_END = (w: number, h: number) => w * h < 500_000 || (navigator.hardwareConcurrency ?? 4) <= 4;
const LONG_FRAME_MS = 34;

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
  private trackSurface: THREE.Mesh | null = null;
  private track: TrackModel | null = null;
  private driverCount = 0;
  private cameraMode: CameraMode = "broadcast";
  /** -1 = auto-follow whichever car is currently in position 1 (the default: the
   * camera should always be watching the race, not a fixed driver-list array slot).
   * Set to a car index to lock focus onto one driver instead. */
  private focusIndex = -1;
  private rafId = 0;
  private alive = true;
  private longFrames = 0;
  private qualityTier: 0 | 1 | 2 = 0; // 0 = full, 1 = no AA, 2 = capped DPR too
  private lastNow = 0;
  private cameraTarget = new THREE.Vector3();
  /** Centre of the circuit in render space; what the orbit camera looks at. */
  private orbitCentre = new THREE.Vector3();
  private cameraPos = new THREE.Vector3();
  private orbitYaw = Math.PI / 4;
  private orbitPitch = 0.6;
  private orbitDist = 400;
  private pointerDown = false;
  private lastPointer = { x: 0, y: 0 };
  private matrixScratch = new THREE.Matrix4();
  private quatScratch = new THREE.Quaternion();
  private posScratch = new THREE.Vector3();
  private scaleScratch = new THREE.Vector3(1, 1, 1);
  private upVec = new THREE.Vector3(0, 1, 0);
  private fpsEma = 60;
  private onPerfSample: ((p: PerfStats) => void) | null = null;
  private perfSampleAcc = 0;
  private stationScratch: Float32Array | null = null;
  private lateralScratch: Float32Array | null = null;
  private headingScratch: Float32Array | null = null;
  /** Eased lateral actually drawn, so a lane change slides instead of snapping. */
  private smoothedLateral: Float32Array | null = null;
  private smoothedValid = false;

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

    const ambient = new THREE.AmbientLight(0xffffff, 0.7);
    const sun = new THREE.DirectionalLight(0xffffff, 0.8);
    sun.position.set(1, 3, 1);
    this.scene.add(ambient, sun);

    canvas.addEventListener("pointerdown", this.onPointerDown);
    window.addEventListener("pointermove", this.onPointerMove);
    window.addEventListener("pointerup", this.onPointerUp);
    canvas.addEventListener("wheel", this.onWheel, { passive: true });

    this.resize();
  }

  setTrack(track: TrackModel) {
    this.track = track;
    const surface = buildTrackMesh(track);
    const outline = buildTrackOutline(track);
    this.scene.add(surface, outline);
    this.trackSurface = surface;
    const pit = buildPitLaneMesh(track);
    if (pit) this.scene.add(pit);
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
    this.cameraTarget.copy(this.orbitCentre);
  }

  setDrivers(driverCount: number, teamColours: (string | null)[]) {
    this.driverCount = driverCount;
    if (this.cars) {
      this.scene.remove(this.cars);
      this.cars.geometry.dispose();
      (this.cars.material as THREE.Material).dispose();
    }
    const geom = new THREE.BoxGeometry(
      CAR_RENDER_LENGTH_M, CAR_RENDER_HEIGHT_M, CAR_RENDER_WIDTH_M,
    );
    const mat = new THREE.MeshStandardMaterial({ vertexColors: false, roughness: 0.5, metalness: 0.3 });
    const mesh = new THREE.InstancedMesh(geom, mat, Math.max(1, driverCount));
    // InstancedMesh computes its frustum-culling bounding sphere from the LOCAL
    // geometry only (a single 5.6x0.9x2 box near the origin) -- it is never expanded
    // to cover the per-instance world matrices. Left at the default, the whole mesh
    // was silently culled every frame the camera did not happen to look at world
    // origin (0,0,0), which is every frame once the camera follows a car around a
    // 5+ km track. Confirmed by instrumenting car matrices: positions were correct,
    // nothing rendered. Disabling frustum culling is the standard fix for an
    // instanced mesh whose instances move across an area much larger than the
    // source geometry.
    mesh.frustumCulled = false;
    mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(driverCount * 3), 3);
    for (let i = 0; i < driverCount; i++) {
      const hex = teamColours[i];
      const c = new THREE.Color(hex ?? HAAS.grey);
      mesh.setColorAt(i, c);
    }
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    this.cars = mesh;
    this.scene.add(mesh);
  }

  setCameraMode(mode: CameraMode) { this.cameraMode = mode; }
  setFocusIndex(i: number) { this.focusIndex = i; }

  /** E-Delta hook (plan section 12/Phase 4): paints the track surface by an external
   * per-station scalar in [0, 1], e.g. the energy shadow price from the Python
   * planner. Pass null to restore the plain surface. A no-op before setTrack(). */
  setShadowPrice(values: Float32Array | null) {
    if (this.trackSurface) applyShadowPriceOverlay(this.trackSurface, values);
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

  private onPointerDown = (e: PointerEvent) => {
    if (this.cameraMode !== "orbit") return;
    this.pointerDown = true;
    this.lastPointer = { x: e.clientX, y: e.clientY };
  };
  private onPointerMove = (e: PointerEvent) => {
    if (!this.pointerDown) return;
    const dx = e.clientX - this.lastPointer.x;
    const dy = e.clientY - this.lastPointer.y;
    this.lastPointer = { x: e.clientX, y: e.clientY };
    this.orbitYaw -= dx * 0.005;
    this.orbitPitch = Math.max(0.1, Math.min(1.4, this.orbitPitch - dy * 0.005));
  };
  private onPointerUp = () => { this.pointerDown = false; };
  private onWheel = (e: WheelEvent) => {
    if (this.cameraMode !== "orbit") return;
    this.orbitDist = Math.max(50, Math.min(4000, this.orbitDist * (1 + e.deltaY * 0.001)));
  };

  private carWorldPos(pose: Float32Array, i: number, out: THREE.Vector3, headingOut?: { value: number }) {
    if (!this.track) return;
    const o = i * POSE_FLOATS_PER_CAR;
    const station = pose[o + 0];
    const lateral = pose[o + 1];
    const heading = pose[o + 3];
    const track = this.track;
    const n = track.x.length;
    const ds = track.lengthMetres / n;
    const s = ((station % track.lengthMetres) + track.lengthMetres) % track.lengthMetres;
    const f = s / ds;
    const i0 = Math.floor(f) % n;
    const i1 = (i0 + 1) % n;
    const frac = f - Math.floor(f);
    const cx = track.x[i0] + (track.x[i1] - track.x[i0]) * frac;
    const cy = track.y[i0] + (track.y[i1] - track.y[i0]) * frac;
    const cz = track.z[i0] + (track.z[i1] - track.z[i0]) * frac;
    const nx = -Math.sin(heading), ny = Math.cos(heading);
    const [rx, ry, rz] = toRenderFrame(cx + nx * lateral, cy + ny * lateral, cz);
    out.set(rx, ry, rz);
    if (headingOut) headingOut.value = heading;
  }

  /** Minimum visual separation applied when two cars' real recorded stations put
   * them within one car length of each other on the SAME piece of track. This is a
   * RENDER-ONLY nudge to the lateral offset used for drawing: real telemetry snaps
   * every car to nearly one line (audit: p98 lateral spread 0.3-0.6 m, far smaller
   * than a car's own 2 m width), so two battling or lapping cars can be recorded at
   * almost the same (station, lateral) even though they are plainly not occupying the
   * same patch of track. It never touches gaps, lap counts, or any other reported
   * value -- only where the box is drawn. */
  /**
   * Assigns each car a lane offset so cars running within a rendered car-length of
   * each other never draw on top of one another.
   *
   * The earlier pairwise "push both apart" version oscillated badly: on the grid, 20
   * cars sit single file ~8 m apart, every car is inside its neighbours' window, and
   * each frame's pairwise passes resolved to a different answer -- which is exactly
   * the cars darting left and right. This version is stable instead: cars are grouped
   * into clusters by station, and within a cluster each car gets a FIXED lane index
   * derived from its running order, so the same situation always yields the same
   * lanes. The result is then eased over time (see updateCars) so even a genuine lane
   * change slides rather than snaps.
   */
  private declutterLateral(n: number, pose: Float32Array) {
    const station = this.stationScratch!, lateral = this.lateralScratch!;
    const track = this.track!;
    const trackLen = track.lengthMetres;
    // Everything here is in REAL metres, the same units the pose carries. The render
    // scale is applied later, once, when the world position is computed -- an earlier
    // version mixed the two and multiplied the lane offsets twice, fanning cars up to
    // ~90 m off the road.
    const windowM = CAR.lengthM * 1.15;
    const laneStepM = CAR.widthM * 1.15;

    const onTrack: number[] = [];
    for (let i = 0; i < n; i++) {
      // Grid, pit-lane, finished and retired cars are all placed deliberately (a
      // staggered grid, the real pit-lane geometry, a parking queue). Shoving them
      // sideways is what produced the fan of cars sitting off the circuit.
      if (pose[i * POSE_FLOATS_PER_CAR + 12] !== POSE_STATUS.track) continue;
      onTrack.push(i);
    }
    // station order makes clusters contiguous; position breaks ties identically
    // every frame so the lane assignment is stable rather than oscillating
    const asArray = onTrack.sort((a, b) => (station[a] - station[b])
      || (pose[a * POSE_FLOATS_PER_CAR + 10] - pose[b * POSE_FLOATS_PER_CAR + 10]));

    let clusterStart = 0;
    for (let k = 1; k <= asArray.length; k++) {
      const prev = asArray[k - 1];
      const cur = k < asArray.length ? asArray[k] : -1;
      let gap = Infinity;
      if (cur >= 0) {
        gap = Math.abs(station[cur] - station[prev]);
        if (gap > trackLen / 2) gap = trackLen - gap;
      }
      if (gap < windowM) continue; // still inside the same cluster

      const size = k - clusterStart;
      if (size > 1) {
        let mean = 0;
        for (let m = clusterStart; m < k; m++) mean += lateral[asArray[m]];
        mean /= size;
        // A road only holds so many cars abreast: clamp the fan to the real half
        // width at this point of the circuit, so a big pack bunches up visually
        // instead of spilling onto the grass.
        const halfW = halfWidthAt(track, station[asArray[clusterStart]]);
        const maxOffset = Math.max(0, halfW - CAR.widthM / 2);
        for (let m = 0; m < size; m++) {
          const car = asArray[clusterStart + m];
          const rank = Math.ceil(m / 2) * (m % 2 === 1 ? 1 : -1);
          const want = mean + rank * laneStepM;
          lateral[car] = Math.max(-maxOffset, Math.min(maxOffset, want));
        }
      }
      clusterStart = k;
    }
  }

  private updateCars(pose: Float32Array, dtWall: number) {
    if (!this.cars || !this.track) return;
    const n = this.driverCount;
    if (!this.stationScratch || this.stationScratch.length !== n) {
      this.stationScratch = new Float32Array(n);
      this.lateralScratch = new Float32Array(n);
      this.headingScratch = new Float32Array(n);
      this.smoothedLateral = new Float32Array(n);
      this.smoothedValid = false;
    }
    const station = this.stationScratch, lateral = this.lateralScratch!;
    const headings = this.headingScratch!;
    const smoothed = this.smoothedLateral!;
    for (let i = 0; i < n; i++) {
      const o = i * POSE_FLOATS_PER_CAR;
      station[i] = pose[o + 0];
      lateral[i] = pose[o + 1];
      headings[i] = pose[o + 3];
    }
    // finished/retired cars are already queued nose-to-tail along the station axis
    // (see timeline.ts's parkedStation), well outside this window, so no separate
    // "is this car still racing" check is needed here.
    this.declutterLateral(n, pose);

    // ease toward the assigned lane; the first frame snaps so cars do not fly in
    const k = this.smoothedValid ? 1 - Math.exp(-dtWall * 6) : 1;
    for (let i = 0; i < n; i++) {
      smoothed[i] += (lateral[i] - smoothed[i]) * k;
      lateral[i] = smoothed[i];
    }
    this.smoothedValid = true;

    for (let i = 0; i < n; i++) {
      if (!this.track) break;
      const track = this.track;
      const tn = track.x.length;
      const ds = track.lengthMetres / tn;
      const s = ((station[i] % track.lengthMetres) + track.lengthMetres) % track.lengthMetres;
      const f = s / ds;
      const i0 = Math.floor(f) % tn;
      const i1 = (i0 + 1) % tn;
      const frac = f - Math.floor(f);
      const cx = track.x[i0] + (track.x[i1] - track.x[i0]) * frac;
      const cy = track.y[i0] + (track.y[i1] - track.y[i0]) * frac;
      const cz = track.z[i0] + (track.z[i1] - track.z[i0]) * frac;
      const heading = headings[i];
      const nx = -Math.sin(heading), ny = Math.cos(heading);
      // lateral is scaled with the road (presentation.ts), so a car sitting halfway
      // to the kerb in the data still sits halfway to the kerb on the widened ribbon
      const lat = lateral[i] * PRESENTATION_SCALE;
      const [rx, ry, rz] = toRenderFrame(cx + nx * lat, cy + ny * lat, cz);
      // BoxGeometry is centred on its own origin, so placing that origin on the road
      // surface buries the bottom half of every car in the tarmac. Lift it by half
      // its height (plus a hair, to stay off the ribbon's z-fighting plane) so the
      // wheels sit ON the track. The telemetry point itself is the car's reference
      // position, which we treat as its centre in plan view.
      this.posScratch.set(rx, ry + CAR_RENDER_HEIGHT_M / 2 + 0.05, rz);
      this.quatScratch.setFromAxisAngle(this.upVec, -heading);
      this.matrixScratch.compose(this.posScratch, this.quatScratch, this.scaleScratch);
      this.cars.setMatrixAt(i, this.matrixScratch);
    }
    this.cars.instanceMatrix.needsUpdate = true;
  }

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
    const focusPos = new THREE.Vector3();
    const headingBox = { value: 0 };
    if (this.driverCount > 0) {
      this.carWorldPos(pose, this.resolveFocusIndex(pose), focusPos, headingBox);
    }

    const desired = new THREE.Vector3();
    let lookAt = focusPos.clone();
    const lag = 1 - Math.exp(-dtWall * 4);

    switch (this.cameraMode) {
      case "orbit": {
        const x = this.orbitDist * Math.sin(this.orbitPitch) * Math.cos(this.orbitYaw);
        const y = this.orbitDist * Math.cos(this.orbitPitch);
        const z = this.orbitDist * Math.sin(this.orbitPitch) * Math.sin(this.orbitYaw);
        desired.set(this.orbitCentre.x + x, this.orbitCentre.y + y, this.orbitCentre.z + z);
        lookAt = this.orbitCentre;
        break;
      }
      case "helicopter": {
        desired.set(focusPos.x, focusPos.y + 220, focusPos.z + 0.01);
        break;
      }
      case "onboard": {
        const back = new THREE.Vector3(-Math.cos(headingBox.value) * 6, 2.2, -Math.sin(headingBox.value) * 6);
        desired.copy(focusPos).add(back);
        lookAt = focusPos.clone().add(
          new THREE.Vector3(Math.cos(headingBox.value) * 20, 0, Math.sin(headingBox.value) * 20),
        );
        break;
      }
      case "broadcast":
      default: {
        const back = new THREE.Vector3(-Math.cos(headingBox.value) * 45, 24, -Math.sin(headingBox.value) * 45);
        desired.copy(focusPos).add(back);
        break;
      }
    }

    if (this.cameraMode === "orbit") {
      this.camera.position.copy(desired);
      this.camera.lookAt(lookAt);
    } else {
      // A pure per-frame lerp assumes small, regular frame times. It breaks in two
      // real situations: the very first frame (camera starts at the scene origin,
      // potentially hundreds of metres from any car) and a stalled/slow frame or a
      // high playback multiplier (the target can move faster, between renders, than
      // a bounded lerp can ever catch up to -- it was measured to DIVERGE, not
      // converge, at 20x speed under a slow frame rate). Snapping past a distance
      // threshold makes both cases instant instead of a multi-second crawl.
      const SNAP_DISTANCE_M = 150;
      if (this.cameraPos.distanceTo(desired) > SNAP_DISTANCE_M) {
        this.cameraPos.copy(desired);
      } else {
        this.cameraPos.lerp(desired, lag);
      }
      this.camera.position.copy(this.cameraPos);
      if (this.cameraTarget.distanceTo(lookAt) > SNAP_DISTANCE_M) {
        this.cameraTarget.copy(lookAt);
      } else {
        this.cameraTarget.lerp(lookAt, lag);
      }
      this.camera.lookAt(this.cameraTarget);
    }
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

      if (frameMs > LONG_FRAME_MS) {
        if (++this.longFrames >= 3 && this.qualityTier < 2) {
          this.qualityTier = (this.qualityTier + 1) as 0 | 1 | 2;
          this.resize();
        }
      } else {
        this.longFrames = 0;
      }

      const pose = this.poseSource.getLatestPose();
      if (pose) {
        this.updateCars(pose.floats, dtWall);
        this.updateCamera(pose.floats, dtWall);
      }
      this.renderer.render(this.scene, this.camera);

      this.perfSampleAcc += dtWall;
      if (this.onPerfSample && this.perfSampleAcc >= 0.5) {
        this.perfSampleAcc = 0;
        this.onPerfSample({ fps: Math.round(this.fpsEma), frameMs, qualityTier: this.qualityTier });
      }
    };
    this.rafId = requestAnimationFrame(frame);
  }

  dispose() {
    this.alive = false;
    cancelAnimationFrame(this.rafId);
    this.canvas.removeEventListener("pointerdown", this.onPointerDown);
    window.removeEventListener("pointermove", this.onPointerMove);
    window.removeEventListener("pointerup", this.onPointerUp);
    this.canvas.removeEventListener("wheel", this.onWheel);
    this.scene.traverse((obj) => {
      if (obj instanceof THREE.Mesh || obj instanceof THREE.LineSegments) {
        obj.geometry.dispose();
        const mat = obj.material;
        if (Array.isArray(mat)) mat.forEach((m) => m.dispose()); else mat.dispose();
      }
    });
    this.renderer.dispose();
  }
}

export function lowEndHint(): boolean {
  return LOW_END(window.innerWidth, window.innerHeight);
}
