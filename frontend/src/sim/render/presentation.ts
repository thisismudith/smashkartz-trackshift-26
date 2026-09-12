import { CAR } from "@/components/loader/physics/constants";

/**
 * PRESENTATION-ONLY size exaggeration.
 *
 * Cars and track width are drawn this many times larger than life. Station (distance
 * along the lap) is NEVER scaled -- that is the real race position and scaling it
 * would falsify gaps, lap times and the running order. Because car size and track
 * width are scaled by the SAME factor, their mutual proportion stays exactly real
 * (a 2 m car on a ~12 m road); what changes is only how large both read against the
 * circuit's real 3-6 km length, which is what makes cars legible at broadcast
 * camera distances.
 *
 * Consequence worth knowing: a longer rendered car means two cars running a real
 * few metres apart visually overlap sooner, which is why the renderer's declutter
 * pass measures against the RENDERED car box rather than the real one.
 */
export const PRESENTATION_SCALE = 2;

export const CAR_RENDER_LENGTH_M = CAR.lengthM * PRESENTATION_SCALE;
export const CAR_RENDER_WIDTH_M = CAR.widthM * PRESENTATION_SCALE;
export const CAR_RENDER_HEIGHT_M = 0.9 * PRESENTATION_SCALE;
