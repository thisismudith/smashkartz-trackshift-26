/**
 * The pass model's feature row, built from what the simulation already knows.
 *
 * The model (M10, CP-14) is fitted on fifteen features at the Detection Line.
 * Sending it only a gap does not fail: it answers from the remaining fourteen
 * NaNs with something close to its base rate, for every car, every lap. That is
 * the worst failure mode this boundary has, because the number looks exactly
 * like a prediction and simply does not move.
 *
 * So every feature the replay can honestly supply is supplied, and the two it
 * cannot are named rather than guessed:
 *
 *  - `corner_type` comes from GET /track/{event}, not from curvature measured
 *    here. The server runs the same M03 classifier the model was fitted with;
 *    a browser-side reimplementation would emit plausible labels ("MEDIUM_LEFT")
 *    that are not in the trained category set, and an unseen category is a
 *    silently worse answer than an absent one.
 *  - `sector` is null in config/geometry for every segment of every circuit and
 *    was trained with a single `__missing__` category. There is nothing to send
 *    and nothing would be learned from it.
 *
 * Nothing here is computed for the model's benefit. Every value is one the sim
 * already displays somewhere on screen, which means a viewer can check the row
 * against the HUD -- and that is the only reason to trust the number it returns.
 */
import type { DashboardRow, WeatherSeries } from "../contract/types";
import { windComponents } from "../replay/wind";

/** The compounds the artifact was fitted with. Anything else is omitted rather
 * than coerced: an unseen category reaches the model as "unknown", which is
 * where it would have landed as a missing value anyway, but without the
 * misleading appearance of having been supplied. */
const TRAINED_COMPOUNDS = new Set(["SOFT", "MEDIUM", "HARD", "INTERMEDIATE"]);

/** The feature row as the service's schema names it. Every key is optional
 * because absence is a legitimate answer that the response reports back. */
export interface PassFeatureRow {
  gap_at_checkpoint?: number;
  closing_rate_s_per_s?: number;
  p_eligible?: number;
  track_temperature?: number;
  attacker_tyre_life_laps?: number;
  defender_tyre_life_laps?: number;
  tyre_life_delta_laps?: number;
  wind_head_component_mps?: number;
  wind_cross_component_mps?: number;
  attacker_tyre_compound?: string;
  defender_tyre_compound?: string;
  tyre_compound_pair?: string;
  corner_type?: string;
  wet_track_flag?: boolean;
}

function compound(row: DashboardRow | null | undefined): string | undefined {
  const raw = row?.compound;
  if (typeof raw !== "string") return undefined;
  const upper = raw.trim().toUpperCase();
  return TRAINED_COMPOUNDS.has(upper) ? upper : undefined;
}

function laps(row: DashboardRow | null | undefined): number | undefined {
  const life = row?.tyreLife;
  return typeof life === "number" && Number.isFinite(life) ? life : undefined;
}

/**
 * Index of the last weather reading at or before `t`.
 *
 * The trackside feed samples about once a minute. Interpolating between two
 * readings would invent a track temperature that was never measured, so the
 * most recent reading stands until the next one arrives.
 */
export function weatherIndexAt(times: number[] | undefined, t: number): number {
  if (!times || times.length === 0) return -1;
  let lo = 0, hi = times.length - 1, best = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) { best = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  return best;
}

/**
 * Rate the gap is CLOSING, in seconds per second, positive while it shrinks.
 *
 * The sign is the whole content of this function. `rules/eligibility.py` defines
 * the feature as positive when closing, while API.md's `gap_rate_ahead_s_per_s`
 * is the derivative of the same gap and therefore negative when closing. They
 * are the same magnitude with opposite signs, so getting it backwards feeds the
 * model a car pulling away as one closing in -- and no response could reveal it.
 *
 * Returns null when the two samples are too close together in session time to
 * divide safely, rather than a large number produced by a small denominator.
 */
export function closingRate(
  previous: { gapS: number; atSessionTime: number } | null | undefined,
  gapS: number,
  sessionTime: number,
  minIntervalS = 0.25,
): number | null {
  if (!previous) return null;
  const dt = sessionTime - previous.atSessionTime;
  if (!(dt >= minIntervalS) || !Number.isFinite(dt)) return null;
  const rate = (previous.gapS - gapS) / dt;
  return Number.isFinite(rate) ? rate : null;
}

/** The `corner_type` of the segment containing `stationM`, from GET /track. */
export function cornerTypeAt(
  segments: ReadonlyArray<{
    start_distance_m?: number | null;
    end_distance_m?: number | null;
    corner_type?: string | null;
  }> | null | undefined,
  stationM: number | null | undefined,
): string | undefined {
  if (!segments || segments.length === 0) return undefined;
  if (typeof stationM !== "number" || !Number.isFinite(stationM)) return undefined;
  for (const segment of segments) {
    const start = segment.start_distance_m;
    const end = segment.end_distance_m;
    if (typeof start !== "number" || typeof end !== "number") continue;
    if (stationM >= start && stationM < end) {
      return typeof segment.corner_type === "string" ? segment.corner_type : undefined;
    }
  }
  return undefined;
}

export interface PassFeatureInputs {
  gapS: number;
  attackerRow: DashboardRow | null;
  defenderRow: DashboardRow | null;
  weather: WeatherSeries | null;
  sessionTime: number;
  /** Direction of travel at the focused car, radians, for the wind projection. */
  headingRad: number | null;
  /** From POST /rules/eligibility, fetched before this row is built. */
  pEligible: number | null;
  /** From the previous scoring tick for this same pairing. */
  previous: { gapS: number; atSessionTime: number } | null;
  /** Segments from GET /track/{event}. */
  segments: ReadonlyArray<{
    start_distance_m?: number | null;
    end_distance_m?: number | null;
    corner_type?: string | null;
  }> | null;
  /** Station of the Detection Line, metres, from the circuit's rule config. */
  detectionM: number | null;
}

/**
 * The row, plus the names of everything left out and why.
 *
 * `omitted` is not diagnostics. The response already reports `features_missing`,
 * but only this side knows *why* each one is missing -- "this session carries no
 * weather series" and "the track route is unreachable" are different facts that
 * arrive at the model identically.
 */
export function buildPassFeatures(
  inputs: PassFeatureInputs,
): { features: PassFeatureRow; omitted: Record<string, string> } {
  const {
    gapS, attackerRow, defenderRow, weather, sessionTime,
    headingRad, pEligible, previous, segments, detectionM,
  } = inputs;

  const features: PassFeatureRow = { gap_at_checkpoint: gapS };
  const omitted: Record<string, string> = {};

  const rate = closingRate(previous, gapS, sessionTime);
  if (rate !== null) features.closing_rate_s_per_s = rate;
  else omitted.closing_rate_s_per_s = "needs two gap samples at least 0.25 s apart";

  if (pEligible !== null) features.p_eligible = pEligible;
  else omitted.p_eligible = "the eligibility route did not answer for this pair";

  const attackerCompound = compound(attackerRow);
  const defenderCompound = compound(defenderRow);
  if (attackerCompound) features.attacker_tyre_compound = attackerCompound;
  else omitted.attacker_tyre_compound = "no compound published for the attacker";
  if (defenderCompound) features.defender_tyre_compound = defenderCompound;
  else omitted.defender_tyre_compound = "no compound published for the defender";
  // The pair is its own feature, and the artifact's categories are spelled
  // "MEDIUM|HARD" -- attacker first. Only formed when BOTH sides are known,
  // because half a pair is not a pair.
  if (attackerCompound && defenderCompound) {
    features.tyre_compound_pair = `${attackerCompound}|${defenderCompound}`;
  }

  const attackerLaps = laps(attackerRow);
  const defenderLaps = laps(defenderRow);
  if (attackerLaps !== undefined) features.attacker_tyre_life_laps = attackerLaps;
  else omitted.attacker_tyre_life_laps = "no tyre age published for the attacker";
  if (defenderLaps !== undefined) features.defender_tyre_life_laps = defenderLaps;
  else omitted.defender_tyre_life_laps = "no tyre age published for the defender";
  // Attacker minus defender, matching API.md 5.5 where a 12-lap attacker behind
  // a 16-lap defender is -4: negative means the attacker is on the fresher set.
  if (attackerLaps !== undefined && defenderLaps !== undefined) {
    features.tyre_life_delta_laps = attackerLaps - defenderLaps;
  }

  const w = weatherIndexAt(weather?.tS as number[] | undefined, sessionTime);
  if (w >= 0 && weather) {
    const trackTemp = weather.trackTempC[w];
    if (typeof trackTemp === "number" && Number.isFinite(trackTemp)) {
      features.track_temperature = trackTemp;
    } else {
      omitted.track_temperature = "the weather reading carries no track temperature";
    }
    const raining = weather.rain[w];
    if (typeof raining === "boolean") features.wet_track_flag = raining;

    const wind = windComponents(weather.windMps[w], weather.windFromDeg?.[w], headingRad);
    if (wind) {
      features.wind_head_component_mps = wind.headMps;
      features.wind_cross_component_mps = wind.crossMps;
    } else {
      // Named together because they are one projection: without the track's own
      // heading a compass bearing is not a head or a cross component at all.
      omitted.wind_head_component_mps = headingRad === null
        ? "no focused car, so no direction of travel to project the wind onto"
        : "the weather reading carries no wind speed or bearing";
      omitted.wind_cross_component_mps = omitted.wind_head_component_mps;
    }
  } else {
    omitted.track_temperature = "this session carries no weather series";
    omitted.wind_head_component_mps = omitted.track_temperature;
    omitted.wind_cross_component_mps = omitted.track_temperature;
    omitted.wet_track_flag = omitted.track_temperature;
  }

  const corner = cornerTypeAt(segments, detectionM);
  if (corner) features.corner_type = corner;
  else {
    omitted.corner_type = segments
      ? "no segment of the derived geometry contains the Detection Line"
      : "GET /track did not answer, and this classification is not reproducible here";
  }

  // `sector` is deliberately absent and is not listed as an omission with a
  // recoverable reason: it is null in config/geometry for every segment of every
  // circuit, and the artifact was fitted with a lone `__missing__` category.
  // There is nothing to send and nothing to be gained by sending it.
  return { features, omitted };
}
