/**
 * Decoder for the replay sample blob written by scripts/simdata/replay.py.
 *
 * Record layout (little-endian, 12 bytes), mirrored exactly from the Python
 * SAMPLE_STRUCT "<HfhHBB":
 *   u16 dtMs        milliseconds since the previous sample (0 for the first sample)
 *   f32 stationM    arc-length position on the track ring, or NaN for ABSENT
 *   i16 lateralCm   signed lateral offset from the centreline, centimetres, or
 *                   LATERAL_ABSENT_CM (INT16_MIN) for ABSENT
 *   u16 speedKph    whole km/h
 *   u8  gearAndBrake  bits 0-6 = gear, bit 7 = brake
 *   u8  throttlePct   0-255 (raw feed occasionally exceeds 100)
 *
 * Decoding produces one Float32Array per channel (a struct-of-arrays), so the
 * runtime can binary-search/interpolate without touching a DataView per frame.
 */

export const SAMPLE_BYTES = 12;

/**
 * The encoder's explicit "this sample has NO position" marker, mirrored from
 * scripts/simdata/replay.py (LATERAL_ABSENT_CM). The encodable lateral range is
 * +-32767 cm, so INT16_MIN can never be a real measurement; replay.py writes it
 * together with stationM = NaN, and the session manifest states the pair in
 * positionIntegrity.absentLateralCm / absentStationM.
 *
 * Decoding it as a number is not a rounding detail: -32768 cm reads as -327.68 m,
 * i.e. the car drawn a third of a kilometre off the centreline -- the exact defect
 * the producer stopped emitting when it replaced `np.clip(lateral, -327, 327)` with
 * an explicit absence. Measured on a freshly built Monaco 2026 Race pack through
 * ReplayTimeline: 440 car-instants at 1 s sampling carried |lateral| > 300 m, worst
 * -327.67999 m (HAD, t=1 s), plus fabricated in-between values such as -181.72 m
 * where sampleLap linearly blended the marker against a real neighbour. Both become
 * NaN here, which is what "no position" means everywhere else in this pipeline.
 */
export const LATERAL_ABSENT_CM = -32768;

/** Same numbers, same justification, as lapSamples.ts's own STEP_TOLERANCE_* (not
 * imported from there to avoid a circular import -- lapSamples.ts already imports
 * DecodedLap from this file). Change only alongside that file's, with a re-measurement:
 * see its comment for what these mean and where they came from. */
const STEP_TOLERANCE_M = 20;
const STEP_TOLERANCE_REL = 2;
const STEP_TOLERANCE_BIAS_M = 10;

export interface DecodedLap {
  n: number;
  /** Absolute lap-relative time, seconds, reconstructed by cumulative-summing dtMs. */
  tS: Float32Array;
  stationM: Float32Array;
  lateralM: Float32Array;
  speedKph: Float32Array;
  gear: Uint8Array;
  brake: Uint8Array;
  throttlePct: Float32Array;
}

export function decodeLap(buf: ArrayBuffer, byteOffset: number, sampleCount: number): DecodedLap {
  const view = new DataView(buf, byteOffset, sampleCount * SAMPLE_BYTES);
  const tS = new Float32Array(sampleCount);
  const stationM = new Float32Array(sampleCount);
  const lateralM = new Float32Array(sampleCount);
  const speedKph = new Float32Array(sampleCount);
  const gear = new Uint8Array(sampleCount);
  const brake = new Uint8Array(sampleCount);
  const throttlePct = new Float32Array(sampleCount);

  let acc = 0;
  for (let i = 0; i < sampleCount; i++) {
    const o = i * SAMPLE_BYTES;
    const dtMs = view.getUint16(o, true);
    acc += dtMs;
    tS[i] = acc / 1000;
    stationM[i] = view.getFloat32(o + 2, true);
    const latCm = view.getInt16(o + 6, true);
    // ABSENT is absent, never a number: see LATERAL_ABSENT_CM. NaN then propagates
    // through sampleLap's interpolation on its own, so an absent sample can neither
    // be drawn nor blended into its neighbours.
    lateralM[i] = latCm === LATERAL_ABSENT_CM ? NaN : latCm / 100;
    speedKph[i] = view.getUint16(o + 8, true);
    const gb = view.getUint8(o + 10);
    gear[i] = gb & 0x7f;
    brake[i] = (gb >> 7) & 1;
    throttlePct[i] = view.getUint8(o + 11);
  }
  return { n: sampleCount, tS, stationM, lateralM, speedKph, gear, brake, throttlePct };
}

/** Linear interpolation of a decoded lap's channels at lap-relative time t (seconds).
 * Returns null if t is outside [tS[0], tS[n-1]]. Station interpolation wraps modulo
 * `trackLength` so it never jumps the long way round the ring near the start/finish
 * line, and lateral is interpolated linearly (never wrapped). */
export function sampleLap(lap: DecodedLap, t: number, trackLength: number) {
  const { tS, n } = lap;
  if (n === 0) return null;
  if (t <= tS[0]) return lapSampleAt(lap, 0);
  if (t >= tS[n - 1]) return lapSampleAt(lap, n - 1);

  // binary search for the bracketing pair
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (tS[mid] <= t) lo = mid; else hi = mid;
  }
  const t0 = tS[lo], t1 = tS[hi];
  const f = t1 > t0 ? (t - t0) / (t1 - t0) : 0;

  const s0 = lap.stationM[lo], s1 = lap.stationM[hi];
  let d = s1 - s0;
  if (d > trackLength / 2) d -= trackLength;
  if (d < -trackLength / 2) d += trackLength;

  // The same "credible step" test alongLapDistance.ts applies when judging a whole lap,
  // applied here at query time: a raw step whose distance disagrees wildly with what the
  // recorded speed channel says happened over the same interval is not a real position
  // change. Measured live, 2026 Australian GP Race: two adjacent decoded samples 0.30 s
  // apart landed 50 m apart (implied ~600 kph -- faster than any 2026 car reaches), which
  // an untested linear blend drew as the car sweeping backward then teleporting forward.
  // An incredible step holds at s0 rather than guessing a "corrected" distance, which is
  // the same choice the absence contract above already makes for a missing sample -- NaN
  // propagates through this exact arithmetic unaffected, since NaN > tol is false.
  const dtS = t1 - t0;
  if (dtS > 0) {
    const expected = ((lap.speedKph[lo] + lap.speedKph[hi]) / 2 / 3.6) * dtS;
    const tol = Math.max(STEP_TOLERANCE_M, STEP_TOLERANCE_REL * expected + STEP_TOLERANCE_BIAS_M);
    if (Math.abs(d - expected) > tol) d = 0;
  }

  let station = s0 + d * f;
  station = ((station % trackLength) + trackLength) % trackLength;

  return {
    stationM: station,
    lateralM: lap.lateralM[lo] + (lap.lateralM[hi] - lap.lateralM[lo]) * f,
    speedKph: lap.speedKph[lo] + (lap.speedKph[hi] - lap.speedKph[lo]) * f,
    gear: f < 0.5 ? lap.gear[lo] : lap.gear[hi],
    brake: f < 0.5 ? lap.brake[lo] : lap.brake[hi],
    throttlePct: lap.throttlePct[lo] + (lap.throttlePct[hi] - lap.throttlePct[lo]) * f,
  };
}

function lapSampleAt(lap: DecodedLap, i: number) {
  return {
    stationM: lap.stationM[i],
    lateralM: lap.lateralM[i],
    speedKph: lap.speedKph[i],
    gear: lap.gear[i],
    brake: lap.brake[i],
    throttlePct: lap.throttlePct[i],
  };
}
