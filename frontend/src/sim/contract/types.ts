/**
 * The one contract both Replay and New Race emit (plan section 6). The renderer, the
 * store and every dashboard panel are written once, against this interface, and never
 * know which source produced it.
 */

import type { LapEnergy } from "../data/source";

export type Provenance = "OBSERVED" | "DERIVED" | "INFERRED" | "SIMULATED" | "RULE" | "DEFAULT";

/** Per-car state at one instant, in TRACK-FRAME coordinates (never raw x/y): a
 * station along the ring's arc length, a signed lateral offset, and the quantities
 * the dashboard and renderer both need. */
export interface CarState {
  driver: string;
  team: string | null;
  stationM: number;
  lateralM: number;
  elevationM: number;
  headingRad: number;
  speedKph: number;
  gear: number;
  throttlePct: number;
  brake: boolean;
  tyreCompound: string | null;
  tyreLife: number | null;
  lapsDone: number;
  lapProgress: number; // 0..1 within the current lap
  position: number; // 1-based running order
  gapToLeaderS: number | null;
  lapsDownFromLeader: number;
  intervalS: number | null;
  inPit: boolean;
  status: "grid" | "track" | "pit" | "finished" | "retired" | "gap";
  provenance: Provenance;
  /** Where this car's POSITION came from. OBSERVED = projected from real telemetry.
   * RULE = a documented placement the feed does not contain (grid slot, parked queue,
   * pit-lane start). Gaps are never computed from a RULE position. */
  /** Where this car's POSITION came from. OBSERVED = projected from real telemetry.
   * RULE = a documented placement the feed does not contain (grid slot, parked queue,
   * pit-lane start). **null = there is no position** -- the producer withdrew one it
   * could not place, so the car is not drawn and no gap is computed from it. null is
   * used rather than a seventh provenance word because absence is not a provenance:
   * every tag in the vocabulary describes where a value CAME FROM, and there is no
   * value here. Gaps are never computed from a RULE or null position. */
  positionProvenance: Provenance | null;
  /** Energy twin summary for the lap this car is on (INFERRED/SIMULATED). */
  energy: LapEnergy | null;
}

export interface RaceEvent {
  sessionTime: number;
  kind: string;
  message: string;
  drivers: string[];
  provenance: Provenance;
}

export interface NeutralisationInterval {
  kind: "SC" | "VSC" | "RED";
  start: number;
  end: number;
}

/**
 * One corner of the circuit.
 *
 * markerLateral and labelAngleDeg used to be parsed away and dropped, and that is why
 * nothing on screen ever contradicted a mirrored render frame: markerLateral is the
 * ONLY shipped quantity that states which SIDE of the road something is on. A
 * direction-aware overlay has to be built against it, so it is part of the contract.
 */
export interface TrackCorner {
  /** Corner number as the circuit publishes it (1-based, not an index). OBSERVED. */
  number: number;
  /** Station along the ring's arc length, metres. DERIVED: the corner marker's X/Y
   * projected onto the ring, never the feed's own Distance field. */
  station: number;
  /** Signed perpendicular offset of the corner marker from the racing line, metres.
   * Positive is to the LEFT of travel -- the same convention as every other lateral
   * in the pipeline (scripts/simdata/geom.py). Null when the source carried none;
   * undefined when the producer of this TrackModel does not supply corner metadata
   * at all. Neither is a zero. */
  markerLateral?: number | null;
  /** Bearing for the corner's number plate on the OFFICIAL MAP, degrees. Display
   * metadata: it belongs to the printed map, not to the telemetry frame. */
  labelAngleDeg?: number | null;
}

/**
 * The measured map from telemetry metres onto the circuit model's own Y-up world frame
 * (scripts/simdata/glb_surface.py, `Fit`):
 *
 *     u  = (scale*x, scale*mirror*y)
 *     wx = cos(yaw)*u.x - sin(yaw)*u.y + txM
 *     wz = sin(yaw)*u.x + cos(yaw)*u.y + tzM
 *     wy = scale*z + tyM
 *
 * Every one of these six numbers was FITTED against the telemetry ring, never read out
 * of the model's own metadata -- including `scale`, which is why "both models are already
 * in real metres" is a result rather than an assumption. `mirror` is a parameter for the
 * same reason: telemetry is right-handed with z up, so the horizontal map picks up a
 * reflection that the fit has to be free to confirm or reject.
 */
export interface TrackSurfaceTransform {
  scale: number;
  yawDeg: number;
  /** +1 or -1. Both circuits measured -1. */
  mirror: number;
  txM: number;
  tzM: number;
  tyM: number;
}

/**
 * The drive surface baked under the ring from a real circuit model, sampled at the SAME
 * stations as the ring (one value per ring vertex, so index i is station i * ds).
 *
 * OPTIONAL, AND ABSENT IS THE NORMAL CASE: 12 of 13 circuits have no model that clears
 * the quality gate, and no artifact built before schemaVersion 2 carries one at all. A
 * circuit without this block keeps the procedural ribbon and today's elevation, which is
 * why absence has to read as "there is no baked surface" and never as a height of zero.
 *
 * Provenance is DERIVED, not OBSERVED: this height was never measured from a car. It is
 * a third-party model's geometry read by an exact raycast under a transform fitted to
 * the OBSERVED ring, and AGENTS.md 13.6 is explicit that producing a value FROM observed
 * inputs does not make it observed. A station the raycast missed is NaN here with its
 * `valid` byte clear -- not a filled-in height, and not a seventh provenance word.
 */
export interface TrackSurface {
  /** vertex spacing of the arrays, metres. Equal to the ring's own ds. */
  dsMetres: number;
  /** file name of the SOURCE model the bake was measured on. */
  source: string;
  /** sha256 of that source model, or null. NOT the hash of the served asset. */
  sourceSha256: string | null;
  /** which Python extractor profile produced the bake. */
  profile: string | null;
  transform: TrackSurfaceTransform;
  /** surface height in the TELEMETRY frame, metres. NaN where the raycast missed. */
  zM: Float32Array;
  /** along-track gradient dz/ds (tan of the slope angle), + uphill. NaN where absent. */
  slope: Float32Array;
  /** cross-track gradient (tan of the camber angle), + when the LEFT of travel is
   * higher -- the same "+ is left of travel" convention as every lateral in the
   * pipeline. NaN where absent, which happens independently of zM. */
  camber: Float32Array;
  /** Distance, metres, from the ring OUT to the edge of drivable surface on each side --
   * the RING is the racing line here, not the road centre, so these are two independent
   * measurements, never a symmetric half-width. Measured at Silverstone: at the grid
   * (station ~5770-5818 m) the asphalt runs 1.75-2.00 m LEFT of the ring and 15.0-17.5 m
   * RIGHT of it -- a car placed by a symmetric RULE half-width there sits on the grass or
   * jammed against the kerb, which is exactly the defect this replaces. NaN where the
   * walk could not take a single step on that side (no asphalt reachable outward), which
   * is a fact worth keeping, not a zero. Independent of zM's own validity. */
  edgeLeftM: Float32Array;
  edgeRightM: Float32Array;
  /** 1 where the station has a measured height, 0 where it has none. */
  valid: Uint8Array;
  residual: { stdM: number | null; maxM: number | null };
  /** fraction of stations with a measured height, and the stricter fraction that landed
   * on a surface the model NAMES as road. Null when the producer reported neither. */
  coverage: number | null;
  roadCoverage: number | null;
  /** served URL of the published, content-hashed model, and the sha256 of those
   * published bytes. Null when the artifact names no asset -- in which case there is
   * nothing to fetch and the circuit stays on its ribbon. */
  assetUrl: string | null;
  assetSha256: string | null;
  /** The leading provenance word of the producer's own tag, validated against the
   * six-word vocabulary. Null when it carried none or carried something else: absence
   * is null, never a seventh word. */
  provenance: Provenance | null;
  /** the producer's full provenance sentence, including the method it describes. */
  provenanceNote: string | null;
}

/** One station's baked surface. Every channel is optional EXCEPT the height: a station
 * can have a measured height and no measurable camber, or a measured height and no
 * measured road edge on either side. */
export interface TrackSurfaceSample {
  zM: number;
  slope: number | null;
  camber: number | null;
  /** Metres from the ring out to drivable surface, this side. Null, independently on
   * either side, where the walk found no road that way -- never zero, and never the
   * other side's value. */
  edgeLeftM: number | null;
  edgeRightM: number | null;
}

export interface TrackModel {
  slug: string;
  event: string;
  lengthMetres: number;
  /** Ring sampled at 1 m, metres, closed (index 0 == index n wraps). */
  x: Float32Array;
  y: Float32Array;
  z: Float32Array;
  halfWidth: Float32Array; // per 25 m bin; index via (station / binMetres) % bins
  widthBinMetres: number;
  timingLines: { sf: number; s1: number | null; s2: number | null };
  /** The circuit's corners, with the metadata the artifacts actually carry. Read a
   * corner's side of the road from markerLateral; see TrackCorner. */
  corners: TrackCorner[];
  /**
   * The OFFICIAL CIRCUIT MAP'S DISPLAY ORIENTATION, degrees, straight from the
   * feed's corners.json `Rotation`. Null when the source did not carry one.
   *
   * IT IS NOT A FRAME OFFSET AND MUST NEVER BE APPLIED AS A ROTATION to the ring,
   * the cars, the pit lane or the camera. It only states how this circuit is
   * conventionally PRINTED (Monaco 315 deg, Barcelona 303 deg, Dutch 0 deg), and
   * rotating the world by it would move every car off its own measured coordinates.
   * Exposed so a map-style view can match the official artwork on request, and so
   * that nobody has to guess what the number in the artifact means.
   */
  mapRotationDeg?: number | null;
  pitLane: {
    entryStation: number | null;
    exitStation: number | null;
    mergeStation: number | null;
    loopLateral: number | null;
  };
  /** The pit lane as SEPARATE roads (entry, exit) in the ring's own frame, metres.
   * Never one stitched path: the two are different pieces of tarmac and joining
   * them folds the ribbon back on itself. Null when too few pit laps to trace. */
  pitLanePath: { role: string; x: Float32Array; y: Float32Array; z: Float32Array }[] | null;
  grid: { order: string[]; pitchMetres: number; unplaced?: string[] | null };
  referenceProfile: { binMetres: number; speedKph: Float32Array; gear: Uint8Array };
  /** The drive surface baked from a real circuit model, when this artifact carries one.
   * Undefined on a TrackModel built from a pre-schemaVersion-2 artifact, null when the
   * artifact carried a block this build could not use. Both mean "no baked surface";
   * read it through surfaceAt(), which never invents a height. */
  surface?: TrackSurface | null;
}

/** Weather is only ever OBSERVED (Replay) or a fixed configuration (New Race, which
 * can still report it through this same shape for the weather strip). Times are
 * session-relative seconds, already normalised to the same t=0 as sampleAt. */
export interface WeatherSeries {
  tS: number[];
  airTempC: number[];
  trackTempC: number[];
  humidityPct: number[];
  rain: boolean[];
  windMps: number[];
  /** Compass bearing the wind blows FROM, degrees. */
  windFromDeg: number[];
  provenance: Provenance;
}

/** The shared per-frame contract. sampleAt is called at ~60 Hz from the worker;
 * dashboardSnapshot at ~10 Hz. */
export interface RaceTimeline {
  provenance: "OBSERVED" | "SIMULATED";
  runId: string;
  track: TrackModel;
  driverList: string[];
  totalLaps: number | null;
  /** The session-time domain this timeline covers, seconds. */
  duration: number;
  sampleAt(sessionTime: number, withGaps?: boolean): Map<string, CarState>;
  events(): RaceEvent[];
  neutralisations(): NeutralisationInterval[];
  weather(): WeatherSeries | null;
}

export interface DashboardRow {
  position: number;
  driver: string;
  team: string | null;
  gapToLeader: string;
  interval: string;
  compound: string | null;
  tyreLife: number | null;
  status: CarState["status"];
  lastLapS: number | null;
  energy: LapEnergy | null;
  speedKph: number;
  gear: number;
  throttlePct: number;
  brake: boolean;
  lapProgress: number;
}

export interface DashboardSnapshot {
  sessionTime: number;
  leaderboard: DashboardRow[];
  activeNeutralisation: NeutralisationInterval | null;
  recentEvents: RaceEvent[];
}
