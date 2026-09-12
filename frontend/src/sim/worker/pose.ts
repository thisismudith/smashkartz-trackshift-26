import type { CarState } from "../contract/types";
import { POSE_FLOATS_PER_CAR } from "./protocol";

/** Pack a frame's car states into one flat Float32Array, in a fixed driver order (so
 * the main thread can index by position without re-parsing driver codes every frame).
 * Pure and allocation-light: pass a reusable `out` buffer to avoid allocating per tick.
 */
export function packPose(
  states: Map<string, CarState>, driverOrder: string[], out?: Float32Array,
): Float32Array {
  const buf = out && out.length === driverOrder.length * POSE_FLOATS_PER_CAR
    ? out
    : new Float32Array(driverOrder.length * POSE_FLOATS_PER_CAR);
  driverOrder.forEach((driver, i) => {
    const s = states.get(driver);
    const o = i * POSE_FLOATS_PER_CAR;
    if (!s) {
      buf.fill(0, o, o + POSE_FLOATS_PER_CAR);
      return;
    }
    buf[o + 0] = s.stationM;
    buf[o + 1] = s.lateralM;
    buf[o + 2] = s.elevationM;
    buf[o + 3] = s.headingRad;
    buf[o + 4] = s.speedKph;
    buf[o + 5] = s.gear;
    buf[o + 6] = s.throttlePct;
    buf[o + 7] = s.brake ? 1 : 0;
    buf[o + 8] = s.lapsDone;
    buf[o + 9] = s.lapProgress;
    buf[o + 10] = s.position;
    buf[o + 11] = i;
  });
  return buf;
}

export interface UnpackedPose {
  stationM: number; lateralM: number; elevationM: number; headingRad: number;
  speedKph: number; gear: number; throttlePct: number; brake: boolean;
  lapsDone: number; lapProgress: number; position: number;
}

export function unpackPoseAt(buf: Float32Array | ArrayBuffer, carIndex: number): UnpackedPose {
  const f = buf instanceof Float32Array ? buf : new Float32Array(buf);
  const o = carIndex * POSE_FLOATS_PER_CAR;
  return {
    stationM: f[o + 0], lateralM: f[o + 1], elevationM: f[o + 2], headingRad: f[o + 3],
    speedKph: f[o + 4], gear: f[o + 5], throttlePct: f[o + 6], brake: f[o + 7] === 1,
    lapsDone: f[o + 8], lapProgress: f[o + 9], position: f[o + 10],
  };
}
