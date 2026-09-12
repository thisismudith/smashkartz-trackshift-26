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
export const CAR_RENDER_HEIGHT_M = 0.9 * PRESENTATION_SCALE;
