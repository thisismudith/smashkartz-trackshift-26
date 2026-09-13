/**
 * Composes one lap time from the fitted parameters (plan section 5.2 / 9): track base
 * + driver/team offsets + session pace trend + tyre degradation (track index x
 * compound multiplier) + dirty-air proximity loss + a two-component noise model
 * (a Normal core plus a rare incident tail), then splits it into three sector times
 * by the track's own sector-length fractions.
 */
import type { TrackModel } from "../contract/types";
import { NEUTRALISATION_MULTIPLIER_DEFAULT, type FittedParams, leafValue } from "./params";

export interface LapModelContext {
  /** The circuit's event display name (TrackModel.event, e.g. "British Grand Prix") --
   * every per-track leaf in FittedParams (fit_params.py's track_fits) is keyed this way,
   * NOT by TrackModel.slug. Using the slug here silently misses every real circuit and
   * falls through to the flat cross-track defaults below, erasing exactly the
   * track-to-track character (Monaco's dirty air, Barcelona's degradation, ...) this
   * whole fit exists to capture. */
  trackEvent: string;
  trackBaseSeconds: number; // the track's own median clean lap time, seconds
  driver: string;
  team: string | null;
  lapIndex: number; // 0-based, lap 1 => 0
  tyreLifeMinusOne: number; // life - 1, i.e. laps run on this set before this one
  compound: "SOFT" | "MEDIUM" | "HARD";
  gapAheadAtLapStart: number | null; // seconds, null if no car ahead / not identified
  neutralisation: "SC" | "VSC" | null;
  pitLossThisLap: number; // seconds added for an in-progress pit stop, 0 otherwise
}

export function composeLapTime(ctx: LapModelContext, params: FittedParams, rng: () => number): number {
  const fuel = leafValue(
    params.sessionPaceTrendPerLap.perTrack[ctx.trackEvent],
    params.sessionPaceTrendPerLap.pooled.value,
  );
  // The driver-offset fit already absorbs the team effect (it is fitted with driver
  // dummies alone, one per real 2026 driver). teamOffsetSeconds is used only as a
  // FALLBACK for a driver the fit never saw (e.g. a generated/synthetic entry in a
  // New Race grid with no historical laps), never added on top of a real driver's
  // own offset.
  const driverOffset = params.driverOffsetSeconds[ctx.driver]
    ? leafValue(params.driverOffsetSeconds[ctx.driver], 0)
    : leafValue(ctx.team ? params.teamOffsetSeconds[ctx.team] : undefined, 0);
  const trackDegIndex = leafValue(params.tyreDegradation.trackIndex[ctx.trackEvent], 0.05);
  const compoundMult = leafValue(params.tyreDegradation.compoundMultiplierDefault[ctx.compound], 1);
  const degradation = trackDegIndex * compoundMult * ctx.tyreLifeMinusOne;

  const dirtyAirB = leafValue(params.dirtyAirLossPerSecondOfProximity[ctx.trackEvent], 0.1);
  const proximity = ctx.gapAheadAtLapStart !== null && ctx.gapAheadAtLapStart < 3
    ? dirtyAirB * (3 - ctx.gapAheadAtLapStart)
    : 0;

  const sigma = leafValue(params.noise.perDriverSigma?.[ctx.driver], params.noise.coreSigma.value);
  const noiseCore = gaussian(rng) * sigma;
  const pIncident = params.noise.incidentProbability.value;
  const incident = rng() < pIncident
    ? params.noise.incidentMeanExcessSeconds.value * (0.5 + rng())
    : 0;

  let lap = ctx.trackBaseSeconds
    + driverOffset
    + fuel * ctx.lapIndex
    + degradation
    + proximity
    + noiseCore
    + incident
    + ctx.pitLossThisLap;

  if (ctx.neutralisation) {
    lap *= NEUTRALISATION_MULTIPLIER_DEFAULT[ctx.neutralisation];
  }
  // never below a "flying lap" floor -- the composed terms can occasionally add up to
  // a physically silly negative or tiny value at the tails of the distributions
  return Math.max(ctx.trackBaseSeconds * 0.7, lap);
}

/** Box-Muller transform: one standard-normal sample from the shared uniform PRNG. */
function gaussian(rng: () => number): number {
  const u1 = Math.max(1e-9, rng());
  const u2 = rng();
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
}

/** Splits a total lap time into three sector times using the track's own sector-
 * boundary stations as length fractions (an even-pace default within each sector;
 * the motion warp only needs the three totals to be internally consistent). */
export function splitIntoSectors(lapTime: number, track: TrackModel): { s1: number; s2: number; s3: number } {
  const L = track.lengthMetres;
  const s1Station = track.timingLines.s1 ?? L / 3;
  const s2Station = track.timingLines.s2 ?? (2 * L) / 3;
  const f1 = s1Station / L;
  const f2 = (s2Station - s1Station) / L;
  const f3 = (L - s2Station) / L;
  return { s1: lapTime * f1, s2: lapTime * f2, s3: lapTime * f3 };
}
