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
  positionProvenance: Provenance;
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
  grid: { order: string[]; pitchMetres: number };
  referenceProfile: { binMetres: number; speedKph: Float32Array; gear: Uint8Array };
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
