/**
 * A display-only scale on the pass model's probability.
 *
 * ONE definition, deliberately. A scale applied ad hoc at each render site is a
 * number nobody can trace back: the panel would show 96%, the service would say
 * 48%, and there would be nothing in the code saying which is which or why.
 *
 * What this is NOT: a calibration. The model's own output is unchanged, the
 * service still returns it, and `raw` below is kept so the untouched value stays
 * reachable. Anything that compares the model against the telemetry -- the
 * showcase's ROC AUC, the empirical rate table, the agreement selection -- runs
 * on the raw value and must continue to.
 *
 * Set SCALE back to 1 to remove it entirely; nothing else needs changing.
 */

/** Display multiplier applied to the model's probability. */
export const RATING_SCALE: number = 2;

export interface DisplayedRating {
  /** What goes on screen, 0..1. */
  shown: number;
  /** What the model actually returned, 0..1. */
  raw: number;
  /** True when the scale changed the number, so a caller can say so. */
  scaled: boolean;
  /** True when the scale pushed it against the ceiling and it was clamped. */
  clamped: boolean;
}

/**
 * Scale a probability for display, clamped to 1.
 *
 * The clamp is not cosmetic: 0.6 doubled is 1.2, and a "120% chance" is not a
 * misleading number so much as a visibly broken one.
 */
export function displayRating(p: number | null | undefined): DisplayedRating | null {
  if (typeof p !== "number" || !Number.isFinite(p)) return null;
  const lifted = p * RATING_SCALE;
  const shown = Math.min(1, Math.max(0, lifted));
  return {
    shown,
    raw: p,
    scaled: RATING_SCALE !== 1,
    clamped: lifted > 1,
  };
}

/** "model returned 48%" — for a tooltip, so the raw value is never lost. */
export function rawRatingNote(rating: DisplayedRating | null): string | undefined {
  if (!rating || !rating.scaled) return undefined;
  return `model returned ${(rating.raw * 100).toFixed(1)}%; shown at ${RATING_SCALE}x`
    + (rating.clamped ? ", clamped to 100%" : "");
}
