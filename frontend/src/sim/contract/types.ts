/**
 * The one contract both Replay and New Race emit (plan section 6). The renderer, the
 * store and every dashboard panel are written once, against this interface, and never
 * know which source produced it.
 */

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
  corners: { number: number; station: number }[];
  pitLane: {
    entryStation: number | null;
    exitStation: number | null;
    mergeStation: number | null;
    loopLateral: number | null;
  };
  /** Explicit XY(Z) polyline of the pit lane, metres, in the ring's own frame.
   * Null when a session has too few pit laps to trace one. */
  pitLanePath: { x: Float32Array; y: Float32Array; z: Float32Array } | null;
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
  sampleAt(sessionTime: number): Map<string, CarState>;
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
