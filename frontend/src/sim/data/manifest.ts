import type {
  Provenance, TrackCorner, TrackModel, TrackSurface, TrackSurfaceSample,
  TrackSurfaceTransform,
} from "../contract/types";
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
  grid: {
    order: string[];
    pitchMetres: number;
    /** Drivers whose lap-1 position is NOT a measurement -- a shared placeholder
     * coordinate, or a projection implausibly far off the ring. Python already
     * separates these from `order` and from `pitStarters`; parsing it here is what
     * stops the frontend inventing a slot for them. */
    unplaced?: string[] | null;
    /** The PLACEMENT Python actually published for each slot. Optional only because
     * an artifact built before it existed has none; every shipped model carries it
     * (12 of 13 with a full field, monaco-grand-prix with 2 and
     * chinese-grand-prix with 0, which is that pack's broken lap-1 positions). */
    slots?: RawGridSlot[] | null;
  };
  /** zCm may be null per vertex once Python stops shipping the held pit-lane elevation
   * as if it were measured; parseTrackModel turns that into NaN and the renderer takes
   * the drawn elevation from the adjacent racing surface either way. */
  pitLanePath: {
    segments: { role: string; xCm: number[]; yCm: number[]; zCm: (number | null)[] }[];
  } | null;
  width: { binMetres: number; halfWidth: number[] };
  referenceProfile: { binMetres: number; speedKph: number[]; gear: number[] };
  /** 2 once a build can carry a `surface` block. Absent on every artifact written
   * before that, which is not an error. */
  schemaVersion?: number;
  /** OPTIONAL, AND USUALLY ABSENT: only a circuit whose real model clears the quality
   * gate carries one (1 of 13 today), and no pre-schemaVersion-2 artifact carries one
   * at all. Absence is the ordinary case and must never be read as a surface of
   * zeroes -- see parseTrackSurface. */
  surface?: RawTrackSurface | null;
}

/** The `surface` block exactly as scripts/simdata/glb_surface.py's `surface_block()`
 * writes it, plus the two fields the publisher adds to name the served asset. Heights
 * and gradients are quantised ints with `null` -- never a stand-in number -- at a
 * station the raycast missed. */
export interface RawTrackSurface {
  dsMetres: number;
  source: string;
  sourceSha256: string | null;
  profile: string | null;
  /** the fitted transform, plus its own restatement in the model's raw Z-up
   * coordinates (glbXOffsetM/glbYOffsetM/glbZOffsetM). The restatement is derived from
   * the same three translations, so only the six-parameter form is parsed. */
  transform: {
    scale: number; yawDeg: number; mirror: number;
    txM: number; tzM: number; tyM: number;
  };
  zCm: (number | null)[];
  slopePermille: (number | null)[];
  camberPermille: (number | null)[];
  /** 1 per station, 1 = the raycast landed. */
  validMask: number[];
  residual: { stdM: number | null; maxM: number | null };
  coverage: number | null;
  roadCoverage?: number | null;
  /** served URL of the published model, `/sim/glb/<slug>.<sha10>.glb`, and the sha256
   * of those published bytes. Added by the publisher, not by the bake. */
  assetUrl?: string | null;
  assetSha256?: string | null;
  provenance?: string | null;
}

/** One starting-grid slot exactly as scripts/simdata/track.py writes it. */
export interface RawGridSlot {
  position: number;
  driver: string | null;
  station: number;
  /** +1 or -1: which side of the ring Python put this slot on. A RULE, like the
   * anchor and the pitch (grid.provenance: "anchor and stagger RULE (absent from the
   * feed)") -- but it is the PRODUCER'S rule, and this is the only copy of it. */
  lateralSign: number;
}

/** A parsed grid slot. Same shape, with anything unusable dropped rather than guessed. */
export interface GridSlot {
  /** 1-based grid position, as published. */
  position: number;
  driver: string | null;
  station: number;
  /** -1 or +1. Never 0: a slot whose sign the producer did not state is dropped. */
  lateralSign: -1 | 1;
}

/**
 * The grid slots the producer published, or [] when the artifact carries none.
 *
 * Why this is parsed at all, when the frontend already derives a slot station from
 * `grid.pitchMetres`: the derivation was MEASURED against every shipped model and the
 * two agree to 5 mm or better on all 232 published slots, so the station is not the
 * problem. `lateralSign` is. The frontend had no copy of it and invented its own from
 * slot parity (`slot % 2 === 0 ? -1 : +1`), which is the OPPOSITE of Python's on every
 * one of those 232 slots -- i.e. the whole grid was drawn mirrored about the ring
 * relative to the placement the producer published. One producer per contract
 * (AGENTS.md 38): the side comes from here now, and parity is only the fallback for a
 * driver the producer did not place.
 */
export function parseGridSlots(raw: RawTrackModel): GridSlot[] {
  const slots = raw.grid?.slots;
  if (!Array.isArray(slots)) return [];
  const out: GridSlot[] = [];
  for (const s of slots) {
    const position = finiteOrNull(s?.position);
    const station = finiteOrNull(s?.station);
    const sign = finiteOrNull(s?.lateralSign);
    // A slot with no side stated is not a slot with side 0; it is a slot this file
    // knows nothing about, and the caller falls back to its own rule for it.
    if (position === null || station === null || (sign !== 1 && sign !== -1)) continue;
    out.push({
      position,
      driver: typeof s.driver === "string" && s.driver ? s.driver : null,
      station,
      lateralSign: sign,
    });
  }
  return out;
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

/** The whole provenance vocabulary. There is no seventh word, and absence is null. */
const PROVENANCE_WORDS = new Set<Provenance>([
  "OBSERVED", "DERIVED", "INFERRED", "SIMULATED", "RULE", "DEFAULT",
]);

/** The leading provenance word of a producer tag such as "DERIVED (exact vertical
 * raycast ...)". Null -- not a guess, and not a new word -- for anything else. */
function leadingProvenance(tag: unknown): Provenance | null {
  if (typeof tag !== "string") return null;
  const word = tag.trim().split(/[^A-Z]/)[0] as Provenance;
  return PROVENANCE_WORDS.has(word) ? word : null;
}

const warned = new Set<string>();
function warnOnce(key: string, message: string): void {
  if (warned.has(key)) return;
  warned.add(key);
  console.warn(message);
}

/** Quantised ints -> metres/ratios, with `null` and non-finite entries becoming NaN.
 * NaN rather than 0 on purpose: 0 is a perfectly plausible height and a perfectly
 * plausible gradient, so it cannot also mean "there is no value here". */
function dequantise(values: (number | null)[] | undefined, n: number, divisor: number): Float32Array {
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const v = values?.[i];
    out[i] = typeof v === "number" && Number.isFinite(v) ? v / divisor : NaN;
  }
  return out;
}

/**
 * Parses the OPTIONAL baked surface block.
 *
 * ABSENCE IS THE ORDINARY ANSWER, not an error: 12 of the 13 shipped circuits have no
 * model that clears the quality gate, and no artifact written before schemaVersion 2
 * carries the block at all. Both return null, silently, and the caller draws the
 * procedural ribbon exactly as it does today.
 *
 * A block that is PRESENT but unusable is a different thing -- a producer bug -- and is
 * reported once and then also refused, because a surface whose arrays do not line up
 * with the ring would stand cars at the wrong stations' heights. The one structural
 * requirement is that there is exactly one sample per ring vertex; everything else
 * (slope, camber, coverage, the asset URL) may be missing on its own without costing
 * the height.
 */
export function parseTrackSurface(raw: RawTrackModel): TrackSurface | null {
  const block = raw.surface;
  if (!block) return null;
  const n = raw.ring.xCm.length;
  const slug = raw.slug ?? "?";

  if (!Array.isArray(block.zCm) || block.zCm.length !== n) {
    warnOnce(`surface-length-${slug}`,
      `[sim] ${slug}: surface block has ${Array.isArray(block.zCm) ? block.zCm.length : "no"} ` +
      `heights for ${n} ring vertices; ignoring it and keeping the procedural ribbon.`);
    return null;
  }
  const t = block.transform;
  const transform: TrackSurfaceTransform = {
    scale: Number(t?.scale), yawDeg: Number(t?.yawDeg), mirror: Number(t?.mirror),
    txM: Number(t?.txM), tzM: Number(t?.tzM), tyM: Number(t?.tyM),
  };
  if (!Object.values(transform).every((v) => Number.isFinite(v))) {
    warnOnce(`surface-transform-${slug}`,
      `[sim] ${slug}: surface block carries no usable transform; keeping the ribbon.`);
    return null;
  }

  const zM = dequantise(block.zCm, n, 100);
  const valid = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    // BOTH conditions: the mask is the producer's verdict, the finite height is the
    // evidence for it, and a station is only usable when the two agree.
    valid[i] = block.validMask?.[i] === 1 && Number.isFinite(zM[i]) ? 1 : 0;
  }

  return {
    dsMetres: finiteOrNull(block.dsMetres) ?? raw.ring.lengthMetres / n,
    source: typeof block.source === "string" ? block.source : "",
    sourceSha256: typeof block.sourceSha256 === "string" ? block.sourceSha256 : null,
    profile: typeof block.profile === "string" ? block.profile : null,
    transform,
    zM,
    slope: dequantise(block.slopePermille, n, 1000),
    camber: dequantise(block.camberPermille, n, 1000),
    valid,
    residual: {
      stdM: finiteOrNull(block.residual?.stdM),
      maxM: finiteOrNull(block.residual?.maxM),
    },
    coverage: finiteOrNull(block.coverage),
    roadCoverage: finiteOrNull(block.roadCoverage),
    assetUrl: typeof block.assetUrl === "string" && block.assetUrl ? block.assetUrl : null,
    assetSha256: typeof block.assetSha256 === "string" ? block.assetSha256 : null,
    provenance: leadingProvenance(block.provenance),
    provenanceNote: typeof block.provenance === "string" ? block.provenance : null,
  };
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

/**
 * A parsed track model, plus the producer-published grid slots.
 *
 * `gridSlots` rides alongside the shared TrackModel rather than inside it because
 * `TrackModel` lives in contract/types.ts and is owned elsewhere; widening a shared
 * contract silently is exactly what AGENTS.md 0.1 forbids. Read it through
 * `gridSlotsOf()`, which works on a bare TrackModel and answers [] when the model came
 * from somewhere that carries none (the generated-race engine, a test fixture).
 */
export type ParsedTrackModel = TrackModel & { gridSlots: GridSlot[] };

/** The producer's published grid slots for a model, or [] when it carries none. */
export function gridSlotsOf(track: TrackModel): GridSlot[] {
  const slots = (track as Partial<ParsedTrackModel>).gridSlots;
  return Array.isArray(slots) ? slots : [];
}

/**
 * How many metres of drivable road either side of the ring the ARTIFACT has MEASURED
 * at `stationM` -- or null when this circuit is drawn as the procedural ribbon, where
 * the question does not arise.
 *
 * THIS IS NOT halfWidthAt(). The two answer different questions and only one of them
 * is a measurement:
 *
 *   halfWidthAt()  is the RIBBON's half-width. The artifact states its own provenance:
 *                  the SHAPE is "DERIVED from per-station lateral extremes and corner
 *                  markers" but the SCALE is "RULE: HALF_WIDTH_MIN_M..HALF_WIDTH_MAX_M
 *                  (6.0-7.5 m), a stated constant ... The position feed does not
 *                  measure track width." On a circuit drawn as the ribbon that is
 *                  self-consistent -- the road the viewer sees IS that half-width, so a
 *                  car placed inside it is on the road it is drawn on.
 *
 *   this function  is about the road a REAL CIRCUIT MODEL has. Once a `surface` block
 *                  is present the viewer is looking at the GLB, not the ribbon, and the
 *                  RULE half-width says nothing about it. Measured at Silverstone by
 *                  raycasting the shipped GLB along the ring's own left normal, in
 *                  0.25 m steps, with the same scorer the bake uses: at the front of
 *                  the grid (stations 5770-5818 m) the asphalt ends 1.75-2.00 m to the
 *                  LEFT of the ring and runs 15.0-17.5 m to the RIGHT. The ring there
 *                  is the RACING LINE out of Club, not the road centre -- the road
 *                  centre is a measured 6.5 m to the right of it -- while the RULE
 *                  half-width claims a symmetric 6.33-7.04 m. The feed's own telemetry
 *                  agrees: over 1,108,923 position samples from the two British packs,
 *                  the largest lateral ever recorded anywhere in that 168 m stretch is
 *                  +2.79 m.
 *
 * So the answer today is 0 for every surfaced circuit, and that is a measurement, not a
 * placeholder: `surface_block()` in scripts/simdata/glb_surface.py bakes exactly ONE
 * probe per station -- the ring itself -- and emits height, slope and camber. No
 * lateral extent is measured, therefore none may be claimed. (`camberPermille` is the
 * nearest thing to a width and it is not one: it is non-null wherever probes at one of
 * 2.0/1.5/1.0 m landed, and which baseline won is not published.)
 *
 * What would make this return a real number: the bake emitting a per-station SIGNED
 * road extent -- left and right separately, because the ring is the racing line and a
 * single symmetric half-width cannot express the 6.5 m offset measured above. That is
 * also when this grows a `stationM` argument; it has none today precisely because the
 * artifact carries nothing that varies along the lap. See the handoff.
 */
export function measuredLateralRoomM(track: Pick<TrackModel, "surface">): number | null {
  if (!track.surface) return null;
  return 0;
}

export function parseTrackModel(raw: RawTrackModel): ParsedTrackModel {
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
    grid: {
      order: raw.grid.order,
      pitchMetres: raw.grid.pitchMetres,
      unplaced: Array.isArray(raw.grid.unplaced) ? raw.grid.unplaced : [],
    },
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
    // null for 12 of 13 circuits and for every pre-schemaVersion-2 artifact. That is
    // the normal case, not a failure: those circuits keep the procedural ribbon.
    surface: parseTrackSurface(raw),
    gridSlots: parseGridSlots(raw),
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

/**
 * The baked surface at an arbitrary station, or null when there is none.
 *
 * NULL IS A REAL ANSWER AND THE COMMON ONE. It means one of three things, and the
 * caller should treat all three identically -- fall back to the ring's own elevation:
 *   - the artifact carries no `surface` block (12 of 13 circuits, and every artifact
 *     built before schemaVersion 2),
 *   - this station's raycast found nothing,
 *   - the station falls between two ring vertices and EITHER of them is invalid.
 *
 * That last rule is the point of this function. Interpolating across a gap would
 * manufacture a height for ground the raycast never hit, and a manufactured height is
 * exactly what puts a car through the road or in the air. Heights are interpolated the
 * same way trackPointAt interpolates the ring -- the arrays share the ring's stations,
 * one sample per vertex -- so the surface and the centreline cannot disagree about
 * which station is which.
 *
 * `slope` and `camber` are independent of the height and of each other: a station can
 * have a measured height and no measurable camber (camber needs a probe out to each
 * side, which can fall off the model at a track edge). Each is null when either
 * bracketing vertex has none.
 */
export function surfaceAt(
  track: Pick<TrackModel, "lengthMetres" | "x" | "surface">, station: number,
): TrackSurfaceSample | null {
  const surface = track.surface;
  if (!surface) return null;
  const n = track.x.length;
  if (surface.zM.length !== n) return null;

  const L = track.lengthMetres;
  const ds = L / n;                       // === ringDsMetres(track), by construction
  const s = ((station % L) + L) % L;
  if (!Number.isFinite(s)) return null;
  const f = s / ds;
  const i0 = Math.floor(f) % n;
  const i1 = (i0 + 1) % n;
  const frac = f - Math.floor(f);
  if (!surface.valid[i0] || !surface.valid[i1]) return null;

  const lerp = (a: Float32Array) => {
    const v0 = a[i0], v1 = a[i1];
    return Number.isFinite(v0) && Number.isFinite(v1) ? v0 + (v1 - v0) * frac : null;
  };
  const zM = lerp(surface.zM);
  if (zM === null) return null;           // unreachable while valid[] agrees with zM
  return { zM, slope: lerp(surface.slope), camber: lerp(surface.camber) };
}
