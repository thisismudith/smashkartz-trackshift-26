import type { TrackCorner, TrackModel } from "../contract/types";
import type { LapEnergy } from "./source";

/** Raw shapes as written by scripts/simdata/*.py (see those files for the exact
 * provenance and derivation of every field). Only what the frontend consumes is
 * typed here; the artifacts carry more (audit trail) than the UI needs. */

export interface RawTrackModel {
  slug: string;
  event: string;
  /** dsMetres is the producer's own vertex spacing. geom.Ring now MEASURES the closed
   * polyline length and sets ds = length / n, so n * ds == lengthMetres holds BY
   * CONSTRUCTION and dsMetres === lengthMetres / xCm.length exactly. Nothing in the
   * frontend indexes with it -- ringDsMetres() derives the same number from the two
   * fields the renderer already has -- but manifest.test.ts pins the two together
   * against every shipped artifact, so a producer-side drift fails a test here. */
  ring: { dsMetres: number; lengthMetres: number; xCm: number[]; yCm: number[]; zCm: number[] };
  timingLines: { sf: { station: number } | null; s1: { station: number } | null; s2: { station: number } | null };
  corners: RawCorners | null;
  pitLane: {
    entryStation: number | null; exitStation: number | null;
    mergeStation: number | null; loopLateral: number | null;
  };
  grid: { order: string[]; pitchMetres: number };
  /** zCm may be null per vertex once Python stops shipping the held pit-lane elevation
   * as if it were measured; parseTrackModel turns that into NaN and the renderer takes
   * the drawn elevation from the adjacent racing surface either way. */
  pitLanePath: {
    segments: { role: string; xCm: number[]; yCm: number[]; zCm: (number | null)[] }[];
  } | null;
  width: { binMetres: number; halfWidth: number[] };
  referenceProfile: { binMetres: number; speedKph: number[]; gear: number[] };
}

export interface RawCorners {
  /** Official map DISPLAY orientation for this circuit, degrees. Never a frame offset;
   * see TrackCorners.rotationDeg and TrackModel.mapRotationDeg. */
  rotationDeg: number | null;
  corners: {
    number: number;
    station: number;
    markerLateral: number | null;
    labelAngleDeg: number | null;
  }[];
}

/** The corner type is part of the shared contract (contract/types.ts) so the renderer
 * can name these fields without a cast. Re-exported here because this is where corners
 * are parsed. */
export type { TrackCorner };

export interface TrackCorners {
  /**
   * The official map's DISPLAY orientation, degrees. It is NOT an offset between the
   * telemetry frame and anything else and MUST NOT be applied as a rotation to the
   * ring, the cars or the camera -- it only says how the circuit is conventionally
   * printed. Exposed so a map-style view can match the official artwork on request.
   */
  rotationDeg: number | null;
  corners: TrackCorner[];
}

function finiteOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Parses the corner block, keeping the metadata parseTrackModel used to discard.
 * Absent fields become null rather than a stand-in number, and a corner whose STATION
 * is unusable is dropped rather than placed at station 0 -- an invented position on the
 * racing line is exactly what AGENTS.md 42.5 forbids. */
export function parseCorners(raw: RawTrackModel): TrackCorners {
  const block = raw.corners;
  if (!block || !Array.isArray(block.corners)) return { rotationDeg: null, corners: [] };
  const corners: TrackCorner[] = [];
  for (const c of block.corners) {
    const station = finiteOrNull(c?.station);
    const number = finiteOrNull(c?.number);
    if (station === null || number === null) continue;
    corners.push({
      number,
      station,
      markerLateral: finiteOrNull(c.markerLateral),
      labelAngleDeg: finiteOrNull(c.labelAngleDeg),
    });
  }
  return { rotationDeg: finiteOrNull(block.rotationDeg), corners };
}

/**
 * The per-lap position frame, exactly as scripts/simdata/replay.py tags it.
 *
 *   "A"    station/lateral projected from the lap's own MEASURED x/y.
 *   "B"    no usable x/y (Monaco Race is 91.2% frame B): station is the distance
 *          channel normalised onto the ring and lateral is hard-zeroed. Not measured.
 *   "NONE" the lap carries no usable position at all. Emitted once py-replay stops
 *          fabricating a linspace for a lap with no distance either (AGENTS.md 42.5).
 *
 * Anything this file does not recognise -- INCLUDING THE FIELD BEING ABSENT, which is
 * what a pack built before the tag existed looks like -- is "UNKNOWN". It is never
 * silently read as "A": presenting an untagged lap as measured telemetry is the
 * fabricated certainty AGENTS.md 42.4 forbids. Read it with lapPositionFrame().
 */
export type PositionFrame = "A" | "B" | "NONE";
export type LapPositionFrame = PositionFrame | "UNKNOWN";

/** The measured discriminators rawio.Lap.position_quality() reports for a lap, if the
 * producer forwards them into the manifest. Numbers, never a verdict -- the threshold
 * that decides "may this lap define the circuit's shape" is not the threshold that
 * decides "may this lap be replayed". null means the lap had too few finite samples to
 * measure that quantity. */
export interface RawPositionQuality {
  xyFraction: number | null;
  pathOverSpan: number | null;
  uniqueFrac: number | null;
  repeatBackFrac: number | null;
  endGapM: number | null;
  medianStepM: number | null;
}

export interface RawLapEntry {
  lap: number;
  byteOffset: number;
  sampleCount: number;
  /** Optional on purpose: absent in any pack built before the tag, and absence means
   * UNKNOWN rather than "A". See PositionFrame; read it with lapPositionFrame(). */
  positionFrame?: PositionFrame | null;
  /** Count of samples whose x/y the stuck/sentinel detector withdrew before the lap was
   * encoded (rawio.Lap.position_dropped). ABSENT MEANS "the producer does not report
   * this", NOT zero -- lapPositionDropped() returns null for that, so a UI can say
   * "unknown" instead of claiming a clean lap. */
  positionDropped?: number | null;
  /** rawio.Lap.position_quality() for this lap, when the producer forwards it. */
  positionQuality?: RawPositionQuality | null;
  lST: number | null;
  sesT: number | null;
  time: number | null;
  pin: number | null;
  pout: number | null;
  status: string;
  pos: number | null;
  compound: string | null;
  stint: number | null;
  life: number | null;
  fresh: boolean | null;
  iacc: boolean;
  del: boolean;
  ff1G: boolean;
  /** Python energy twin output for this lap; null when it could not be computed. */
  energy: LapEnergy | null;
}

/** The lap's position frame, normalised. An unrecognised or missing tag is "UNKNOWN". */
export function lapPositionFrame(lap: { positionFrame?: string | null }): LapPositionFrame {
  const f = lap.positionFrame;
  return f === "A" || f === "B" || f === "NONE" ? f : "UNKNOWN";
}

/** True ONLY for frame A -- i.e. only when this lap's station and lateral come from
 * projecting real measured coordinates. Frame B, NONE and UNKNOWN are all false, so a
 * caller that gates gap/interval maths on "measurable" cannot accidentally feed it a
 * distance-normalised station. */
export function lapPositionsMeasured(lap: { positionFrame?: string | null }): boolean {
  return lapPositionFrame(lap) === "A";
}

/** Samples whose position was withdrawn on this lap, or null when the producer does
 * not report it. Null is "unknown", which is not the same claim as 0. */
export function lapPositionDropped(lap: { positionDropped?: number | null }): number | null {
  return finiteOrNull(lap.positionDropped);
}

export interface RawDriverEntry {
  driver: string;
  team: string | null;
  laps: RawLapEntry[];
}

export interface RawSessionManifest {
  event: string;
  session: string;
  trackSlug: string;
  trackLengthMetres: number;
  sampleStructBytes: number;
  binFile: string;
  drivers: RawDriverEntry[];
  raceControl: { sessionTime: number; kind: string; message: string; cars: string[] }[];
  neutralisation: { kind: "SC" | "VSC" | "RED"; start: number; end: number }[];
  /** wT is session-absolute (same as lST/sesT) and wR is a boolean array even though
   * it is typed as number[] here for a uniform shape; consumers normalise wT by the
   * same race-start offset as everything else before use. */
  weather: {
    wT: number[]; wAT: number[]; wTT: number[]; wH: number[];
    wR: boolean[]; wWS: number[]; wWD?: number[];
  } | null;
  /** hasPositions is true when ANY lap is frame A. positionSamplesDropped is optional:
   * the session-wide count the sentinel/stuck detector withdrew, absent (not 0) until
   * py-replay reports it. */
  capabilities: {
    hasPositions: boolean;
    hasDriverAhead: boolean;
    positionSamplesDropped?: number | null;
  };
}

export function parseTrackModel(raw: RawTrackModel): TrackModel {
  const n = raw.ring.xCm.length;
  const x = new Float32Array(n), y = new Float32Array(n), z = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    x[i] = raw.ring.xCm[i] / 100;
    y[i] = raw.ring.yCm[i] / 100;
    z[i] = raw.ring.zCm[i] / 100;
  }
  const halfWidth = Float32Array.from(raw.width.halfWidth);
  const profile = raw.referenceProfile;
  const corners = parseCorners(raw);
  return {
    slug: raw.slug,
    event: raw.event,
    lengthMetres: raw.ring.lengthMetres,
    x, y, z,
    halfWidth,
    widthBinMetres: raw.width.binMetres,
    timingLines: {
      sf: raw.timingLines.sf?.station ?? 0,
      s1: raw.timingLines.s1?.station ?? null,
      s2: raw.timingLines.s2?.station ?? null,
    },
    corners: corners.corners,
    // Carried, never applied: it is the printed map's orientation, not a frame offset.
    mapRotationDeg: corners.rotationDeg,
    pitLane: {
      entryStation: raw.pitLane.entryStation,
      exitStation: raw.pitLane.exitStation,
      mergeStation: raw.pitLane.mergeStation,
      loopLateral: raw.pitLane.loopLateral,
    },
    grid: { order: raw.grid.order, pitchMetres: raw.grid.pitchMetres },
    pitLanePath: raw.pitLanePath
      ? raw.pitLanePath.segments.map((seg) => ({
          role: seg.role,
          x: Float32Array.from(seg.xCm, (v) => v / 100),
          y: Float32Array.from(seg.yCm, (v) => v / 100),
          // an explicitly unavailable elevation stays unavailable (NaN), rather than
          // silently becoming 0 m above sea level
          z: Float32Array.from(seg.zCm, (v) => (v === null ? NaN : v / 100)),
        }))
      : null,
    referenceProfile: {
      binMetres: profile.binMetres,
      speedKph: Float32Array.from(profile.speedKph),
      gear: Uint8Array.from(profile.gear),
    },
  };
}

/**
 * The ring's vertex spacing in metres, and the ONE place that derivation lives.
 *
 * geom.Ring measures the closed polyline length and sets ds = length / n, so
 * n * ds == lengthMetres by construction: this is EXACT, not an approximation of the
 * artifact's dsMetres, and it matches the producer's own spacing bit for bit (pinned
 * on all 13 shipped models in manifest.test.ts). Every station -> vertex-index mapping
 * in the frontend must come through here so there is a single definition rather than a
 * copy per render file.
 */
export function ringDsMetres(track: Pick<TrackModel, "lengthMetres" | "x">): number {
  return track.lengthMetres / track.x.length;
}

/**
 * Heading of the ring between vertices a0 and b0, radians in the DATA frame.
 *
 * Two ring vertices can be bit-identical: measured on the shipped models there is
 * exactly one zero-length segment on the Hungarian ring and one on Monaco's (every
 * other circuit's minimum step is 0.32-0.98 m). atan2(0, 0) returns 0, i.e. "due +x",
 * which is a fabricated bearing that the renderer then faithfully points a car along.
 * Widen the chord symmetrically -- keeping the same midpoint, so the heading does not
 * shift phase -- until it has length, and report NaN rather than a direction if the
 * ring is collapsed for eight vertices in a row.
 */
function chordHeading(track: Pick<TrackModel, "x" | "y">, a0: number, b0: number): number {
  const n = track.x.length;
  let a = a0, b = b0;
  for (let k = 0; k < 8; k++) {
    const dx = track.x[b] - track.x[a];
    const dy = track.y[b] - track.y[a];
    if (dx !== 0 || dy !== 0) return Math.atan2(dy, dx);
    a = (a - 1 + n) % n;
    b = (b + 1) % n;
  }
  return NaN;
}

/** Ring point at an arbitrary station, with the heading of the ring segment it lands
 * on. ds is lengthMetres / n (ringDsMetres): the ring is resampled at a constant
 * spacing and Ring.ds is derived from the measured length, so station and vertex index
 * are the same axis and this indexing is exact.
 *
 * NOTE the heading is the FORWARD difference across the one segment the station falls
 * in, not a smoothed tangent (an earlier comment here claimed a central difference; it
 * never was one). Measured per-metre heading jitter on the shipped rings: median
 * 0.36-0.52 deg, p99 1.6-3.7 deg. */
export function trackPointAt(track: TrackModel, station: number) {
  const n = track.x.length;
  const ds = ringDsMetres(track);
  const s = ((station % track.lengthMetres) + track.lengthMetres) % track.lengthMetres;
  const f = s / ds;
  const i0 = Math.floor(f) % n;
  const i1 = (i0 + 1) % n;
  const frac = f - Math.floor(f);
  const x = track.x[i0] + (track.x[i1] - track.x[i0]) * frac;
  const y = track.y[i0] + (track.y[i1] - track.y[i0]) * frac;
  const z = track.z[i0] + (track.z[i1] - track.z[i0]) * frac;
  const heading = chordHeading(track, i0, i1);
  return { x, y, z, heading };
}

/**
 * Half-width is stored one value per widthBinMetres bin (25 m), binned in Python as
 * floor(station / binMetres), so bin i covers [i*bin, (i+1)*bin).
 *
 * Indexing straight into that array makes the track edge jump in a visible staircase
 * every 25 m, so this interpolates linearly between adjacent BIN CENTRES, wrapping
 * circularly. The catch the old version missed: there are ceil(length / bin) bins, so
 * THE LAST BIN IS CLIPPED by the ring length and its centre is not (nb - 0.5) * bin.
 * Assuming a full-width final bin misplaced the edge across the start/finish line by
 * up to 0.19 m of half-width at Canada and 0.11 m at Silverstone (0 m where the length
 * divides by the bin size exactly, as at Spa).
 */
export function halfWidthAt(track: TrackModel, station: number): number {
  const nb = track.halfWidth.length;
  if (nb === 0) return NaN;
  if (nb === 1) return track.halfWidth[0];
  const L = track.lengthMetres;
  const bin = track.widthBinMetres;
  const s = ((station % L) + L) % L;
  const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

  const cFirst = bin / 2;
  const cPrev = (nb - 1.5) * bin;                              // centre of the last FULL bin
  const cLast = ((nb - 1) * bin + Math.min(nb * bin, L)) / 2;  // centre of the clipped bin

  if (s >= cPrev && s <= cLast) {
    return lerp(track.halfWidth[nb - 2], track.halfWidth[nb - 1], (s - cPrev) / (cLast - cPrev));
  }
  if (s > cLast || s < cFirst) {                               // the wrap, clipped bin -> bin 0
    const span = cFirst + L - cLast;
    const off = s < cFirst ? s + L - cLast : s - cLast;
    return lerp(track.halfWidth[nb - 1], track.halfWidth[0], off / span);
  }
  const f = s / bin - 0.5;                                     // uniform interior bins
  const i0 = Math.floor(f);
  return lerp(track.halfWidth[i0], track.halfWidth[i0 + 1], f - i0);
}
