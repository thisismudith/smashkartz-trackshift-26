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
