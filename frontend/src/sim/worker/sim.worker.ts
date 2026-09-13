/// <reference lib="webworker" />
/**
 * Owns the race clock. Loads the track model and a replay pack, then ticks a fixed-
 * step accumulator (mirroring the intro loader's runtime.ts pattern) and posts one
 * transferable pose buffer per tick plus a ~10 Hz dashboard snapshot. The main thread
 * never computes race state -- it only reads the pose buffer into render state.
 */
import type { RaceTimeline } from "../contract/types";
import { parseTrackModel, type RawSessionManifest, type RawTrackModel } from "../data/manifest";
import { GeneratedTimeline } from "../engine/generatedTimeline";
import type { FittedParams } from "../engine/params";
import { runRace } from "../engine/raceEngine";
import { buildDashboardSnapshot } from "../replay/dashboard";
import { ReplayTimeline } from "../replay/timeline";
import { packPose } from "./pose";
import { POSE_FLOATS_PER_CAR } from "./protocol";
import type { GeneratedRaceRequest, MainToWorker, WorkerToMain } from "./protocol";

const ctx = self as unknown as DedicatedWorkerGlobalScope;

const TICK_HZ = 60;
const TICK_S = 1 / TICK_HZ;
const DASHBOARD_HZ = 10;
const MAX_TIME_SCALE = 100;

let timeline: RaceTimeline | null = null;
let driverOrder: string[] = [];
let sessionTime = 0;
let playing = false;

/** Every playback change goes through here so the main thread is always told. */
function setPlaying(next: boolean, atEnd = false) {
  playing = next;
  post({ type: "playback", playing, atEnd });
}
let speedMultiplier = 1;
let lastTickAt = 0;
let intervalId: ReturnType<typeof setInterval> | null = null;
let dashboardAcc = 0;
let poseScratch: Float32Array | null = null;

function post(msg: WorkerToMain, transfer?: Transferable[]) {
  if (transfer) ctx.postMessage(msg, transfer);
  else ctx.postMessage(msg);
}

function afterTimelineReady() {
  if (!timeline) return;
  driverOrder = timeline.driverList;
  // MUST be POSE_FLOATS_PER_CAR, not a literal. This read 12 while the layout is 13
  // floats wide, and packPose only reuses a buffer whose length matches exactly -- so
  // the scratch was rejected and a fresh Float32Array was allocated on EVERY tick, 60
  // times a second, which is the one thing the protocol's own docstring says this
  // buffer exists to avoid. Nothing was corrupted, so nothing looked wrong.
  poseScratch = new Float32Array(driverOrder.length * POSE_FLOATS_PER_CAR);
  sessionTime = 0;
  // a fresh timeline always starts paused; announce it, or the UI keeps showing
  // "Pause" from the previous session and its next click looks like a dead button
  setPlaying(false);
  post({ type: "ready", driverList: driverOrder, totalLaps: timeline.totalLaps, duration: timeline.duration, clockOffsetS: timeline.clockOffsetS ?? 0 });
  post({
    type: "meta",
    events: timeline.events(),
    neutralisations: timeline.neutralisations(),
    weather: timeline.weather(),
  });
  startLoop();
}

async function init(trackUrl: string, manifestUrl: string, binUrl: string) {
  try {
    const [trackRaw, manifest, binResp] = await Promise.all([
      fetch(trackUrl).then((r) => r.json() as Promise<RawTrackModel>),
      fetch(manifestUrl).then((r) => r.json() as Promise<RawSessionManifest>),
      fetch(binUrl).then((r) => r.arrayBuffer()),
    ]);
    const track = parseTrackModel(trackRaw);
    timeline = new ReplayTimeline(manifest, track, binResp);
    afterTimelineReady();
  } catch (err) {
    post({ type: "error", message: err instanceof Error ? err.message : String(err) });
  }
}

/** Builds a New Race timeline entirely inside the worker: the engine runs here, not
 * on the main thread, so it shares the exact same tick/pose/dashboard pipeline as
 * replay -- proving the plan's "one renderer, one dashboard, two sources" contract
 * for real rather than by two parallel code paths. */
async function initGenerated(request: GeneratedRaceRequest) {
  try {
    const [trackRaw, params] = await Promise.all([
      fetch(request.trackUrl).then((r) => r.json() as Promise<RawTrackModel>),
      fetch(request.paramsUrl).then((r) => r.json() as Promise<FittedParams>),
    ]);
    const track = parseTrackModel(trackRaw);
    const result = runRace({
      track, params, entries: request.entries, totalLaps: request.totalLaps, seed: request.seed,
    });
    timeline = new GeneratedTimeline(result, track);
    afterTimelineReady();
  } catch (err) {
    post({ type: "error", message: err instanceof Error ? err.message : String(err) });
  }
}

function startLoop() {
  // Re-initialising (a fresh New Race seed, or switching sessions) must replace any
  // running loop rather than leaving it a no-op: two intervals ticking the same
  // globals would double-post poses against whichever timeline lost the race.
  if (intervalId !== null) clearInterval(intervalId);
  lastTickAt = performance.now();
  intervalId = setInterval(tick, TICK_S * 1000);
}

function tick() {
  if (!timeline) return;
  const now = performance.now();
  const dtWall = Math.min((now - lastTickAt) / 1000, 0.25); // clamp a stalled tick
  lastTickAt = now;

  if (playing) {
    const next = sessionTime + dtWall * speedMultiplier;
    if (next >= timeline.duration) {
      // Reaching the flag used to clamp the clock while still calling itself "playing",
      // so the race silently froze with a Pause button that did nothing.
      sessionTime = timeline.duration;
      setPlaying(false, true);
    } else {
      sessionTime = Math.max(0, next);
    }
  }

  // Decide up front whether this tick feeds the dashboard; the expensive gap search
  // is then done once every sixth tick instead of every one.
  const buildsDashboard = dashboardAcc + dtWall >= 1 / DASHBOARD_HZ;
  const states = timeline.sampleAt(sessionTime, buildsDashboard);
  poseScratch = packPose(states, driverOrder, poseScratch ?? undefined);
  // a fresh copy is transferred each tick (the scratch buffer is rebuilt next tick);
  // this keeps the worker's own reference stable without an explicit ping-pong pair,
  // at the cost of one allocation per tick -- documented as a v1 simplification of
  // the plan's double-buffer scheme.
  const toSend = poseScratch.slice();
  post(
    { type: "pose", sessionTime, buffer: toSend.buffer, carCount: driverOrder.length },
    [toSend.buffer],
  );

  dashboardAcc += dtWall;
  // same flag the sampling decision used, so the two can never disagree and emit a
  // dashboard built from a states map that had its gap search skipped
  if (buildsDashboard) {
    dashboardAcc = 0;
    const snapshot = buildDashboardSnapshot(
      states, sessionTime, timeline.events(), timeline.neutralisations(),
    );
    post({ type: "dashboard", snapshot });
  }
}

ctx.onmessage = (ev: MessageEvent<MainToWorker>) => {
  const msg = ev.data;
  switch (msg.type) {
    case "init":
      void init(msg.trackUrl, msg.manifestUrl, msg.binUrl);
      break;
    case "initGenerated":
      void initGenerated(msg.request);
      break;
    case "play":
      // replaying from the flag restarts rather than sitting stuck at the end
      if (timeline && sessionTime >= timeline.duration) sessionTime = 0;
      setPlaying(true);
      break;
    case "pause":
      setPlaying(false);
      break;
    case "seek":
      if (timeline) sessionTime = Math.max(0, Math.min(timeline.duration, msg.sessionTime));
      break;
    case "setSpeed":
      speedMultiplier = Math.max(0, Math.min(MAX_TIME_SCALE, msg.multiplier));
      break;
  }
};
