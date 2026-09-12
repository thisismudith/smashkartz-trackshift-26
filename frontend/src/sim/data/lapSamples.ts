/**
 * The session sample blob: fetched once, decoded per lap, resampled onto the ring.
 *
 * Three jobs live here, and none of them belongs in a React component.
 *
 * 1. ONE fetch per session. The blob is 3-10 MB and the telemetry view changes driver and
 *    lap constantly; re-fetching per change would re-download the race to redraw one line.
 *    The cache is keyed by URL and stores the PROMISE, so two panels mounting in the same
 *    tick share a single request rather than racing two.
 *
 * 2. Turning the ring station channel into a monotone distance-along-lap axis. The encoder
 *    writes station modulo the ring length, so a lap that starts 10 m before the line reads
 *    5815 m, 15 m, 27 m ... -- plotted raw that is a trace which jumps backwards at the
 *    start. Unwrapping is the whole reason this file exists rather than a `.map()` in the view.
 *
 * 3. Saying when that axis cannot be trusted. UI.md section 1.4 records three circuits with
 *    measured position faults, and Hungary's is the dangerous one: the frame tag still says
 *    "A" (projected from measured x/y) while the underlying coordinates are a stale-anchor
 *    sample-and-hold, so the station channel teleports. Every station step is therefore
 *    checked against the distance the SPEED channel says the car covered, and the
 *    disagreements are counted and reported instead of being drawn as if they happened.
 *    Measured over the shipped 2026 packs, per clean lap, as a fraction of positioned steps:
 *    Silverstone max 0.0041, Suzuka max 0.0124, Monaco max 0.0088, Spa max 0.0108; Hungary
 *    median 0.0577, with 82.2% of its clean laps above 0.02. The limit below sits in that gap.
 *
 * Never uses the telemetry `distance` channel for any of this: it is per-driver integrated
 * wheel speed and disagrees between cars at the same circuit by up to 4.14% (UI.md 1.4).
 * The ring is the coordinate system.
 */

import { decodeLap, SAMPLE_BYTES, type DecodedLap } from "./codec";
import type { RawLapEntry } from "./manifest";

/* ------------------------------------------------------------------ blob --- */

const blobs = new Map<string, Promise<ArrayBuffer>>();

/**
 * The session's sample blob, fetched at most once per URL.
 *
 * A rejected fetch is evicted, so a transient network failure does not poison the session for
 * the life of the tab -- the next call retries instead of re-throwing a stale error.
 */
export function loadSessionSamples(binUrl: string): Promise<ArrayBuffer> {
  const hit = blobs.get(binUrl);
  if (hit) return hit;
  const p = fetch(binUrl)
    .then((r) => {
      if (!r.ok) throw new Error(`sample blob ${binUrl} responded ${r.status}`);
      return r.arrayBuffer();
    })
    .catch((e: unknown) => {
      blobs.delete(binUrl);
      throw e;
    });
  blobs.set(binUrl, p);
  return p;
}

/** Drops cached blobs. Exported for tests and for a caller that wants the memory back. */
export function clearSessionSampleCache(url?: string) {
  if (url === undefined) blobs.clear();
  else blobs.delete(url);
}

/**
 * One lap out of the blob.
 *
 * The bounds check is not defensive noise: a manifest and a blob are two separate artifacts
 * that can go out of step across a rebuild, and DataView's own RangeError names neither the
 * lap nor the file. Failing with the lap number is the difference between a five-minute and a
 * five-second diagnosis.
 */
export function sampleLapFromBuffer(buf: ArrayBuffer, entry: RawLapEntry): DecodedLap {
  if (entry.sampleCount <= 0) throw new Error(`lap ${entry.lap} carries no samples`);
  const need = entry.sampleCount * SAMPLE_BYTES;
  if (entry.byteOffset < 0 || entry.byteOffset + need > buf.byteLength) {
    throw new Error(
      `lap ${entry.lap} wants bytes ${entry.byteOffset}..${entry.byteOffset + need} of a ` +
        `${buf.byteLength}-byte blob — manifest and blob disagree`,
    );
  }
  return decodeLap(buf, entry.byteOffset, entry.sampleCount);
}

/* ------------------------------------------------- distance along the lap --- */

/**
 * A station step is "not credible" when it differs from the distance the speed channel says
 * the car covered by more than this. The absolute floor covers the low-speed case (expected
 * distance near zero, where a relative test means nothing); the relative term keeps an
 * ordinary flat-out straight from tripping on projection noise.
 *
 * These exact constants produced the separation quoted in the file header. They decide which
 * laps this view is willing to draw, so change them only with a re-measurement.
 */
const STEP_TOLERANCE_M = 20;
const STEP_TOLERANCE_REL = 2;
const STEP_TOLERANCE_BIAS_M = 10;

/** Above this fraction of positioned steps, the station axis is not a usable x axis. */
export const STATION_ANOMALY_LIMIT = 0.02;

export interface AlongLap {
  /** Ring station of the lap's first positioned sample, metres. NaN if it has none. */
  station0: number;
  /** Distance travelled since that sample, metres. NaN where the sample has no position. */
  distanceM: Float32Array;
  /** 0 where the step INTO this sample disagreed with the integrated speed. */
  credible: Uint8Array;
  anomalies: number;
  positioned: number;
  /** Samples the encoder marked ABSENT (no position at all). */
  absent: number;
  /** Distance at the furthest positioned sample: how much of the ring this lap covers. */
  coveredM: number;
  /** anomalies / positioned steps, or null when there were no steps to judge. */
  anomalyFraction: number | null;
}

/** Shortest signed distance from a to b around a ring of length L. */
function ringDelta(a: number, b: number, L: number): number {
  let d = (b - a) % L;
  if (d > L / 2) d -= L;
  if (d < -L / 2) d += L;
  return d;
}

/**
 * Unwraps the modulo-L station channel into distance travelled since the lap's first sample.
 *
 * Steps run between consecutive POSITIONED samples, so an absent run is bridged rather than
 * restarting the accumulator -- the car did keep moving, and the speed channel is still there.
 * That bridge is where aliasing is most likely, which is why the same credibility test covers it.
 */
export function alongLapDistance(lap: DecodedLap, trackLengthM: number): AlongLap {
  const n = lap.n;
  const distanceM = new Float32Array(n).fill(NaN);
  const credible = new Uint8Array(n).fill(1);
  let station0 = NaN;
  let acc = 0;
  let covered = 0;
  let prev = -1;
  let positioned = 0;
  let absent = 0;
  let anomalies = 0;
  let steps = 0;

  for (let i = 0; i < n; i++) {
    const s = lap.stationM[i];
    if (!Number.isFinite(s)) {
      absent++;
      continue;
    }
    positioned++;
    if (prev < 0) {
      station0 = s;
      distanceM[i] = 0;
      prev = i;
      continue;
    }
    const d = ringDelta(lap.stationM[prev], s, trackLengthM);
    // what the speed channel says happened over the same interval
    const dtS = lap.tS[i] - lap.tS[prev];
    const expected = ((lap.speedKph[i] + lap.speedKph[prev]) / 2 / 3.6) * dtS;
    const tol = Math.max(STEP_TOLERANCE_M, STEP_TOLERANCE_REL * expected + STEP_TOLERANCE_BIAS_M);
    steps++;
    if (Math.abs(d - expected) > tol) {
      credible[i] = 0;
      anomalies++;
    }
    acc += d;
    distanceM[i] = acc;
    if (acc > covered) covered = acc;
    prev = i;
  }

  return {
    station0,
    distanceM,
    credible,
    anomalies,
    positioned,
    absent,
    coveredM: covered,
    anomalyFraction: steps > 0 ? anomalies / steps : null,
  };
}

/** Ascending ring stations, one every ~`stepM` metres, covering [0, trackLengthM). */
export function stationGrid(trackLengthM: number, stepM = 5): Float32Array {
  const n = Math.max(2, Math.ceil(trackLengthM / stepM));
  const grid = new Float32Array(n);
  for (let i = 0; i < n; i++) grid[i] = (i * trackLengthM) / n;
  return grid;
}

/* -------------------------------------------------------------- resample --- */

export interface ResampledLap {
  /** km/h, linearly interpolated. NaN where the lap has no credible measurement here. */
  speedKph: Float32Array;
  /** percent; the raw feed occasionally exceeds 100 (measured max 104) and is not clamped. */
  throttlePct: Float32Array;
  /** 1 on, 0 off, NaN unknown. Held from the preceding sample -- a brake is not interpolated. */
  brake: Float32Array;
  /** Held from the preceding sample. NaN unknown. A gear is a state, never an average. */
  gear: Float32Array;
  /** Elapsed lap time at this station, seconds since the lap's first sample. */
  elapsedS: Float32Array;
  /** How many grid stations this lap actually produced a value for. */
  covered: number;
}

/**
 * Puts one lap on a shared ring-station grid.
 *
 * Absent and not-credible samples are holes, not values: a grid station whose bracketing pair
 * includes an incredible step reads NaN, so the line breaks there rather than drawing a
 * confident segment across a position the data does not support.
 *
 * The scan is forward over both arrays (the grid ascends; so does the lap's distance axis once
 * unwrapped), which is also what makes it tolerant of the sub-metre backward jitter real
 * projections contain -- a binary search over an assumed-sorted array is not.
 */
export function resampleByStation(
  lap: DecodedLap,
  grid: Float32Array,
  trackLengthM: number,
  along: AlongLap = alongLapDistance(lap, trackLengthM),
): ResampledLap {
  const m = grid.length;
  const speedKph = new Float32Array(m).fill(NaN);
  const throttlePct = new Float32Array(m).fill(NaN);
  const brake = new Float32Array(m).fill(NaN);
  const gear = new Float32Array(m).fill(NaN);
  const elapsedS = new Float32Array(m).fill(NaN);
  const empty = { speedKph, throttlePct, brake, gear, elapsedS, covered: 0 };

  // indices of positioned samples, in distance order
  const idx: number[] = [];
  for (let i = 0; i < lap.n; i++) if (Number.isFinite(along.distanceM[i])) idx.push(i);
  if (idx.length < 2 || !Number.isFinite(along.station0)) return empty;

  // Each grid station re-expressed as progress since THIS lap's start, then visited in that
  // order so one forward pointer serves the whole grid across the start/finish wrap.
  const progress = new Float32Array(m);
  const order: number[] = [];
  for (let g = 0; g < m; g++) {
    progress[g] = (((grid[g] - along.station0) % trackLengthM) + trackLengthM) % trackLengthM;
    order.push(g);
  }
  order.sort((a, b) => progress[a] - progress[b]);

  let j = 0;
  let covered = 0;
  for (const g of order) {
    const p = progress[g];
    if (p > along.coveredM) continue; // the lap never reached this part of the ring
    while (j + 1 < idx.length - 1 && along.distanceM[idx[j + 1]] <= p) j++;
    const a = idx[j];
    const b = idx[j + 1];
    const d0 = along.distanceM[a];
    const d1 = along.distanceM[b];
    if (p < d0) continue; // before the first sample: nothing to interpolate from
    if (!along.credible[b]) continue; // a step the speed channel contradicts is a hole
    const span = d1 - d0;
    const f = span > 0 ? Math.min(1, Math.max(0, (p - d0) / span)) : 0;
    speedKph[g] = lap.speedKph[a] + (lap.speedKph[b] - lap.speedKph[a]) * f;
    throttlePct[g] = lap.throttlePct[a] + (lap.throttlePct[b] - lap.throttlePct[a]) * f;
    elapsedS[g] = lap.tS[a] + (lap.tS[b] - lap.tS[a]) * f;
    // state channels hold: blending gear 7 and gear 2 into 4.5 invents a gear
    brake[g] = lap.brake[a];
    gear[g] = lap.gear[a];
    covered++;
  }

  return { speedKph, throttlePct, brake, gear, elapsedS, covered };
}

/* ----------------------------------------------------------------- delta --- */

/**
 * Cumulative time delta between two laps, station by station.
 *
 * CONVENTION: positive means A is BEHIND -- A took longer to get from the reference station to
 * this one. The reference is the first grid station both laps cover, and the series is zeroed
 * there, because the two laps' first samples sit a few metres apart on the ring and reading
 * that offset as a gap would open the trace on a number nobody drove.
 *
 * NaN wherever either lap has no credible measurement, so a gap in the delta is a gap in the
 * evidence rather than a flat line held at the last known value.
 */
export function cumulativeDelta(
  a: DecodedLap,
  b: DecodedLap,
  grid: Float32Array,
  trackLengthM: number,
  alongA: AlongLap = alongLapDistance(a, trackLengthM),
  alongB: AlongLap = alongLapDistance(b, trackLengthM),
): Float32Array {
  const ra = resampleByStation(a, grid, trackLengthM, alongA);
  const rb = resampleByStation(b, grid, trackLengthM, alongB);
  const out = new Float32Array(grid.length).fill(NaN);

  let zero: number | null = null;
  for (let g = 0; g < grid.length; g++) {
    const ta = ra.elapsedS[g];
    const tb = rb.elapsedS[g];
    if (!Number.isFinite(ta) || !Number.isFinite(tb)) continue;
    const d = ta - tb;
    if (zero === null) zero = d;
    out[g] = d - zero;
  }
  return out;
}
