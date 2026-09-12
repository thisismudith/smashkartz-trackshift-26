import { CAR } from "@/components/loader/physics/constants";

/**
 * WORLD scale: car, road, lateral offsets and pit ribbon are all laid out at TRUE SIZE
 * (scale 1). This was measured, not guessed.
 *
 * Inflating the WORLD was tried at 1.25x, 1.5x and 2x and each step made the cars
 * overlap more, because PRESENTATION_SCALE grows the car and the road but not the
 * longitudinal gaps between cars, which are real metres. Over a full Australian GP race,
 * sampling 120 instants:
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
 * (Those lateral spans were measured against an older width block. The shipped artifacts
 * now carry 12.17-15.00 m of full ribbon width at Silverstone, median 13.27 m, measured
 * 13 Sep 2026 from public/sim. The conclusion is unchanged: it turns on the longitudinal
 * gaps, which no scale grows.)
 *
 * Distances are made readable by bringing the CAMERA in (see scene.ts), which costs no
 * fidelity, rather than by inflating the world, which costs correctness.
 */
export const PRESENTATION_SCALE = 1;

/**
 * VISUAL-ONLY size of the drawn car, as a multiple of its true size.
 *
 * Why this exists as a SEPARATE number from CAR_RENDER_LENGTH_M / CAR_RENDER_WIDTH_M:
 * those two are the real dimensions of a 2026 car (5.6 m x 2.0 m) and are read by code
 * that reasons about SPACE, not about pixels --
 *
 *     raceEngine.standingStart.minGapMetres   hard launch non-interpenetration gap
 *     generatedTimeline lap-1 wrap check      cars-length proximity at the line
 *     timeline.columnLateral                  "does a whole car still fit on the road"
 *     scene.declutterLanes                    lane window/step (reads CAR.* directly)
 *
 * Inflating those would silently change where cars are ALLOWED to be, which is a
 * simulation answer, not a drawing choice. CAR_VISUAL_SCALE instead multiplies the
 * vertices buildF1CarGeometry emits and nothing else, so the picture changes and every
 * spatial decision stays at true metres.
 *
 * Why it is needed: on a circuit drawn from its real GLB (Silverstone) the tarmac is
 * visibly wider than the procedural ribbon it replaces -- and that ribbon is already
 * 12.17-15.00 m wide there (median 13.27 m, measured from the shipped width block) --
 * so a 2.0 m car reads as a speck. 1.3 reads clearly bigger (69 % more plan area) while
 * staying defensible against the spacing the simulation actually permits:
 *
 *     drawn length 5.6 -> 7.28 m vs the 5.60 m launch minimum gap
 *     drawn width  2.0 -> 2.60 m vs the 2.30 m declutter lane step (CAR.widthM * 1.15)
 *
 * i.e. two cars held at exactly those minima now touch on screen (worst case: 1.68 m of
 * drawn overlap nose-to-tail at the launch minimum, 0.30 m flank-to-flank in a maximally
 * squeezed declutter cluster); past ~1.4 a tight pack visibly interpenetrates. Abreast,
 * a 13.27 m ribbon still takes 5.1 drawn cars where it took 6.6 true ones. The cost is
 * cosmetic and bounded; the benefit is that a car is legible on real tarmac.
 *
 * Anything that needs the DRAWN extent must measure the geometry (see carInstanceY)
 * rather than multiply by this constant, so it cannot go stale.
 */
export const CAR_VISUAL_SCALE = 1.3;

export const CAR_RENDER_LENGTH_M = CAR.lengthM * PRESENTATION_SCALE;
export const CAR_RENDER_WIDTH_M = CAR.widthM * PRESENTATION_SCALE;

/* There is deliberately no CAR_RENDER_HEIGHT_M. It was 0.9 m, the height of the
 * BoxGeometry that buildF1CarGeometry replaced, and scene.ts lifted every car by
 * CAR_RENDER_HEIGHT_M / 2 + 0.05 = 0.500 m. The built geometry's lowest point is
 * -0.300 m at true size (the wheels), so that lift left the contact patch 0.200 m above
 * the tarmac on every circuit. The lift is measured from the geometry's own bounding box
 * now, so it cannot go stale again -- which is also what let CAR_VISUAL_SCALE be
 * introduced without touching the ride height: at 1.3 the lowest point is -0.390 m and
 * the lift grows with it, on its own. */

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
 * lands on the road whatever shape -- and whatever CAR_VISUAL_SCALE -- buildF1CarGeometry
 * returns. Feeding it a minY measured from a DIFFERENT build than the one being drawn is
 * the one way to break this: at scale 1.3 an unscaled minY sinks the car 0.090 m into the
 * tarmac. carGeometry.test.ts pins that.
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
 * `geometryMinY * (scale - 1)` further down -- 0.027 m into the tarmac at the measured
 * minY of -0.390 m (CAR_VISUAL_SCALE 1.3; it was 0.021 m at the true-size -0.300 m) and a
 * 1.07 outline scale. Lifting by exactly that puts the ghost's underside back on the same
 * plane as the car's contact patch, for any geometry and any scale.
 */
export function focusGhostY(
  carY: number, geometryMinY: number, scale: number = FOCUS_OUTLINE_SCALE,
): number {
  return carY - geometryMinY * (scale - 1);
}
