/**
 * Message protocol between the main thread and sim.worker.ts. Pose packets are
 * transferred (not copied): the worker gives up ownership of the buffer, the main
 * thread reads it and (in the real render loop) transfers a scratch buffer back next
 * tick so the worker never allocates in its hot path.
 *
 * Pose layout, one record per car, Float32Array, POSE_FLOATS_PER_CAR floats each:
 *   [0] stationM  [1] lateralM  [2] elevationM  [3] headingRad  [4] speedKph
 *   [5] gear      [6] throttlePct  [7] brake(0/1)  [8] lapsDone  [9] lapProgress
 *   [10] position [11] driverIndex (index into the driver list sent once at Init)
 *   [12] status   (POSE_STATUS_*): the renderer must know a car is on the grid, in
 *                 the pit lane or parked, because those are placed deliberately and
 *                 must not be shoved sideways by the on-track declutter pass.
 */
export const POSE_FLOATS_PER_CAR = 13;

export const POSE_STATUS = {
  grid: 0, track: 1, pit: 2, finished: 3, retired: 4, gap: 5,
} as const;

export type PoseStatusName = keyof typeof POSE_STATUS;

export function poseStatusCode(status: PoseStatusName): number {
  return POSE_STATUS[status];
}

export interface GeneratedRaceRequest {
  trackUrl: string;
  paramsUrl: string;
  entries: { driver: string; team: string | null }[];
  totalLaps: number;
  seed: number;
}

export type MainToWorker =
  | { type: "init"; trackUrl: string; manifestUrl: string; binUrl: string }
  | { type: "initGenerated"; request: GeneratedRaceRequest }
  | { type: "play" }
  | { type: "pause" }
  | { type: "seek"; sessionTime: number }
  | { type: "setSpeed"; multiplier: number };

export type WorkerToMain =
  | { type: "ready"; driverList: string[]; totalLaps: number | null; duration: number; clockOffsetS: number }
  | { type: "meta"; events: unknown[]; neutralisations: unknown[]; weather: unknown }
  | { type: "pose"; sessionTime: number; buffer: ArrayBuffer; carCount: number }
  | { type: "dashboard"; snapshot: unknown }
  /** Authoritative playback state. The worker owns it; the UI mirrors it, so the
   * two can never disagree about whether the race is running. */
  | { type: "playback"; playing: boolean; atEnd: boolean }
  | { type: "error"; message: string };
