/**
 * Haas F1 livery palette (2023 reference sheet). The UI must stay inside these four hues.
 * Also exposed as CSS variables in `src/app/globals.css`.
 */
export const HAAS = {
  white: "#EFEFEF",
  grey: "#AEAEAE",
  red: "#DA291C",
  black: "#111111",
} as const;

export type HaasColour = (typeof HAAS)[keyof typeof HAAS];
