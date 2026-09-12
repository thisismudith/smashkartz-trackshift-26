/**
 * The one external store for the sim UI. Two very different update rates share it
 * deliberately: the 60 Hz pose buffer is read IMPERATIVELY by the renderer's own
 * animation loop (never through React state, so it never triggers a re-render), while
 * the ~10 Hz dashboard snapshot is exposed through subscribe/getSnapshot for
 * `useSyncExternalStore`, which is the only thing that wakes React up.
 */
"use client";

import type { DashboardSnapshot, NeutralisationInterval, RaceEvent, WeatherSeries } from "../contract/types";
import type { GeneratedRaceRequest, MainToWorker, WorkerToMain } from "../worker/protocol";

export interface PoseFrame {
  floats: Float32Array;
  sessionTime: number;
  carCount: number;
  arrivedMs: number;
}

export interface SimStoreState {
  ready: boolean;
  driverList: string[];
  totalLaps: number | null;
  duration: number;
  error: string | null;
  playing: boolean;
  atEnd: boolean;
}

export interface SimMeta {
  events: RaceEvent[];
  neutralisations: NeutralisationInterval[];
  weather: WeatherSeries | null;
}

export class SimStore {
  private worker: Worker | null = null;
  private listeners = new Set<() => void>();
  private state: SimStoreState = {
    ready: false, driverList: [], totalLaps: null, duration: 0, error: null,
    playing: false, atEnd: false,
  };
  private dashboard: DashboardSnapshot | null = null;
  private meta: SimMeta | null = null;
  private latestPose: PoseFrame | null = null;
  private prevPose: PoseFrame | null = null;

  private ensureWorker() {
    if (this.worker) return;
    this.worker = new Worker(new URL("../worker/sim.worker.ts", import.meta.url), { type: "module" });
    this.worker.onmessage = (ev: MessageEvent<WorkerToMain>) => this.handleMessage(ev.data);
  }

  start(trackUrl: string, manifestUrl: string, binUrl: string) {
    this.ensureWorker();
    this.send({ type: "init", trackUrl, manifestUrl, binUrl });
  }

  /** New Race: the SAME worker builds and ticks a GeneratedTimeline instead of a
   * ReplayTimeline (see sim.worker.ts's initGenerated). Callable again on the same
   * store to "re-roll" a new seed without tearing down the renderer. */
  startGenerated(request: GeneratedRaceRequest) {
    this.ensureWorker();
    this.state = {
      ready: false, driverList: [], totalLaps: null, duration: 0, error: null,
      playing: false, atEnd: false,
    };
    this.dashboard = null;
    this.meta = null;
    this.emit();
    this.send({ type: "initGenerated", request });
  }

  dispose() {
    this.worker?.terminate();
    this.worker = null;
    this.listeners.clear();
  }

  private send(msg: MainToWorker) {
    this.worker?.postMessage(msg);
  }

  play() { this.send({ type: "play" }); }
  pause() { this.send({ type: "pause" }); }
  seek(sessionTime: number) { this.send({ type: "seek", sessionTime }); }
  setSpeed(multiplier: number) { this.send({ type: "setSpeed", multiplier }); }

  private handleMessage(msg: WorkerToMain) {
    switch (msg.type) {
      case "ready":
        this.state = {
          ...this.state,
          ready: true, driverList: msg.driverList, totalLaps: msg.totalLaps,
          duration: msg.duration, error: null,
        };
        this.emit();
        break;
      case "meta":
        this.meta = {
          events: msg.events as RaceEvent[],
          neutralisations: msg.neutralisations as NeutralisationInterval[],
          weather: msg.weather as WeatherSeries | null,
        };
        this.emit();
        break;
      case "pose": {
        // wrapped ONCE here, on message arrival, rather than once per render frame:
        // the renderer's own rAF loop can run at a different rate than pose messages
        // arrive, so re-wrapping in the render loop would allocate a throwaway
        // Float32Array up to 60 times a second for no reason.
        // Two frames are kept, not one: the sim ticks at a fixed 60 Hz while the
        // display may refresh at 165. Holding the previous frame lets the renderer
        // interpolate between them, so motion is smooth at any refresh rate instead
        // of stepping 60 times a second on a faster panel.
        this.prevPose = this.latestPose;
        this.latestPose = {
          floats: new Float32Array(msg.buffer),
          sessionTime: msg.sessionTime,
          carCount: msg.carCount,
          arrivedMs: performance.now(),
        };
        break; // deliberately does NOT call emit(): this must never trigger a React render
      }
      case "dashboard":
        this.dashboard = msg.snapshot as DashboardSnapshot;
        this.emit();
        break;
      case "playback":
        this.state = { ...this.state, playing: msg.playing, atEnd: msg.atEnd };
        this.emit();
        break;
      case "error":
        this.state = { ...this.state, error: msg.message };
        this.emit();
        break;
    }
  }

  private emit() {
    for (const l of this.listeners) l();
  }

  // ---- React-facing (useSyncExternalStore) ----
  subscribe = (cb: () => void) => {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  };
  getState = (): SimStoreState => this.state;
  getDashboard = (): DashboardSnapshot | null => this.dashboard;
  getMeta = (): SimMeta | null => this.meta;

  // ---- Renderer-facing (imperative, called from inside a rAF loop) ----
  getLatestPose() { return this.latestPose; }
  getPrevPose() { return this.prevPose; }
  getDriverList() { return this.state.driverList; }
}
