/**
 * Turns three sector times into a smooth speed-over-station profile for one lap, by
 * time-axis-warping the track's synthetic median speed profile (built in
 * scripts/simdata/track.py's reference_speed_profile, from many real laps -- never a
 * single fast lap). Measured in the plan: a pointwise-median spine plus a per-sector
 * warp reproduces a real lap within 5-6 km/h RMS, and three sector times cut drawn-
 * position error by 30-41% versus warping by lap time alone. The warp is a pure,
 * piecewise time-axis rescale -- exact on sector times, and cheap (no array rebuild).
 */
import type { TrackModel } from "../contract/types";

export interface RefTimeTable {
  /** Cumulative time (seconds) to reach each reference-profile bin, integrating
   * ds / v_ref(station); index i corresponds to station i * binMetres. Monotonic. */
  cumTime: Float32Array;
  binMetres: number;
  trackLength: number;
}

const MIN_SPEED_KPH = 30; // guards against a division by ~0 in a near-stationary bin

export function buildRefTimeTable(track: TrackModel): RefTimeTable {
  const { speedKph, binMetres } = track.referenceProfile;
  const n = speedKph.length;
  const cumTime = new Float32Array(n + 1);
  for (let i = 0; i < n; i++) {
    const kph = Math.max(MIN_SPEED_KPH, speedKph[i]);
    const mps = kph / 3.6;
    cumTime[i + 1] = cumTime[i] + binMetres / mps;
  }
  return { cumTime, binMetres, trackLength: track.lengthMetres };
}

/** Reference cumulative time at an arbitrary station (linear interpolation between
 * the table's bins, wrapping at the track length). */
export function refTimeAtStation(table: RefTimeTable, station: number): number {
  const n = table.cumTime.length - 1;
  const s = ((station % table.trackLength) + table.trackLength) % table.trackLength;
  const f = s / table.binMetres;
  const i0 = Math.min(n - 1, Math.floor(f));
  const i1 = Math.min(n, i0 + 1);
  const frac = f - i0;
  return table.cumTime[i0] + (table.cumTime[i1] - table.cumTime[i0]) * frac;
}

/** Inverts the (monotonic) cumulative-time table: given a target reference time,
 * find the station that reaches it. Binary search on the precomputed array. */
function stationAtRefTime(table: RefTimeTable, targetTime: number): number {
  const arr = table.cumTime;
  let lo = 0, hi = arr.length - 1;
  if (targetTime <= arr[0]) return 0;
  if (targetTime >= arr[hi]) return table.trackLength;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] <= targetTime) lo = mid; else hi = mid;
  }
  const frac = (targetTime - arr[lo]) / (arr[hi] - arr[lo] || 1);
  return (lo + frac) * table.binMetres;
}

export interface SectorTimes {
  s1: number; s2: number; s3: number; // seconds, s1+s2+s3 = lapTime
}

export interface LapWarp {
  /** Station (metres) and speed (km/h) at lap-relative time t (seconds), 0 <= t <= lapTime. */
  sampleAt(t: number): { stationM: number; speedKph: number; gear: number };
  lapTime: number;
}

/** Builds the piecewise warp for one lap. `s1Station`/`s2Station` are the track's own
 * sector-boundary stations (from the timing-line derivation in track.py). */
export function buildLapWarp(
  track: TrackModel, refTable: RefTimeTable, sectors: SectorTimes,
): LapWarp {
  const s1Station = track.timingLines.s1 ?? track.lengthMetres / 3;
  const s2Station = track.timingLines.s2 ?? (2 * track.lengthMetres) / 3;
  const refS1 = refTimeAtStation(refTable, s1Station);
  const refS2 = refTimeAtStation(refTable, s2Station);
  const refEnd = refTable.cumTime[refTable.cumTime.length - 1];

  const k1 = sectors.s1 / Math.max(1e-6, refS1);
  const k2 = sectors.s2 / Math.max(1e-6, refS2 - refS1);
  const k3 = sectors.s3 / Math.max(1e-6, refEnd - refS2);
  const t1 = sectors.s1;
  const t2 = sectors.s1 + sectors.s2;
  const lapTime = sectors.s1 + sectors.s2 + sectors.s3;

  const { speedKph, gear, binMetres } = track.referenceProfile;
  const nBins = speedKph.length;

  function profileAt(station: number) {
    const f = (((station % refTable.trackLength) + refTable.trackLength) % refTable.trackLength) / binMetres;
    const i0 = Math.min(nBins - 1, Math.floor(f));
    const i1 = (i0 + 1) % nBins;
    const frac = f - i0;
    return {
      speedKph: speedKph[i0] + (speedKph[i1] - speedKph[i0]) * frac,
      gear: frac < 0.5 ? gear[i0] : gear[i1],
    };
  }

  function sampleAt(t: number) {
    const clamped = Math.max(0, Math.min(lapTime, t));
    let k: number, refTarget: number;
    if (clamped <= t1) {
      k = k1; refTarget = clamped / k1;
    } else if (clamped <= t2) {
      k = k2; refTarget = refS1 + (clamped - t1) / k2;
    } else {
      k = k3; refTarget = refS2 + (clamped - t2) / k3;
    }
    const station = stationAtRefTime(refTable, refTarget);
    const prof = profileAt(station);
    return { stationM: station, speedKph: prof.speedKph / Math.max(1e-6, k), gear: prof.gear };
  }

  return { sampleAt, lapTime };
}

// ======================================================================================
// STANDING START. Everything below APPLIES the model fitted in
// scripts/simdata/launch.py and shipped in params.json under `standingStart`; it fits
// nothing. The two closed forms here (launchProfile, launchTimeLossS) are ports of that
// module's `launch_profile` and `launch_time_loss_s` -- the same three phases, the same
// algebra -- because an artifact can ship numbers but not the law that consumes them.
// Every NUMBER they are called with comes from the artifact; raceEngine.ts reads it.
// ======================================================================================

/** One car's launch, in SI. Mirrors launch.LaunchParams field for field. */
export interface LaunchKinematics {
  /** delay from the start signal to first motion, seconds */
  reactionS: number;
  /** constant acceleration from rest, m/s^2 */
  accelMps2: number;
  /** speed at which the fitted launch stops and the pace model takes over. The law is
   * MEASURED only to standingStart.launch.validToSpeedKph (120 kph = 33.33 m/s);
   * above that a caller is extrapolating, so raceEngine.ts refuses to build it. */
  handoverSpeedMps: number;
}

export type LaunchPhase = "GRID" | "LAUNCH" | "HANDOVER" | "QUEUED";

/** Port of launch.launch_profile: (distance travelled, speed, phase) at `tS` after the
 * signal. Continuous in both distance and speed at each join. */
export function launchProfile(
  tS: number, p: LaunchKinematics,
): { distanceM: number; speedMps: number; phase: "GRID" | "LAUNCH" | "HANDOVER" } {
  if (!(p.accelMps2 > 0)) throw new Error("accelMps2 must be positive");
  if (!(p.handoverSpeedMps > 0)) throw new Error("handoverSpeedMps must be positive");
  const tau = tS - p.reactionS;
  if (tau <= 0) return { distanceM: 0, speedMps: 0, phase: "GRID" };
  const tHand = p.handoverSpeedMps / p.accelMps2;
  if (tau < tHand) {
    return { distanceM: 0.5 * p.accelMps2 * tau * tau, speedMps: p.accelMps2 * tau, phase: "LAUNCH" };
  }
  const dHand = 0.5 * p.handoverSpeedMps * tHand; // = v^2 / (2a)
  return {
    distanceM: dHand + p.handoverSpeedMps * (tau - tHand),
    speedMps: p.handoverSpeedMps,
    phase: "HANDOVER",
  };
}

/** Port of launch.launch_time_loss_s: seconds this launch costs against a car already at
 * handoverSpeed at t = 0, i.e. reaction + v / (2a). This is the ONLY part of the lap-1
 * penalty that follows from the launch, and an engine that RUNS the launch reproduces it
 * by construction -- so standingStart.lap1.penaltySplit.launchLossSeconds must not also
 * be added on top; only penaltySplit.remainderSeconds is. */
export function launchTimeLossS(p: LaunchKinematics): number {
  return p.reactionS + p.handoverSpeedMps / (2 * p.accelMps2);
}

export function wrapStation(value: number, period: number): number {
  return ((value % period) + period) % period;
}

/** Duration of one reference lap on the table's own time axis. */
function refLapTime(table: RefTimeTable): number {
  return table.cumTime[table.cumTime.length - 1];
}

/**
 * Reference cumulative time at an UNWRAPPED progress: signed metres past the timing
 * line, free to be negative or to exceed one lap. Monotone over the whole real line,
 * which refTimeAtStation is not -- and the grid needs exactly that, because the back of
 * a 22-car field sits tens of metres BEHIND the line, so its wrapped station is near the
 * lap length and a wrapped lookup would place it a whole lap AHEAD of pole.
 */
export function refTimeAtProgress(table: RefTimeTable, progressM: number): number {
  const k = Math.floor(progressM / table.trackLength);
  return k * refLapTime(table) + refTimeAtStation(table, progressM - k * table.trackLength);
}

/** Inverse of refTimeAtProgress. The within-lap station is clamped to the track length
 * so the result stays monotone across the lap join: the reference profile's bins cover
 * ceil(L / bin) * bin metres, a few metres MORE than the ring, and without the clamp the
 * final bin reports a progress past L while the next lap restarts at exactly L. */
export function progressAtRefTime(table: RefTimeTable, refT: number): number {
  const lap = refLapTime(table);
  const k = Math.floor(refT / lap);
  const within = stationAtRefTime(table, refT - k * lap);
  return k * table.trackLength + Math.min(table.trackLength, Math.max(0, within));
}

export interface MotionSample {
  /** Signed metres past the timing line, monotonic in t and free to be negative.
   * GAPS BETWEEN CARS MUST BE TAKEN FROM THIS, never from stationM -- launch.py's
   * launch_state says the same thing for the same reason. */
  progressM: number;
  stationM: number;
  speedKph: number;
  gear: number;
  phase: LaunchPhase;
}

/** One car's box and launch, as launchStateAt needs them. */
export interface LaunchGridCar {
  driver: string;
  /** Signed metres past the timing line of the box: launch.slot_offset_m, i.e.
   * anchor - (slot - 1) * pitch. Negative for a box short of the line. */
  offsetM: number;
  launch: LaunchKinematics;
}

export interface LaunchCarState extends MotionSample {
  driver: string;
  /** 1-based grid slot: the car's index in the array passed in, plus one. */
  gridPosition: number;
}

/**
 * Port of launch.launch_state: where every car is, and how fast, `tS` after the signal.
 * `grid` is in GRID ORDER, pole first. Pure.
 *
 * `minGapM` is the same hard NON-INTERPENETRATION constraint the Python takes: no car may
 * be closer than this to the one ahead of it in grid order. Two solid bodies cannot share
 * a metre of track, so it is physics, not a fitted behaviour -- and it is deliberately
 * NOT a car-following model: a held car sits at exactly the gap and is tagged QUEUED, so
 * a caller can see that its free-air launch was interrupted rather than believe a number
 * that was quietly changed. It is needed because the fitted accelerations genuinely
 * differ by about 1 m/s^2 across a field whose boxes are 8 m apart.
 */
export function launchStateAt(
  grid: LaunchGridCar[],
  tS: number,
  opts: { ringLengthM: number; minGapM?: number | null },
): LaunchCarState[] {
  const L = opts.ringLengthM;
  if (!(L > 0)) throw new Error("ringLengthM must be positive");
  const minGap = opts.minGapM ?? null;
  if (minGap !== null && minGap < 0) throw new Error("minGapM cannot be negative");
  const out: LaunchCarState[] = [];
  for (let i = 0; i < grid.length; i++) {
    const car = grid[i];
    const lp = launchProfile(tS, car.launch);
    let progressM = car.offsetM + lp.distanceM;
    let speedMps = lp.speedMps;
    let phase: LaunchPhase = lp.phase;
    if (minGap !== null && out.length) {
      const limit = out[out.length - 1].progressM - minGap;
      if (progressM > limit) {
        progressM = limit;
        speedMps = Math.min(speedMps, out[out.length - 1].speedKph / 3.6);
        phase = "QUEUED";
      }
    }
    out.push({
      driver: car.driver,
      gridPosition: i + 1,
      progressM,
      stationM: wrapStation(progressM, L),
      speedKph: speedMps * 3.6,
      // The fitted launch carries no gear channel. 0 is the value the grid state has
      // always used for "not a measured gear"; reporting the flying lap's 7th at 40 kph
      // would be worse than reporting none.
      gear: 0,
      phase,
    });
  }
  return out;
}

export interface StandingStartLapOptions {
  /** Field-wide instant at which the launch model hands over to the pace model. */
  handoverTimeS: number;
  /** The car's progress at that instant, AFTER the non-interpenetration constraint --
   * i.e. launchStateAt(handoverTimeS)'s answer for this car. */
  handoverProgressM: number;
  /** Seconds from the start signal to this car's lap-1 line crossing. */
  lapTime: number;
}

export interface StandingStartLap {
  /** Valid for t >= handoverTimeS. Before that the launch is a FIELD-level computation
   * (launchStateAt, which applies the non-interpenetration constraint across the grid)
   * and this returns the handover state so the two agree at the join. */
  sampleAt(t: number): MotionSample;
  lapTime: number;
  handoverTimeS: number;
  /** The single time-warp applied to the reference profile over the rest of lap 1.
   * Above 1 means lap 1 is slower than the reference lap, which it always is. */
  warpFactor: number;
}

/**
 * The rest of lap 1 after the launch: the reference speed profile, ENTERED AT THE
 * POSITION THE LAUNCH LEFT THE CAR IN and time-warped by one uniform factor so the lap
 * ends exactly at the crossing the lap table says it does. Lap 1 always ends at progress
 * == trackLength, whatever the grid slot: gridOffset + (L - gridOffset) == L.
 *
 * The join is exact in POSITION and STEPS IN SPEED, from the 120 kph validity ceiling to
 * whatever the warped flying-lap profile is doing at that station. That step is a
 * property of the shipped model, not of this code: standingStart.launch is fitted only to
 * 120 kph and the artifact carries nothing about the rise from there to racing speed. The
 * alternatives are to extrapolate the fitted law past its own measurement or to invent a
 * blend, and both are worse than a documented step. The step is taken by the WHOLE FIELD
 * AT ONCE (see GeneratedTimeline's field-wide handover) so that it cannot reorder cars or
 * drive one through another.
 *
 * Lap 1 is warped by ONE factor rather than the three sector factors buildLapWarp uses: a
 * standing start's sector 1 contains the launch, so splitting the lap-1 time by sector
 * length would charge launch loss to the driver's sector pace. Laps 2+ keep buildLapWarp.
 */
export function buildStandingStartLap(
  track: TrackModel, refTable: RefTimeTable, opts: StandingStartLapOptions,
): StandingStartLap {
  const L = track.lengthMetres;
  const { handoverTimeS, handoverProgressM } = opts;
  const lapTime = Math.max(handoverTimeS + 1e-3, opts.lapTime);

  const refAtExit = refTimeAtProgress(refTable, handoverProgressM);
  const refAtEnd = refTimeAtProgress(refTable, L);
  const refSpan = Math.max(1e-6, refAtEnd - refAtExit);
  const warpFactor = Math.max(1e-3, (lapTime - handoverTimeS) / refSpan);

  const { speedKph, gear, binMetres } = track.referenceProfile;
  const nBins = speedKph.length;
  function profileAt(station: number) {
    const f = wrapStation(station, L) / binMetres;
    const i0 = Math.min(nBins - 1, Math.floor(f));
    const i1 = (i0 + 1) % nBins;
    const frac = f - i0;
    return {
      speedKph: speedKph[i0] + (speedKph[i1] - speedKph[i0]) * frac,
      gear: frac < 0.5 ? gear[i0] : gear[i1],
    };
  }

  function sampleAt(t: number): MotionSample {
    const clamped = Math.max(handoverTimeS, Math.min(lapTime, t));
    const refT = refAtExit + (clamped - handoverTimeS) / warpFactor;
    const progressM = progressAtRefTime(refTable, refT);
    const prof = profileAt(progressM);
    return {
      progressM,
      stationM: wrapStation(progressM, L),
      speedKph: prof.speedKph / warpFactor,
      gear: prof.gear,
      phase: "HANDOVER",
    };
  }

  return { sampleAt, lapTime, handoverTimeS, warpFactor };
}
