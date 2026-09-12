/**
 * Haas F1 livery palette. White / grey / red / black come from the 2023 reference sheet; `blue` is
 * the royal blue of the VF-21 livery (rear-deck flash, front-wing endplate band); `yellow` is the
 * FIA grid/track-marking yellow used for the painted grid-slot line.
 *
 * The UI must stay inside these hues. Also exposed as CSS variables in `src/app/globals.css`.
 */
export const HAAS = {
  white: "#EFEFEF",
  grey: "#AEAEAE",
  red: "#DA291C",
  blue: "#1B40A6",
  yellow: "#FFD60A",
  black: "#111111",
} as const;

export type HaasColour = (typeof HAAS)[keyof typeof HAAS];

/**
 * Chart colours, derived from HAAS and VALIDATED — not chosen by eye.
 *
 * The raw Haas set fails as a categorical palette on our near-black surface: `white` and
 * `grey` are achromatic (chroma 0 — they read as grey, not as a series), and `blue` sits at
 * 1.93:1 contrast, below the 3:1 floor. Two hues were re-stepped to fix that, keeping the
 * hue and moving only lightness:
 *
 *   red   #DA291C  unchanged
 *   blue  #3A63D6  HAAS.blue lightened to clear the contrast floor
 *   gold  #A88606  HAAS.yellow darkened into the dark-mode lightness band (L 0.48-0.67)
 *
 * Verified with the dataviz validator against the real surface (#111111):
 *   lightness band PASS · chroma floor PASS · CVD separation PASS (worst adjacent
 *   dE 27.8 protan / 19.7 tritan) · normal-vision floor PASS (32.6) · contrast PASS (>= 3:1)
 *
 * THE PALETTE SUPPORTS AT MOST THREE CHROMATIC CATEGORICAL SERIES. For more categories use
 * team colours from the catalogue (colour then follows the entity, never its rank, and the
 * 3-letter code carries identity so it is never colour-alone), small multiples, or "Other".
 * Never generate a fourth hue.
 *
 * Re-run the validator if these change; a FAIL blocks the change. See UI.md section 7.2.
 */
export const CHART = {
  /** Fixed categorical order. Assign by index, never cycle. */
  series: ["#DA291C", "#3A63D6", "#A88606"],
  /** Diverging: two hues with a NEUTRAL grey midpoint (never a hue at the middle). */
  diverging: { negative: "#DA291C", mid: "#5A5A5A", positive: "#3A63D6" },
  /** Reserved for state. Never reused as a series colour; always paired with a word or icon. */
  status: { critical: "#DA291C", warning: "#A88606", muted: "#AEAEAE" },
  /** Recessive furniture. Text always wears these, never a series colour. */
  axis: "#5A5A5A",
  grid: "#2A2A2A",
  surface: "#111111",
} as const;
