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
