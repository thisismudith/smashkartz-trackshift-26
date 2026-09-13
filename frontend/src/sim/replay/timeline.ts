/**
 * Builds a RaceTimeline (the shared contract) from a decoded replay pack.
 *
 * ---------------------------------------------------------------------------
 * RUNNING ORDER: LIVE vs OFFICIAL  (the decision, and why)
 * ---------------------------------------------------------------------------
 * Two signals describe who is where, and they update at different rates:
 *
 *   OFFICIAL  `pos` on each lap row -- "the driver's position at the END of this lap"
 *             (DATA_REFERENCE.md; for Race sessions it is the Ergast/Jolpica value).
 *             It is a snapshot taken at a line crossing, so it is stale for as long
 *             as a car has been on its current lap.
 *   MEASURED  distance covered = completed laps + distance round the current lap from
 *             the START/FINISH line. Continuous, and it is what the 3D view draws.
 *
 * Measured on the 2026 Australian GP pack (20 drivers, 5068 s of race, sampled every
 * 5 s -> 1014 instants):
 *   - `pos` is populated on 1004 / 1006 lap rows (99.8 %).
 *   - No two drivers ever share a `pos` on the same lap NUMBER, so per lap it is a
 *     clean permutation.
 *   - BUT at 246 / 1014 instants (24.3 %) two or more cars carry the same official
 *     position, because each car's snapshot was taken at its own crossing time. At a
 *     further 1499 car-instants (about 7 % of all of them) the last completed lap has
 *     no `pos` at all -- mostly cars that stopped on a lap the feed never classified.
 *
 * So the official classification is NOT a total order at an arbitrary instant and
 * cannot be the sole sort key. It is also stale by up to a whole lap, which is what
 * makes a car visibly ahead in the 3D view sit below on the board.
 *
 * The rule implemented here:
 *   1. OFFICIAL ANCHORS THE SLOTS. Every car's anchor is the `pos` recorded at the end
 *      of its last COMPLETED lap -- strictly causal, never the current lap's own `pos`
 *      (that is the future). A car whose last completed lap has no `pos` is given an
 *      anchor interpolated between its measured neighbours' anchors, so a missing
 *      field never teleports anyone.
 *   2. MEASURED DECIDES WHO FILLS THEM. The cars whose position is OBSERVED take those
 *      anchored slots in measured-distance order. Rationale: (a) it is the order the
 *      viewer can see, (b) official is stale within the lap by construction, and
 *      (c) a gap is defined as "how long ago was the car ahead as far round as I am
 *      now", so ordering by that same distance is what makes the gap column monotone
 *      down the board instead of the user's complaint (see `timeAtProgress`).
 *   3. RULE PLACEMENTS NEVER MOVE. A car on a grid slot, a pit-lane start or in the
 *      parked queue keeps the slot its anchor gave it, and never gets a gap. Once a
 *      car is parked nothing about it is being measured any more, so the classification
 *      is the only thing left that can rank it -- measured on the real pack, every one
 *      of the 52 adjacent pairs of classified cars matches the official result.
 *      A placement owns BOTH of its coordinates: station AND lateral. Telemetry fills in
 *      speed, gear, throttle and brake on a placed car, never its position.
 *   4. A DERIVED POSITION IS NOT A MEASURED ONE. A lap the feed had no x/y for is written
 *      in position-frame B (see `PositionFrame`): its station is the driver's own
 *      integrated wheel-speed distance linearly rescaled onto one lap of the ring, with
 *      the lateral hard-zeroed. Those cars are tagged DERIVED, which keeps them out of
 *      the measured re-ordering in (2) and out of the gap column entirely -- a
 *      per-driver rescaled integral cannot answer "how long ago was the leader here".
 *      Measured: every built session is 100 % frame A except monaco-grand-prix/Race,
 *      which is 91.2 % frame B.
 *
 * Measured effect on the real pack: 96.96 % of adjacent pairs of RUNNING cars are in
 * the same order as the official classification, and the 3 % that are not are live
 * on-track swaps the tower has not seen a line crossing for yet. Each car reports
 * `orderSource` (OFFICIAL | MEASURED | RULE) so the UI can mark those rows provisional
 * rather than silently mixing the two sources.
 *
 * Simplification kept from the previous implementation: there is no hysteresis band on
 * near-tied on-track swaps (the plan wants the sign to persist >= 1 s). Adding one here
 * would order two cars against their measured separation and so break the gap
 * monotonicity guarantee; it belongs in the UI's row animation, not in the data.
 */
import type {
  Provenance,
  CarState, NeutralisationInterval, RaceEvent, RaceTimeline, TrackModel,
} from "../contract/types";
import { type DecodedLap, decodeLap, sampleLap } from "../data/codec";
import type { RawDriverEntry, RawLapEntry, RawSessionManifest } from "../data/manifest";
import {
  gridSlotStation, gridSlotsOf, halfWidthAt, lapPositionFrame, measuredLateralRoomM,
  trackPointAt,
} from "../data/manifest";
// The two-column grid stagger and the parked queue are PRESENTATION placements, so they
// legitimately take the presentation layer's car width -- the same 2.0 m the renderer
// actually draws. Nothing else in this file depends on the renderer.
import { CAR_RENDER_WIDTH_M } from "../render/presentation";

interface DriverLaps {
  entry: RawDriverEntry;
  laps: RawLapEntry[]; // sorted by lap number
  decoded: Map<number, DecodedLap>;
}

/** Which of the two signals put this car in its slot on the board.
 * OFFICIAL = the classification recorded at its last line crossing.
 * MEASURED = distance covered round the current lap (a live, on-track swap).
 * RULE     = a documented placement, not a measurement (grid slot, pit-lane start,
 *            parked queue) -- these are never given a gap either. */
export type OrderSource = "OFFICIAL" | "MEASURED" | "RULE";

/**
 * Which coordinate frame the drawn position came from, straight off the lap row the
 * sample was taken from (`RawLapEntry.positionFrame`, written by scripts/simdata/replay.py).
 *
 *   A     the car's own x/y projected onto the ring. A measurement.
 *   B     NO usable x/y for that lap. replay.py falls back to
 *         `station = (d - d[0]) / span * ringLength` with `lateral = 0`, i.e. the
 *         per-driver integrated wheel-speed distance channel linearly rescaled onto one
 *         lap of the ring, which places the car single-file on the centreline. That is
 *         a DERIVED quantity, not telemetry.
 *   null  the position is a RULE placement (grid slot, pit-lane start, parked queue),
 *         so it came from neither frame.
 *
 * Measured across the 18 built packs: every session is 100 % frame A except
 * monaco-grand-prix/Race, which is 1324 frame-B laps to 128 frame-A (91.2 %). This tag
 * was carried all the way into RawLapEntry and then read by nobody, so 91 % of Monaco
 * was presented as measured position and given measured gaps.
 */
export type PositionFrame = "A" | "B";

/** What ReplayTimeline actually emits: the shared CarState plus the label saying which
 * signal decided the row's place. Structurally still a CarState, so every consumer
 * written against the contract keeps working. */
export interface RankedCarState extends CarState {
  orderSource: OrderSource;
  /** The frame the drawn position came from; null for a RULE placement. Surfaced so a
   * renderer or panel can mark a frame-B car as not-measured without re-deriving it.
   * `positionProvenance` already says DERIVED for frame B. */
  positionFrame: PositionFrame | null;
}

/** The official classification recorded for a lap, when the feed has one. `pos` is the
 * position at the END of that lap, so it is only usable once the lap is complete. */
function officialOf(lap: RawLapEntry | undefined): number | null {
  return lap && typeof lap.pos === "number" ? lap.pos : null;
}

/** The most recent official classification this car can legally know at time `t`:
 * scan BACKWARDS from the lap before `idx` for the first lap that both carries a `pos`
 * and has actually finished (sesT <= t).
 *
 * Two things this guards that a bare `laps[idx - 1].pos` did not:
 *  - causality. `pos` is the end-of-lap value, so the current lap's own `pos` is the
 *    future and is never read.
 *  - holes. 0.2 % of lap rows carry no `pos` (crash laps; the field is null for every
 *    non-Race session), and a driver can be missing a lap row entirely. Falling back to
 *    the previous crossing is stale but true; inventing one would not be. */
function lastCompletedOfficial(laps: RawLapEntry[], idx: number, t: number): number | null {
  for (let i = Math.min(idx, laps.length) - 1; i >= 0; i--) {
    const l = laps[i];
    if (l.sesT === null || l.sesT > t) continue;
    const pos = officialOf(l);
    if (pos !== null) return pos;
  }
  return null;
}

/** Signed distance from the start/finish line, in (-L/2, L/2]. */
function signedFromLine(station: number, trackLength: number, sfStation: number): number {
  const wrapped = (((station - sfStation) % trackLength) + trackLength) % trackLength;
  return wrapped > trackLength / 2 ? wrapped - trackLength : wrapped;
}

/**
 * How far round the lap a car is, in laps, measured from the START/FINISH LINE so that
 * every car shares one reference.
 *
 * Two things this has to get right, both of which were breaking the running order:
 *
 *  - It must not be elapsed-time-over-own-lap-time. Dividing by each car's own lap
 *    duration makes a car on a quicker lap read as further round, at the same instant,
 *    than a slower car physically ahead of it.
 *  - It must not be `wrap(station - line) / L` either. A decoded lap does not start
 *    exactly at the line. Measured over the 986 non-first lap rows of the 2026
 *    Australian pack, the first sample sits at a median of 2.8 m BEFORE it, 70.9 % of
 *    rows start before it, and the 5th percentile is 16.6 m before. Wrapping turns that
 *    into 0.9995 of a lap done instead of -0.0005 -- a whole lap ahead of the truth --
 *    until the car reaches the line. The same wrap made the old closest-station gap
 *    lookup match a car near the line against the leader a whole lap earlier: measured
 *    worst case 87.8 s of gap appearing out of nowhere, which is exactly the
 *    "gaps get smaller as you read down the order" complaint.
 *
 * So progress is anchored to where the lap's own trace STARTS relative to the line and
 * then accumulates distance travelled from there. That is continuous across the lap
 * boundary, gives grid slots (which sit behind the line) the negative fraction they
 * deserve with no lap-1 special case, and may exceed 1 by the same few metres when the
 * trace starts just past the line.
 */
function lapDistanceFraction(
  lap: DecodedLap, sample: { stationM: number } | null,
  trackLength: number, sfStation: number, relT: number,
): number {
  if (!sample || lap.n === 0) return 0;
  const wrap = (v: number) => ((v % trackLength) + trackLength) % trackLength;
  let travelled = wrap(sample.stationM - lap.stationM[0]);
  // The trace covers almost exactly one lap, so one station matches both its start and
  // its end. Lap-relative TIME breaks the tie: past halfway a car cannot be back at the
  // few metres it set off from.
  //
  // The tie only EXISTS inside that overlap -- the few tens of metres the trace starts
  // before the line and therefore drives over twice. Testing `travelled < L/2` instead
  // applied the correction across the whole first half of the lap, so any car whose lap
  // was slower early than late (a safety car, traffic, an in/out lap) gained a phantom
  // whole lap: measured across all 16 packs, 7734 / 294451 running car-instants reported
  // a lapProgress above 1.02, the board's P1 carried a phantom lap at 48.0 % of sampled
  // instants at Monza, and 32.0 % of Monza's reported gaps were the clamped value +0.0.
  // Restricting the window to the real overlap takes those to 836 / 294451, 0.1 % and
  // 0.0 % respectively, and improves every pack on every one of those measures.
  const OVERLAP_FRACTION = 0.02; // ~66 m at Monaco, ~140 m at Spa; the trace overlap is tens of metres
  const dur = lap.tS[lap.n - 1];
  if (dur > 0 && relT > dur * 0.5 && travelled < trackLength * OVERLAP_FRACTION) travelled += trackLength;
  return (signedFromLine(lap.stationM[0], trackLength, sfStation) + travelled) / trackLength;
}

/**
 * Where a two-column placement puts its columns, as a fraction of the half-width of the
 * road it is being drawn on: each column's centre sits midway between the ring and the
 * edge, so the two columns are separated by one half-width.
 *
 * Not a chosen distance, but NOT a measured one either -- the fraction is applied to
 * whatever half-width the circuit's drawn road has, and on the 12 procedural circuits
 * that half-width is itself a RULE scale (see measuredLateralRoomM). The feed carries
 * no lateral at all on the grid (audit: observedLateralStd 0.07 m -- every car snapped
 * to the centreline), so the whole stagger is a labelled RULE placement.
 *
 * On the ribbon circuits, half-width at the grid slots runs 6.02-7.50 m, so the columns
 * land at +/-3.01..3.75 m and are 6.02-7.50 m apart -- against the flat +/-1.8 m
 * (3.6 m apart) this replaced, which read as a single file on screen. On a circuit
 * drawn from a real model this fraction has nothing measured to multiply and the
 * columns collapse; that is the honest answer, not a regression. See columnLateral.
 *
 * NEVER apply this fraction to a per-side `measuredLateralRoomM` value: that room is
 * genuinely asymmetric (Silverstone's grid measures 1.5-3.5 m one side, 17.0-17.5 m the
 * other, because the ring is the racing line, not the road centre, and the wide side's
 * "drivable surface" reaches into the paved pit apron beside the straight) and multiplying
 * it gave an 8.75 m column on the wide side alone -- cars visibly standing in the gravel,
 * not a starting grid. This constant is for the RIBBON's half-width only, which is
 * symmetric by construction.
 */
const GRID_COLUMN_FRACTION = 0.5;

/** Used only when the track model carries no usable half-width at all (no built pack
 * does -- every one measures 6.0-7.5 m). Keeps a degenerate model from stacking the
 * whole field on one line; it is the pre-existing flat stagger, tagged DEFAULT in spirit. */
const GRID_LATERAL_FALLBACK_M = 1.8;

/**
 * Signed lateral offset for a two-column placement at `stationM`, metres, on the side
 * `sign` (-1 or +1). Even slots sit on one side, odd on the other, as a real starting
 * grid does.
 *
 * Clamped so the car always FITS: the outer edge of a CAR_RENDER_WIDTH_M car may not
 * pass the edge of the road. On a narrow road that pulls the columns in, and on a road
 * narrower than a car it collapses to a single file (0) rather than drawing cars off
 * the tarmac.
 *
 * WHICH ROAD IS BEING FITTED IS THE WHOLE POINT (see measuredLateralRoomM):
 *
 *  - drawn as the procedural ribbon (12 of the 13 shipped circuits): the ribbon IS
 *    `halfWidthAt`, so fitting the columns inside it puts them on the road the viewer
 *    is looking at. Unchanged.
 *  - drawn from a real circuit model (british-grand-prix today): the ribbon is not what
 *    is on screen, and `halfWidthAt` is a RULE scale that the artifact itself says is
 *    not a measurement of track width. `measuredLateralRoomM` instead reads the ACTUAL
 *    drivable extent THIS side of the ring at THIS station, signed per side -- never a
 *    symmetric half-width, because the two sides can differ by an order of magnitude
 *    (see below). A station or side the road-edge walk could not reach falls back to
 *    single file on THAT side only, rather than borrowing the other side's number or
 *    the ribbon's RULE constant, both of which would be claiming a measurement that
 *    does not exist.
 *
 * Measured, before this rule existed, at the 2026 British GP: the 21 grid slots were
 * placed at +/-3.17..3.52 m while the shipped GLB's asphalt at the front of the grid
 * ends 1.75-2.00 m to the LEFT of the ring (and 15.0-17.5 m to the right, because the
 * ring is the racing line out of Club, 6.5 m off the road centre). Ten of the 21 cars
 * therefore stood 1.2-1.8 m out on the grass, and the eleven that did not were jammed
 * against the left edge -- the field "strung out parallel to the road rather than on
 * it" that the screenshot shows.
 *
 * A first fix capped the stagger's SIZE but still centred it ON THE RING, which is
 * wrong for exactly the same reason: the ring being 6.5 m off the road's true centre
 * here means a ring-centred grid reads as the whole field parked against the left
 * side of the road, with most of the actual tarmac empty to the right of every car.
 * A real grid is laid out across the WIDTH OF THE ROAD, not around wherever the
 * fastest lap happened to thread through the corner behind it -- so when BOTH sides
 * are measured, the stagger is centred on the road's own measured midpoint
 * (`leftRoom` and `rightRoom` together bound the true road, even though neither
 * alone is the centre), and only falls back to ring-centred single-side placement
 * when just one side has a measurement to place against.
 */
/** Metres either side of a grid station to sample when smoothing the road's measured
 * centre, small relative to the 8 m grid pitch so it never blends into a neighbouring
 * car's own station. See roadCentreFromRing. */
const CENTRE_SMOOTH_OFFSETS_M = [-4, -2, 0, 2, 4];

/**
 * The road's measured midpoint at `stationM`, in the ring's "+ = left" lateral frame,
 * smoothed over a few nearby stations rather than trusted at the exact one: the
 * per-station road-edge walk that produces edgeLeftM/edgeRightM is a GLB bake and can
 * carry a single noisy station. Measured at the British GP grid: one slot's OWN
 * station computed a centre 18-20 m from both immediate neighbours 8 m either side --
 * a single-point spike, not a road feature 8 m of straight tarmac could produce. The
 * median of a handful of samples is one bad reading away from the same spike; the
 * single-station value was zero readings away. Null when fewer than two samples had
 * both sides measured, i.e. there is nothing to smooth.
 */
function roadCentreFromRing(track: TrackModel, stationM: number): number | null {
  const samples: number[] = [];
  for (const d of CENTRE_SMOOTH_OFFSETS_M) {
    const pair = roadEdgesAt(track, stationM + d);
    if (pair) samples.push((pair.left - pair.right) / 2);
  }
  if (samples.length < 2) return null;
  samples.sort((a, b) => a - b);
  const mid = samples.length >> 1;
  return samples.length % 2 ? samples[mid] : (samples[mid - 1] + samples[mid]) / 2;
}

/**
 * The widest a station's two measured edges may be apart and still describe ONE ROAD.
 *
 * The bake's edge walk stops at the first step that is not drivable surface, which is
 * the right rule on a circuit whose track is bounded by grass or gravel and the wrong
 * one wherever asphalt run-off, a pit apron or a service road is CONTINUOUS with the
 * track: the walk crosses the painted edge and keeps going to its own 40 m cap.
 * Measured over Silverstone's 5806 two-sided stations, left + right exceeds 25 m at
 * 23.3 % of them and reaches 80 m, against a circuit that is 12-18 m wide and a shipped
 * ribbon of 12.17-15.00 m. 25 m is wider than any F1 circuit's tarmac and narrower than
 * every one of those leaks.
 *
 * A pair this cannot be is not a road measurement, so it does not get to say where the
 * middle of the road is -- placing a grid on one put half the British field 9 m one side
 * of the ring and the other half 8 m the other, an 18 m break down the middle of a
 * straight grid. The station then falls back to the single-sided placement, which is the
 * same answer a station with only one measured edge already gets.
 */
const MAX_MEASURED_ROAD_WIDTH_M = 25;

/**
 * Metres to shift the two columns LEFT of the road centre the edge walk computes.
 *
 * MEASURED against the circuit model's own painted grid boxes, the same top-down render
 * that produced CAR_REFERENCE_AHEAD_M (23.479 px/m): with the columns centred on the
 * walked midpoint, ANT sat 0.54 m and HAM 0.80 m to the RIGHT of the box painted under
 * them. Consistent in sign across the field and a fraction of a car's width, which is
 * what a centre derived from a walk quantised in 0.5 m steps can be out by.
 *
 * It corrects the CENTRE, not the stagger, so the two columns stay the same distance
 * apart; and it applies only where there is a measured road to be off the middle of --
 * a ribbon circuit has no painted boxes to calibrate against and is left alone.
 */
const GRID_COLUMN_CALIBRATION_M = 0.65;

/** How far behind its own box a stationary car may read and still count as being in it,
 * metres. The feed's station jitters by centimetres under a car that is not moving; this
 * is a car length, far below the ~300-600 m launch window and far above that jitter. */
const BOX_JITTER_M = 5;

/**
 * Metres off the racing line beyond which a car on a pit lap is on the PIT ROAD.
 *
 * Measured on both sides. Genuine on-circuit laterals: over 52,383 status-"track"
 * instants across the 2026 British GP race, 99.84 % sit within 3 m of the line and the
 * 99.9th percentile is 4.57 m. The lane on the other side: 19.3 m off the ring at
 * Silverstone's pit exit and 35.0 m at the boxes, ~22 m at Spa's entry. 8 m is above
 * every on-circuit reading and less than half the nearest part of any lane.
 */
const PIT_DIVERGENCE_M = 8;

/** Both measured edges at a station, or null unless the pair describes one road. */
function roadEdgesAt(
  track: TrackModel, stationM: number,
): { left: number; right: number } | null {
  const left = measuredLateralRoomM(track, stationM, 1);
  const right = measuredLateralRoomM(track, stationM, -1);
  if (left === null || right === null) return null;
  if (left + right > MAX_MEASURED_ROAD_WIDTH_M) return null;
  return { left, right };
}

function columnLateral(
  track: TrackModel, stationM: number, sign: number,
): number {
  const surfaced = Boolean(track.surface);
  if (surfaced) {
    const carHalfWidth = CAR_RENDER_WIDTH_M / 2;
    const edges = roadEdgesAt(track, stationM);
    if (edges) {
      const { left: leftRoom, right: rightRoom } = edges;
      // The road's own measured midpoint, in the ring's "+ = left" lateral frame --
      // e.g. leftRoom 1.75 m, rightRoom 17.5 m puts the centre 7.875 m RIGHT of the
      // ring, matching the 6.5 m this file's own measurement above found by hand.
      // Smoothed against single-station bake noise (see roadCentreFromRing); falls
      // back to this exact station's own value only when too few neighbours measured
      // both sides to smooth with.
      const centreFromRing = (roadCentreFromRing(track, stationM) ?? (leftRoom - rightRoom) / 2)
        + GRID_COLUMN_CALIBRATION_M;
      const target = centreFromRing + sign * GRID_LATERAL_FALLBACK_M;
      // Still never past the edge of the actually-measured road AT THIS STATION,
      // regardless of what the smoothed aim point says -- a car parked here must fit
      // on the road here, not on its neighbours' average road.
      return Math.max(-(rightRoom - carHalfWidth), Math.min(leftRoom - carHalfWidth, target));
    }
    // Only one side has a usable measurement: there is no true centre to place against,
    // so fall back to the single-side, ring-relative placement -- single file on the
    // unmeasured side, exactly as when nothing at all is measured (see below).
    const measured = measuredLateralRoomM(track, stationM, sign);
    if (measured === null) return 0;
    const fits = measured - carHalfWidth;
    const offset = Math.max(0, Math.min(GRID_LATERAL_FALLBACK_M, fits));
    return offset === 0 ? 0 : sign * offset;
  }

  const half = halfWidthAt(track, stationM);
  // The ribbon's one degenerate case: a model with no usable half-width at all.
  if (!Number.isFinite(half) || half <= 0) {
    return sign * GRID_LATERAL_FALLBACK_M;
  }
  const fits = half - CAR_RENDER_WIDTH_M / 2;
  const offset = Math.max(0, Math.min(half * GRID_COLUMN_FRACTION, fits));
  // `sign * 0` is -0 for sign -1, which is a different value from 0 under Object.is and
  // would publish two distinct "on the centreline" laterals. There is one centreline.
  return offset === 0 ? 0 : sign * offset;
}

/**
 * Metres from `stationM` to the next corner, or null when the model names no corners.
 *
 * Used as the LAUNCH WINDOW: the distance over which the field stops being a grid and
 * becomes a queue. It is a per-circuit measurement, not a chosen number -- pole to the
 * first corner is 336.6 m at Silverstone, 318.9 m at Zandvoort, 602.6 m at Monza.
 */
function metresToNextCorner(track: TrackModel, stationM: number): number | null {
  const L = track.lengthMetres;
  let best: number | null = null;
  for (const c of track.corners) {
    const d = (((c.station - stationM) % L) + L) % L;
    if (d > 0 && (best === null || d < best)) best = d;
  }
  return best;
}

/** Alternating sides, the fallback rule for a placement the producer never published
 * (the parked queue, and any driver missing from `grid.slots`). */
function paritySign(slot: number): number {
  return slot % 2 === 0 ? -1 : 1;
}


function decodeAll(bin: ArrayBuffer, drivers: RawDriverEntry[]): Map<string, DriverLaps> {
  const out = new Map<string, DriverLaps>();
  for (const d of drivers) {
    const laps = [...d.laps].sort((a, b) => a.lap - b.lap);
    const decoded = new Map<number, DecodedLap>();
    for (const l of laps) {
      if (l.sampleCount > 0) decoded.set(l.lap, decodeLap(bin, l.byteOffset, l.sampleCount));
    }
    out.set(d.driver, { entry: d, laps, decoded });
  }
  return out;
}

/** lST/sesT/pin/pout in the raw data are SESSION-ABSOLUTE (measured: lap 1's lST is
 * identical across every driver but is typically 2000-3500 s into the recorded
 * session, not 0 -- the recording starts well before lights-out). The public
 * RaceTimeline contract promises sampleAt(t) for t in [0, duration], so every
 * absolute timestamp is shifted here, once, by the earliest lap-1 lST -- the same
 * "race start" instant the plan identifies from race-control text. Returns a deep
 * copy; the raw manifest passed in is never mutated. */
function normaliseToRaceStart(manifest: RawSessionManifest): {
  drivers: RawDriverEntry[]; raceControl: RawSessionManifest["raceControl"];
  neutralisation: RawSessionManifest["neutralisation"]; weather: RawSessionManifest["weather"];
  offset: number;
} {
  let offset = Infinity;
  for (const d of manifest.drivers) {
    for (const l of d.laps) {
      if (l.lST !== null && l.lST < offset) offset = l.lST;
    }
  }
  if (!Number.isFinite(offset)) offset = 0;

  const shift = (v: number | null) => (v === null ? null : v - offset);
  const drivers = manifest.drivers.map((d) => ({
    ...d,
    laps: d.laps.map((l) => ({ ...l, lST: shift(l.lST), sesT: shift(l.sesT), pin: shift(l.pin), pout: shift(l.pout) })),
  }));
  const raceControl = manifest.raceControl.map((m) => ({ ...m, sessionTime: m.sessionTime - offset }));
  const neutralisation = manifest.neutralisation.map((n) => ({ ...n, start: n.start - offset, end: n.end - offset }));
  const weather = manifest.weather
    ? { ...manifest.weather, wT: manifest.weather.wT.map((v) => v - offset) }
    : null;
  return { drivers, raceControl, neutralisation, weather, offset };
}

export class ReplayTimeline implements RaceTimeline {
  readonly provenance = "OBSERVED" as const;
  readonly runId: string;
  readonly track: TrackModel;
  readonly driverList: string[];
  readonly totalLaps: number | null;
  readonly duration: number;
  /**
   * Seconds subtracted from every absolute session timestamp to make this
   * timeline start at 0 (the earliest lap-1 lST).
   *
   * Exposed because anything holding an ABSOLUTE session time -- the evidence
   * reel's overtake moments come straight out of the telemetry parquet -- has to
   * subtract this before seeking. Without it a seek to 3670 lands 3670 s after
   * the race start instead of at it, which is a different lap and a car on its
   * own.
   */
  readonly clockOffsetS: number;

  private byDriver: Map<string, DriverLaps>;
  private raceControl: RawSessionManifest["raceControl"];
  private neutral: RawSessionManifest["neutralisation"];
  private weatherRaw: RawSessionManifest["weather"];
  private retiredOrder: string[] = [];
  private finishOrder: string[] = [];
  private parkIndex = new Map<string, number>();
  /** Grid order actually used for placement. Falls back to lap-1 finishing order
   * when the track model's own grid detection covered too little of the field. */
  private gridOrder: string[] = [];
  /** Drivers whose lap-1 position is not a measurement, straight from the track model.
   * Kept as a Set because sampleAt consults it at 60 Hz. */
  private gridUnplaced = new Set<string>();
  /** Drivers the PRODUCER placed in a box. Not the same as `gridIndex`, which also
   * carries everyone gridOrder had to append (a pit-lane starter, a driver the producer
   * could not place) at indices past the last published slot. Only a driver in here may
   * be held on a grid slot. */
  private gridBoxed = new Set<string>();
  /** Metres from pole's box to the first corner, or null when the model names none. */
  private launchWindowM: number | null = null;
  /** driver -> index in gridOrder. sampleAt runs at 60 Hz; indexOf inside a comparator
   * is O(n) per comparison, this is O(1). */
  private gridIndex = new Map<string, number>();
  private retiredSet = new Set<string>();
  /** Drivers whose lap 1 carries a pit-exit time: they really did start from the lane. */
  private pitStarters = new Set<string>();
  /** driver -> the side of the ring the PRODUCER published for that driver's grid slot
   * (grid.slots[].lateralSign). Absent for a driver the producer did not place, and for
   * a model that carries no slots at all, in which case `paritySign` decides. */
  private slotSign = new Map<string, number>();

  constructor(manifest: RawSessionManifest, track: TrackModel, bin: ArrayBuffer) {
    this.runId = `obs:${manifest.trackSlug}/${manifest.session.toLowerCase()}`;
    this.track = track;
    const normalised = normaliseToRaceStart(manifest);
    this.byDriver = decodeAll(bin, normalised.drivers);
    this.driverList = normalised.drivers.map((d) => d.driver);
    this.raceControl = normalised.raceControl;
    this.neutral = normalised.neutralisation;
    this.weatherRaw = normalised.weather;

    let maxLap = 0;
    let maxSesT = 0;
    for (const dl of this.byDriver.values()) {
      for (const l of dl.laps) {
        if (l.lap > maxLap) maxLap = l.lap;
        if (l.sesT !== null && l.sesT > maxSesT) maxSesT = l.sesT;
      }
    }
    this.totalLaps = maxLap || null;
    this.duration = maxSesT;
    this.clockOffsetS = normalised.offset;

    // Finished vs retired must be judged by LAP COUNT REACHED, never by session time:
    // the winner typically has the EARLIEST final sesT of any finisher (they simply
    // took less time), so comparing "last sesT" against the race maximum would
    // misclassify the winner as retired. A driver is retired only if their total
    // completed (non-generated) laps fall short of the race's own lap count.
    const stops: { driver: string; t: number }[] = [];
    for (const [driver, dl] of this.byDriver) {
      const completed = dl.laps.filter((l) => !l.ff1G && l.sesT !== null).length;
      if (this.totalLaps !== null && completed < this.totalLaps) {
        const last = dl.laps[dl.laps.length - 1];
        stops.push({ driver, t: last?.sesT ?? 0 });
      }
    }
    stops.sort((a, b) => a.t - b.t);
    this.retiredOrder = stops.map((s) => s.driver);
    this.retiredSet = new Set(this.retiredOrder);

    // finish order (winner first): needed only for the presentation "parking" spread
    // below, never for the leaderboard itself (which is ranked live in sampleAt).
    const finishes: { driver: string; t: number }[] = [];
    for (const [driver, dl] of this.byDriver) {
      if (this.retiredSet.has(driver)) continue;
      const last = dl.laps[dl.laps.length - 1];
      if (last?.sesT !== null && last?.sesT !== undefined) finishes.push({ driver, t: last.sesT });
    }
    // A driver started from the pit lane iff their LAP 1 has a pit-exit time. That is
    // a measured field; absence from the track model's grid order is not. Grid
    // detection needs clean lap-1 positions, and on the sessions the audit flagged as
    // corrupt it collapses -- it found 2 of 22 cars at Monaco and 0 of 18 in China --
    // which previously dumped nearly the whole field into the pit lane at the start.
    for (const [driver, dl] of this.byDriver) {
      const lap1 = dl.laps.find((l) => l.lap === 1);
      if (lap1?.pout !== null && lap1?.pout !== undefined) this.pitStarters.add(driver);
    }

    const detected = track.grid.order.filter((d) => this.byDriver.has(d));
    if (detected.length >= this.byDriver.size * 0.6) {
      // trustworthy: keep it, appending anyone it missed
      this.gridOrder = [...detected,
        ...[...this.byDriver.keys()].filter((d) => !detected.includes(d))];
      // The appended drivers are exactly the ones with NO box; the producer's own order
      // is the list of cars that have one.
      this.gridBoxed = new Set(detected);
    } else {
      // too sparse to believe; order by who completed lap 1 first, which needs only
      // the lap clock and is unaffected by broken position data
      this.gridOrder = [...this.byDriver.entries()]
        .map(([driver, d]) => ({ driver, t: d.laps.find((l) => l.lap === 1)?.sesT ?? Infinity }))
        .sort((a, b) => a.t - b.t)
        .map((x) => x.driver);
    }

    // Pole's run to the first corner, measured once: `launchBlend` asks per sample and
    // the corner list does not change with time.
    this.launchWindowM = metresToNextCorner(track, gridSlotStation(track, 0));
    // the fallback order is lap-1 completion order: every driver in it is a starter the
    // producer simply could not slot, so all of them keep a box
    if (this.gridBoxed.size === 0) this.gridBoxed = new Set(this.gridOrder);
    this.gridOrder.forEach((d, i) => this.gridIndex.set(d, i));
    this.gridUnplaced = new Set(this.track.grid.unplaced ?? []);
    // The side of the ring each slot sits on is the PRODUCER's to state, and it states
    // it per driver. Re-deriving it from slot parity here put every one of the 232
    // published slots on the opposite side from the placement the artifact carries.
    for (const slot of gridSlotsOf(this.track)) {
      if (slot.driver !== null) this.slotSign.set(slot.driver, slot.lateralSign);
    }

    finishes.sort((a, b) => a.t - b.t);
    this.finishOrder = finishes.map((f) => f.driver);
    this.finishOrder.forEach((d, i) => this.parkIndex.set(d, i));
    this.retiredOrder.forEach((d, i) => this.parkIndex.set(d, this.finishOrder.length + i));
  }

  /** Real telemetry ends the instant a car crosses the line, so two cars that finish
   * a few seconds apart would otherwise sit at (almost) the exact same point forever
   * after -- multiple 5.6 x 2 m boxes pinned to one spot is not a rendering choice,
   * it is real width-spread data (audit: p98 0.3-0.6 m) being smaller than a car's own
   * footprint. Once a car is finished/retired this queues it, nose-to-tail, behind the
   * line at the real grid spacing -- a labelled PRESENTATION placement, not a claim
   * about where the car actually is (it does not feed gaps, laps, or any other
   * reported number). */
  private parkedStation(driver: string): number {
    // Queued in the GRID's own boxes, one car per box behind pole, rather than behind
    // the timing line. Same reason the grid moved (gridSlotStation): at Silverstone the
    // line is 110.8 m short of the boxes and the road behind it is the exit of Club, so
    // a queue measured from the line parked the finishers around a 45 deg bend.
    return gridSlotStation(this.track, (this.parkIndex.get(driver) ?? 0) + 1);
  }

  /**
   * Progress a car released from the pit lane ranks at: BEHIND EVERY GRID BOX.
   *
   * Its station is a real measurement and is drawn as one, but it is a pit-lane
   * coordinate projected onto the RACING ring, so it is not comparable with a station
   * measured on the circuit -- the same reason `gapUsable` already refuses to compute a
   * gap from it. Measured at the 2026 British GP: ALO sits in the lane at ring station
   * 351.2 m while the stationary field is on its boxes at 110.8 m and behind, so ranking
   * the two on the same axis put the one car that had not started the race at P1.
   */
  private pitLaneRankProgress(): number {
    return -((this.gridOrder.length + 1) * this.track.grid.pitchMetres)
      / this.track.lengthMetres;
  }

  /** Where a pit-lane starter is placed before its own telemetry begins.
   *
   * The station is the pit EXIT and so is the lateral. They used to come from different
   * points of the lane -- `exitStation` paired with `loopLateral`, which is the offset of
   * the BOX -- and at Silverstone the box is 34.95 m off the ring where the exit road at
   * that station is 19.33 m, so the car was parked 15.6 m beyond the pit road. An
   * artifact too old to carry `exitLateral` keeps the old pairing rather than inventing
   * one.
   */
  private pitLaneStart(lapEntry: RawLapEntry | null): ReturnType<ReplayTimeline["progress"]> {
    const pit = this.track.pitLane;
    return {
      lapsDone: 0, lapProgress: 0, rankProgress: this.pitLaneRankProgress(),
      stationM: pit.exitStation ?? 0,
      lapEntry, kind: "pit", officialPos: null,
      lateralOverride: pit.exitLateral ?? pit.loopLateral ?? 0,
      posProvenance: "RULE", positionFrame: null,
    };
  }

  /** The parked queue's lateral, laid out in the same two columns as the grid.
   *
   * It has to be placed explicitly. A finished or retired car used to get a RULE STATION
   * from parkedStation() and no lateral at all, so sampleAt painted it with the last
   * lateral its telemetry happened to carry -- measured at the flag on the shipped
   * packs: 327.00 m at Hungary (the replay.py clip), 60.19 m at Miami, 34.93 m at
   * Canada, 35.55 m at Monaco, 34.90 m at Britain. Cars drawn tens to hundreds of metres
   * beside the queue they are supposed to be sitting in. */
  private parkedLateral(driver: string): number {
    const idx = this.parkIndex.get(driver) ?? 0;
    // The parked queue is this file's own presentation placement, not one the producer
    // publishes, so its side comes from the fallback rule rather than grid.slots.
    return columnLateral(this.track, this.parkedStation(driver), paritySign(idx));
  }

  /**
   * The drawn lateral for a car still on its run to the first corner, or null once it is
   * past that (and for every car that never had a box).
   *
   * Eases the RULE stagger into whatever the feed reports, over the measured distance
   * from the box to the first corner. Null -- not 0 -- everywhere it does not apply, so
   * the caller falls through to the ordinary measured path untouched.
   *
   * A sample with no usable lateral of its own blends toward the centreline instead of
   * toward NaN: an absent reading must not be able to hide a car that is plainly there.
   */
  private launchBlend(
    driver: string, lap: RawLapEntry, sample: { stationM: number; lateralM: number } | null,
  ): number | undefined | null {
    if (lap.lap !== 1 || !sample || !Number.isFinite(sample.stationM)) return null;
    if (!this.gridBoxed.has(driver) || this.pitStarters.has(driver)) return null;
    const slot = this.gridIndex.get(driver) ?? -1;
    if (slot < 0) return null;
    const window = this.launchWindowM;
    if (window === null) return null;
    const box = gridSlotStation(this.track, slot);
    const L = this.track.lengthMetres;
    // FORWARD distance from the box, with the one wrap that is not forward motion folded
    // back to zero: a stationary car's station jitters either side of its own box by
    // centimetres, and a plain modulo reads a car 0.05 m short of its box as 5825.69 m
    // PAST it, which flickered it onto the racing line for a frame (measured on STR, the
    // one 2026 British GP car whose box is behind the timing line).
    //
    // It must NOT be a signed difference clamped at zero, which is what this was: that
    // maps every car more than half a lap from its box back to "still in its box", so the
    // stagger never decayed and the whole field was drawn 2-9 m off the racing line for
    // the WHOLE of lap 1 while the feed was reporting 0.0-0.15 m.
    const forward = (((sample.stationM - box) % L) + L) % L;
    const travelled = forward > L - BOX_JITTER_M ? 0 : forward;
    if (travelled >= window) return null;
    const u = travelled / window;
    const boxLateral = columnLateral(
      this.track, box, this.slotSign.get(driver) ?? paritySign(slot),
    );
    const measured = Number.isFinite(sample.lateralM) ? sample.lateralM : 0;
    return boxLateral * (1 - u) + measured * u;
  }

  /**
   * Whether a sample is on the PIT ROAD rather than on the circuit.
   *
   * Only ever asked of a lap that is already known to pit (`pin` is not null), which is
   * what makes both of its tests safe: every car crosses the entry stretch every lap, and
   * a car can run wide without pitting. What this adds is WHEN, for a car that is going
   * to pit -- the moment it leaves the circuit rather than the moment the lane's timing
   * point sees it, which measures 3.0-4.9 s later on every in-lap of the British pack.
   *
   * The station half is false when the artifact publishes no entry station, which is the
   * honest answer for a model that cannot say where its pit lane begins.
   *
   */
  private onPitRoad(sample: { stationM: number; lateralM: number } | null): boolean {
    if (!sample || !Number.isFinite(sample.stationM)) return false;
    // Off the racing line by more than any car on the circuit ever is. The published
    // entry is a POINT, and a car does not leave the track at a point: measured, the
    // divergence begins 18 m BEFORE the published entry at Spa and 4 m after it at
    // Silverstone, so the station test alone misses the first seconds at some circuits.
    if (Number.isFinite(sample.lateralM)
      && Math.abs(sample.lateralM) > PIT_DIVERGENCE_M) return true;
    const entry = this.track.pitLane.entryStation;
    if (entry === null) return false;
    const L = this.track.lengthMetres;
    const span = (((this.track.timingLines.sf - entry) % L) + L) % L;
    const along = (((sample.stationM - entry) % L) + L) % L;
    return along <= span;
  }

  /** The last real sample of a lap: used for the brief, effectively-instantaneous
   * "gap" state between one lap's sesT and the next lap's lST (see currentLap),
   * rather than snapping every such car to the same canonical line point. */
  private lastKnownPosition(dl: DriverLaps, lap: RawLapEntry): number {
    const decoded = dl.decoded.get(lap.lap);
    if (decoded && decoded.n > 0) return decoded.stationM[decoded.n - 1];
    return this.track.timingLines.sf;
  }

  private currentLap(dl: DriverLaps, t: number): { lap: RawLapEntry; idx: number } | null {
    let idx = -1;
    for (let i = 0; i < dl.laps.length; i++) {
      const l = dl.laps[i];
      if (l.lST !== null && l.lST <= t) idx = i; else break;
    }
    return idx >= 0 ? { lap: dl.laps[idx], idx } : null;
  }

  /** Laps completed (excluding FastF1-generated partial laps, per the plan) at time t,
   * plus fractional progress through the current lap, 0..1.
   *
   * `rankProgress` is the same quantity as (lapsDone + lapProgress) EXCEPT for cars that
   * are sitting at the line rather than running: a car that has just completed lap n is
   * exactly n laps round, not n + 1. The public `lapProgress` keeps reporting 1 for those
   * (the UI draws a full bar) while ranking and the lapped-car test use the honest value.
   * Getting this wrong reported a car 5 s behind the winner as a full lap down. */
  private progress(dl: DriverLaps, t: number): {
    lapsDone: number; lapProgress: number; rankProgress: number; stationM: number;
    lapEntry: RawLapEntry | null;
    kind: CarState["status"]; lateralOverride?: number; cur?: { lap: RawLapEntry; idx: number } | null;
    posProvenance: Provenance | null; officialPos: number | null; positionFrame: PositionFrame | null;
  } {
    const cur = this.currentLap(dl, t);
    // Grid slots sit BEHIND the start/finish line, and a car on the run to the line
    // reports a negative lap fraction for exactly that reason. Expressing the placed
    // slot on the same metre scale (slot n is n+1 car pitches back) is what lets a
    // stationary car and a launched car be compared at all -- with the slot pinned at
    // 0 the whole stationary field outranked everyone who had already moved.
    const slotFraction = (slot: number) =>
      -((slot + 1) * this.track.grid.pitchMetres) / this.track.lengthMetres;
    if (!cur) {
      const first = dl.laps[0];
      const slot = this.gridIndex.get(dl.entry.driver) ?? -1;
      if (this.pitStarters.has(dl.entry.driver)) {
        // Genuinely started from the pit lane (their lap 1 has a pit-exit time --
        // measured: ALO at the 2026 British GP). They are released after the field has
        // gone, so they rank behind the last grid slot.
        return this.pitLaneStart(first ?? null);
      }
      // A driver Python could not place has NO grid position, and the old fallback gave
      // EVERY such driver the same slot (gridOrder.length), stacking them on one point.
      // At Monaco that is 19 of 22 cars on a single coordinate -- the pile visible in
      // the render. Python already separates them into grid.unplaced with the reason;
      // the honest answer is the same as any other absent position: no station, no gap,
      // and the renderer draws nothing rather than drawing a heap.
      if (this.gridUnplaced.has(dl.entry.driver)) {
        return {
          lapsDone: 0, lapProgress: 0, rankProgress: slotFraction(this.gridOrder.length),
          stationM: NaN,
          lapEntry: first ?? null, kind: "grid", lateralOverride: NaN,
          posProvenance: null, officialPos: null, positionFrame: null,
        };
      }
      const safeSlot = slot >= 0 ? slot : this.gridOrder.length;
      const slotStation = gridSlotStation(this.track, safeSlot);
      // A real starting grid is two staggered columns, not a single file on the
      // centreline. The audit found the raw data gives a usable grid ORDER and ~8 m
      // spacing but no lateral at all (every car is snapped to one line), so the
      // left/right stagger is a labelled RULE-style presentation choice: the SIDE is
      // the producer's (grid.slots[].lateralSign), and the distance is whatever the
      // drawn road has been measured to support at that slot -- which on a circuit
      // drawn from a real model is nothing at all. See columnLateral.
      return {
        lapsDone: 0, lapProgress: 0, rankProgress: slotFraction(safeSlot),
        stationM: slotStation,
        lapEntry: first ?? null, kind: "grid",
        lateralOverride: columnLateral(
          this.track, slotStation,
          this.slotSign.get(dl.entry.driver) ?? paritySign(safeSlot),
        ),
        posProvenance: "RULE", officialPos: null, positionFrame: null,
      };
    }
    const { lap, idx } = cur;
    const decoded = dl.decoded.get(lap.lap);
    const nonFf1gLapsBefore = dl.laps.slice(0, idx).filter((l) => !l.ff1G && l.sesT !== null).length;
    // Which frame this lap's stations were written in, and therefore whether a station
    // taken from it is a MEASUREMENT or a rescaled wheel-speed integral. See PositionFrame.
    // Every observed branch below reads its station out of exactly this lap, so one test
    // covers all of them.
    // Anything that is not explicitly "A" is NOT a measurement. The old test collapsed
    // frame NONE (the encoder saying this lap has no usable position channel AT ALL) and
    // a missing tag (an older pack that predates the field) into "A", so both were
    // published as OBSERVED -- claiming a measurement precisely where the producer said
    // there is none. lapPositionFrame/lapPositionsMeasured exist to make that mistake
    // unavailable; use them rather than re-testing the string here.
    const rawFrame = lapPositionFrame(lap);
    const frame: PositionFrame | null =
      rawFrame === "B" ? "B" : rawFrame === "A" ? "A" : null;
    // null, not a provenance word: frame NONE means the encoder found no usable
    // position on this lap at all, and absence is not a place a value came from.
    const framedProvenance: Provenance | null = rawFrame === "A" ? "OBSERVED"
      : rawFrame === "B" ? "DERIVED"
      : null;

    if (lap.sesT !== null && t > lap.sesT) {
      // this lap is finished; are we between laps or at the end of the session?
      const next = dl.laps[idx + 1];
      const lapsDone = nonFf1gLapsBefore + (lap.ff1G ? 0 : 1);
      // This lap is over, so its own end-of-lap `pos` is now history, not the future.
      const finishedOfficial = officialOf(lap) ?? lastCompletedOfficial(dl.laps, idx, t);
      if (!next) {
        const isFinisher = !this.retiredSet.has(dl.entry.driver);
        return {
          lapsDone, lapProgress: 1, rankProgress: lapsDone,
          stationM: this.parkedStation(dl.entry.driver),
          lateralOverride: this.parkedLateral(dl.entry.driver),
          lapEntry: lap, kind: isFinisher ? "finished" : "retired",
          posProvenance: "RULE", officialPos: finishedOfficial, positionFrame: null,
        };
      }
      return {
        lapsDone, lapProgress: 1, rankProgress: lapsDone,
        stationM: this.lastKnownPosition(dl, lap), lapEntry: lap,
        kind: "gap", posProvenance: framedProvenance, officialPos: finishedOfficial,
        positionFrame: frame,
      };
    }

    if (!decoded || decoded.n === 0 || lap.lST === null) {
      return {
        lapsDone: nonFf1gLapsBefore, lapProgress: 0, rankProgress: nonFf1gLapsBefore,
        stationM: this.lastKnownPosition(dl, lap),
        lapEntry: lap, kind: "gap", posProvenance: framedProvenance, positionFrame: frame,
        // the CURRENT lap is still running, so its own `pos` is the future: use the
        // last one this car has actually earned.
        officialPos: lastCompletedOfficial(dl.laps, idx, t),
      };
    }
    const rel = t - lap.lST;
    const sample = sampleLap(decoded, rel, this.track.lengthMetres);

    // Before the car has actually launched, its lap-1 position is not usable: the
    // audit measured lap-1 xy staying STALE for 16-60 s and every stationary car
    // snapped to the centreline, so the whole field reads as one heap on the line.
    // Measured consequence: 70 % of frames in the first minute had cars intersecting,
    // versus 0 % after 20 minutes. While a car is still stationary on lap 1, keep it
    // in its own grid slot instead.
    // ...but only for a car that HAS a box. A pit-lane starter has none: it is held at
    // the end of the lane, and snapping it to a slot index past the last published one
    // invented a 22nd grid box on the racing line and left ALO parked in it for the whole
    // of the 2026 British GP, from the moment it stopped at the pit exit light.
    const boxed = this.gridBoxed.has(dl.entry.driver) && !this.pitStarters.has(dl.entry.driver);
    const stationarySlot = boxed ? (this.gridIndex.get(dl.entry.driver) ?? -1) : -1;
    if (lap.lap === 1 && sample && sample.speedKph < 1 && stationarySlot >= 0) {
      const slotStation = gridSlotStation(this.track, stationarySlot);
      return {
        lapsDone: 0, lapProgress: 0, rankProgress: slotFraction(stationarySlot),
        stationM: slotStation,
        lapEntry: lap, kind: "grid",
        lateralOverride: columnLateral(
          this.track, slotStation,
          this.slotSign.get(dl.entry.driver) ?? paritySign(stationarySlot),
        ),
        posProvenance: "RULE", positionFrame: null,
        // lap 1 is in progress and nothing has been classified yet
        officialPos: lastCompletedOfficial(dl.laps, idx, t),
      };
    }
    // THE LAUNCH. A car that has just left its box is drawn between the box and the
    // racing line, not snapped from one to the other.
    //
    // This is a presentation rule and it replaces nothing measured, because THE FEED
    // CARRIES NO LATERAL AT ALL HERE. Measured over the 2026 British GP's whole lap 1:
    // the standard deviation of every car's reported lateral is 0.03-0.16 m for the
    // first 1200 m and the largest single reading is 0.72 m, on a road 15-18 m wide with
    // a field that is genuinely two abreast off the line. Every car's x/y projects onto
    // the reference line, so "their own position" and "one single line on the racing
    // line" are the same thing in this data -- which is why the stagger vanished the
    // instant telemetry took over and the whole field slid sideways onto one file.
    //
    // The window is the circuit's own: pole to the first corner (336.6 m at Silverstone,
    // 318.9 m at Zandvoort, 602.6 m at Monza), which is the stretch over which a real
    // field does funnel from two columns into a queue. Past it the drawn lateral is the
    // feed's own value again, whatever that is worth.
    const launch = this.launchBlend(dl.entry.driver, lap, sample);
    if (launch !== null) {
      return {
        lapsDone: nonFf1gLapsBefore,
        lapProgress: lapDistanceFraction(
          decoded, sample, this.track.lengthMetres, this.track.timingLines.sf, rel,
        ),
        rankProgress: nonFf1gLapsBefore + lapDistanceFraction(
          decoded, sample, this.track.lengthMetres, this.track.timingLines.sf, rel,
        ),
        stationM: sample!.stationM,
        lapEntry: lap, kind: "track", cur,
        lateralOverride: launch,
        // The STATION is the measurement it always was; only the lateral is placed.
        posProvenance: framedProvenance, positionFrame: frame,
        officialPos: lastCompletedOfficial(dl.laps, idx, t),
      };
    }
    // A lap carrying a pit entry OR exit is a pit lap for its whole length. The
    // out-lap matters as much as the in-lap: the car spent part of it crawling down
    // the lane, so the station-derived gap is meaningless there (it was reporting a
    // P2 car as +90 s behind while P3 showed +4 s, which cannot both be true). The
    // dashboard shows PIT for these instead of a number it cannot stand behind.
    //
    // ON AN IN-LAP THE CLOCK IS NOT THE ENTRY. `pin` is the lane's own timing point,
    // some way down it, and the car has left the circuit well before that: measured over
    // all 52 in-laps of the 2026 British GP race, the car is already more than 15 m off
    // the ring 3.0-4.9 s BEFORE its own `pin` (median 3.7 s), at station 5326-5336
    // against a published pit entry of 5322.1. Every lap, no exceptions.
    //
    // For those seconds the car was drawn with honest pit-lane coordinates -- tens of
    // metres off the racing line -- while being called an on-circuit car, so it took the
    // racing surface's elevation instead of the lane's and declutterLanes was free to
    // shove it sideways. It reads as a car that has left the track. The entry the
    // producer publishes is the real boundary, so that is what decides it.
    const inPit = (lap.pin !== null && (t >= lap.pin || this.onPitRoad(sample)))
      || lap.pout !== null;
    // DISTANCE along the lap, never elapsed-time-over-own-lap-time. Dividing by each
    // car's own lap duration makes a car on a quicker lap read as further round at
    // the same instant than a slower car physically ahead of it -- which put a car
    // sitting second on the road at the top of the timing screen, with gaps that
    // decreased down the order. Distance is what actually decides who leads.
    const frac = lapDistanceFraction(
      decoded, sample, this.track.lengthMetres, this.track.timingLines.sf, rel,
    );
    // A withdrawn position makes `frac` non-finite. Two things must NOT happen then:
    // the car must not be ranked by a NaN (Array.sort with a NaN key is
    // implementation-defined, so one absent car scrambles the whole order), and it must
    // not be handed a plausible station. Falling back to the lap-table anchor is honest
    // -- lapsDone comes from the timing feed, not from x/y, so it is still measured --
    // and the station stays absent so no consumer can draw or gap it.
    const positionKnown = Number.isFinite(frac)
      && (!sample || Number.isFinite(sample.stationM));
    // A car that has not yet been RELEASED from the pit lane is not in the race, however
    // far down the ring its lane coordinates happen to project. Its own pit-exit time
    // says when that stops being true, so this holds only until `pout` and only on lap 1;
    // its drawn position stays the measurement throughout. Without it the 2026 British GP
    // opened with ALO -- stationary at the pit exit light, ring station 351 m -- shown as
    // the leader, ahead of a whole field still on its boxes at 110 m and behind.
    const heldInLane = lap.lap === 1 && this.pitStarters.has(dl.entry.driver)
      && lap.pout !== null && t < lap.pout;
    const measuredProgress = nonFf1gLapsBefore + (positionKnown ? frac : 0);
    return {
      lapsDone: nonFf1gLapsBefore,
      lapProgress: positionKnown ? frac : 0,
      rankProgress: heldInLane ? this.pitLaneRankProgress() : measuredProgress,
      // NOT the start/finish line. Substituting a real place on the circuit for an
      // unknown one is the same fabrication the encoder just stopped doing; NaN keeps
      // it absent, and the renderer skips a car it cannot place.
      stationM: positionKnown && sample ? sample.stationM : NaN,
      lapEntry: lap, kind: inPit ? "pit" : "track", cur,
      // Frame B has no x/y at all: the station is the driver's own integrated wheel-speed
      // distance rescaled onto one lap of the ring and the lateral is hard-zeroed, so it
      // is DERIVED. That is not cosmetic -- it drops the car out of `rank`'s measured
      // pool and out of `gapUsable`, so a derived position stops being published as a
      // measured gap. Measured at Monaco/Race: 10,574 of 12,148 reported gaps were
      // computed from a rescaled wheel-speed integral.
      posProvenance: framedProvenance, positionFrame: frame,
      // strictly causal: the classification at the end of the last COMPLETED lap.
      // Using the current lap's own pos would be reading the future.
      officialPos: lastCompletedOfficial(dl.laps, idx, t),
    };
  }

  /**
   * The INVERSE of a driver's progress curve: the session time at which this driver had
   * covered `x` laps of progress -- the same quantity `rankProgress` reports.
   *
   * This is how a gap is defined: "how long ago was the leader as far round as I am
   * now". Two properties make it the right primitive:
   *  - it is monotone in x by construction, so ordering the field by rankProgress (which
   *    is what `rank` does for every measured car) makes the reported gaps monotone down
   *    the board. That is a guarantee, not a hope.
   *  - it never has to match lap NUMBERS between two cars, which is what the previous
   *    closest-station-within-the-matching-lap scan did. That scan could not tell the
   *    start of a lap from its end, because a decoded lap starts a few metres before the
   *    line (median 2.8 m on the Australian pack) and therefore contains the same station
   *    twice; picking the wrong one added a whole lap to the gap (measured: 87.8 s).
   *
   * Returns null when x falls outside the stretch this driver's decoded laps cover
   * (a hole in the feed, or a progress this driver has not reached) -- the dashboard
   * prints a dash rather than a number that cannot be stood behind.
   */
  private timeAtProgress(dl: DriverLaps, x: number): number | null {
    const L = this.track.lengthMetres;
    const sf = this.track.timingLines.sf;
    // Distance covered only ever increases -- a car does not drive backwards -- so both
    // the lap anchors and the within-lap travel are taken as running maxima. Without
    // that, positional jitter of a few centimetres (and lap rows the feed did not
    // classify, which stall the lap count) make the inverse non-monotone, and a
    // non-monotone inverse is a gap column that ticks backwards: measured on the 2026
    // Monaco pack, one instant where the board's gaps fell by 0.08 s down the order.
    let lapsBefore = 0;
    let baseMax = -Infinity;
    let best: { lST: number; dec: DecodedLap; base: number } | null = null;
    // Consecutive lap traces OVERLAP by the few metres each one starts before the line,
    // so the tail of lap N can carry a timestamp slightly later than the head of lap
    // N+1. Capping lap N's answers at the moment lap N+1 began keeps the inverse
    // monotone across the boundary, and is true by definition: the car cannot have
    // passed a point on lap N after it had started lap N+1.
    let nextStart = Infinity;
    for (const lap of dl.laps) {
      const dec = dl.decoded.get(lap.lap);
      if (dec && dec.n > 0 && lap.lST !== null) {
        // A lap whose FIRST sample has a withdrawn position contributes NaN here, and
        // NaN poisons a running maximum permanently: Math.max(anything, NaN) is NaN and
        // `NaN > x` is false, so baseMax never recovers, `target` becomes NaN, and this
        // whole driver's timing inverse silently returns nothing for the rest of the
        // session -- the gap column just empties. Skip the unusable anchor instead; the
        // later laps still anchor the inverse.
        const anchor = lapsBefore + signedFromLine(dec.stationM[0], L, sf) / L;
        if (Number.isFinite(anchor)) {
          baseMax = Math.max(baseMax, anchor);
          if (baseMax > x) { nextStart = lap.lST; break; }
          best = { lST: lap.lST, dec, base: baseMax };
        }
      }
      if (!lap.ff1G && lap.sesT !== null) lapsBefore++;
    }
    if (!best) return null;
    const target = (x - best.base) * L;
    if (target < 0) return null;
    const { dec, lST } = best;
    let raw = 0, travelled = 0;
    for (let j = 1; j < dec.n; j++) {
      let step = dec.stationM[j] - dec.stationM[j - 1];
      // A withdrawn position on either end makes this step unknowable. Treating it as
      // zero says "the car did not move over this interval", which is wrong but BOUNDED
      // and keeps the walk monotone; adding NaN would poison `raw` for every later
      // sample and lose the whole lap, which is wrong AND unbounded.
      if (!Number.isFinite(step)) step = 0;
      if (step < -L / 2) step += L;
      if (step > L / 2) step -= L;
      raw += step;
      const previous = travelled;
      travelled = Math.max(travelled, raw);
      if (travelled >= target) {
        const span = travelled - previous;
        const f = span > 0 ? (target - previous) / span : 0;
        return Math.min(nextStart, lST + dec.tS[j - 1] + (dec.tS[j] - dec.tS[j - 1]) * f);
      }
    }
    return null; // this driver never got that far on the lap that should contain x
  }

  /**
   * Turns one instant's per-car progress into the running order, applying the
   * OFFICIAL-anchors / MEASURED-fills rule documented at the top of this file.
   *
   * Why this is built as a single lexicographic key rather than an if-ladder comparator:
   * the previous comparator mixed "compare official positions" with "compare measured
   * progress" depending on which fields happened to be populated, which is NOT
   * TRANSITIVE. With A.official=2, B.official=1 and C carrying no official at all, A<B
   * by official while A,C and B,C fall through to progress -- Array.sort on an
   * intransitive comparator produces an engine-defined order, and 24.3 % of instants in
   * the real data reach exactly that mixed state (see the header). Every key below is a
   * scalar compared in fixed order, so the result is a strict total order.
   */
  private rank(
    active: (readonly [string, ReturnType<ReplayTimeline["progress"]>])[],
  ): {
    order: (readonly [string, ReturnType<ReplayTimeline["progress"]>])[];
    source: Map<string, OrderSource>;
  } {
    const gridOf = (d: string) => this.gridIndex.get(d) ?? Number.MAX_SAFE_INTEGER;
    // A car that never took part at all sorts last; being momentarily STATIONARY is not
    // the same thing. Demoting every "grid" car below every "track" car meant the first
    // car whose telemetry ticked over 1 km/h jumped to P1 ahead of the whole stationary
    // field -- which is how a car sitting fourth on the road was shown as the leader.
    const started = (p: ReturnType<ReplayTimeline["progress"]>) => (p.lapEntry !== null ? 0 : 1);

    // 1. The physical order: distance covered, measured from the start/finish line.
    const byDistance = (
      a: readonly [string, ReturnType<ReplayTimeline["progress"]>],
      b: readonly [string, ReturnType<ReplayTimeline["progress"]>],
    ) => started(a[1]) - started(b[1])
      || b[1].rankProgress - a[1].rankProgress
      || gridOf(a[0]) - gridOf(b[0])
      || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0);
    const phys = active.slice().sort(byDistance);

    // 2. Anchors from the official classification. A car without one is placed halfway
    //    between its measured neighbours that do have one, so a hole in the feed shifts
    //    nobody. With no official value anywhere (every non-Race session, and lap 1 of a
    //    race) the anchors degrade to the measured order itself.
    const n = phys.length;
    const anchor = new Map<string, number>();
    const hasOfficial = phys.some(([, p]) => p.officialPos !== null);
    if (!hasOfficial) {
      phys.forEach(([d], i) => anchor.set(d, i));
    } else {
      const before: (number | null)[] = new Array(n).fill(null);
      const after: (number | null)[] = new Array(n).fill(null);
      let run: number | null = null;
      for (let i = 0; i < n; i++) { run = phys[i][1].officialPos ?? run; before[i] = run; }
      run = null;
      for (let i = n - 1; i >= 0; i--) { run = phys[i][1].officialPos ?? run; after[i] = run; }
      for (let i = 0; i < n; i++) {
        const own = phys[i][1].officialPos;
        if (own !== null) { anchor.set(phys[i][0], own); continue; }
        const a = before[i], b = after[i];
        anchor.set(phys[i][0], a !== null && b !== null ? (a + b) / 2
          : a !== null ? a + 0.5 : (b as number) - 0.5);
      }
    }

    // 3. The measured refinement. Cars whose position is OBSERVED hand their anchored
    //    slots back into one pool and take them again in measured order. This permutes
    //    ONLY within that set, so every RULE placement keeps the slot the official
    //    classification gave it, while the on-track pack is ordered by what the viewer
    //    can actually see -- and, critically, by the same quantity the gaps are measured
    //    from, which is what makes the gap column monotone.
    //
    //    A frame-B car is DERIVED, not OBSERVED, so it stays on its official anchor too.
    //    That is the point: its "measured distance" is its own integrated wheel speed
    //    rescaled to one lap of the ring, which is not comparable car-to-car, and at
    //    Monaco that is 91 % of the field. Ordering the race by the classification there
    //    is the honest answer, not a degraded one.
    const slotted = new Map(anchor);
    const source = new Map<string, OrderSource>();
    const measurable = phys.filter(([, p]) => p.posProvenance === "OBSERVED");
    const pool = measurable.map(([d]) => anchor.get(d)!).sort((a, b) => a - b);
    measurable.forEach(([d, p], k) => {
      slotted.set(d, pool[k]);
      source.set(d, p.officialPos !== null && pool[k] === anchor.get(d)! ? "OFFICIAL" : "MEASURED");
    });
    for (const [d, p] of phys) {
      if (!source.has(d)) source.set(d, p.officialPos !== null ? "OFFICIAL" : "RULE");
    }

    // 4. One strict total order. Restricted to the OBSERVED cars this is exactly `phys`
    //    (their slotted anchors are non-decreasing along it and ties fall back to
    //    distance), which is the property the gap-monotonicity test relies on.
    const order = phys.slice().sort((a, b) => started(a[1]) - started(b[1])
      || slotted.get(a[0])! - slotted.get(b[0])!
      || b[1].rankProgress - a[1].rankProgress
      || gridOf(a[0]) - gridOf(b[0])
      || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    return { order, source };
  }

  /**
   * @param withGaps compute gap-to-leader and interval, which cost two linear scans
   * of a ~700-sample lap PER CAR. Those numbers are only ever displayed on the ~10 Hz
   * dashboard, so the 60 Hz pose path passes false and skips roughly 1.7 M array
   * steps a second that nothing was going to read.
   */
  sampleAt(t: number, withGaps = true): Map<string, RankedCarState> {
    const out = new Map<string, RankedCarState>();
    const progressByDriver = new Map<string, ReturnType<ReplayTimeline["progress"]>>();
    for (const [driver, dl] of this.byDriver) progressByDriver.set(driver, this.progress(dl, t));

    const active = [...progressByDriver.entries()]
      .filter(([d]) => !this.retiredSet.has(d) || progressByDriver.get(d)!.kind !== "retired");
    const { order: ranked, source } = this.rank(active);
    const retiredRanked = this.retiredOrder
      .filter((d) => progressByDriver.get(d)?.kind === "retired")
      .map((d) => [d, progressByDriver.get(d)!] as const);
    for (const [d] of retiredRanked) source.set(d, "RULE");
    const order = [...ranked, ...retiredRanked];

    // Total progress, not the integer lap count: the instant the leader crosses the
    // line their lapsDone jumps by one, and every car still a few seconds behind on
    // the same lap would otherwise be reported "+1 LAP" down. A car is only really
    // lapped once it is a FULL lap of progress behind.
    const leaderProgress = order.length ? order[0][1].rankProgress : 0;

    order.forEach(([driver, p], i) => {
      const dl = this.byDriver.get(driver)!;
      const pt = trackPointAt(this.track, p.stationM);
      const { z, heading } = pt;

      // A RULE placement carries its own lateral and TELEMETRY MUST NOT OVERWRITE IT.
      // This used to be an unconditional `lateralM = s.lateralM`, which meant the placed
      // grid slots, the pit-lane start and the parked queue kept their placed STATION
      // while being painted with whatever lateral the car's own samples happened to
      // carry -- and on a stationary car that lateral is a projection of an absent or
      // frozen coordinate. Measured on the shipped packs, the median |lateral| drawn on
      // a car whose status is "grid": Monaco 35.55 m, Monza 19.00 m, Silverstone Sprint
      // 18.91 m, Hungary 13.83 m, Montreal 8.55 m -- a diagonal line of cars beside the
      // circuit instead of a starting grid -- while the RULE stagger was silently lost
      // on all 16 readable packs. Speed/gear/throttle/brake stay telemetry either way:
      // they are measured even when the position is placed.
      let lateralM = p.lateralOverride ?? 0, speedKph = 0, gear = 0, throttlePct = 0, brake = false;
      const placed = p.lateralOverride !== undefined;
      // reuse the lap progress() already located rather than scanning the lap list again
      const cur = p.cur !== undefined ? p.cur : this.currentLap(dl, t);
      if (cur && cur.lap.lST !== null) {
        const decoded = dl.decoded.get(cur.lap.lap);
        if (decoded) {
          const s = sampleLap(decoded, t - cur.lap.lST, this.track.lengthMetres);
          if (s) {
            if (!placed) lateralM = s.lateralM;
            speedKph = s.speedKph;
            gear = s.gear;
            throttlePct = s.throttlePct;
            brake = s.brake === 1;
          }
        }
      }

      let gapToLeaderS: number | null = null;
      let intervalS: number | null = null;
      const lapsDownFromLeader = Math.max(
        0, Math.floor(leaderProgress - p.rankProgress),
      );
      // A gap is a measurement. Computing one from a placed position (grid slot,
      // parked queue, pit start) would dress a rule up as data, so it is not done at
      // all: the dashboard shows a dash instead.
      //
      // A car in the pit lane is excluded for the same reason even though its position
      // IS observed: while it is in the lane its station is a projection of pit-lane
      // coordinates onto the racing ring, so the time the leader passed "that station"
      // is not the time it passed this car. That is what reported a P2 car as +90 s
      // behind while P3 showed +4 s. The dashboard already prints PIT there.
      //
      // Requiring OBSERVED (not merely "not RULE") is what keeps a frame-B car out:
      // its station is a rescaled wheel-speed integral, so `timeAtProgress` against it
      // would answer in the units of a different quantity from the one being asked about.
      const gapUsable = (q: ReturnType<ReplayTimeline["progress"]>) =>
        q.posProvenance === "OBSERVED" && q.kind !== "pit";
      const leaderObserved = gapUsable(order[0][1]);
      const aheadObserved = i > 0 && gapUsable(order[i - 1][1]);
      const selfObserved = gapUsable(p);
      if (withGaps && i > 0 && lapsDownFromLeader === 0 && selfObserved
          && (leaderObserved || aheadObserved)) {
        if (leaderObserved) {
          const at = this.timeAtProgress(this.byDriver.get(order[0][0])!, p.rankProgress);
          if (at !== null) gapToLeaderS = Math.max(0, t - at);
        }
        if (aheadObserved) {
          const at = this.timeAtProgress(this.byDriver.get(order[i - 1][0])!, p.rankProgress);
          if (at !== null) intervalS = Math.max(0, t - at);
        }
      }

      out.set(driver, {
        driver,
        team: dl.entry.team,
        stationM: p.stationM,
        lateralM,
        elevationM: z,
        headingRad: heading,
        speedKph,
        gear,
        throttlePct,
        brake,
        tyreCompound: p.lapEntry?.compound ?? null,
        tyreLife: p.lapEntry?.life ?? null,
        lapsDone: p.lapsDone,
        lapProgress: p.lapProgress,
        position: i + 1,
        gapToLeaderS,
        lapsDownFromLeader,
        intervalS,
        inPit: p.kind === "pit",
        status: p.kind,
        provenance: "OBSERVED",
        positionProvenance: p.posProvenance,
        positionFrame: p.positionFrame,
        orderSource: source.get(driver) ?? "RULE",
        energy: p.lapEntry?.energy ?? null,
      });
    });
    return out;
  }

  events(): RaceEvent[] {
    return this.raceControl.map((m) => ({
      sessionTime: m.sessionTime,
      kind: m.kind,
      message: m.message,
      drivers: m.cars,
      provenance: "OBSERVED" as const,
    }));
  }

  neutralisations(): NeutralisationInterval[] {
    return this.neutral;
  }

  weather() {
    if (!this.weatherRaw) return null;
    return {
      tS: this.weatherRaw.wT,
      airTempC: this.weatherRaw.wAT,
      trackTempC: this.weatherRaw.wTT,
      humidityPct: this.weatherRaw.wH,
      rain: this.weatherRaw.wR,
      windMps: this.weatherRaw.wWS,
      windFromDeg: this.weatherRaw.wWD ?? [],
      provenance: "OBSERVED" as const,
    };
  }
}
