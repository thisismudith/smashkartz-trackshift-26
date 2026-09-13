/**
 * Wind projected onto the direction the car is actually travelling.
 *
 * API.md section 8 is explicit that the UI must "render wind as head / cross
 * components relative to the track, not a compass direction", and section 7's
 * bundle rules refuse raw `wind_direction_deg` outright: a bearing tells a race
 * engineer nothing without the track's own heading beside it, and the same 265
 * degrees is a headwind on one straight and a tailwind on the next. MODELS.md
 * M33 defines the pair as the wind projected onto `track_heading_deg`.
 *
 * This is a geometric projection of two measured quantities, not a model: no
 * fitting, no assumption, and the same arithmetic M33 specifies. The station it
 * is taken at matters and is the caller's choice -- these components are only
 * meaningful somewhere, so the panel names where.
 */

/** Metres per second, signed, relative to the direction of travel. */
export interface WindComponents {
  /** Positive is a HEADWIND (blowing against travel, costing speed); negative a
   * tailwind. Named for the effect rather than the vector so the sign cannot be
   * read backwards off the screen. */
  headMps: number;
  /** Positive blows from the LEFT of travel toward the right, matching the
   * "+ is left" convention every other lateral in this pipeline uses. */
  crossMps: number;
}

/**
 * `windFromDeg` is the compass bearing the wind blows FROM, as the trackside
 * feed reports it. `headingRad` is the direction of travel in the ring's own
 * frame, as trackPointAt returns it.
 *
 * Both are needed: with only one, there is no projection to make. Returns null
 * rather than zero when either is missing, because a calm wind and an unknown
 * wind are different facts and a zero here would read as the former.
 */
export function windComponents(
  windMps: number | undefined | null,
  windFromDeg: number | undefined | null,
  headingRad: number | undefined | null,
): WindComponents | null {
  if (typeof windMps !== "number" || !Number.isFinite(windMps)) return null;
  if (typeof windFromDeg !== "number" || !Number.isFinite(windFromDeg)) return null;
  if (typeof headingRad !== "number" || !Number.isFinite(headingRad)) return null;

  // The bearing is where the wind comes FROM; the vector it blows TOWARD is 180
  // degrees away. Compass bearings run clockwise from north, the ring's heading
  // runs counter-clockwise from +x, so the compass angle is converted before
  // the two are compared -- not doing so silently transposes head and cross.
  const blowsTowardDeg = windFromDeg + 180;
  const blowsTowardRad = ((90 - blowsTowardDeg) * Math.PI) / 180;

  // Angle between where the wind is going and where the car is going.
  const delta = blowsTowardRad - headingRad;
  // Both components describe the wind COMING AT the car, which is the negative
  // of the vector it travels along -- hence the shared sign flip. Wind blowing
  // WITH travel (delta 0) is therefore a tailwind, and wind travelling toward
  // the car's right is wind arriving from its left.
  return {
    headMps: -windMps * Math.cos(delta),
    crossMps: -windMps * Math.sin(delta),
  };
}

/** "2.1 m/s headwind" / "0.4 m/s tailwind" / "calm", for a signed head component. */
export function describeHead(headMps: number, digits = 1): string {
  const magnitude = Math.abs(headMps);
  if (magnitude < 0.05) return "calm";
  return `${magnitude.toFixed(digits)} m/s ${headMps > 0 ? "headwind" : "tailwind"}`;
}

/** "1.2 m/s from the left" / "0.3 m/s from the right" / "none". */
export function describeCross(crossMps: number, digits = 1): string {
  const magnitude = Math.abs(crossMps);
  if (magnitude < 0.05) return "none";
  return `${magnitude.toFixed(digits)} m/s from the ${crossMps > 0 ? "left" : "right"}`;
}
