import { CAR } from "@/components/loader/physics/constants";

/**
 * Car and road are drawn at TRUE SIZE (scale 1). This was measured, not guessed.
 *
 * Inflating the cars was tried at 1.25x, 1.5x and 2x and each step made them
 * overlap more, because the gaps between cars are real metres and do not grow with
 * the car. Over a full Australian GP race, sampling 120 instants:
 *
 *     scale   frames with an overlap   worst simultaneous pairs
 *     1.0                         1 %                        6
 *     1.5                         2 %                       13
 *     2.0                         5 %                       18
 *
 * The road also only has 4.3-8.3 m of usable lateral span, so at 2x (a 4 m wide
 * car) barely 2.1 cars fit abreast versus 3.2 at true size -- a pack simply cannot
 * be laid out without cars intersecting. At 2x the pit ribbon likewise grew to 24 m
 * while its centre sits only ~13 m off the racing line, so it swallowed the track.
 *
 * Cars are made readable by bringing the CAMERA in (see scene.ts), which costs no
 * fidelity, rather than by inflating geometry, which costs correctness.
 */
export const PRESENTATION_SCALE = 1;

export const CAR_RENDER_LENGTH_M = CAR.lengthM * PRESENTATION_SCALE;
export const CAR_RENDER_WIDTH_M = CAR.widthM * PRESENTATION_SCALE;

/* There is deliberately no CAR_RENDER_HEIGHT_M. It was 0.9 m, the height of the
 * BoxGeometry that buildF1CarGeometry replaced, and scene.ts lifted every car by
 * CAR_RENDER_HEIGHT_M / 2 + 0.05 = 0.500 m. The built geometry's lowest point is
 * -0.300 m (the wheels), so that lift left the contact patch 0.200 m above the tarmac
 * on every circuit. The lift is measured from the geometry's own bounding box now, so
 * it cannot go stale again. */

/**
 * Gap left between the tyre contact patch and the ribbon, metres.
 *
 * Small enough to read as a car sitting on the road (and about a real F1 ride height),
 * large enough that the wheel's lowest vertex -- the 14-gon cylinder has one EXACTLY at
 * its bottom -- is not coplanar with the surface. The car material carries no
 * polygonOffset, so a coplanar contact line would shimmer.
 */
export const CAR_GROUND_CLEARANCE_M = 0.01;

/**
 * World Y for a car instance whose road surface sits at render-frame height
 * `surfaceY`, given the lowest point of the car geometry in its own local frame.
 *
 * Takes the geometry's measured extent rather than a constant, so the contact patch
 * lands on the road whatever shape buildF1CarGeometry returns.
 */
export function carInstanceY(surfaceY: number, geometryMinY: number): number {
  return surfaceY - geometryMinY + CAR_GROUND_CLEARANCE_M;
}

/**
 * How far proud of the bodywork the focused car's ghost outline sits (a uniform
 * scale about the geometry's own origin).
 */
export const FOCUS_OUTLINE_SCALE = 1.07;

/**
 * World Y for the focused car's ghost outline.
 *
 * A uniform scale is applied about the GEOMETRY's origin, not about the contact
 * patch, so a ghost drawn at the car's own instance Y has its underside pushed
 * `geometryMinY * (scale - 1)` further down -- 0.021 m into the tarmac at the measured
 * minY of -0.300 m and a 1.07 scale. Lifting by exactly that puts the ghost's underside
 * back on the same plane as the car's contact patch, for any geometry and any scale.
 */
export function focusGhostY(
  carY: number, geometryMinY: number, scale: number = FOCUS_OUTLINE_SCALE,
): number {
  return carY - geometryMinY * (scale - 1);
}
