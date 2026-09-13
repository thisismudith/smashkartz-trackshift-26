/**
 * The 2026 Overtake geometry, drawn on the circuit: the Detection Line, each
 * activation zone's Activation Line, and the zone itself.
 *
 * WHERE THE NUMBERS COME FROM, because the two lines are NOT the same kind of
 * fact and the render must not flatten them into one:
 *
 *  - the DETECTION LINE is one per lap, at Safety Car Line 1 (the pit-entry
 *    bollard), stated verbatim in the 2026 Race Director's notes and measured
 *    in telemetry -- `DERIVED_TELEMETRY` in config/rules/2026/<event>.yaml.
 *  - an ACTIVATION LINE is `PROXY_HISTORICAL_DRS`: where DRS was actually open
 *    at the same circuit in 2022-2025, used as a stand-in for a 2026 Overtake
 *    zone whose real position has not been sourced. It is a development-only
 *    proxy and its own config says so.
 *
 * They are therefore drawn differently on purpose -- the detection line solid,
 * the proxy zone dashed and dimmer -- so nobody reads a proxy as a surveyed
 * line. Provenance that only exists in a tooltip is provenance nobody sees.
 *
 * Everything here is absence-tolerant: a circuit with no detection line, no
 * zones, or neither simply contributes nothing to the scene. Nine of the 2026
 * events are missing one or both, and that is a fact about the sourcing, not a
 * failure to handle.
 */
import * as THREE from "three";
import { HAAS } from "@/lib/palette";
import type { TrackModel } from "../contract/types";
import { halfWidthAt, trackPointAt } from "../data/manifest";
import { toRenderFrame } from "./trackMesh";

/** One activation zone as the rules config carries it. */
export interface OvertakeZone {
  zone: number;
  /** The config's own label for this zone -- "A1", not 1. The 2026 notes number
   * them that way and renumbering them 1..n makes the panel disagree with the
   * document it is quoting. */
  label: string;
  /** This zone's OWN Detection Line, metres. The config carries one per zone,
   * not one per lap: a circuit with four zones has four detection lines. */
  detectionM: number | null;
  detectionSource: string | null;
  /** Metres along the lap where the zone opens. Null when unsourced. */
  activationM: number | null;
  /** Metres along the lap where it closes. Null when unsourced -- which is the
   * case for every 2026 event today, `zone_end_m` being UNVERIFIED throughout.
   * A null end must not suppress a sourced activation line. */
  endM: number | null;
  /** The config's own `value_source` for this zone, shown as-is. */
  source: string | null;
}

export interface OvertakeGeometry {
  /** One Detection Line per lap, metres. Null when this event has none. */
  detectionM: number | null;
  detectionSource: string | null;
  zones: OvertakeZone[];
}

/** Metres of clearance above the ribbon, so a gate never z-fights the road. */
const GATE_LIFT_M = 0.35;

/** Along-track thickness of the painted band at a line, metres.
 *
 * A hairline across the road is invisible from a chase camera at 300 kph: it is
 * one pixel for one frame. A band this wide reads as a marking on the track the
 * way a real painted line does, and is still far narrower than the 300-500 m
 * zones it sits at the ends of. */
const LINE_BAND_M = 6;

/** Height of the vertical posts at each end of a gate, metres. Posts are what
 * make a line visible from BEHIND, which is the angle a chase camera has. */
const POST_HEIGHT_M = 4;

/** How far past the road edge each gate post extends, metres. */
const GATE_OVERHANG_M = 2.5;

/** Sampling step along a zone band, metres. Coarse enough to stay cheap, fine
 * enough that a band around a corner does not visibly cut the apex. */
const BAND_STEP_M = 12;

function crossPoints(track: TrackModel, stationM: number): [THREE.Vector3, THREE.Vector3] | null {
  const point = trackPointAt(track, stationM);
  if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) return null;
  const half = halfWidthAt(track, stationM);
  const reach = (Number.isFinite(half) && half > 0 ? half : 6) + GATE_OVERHANG_M;
  // Left of travel is +90 degrees from the heading, the same convention every
  // other lateral in this pipeline uses.
  const nx = Math.cos(point.heading + Math.PI / 2);
  const ny = Math.sin(point.heading + Math.PI / 2);
  const a = toRenderFrame(point.x + nx * reach, point.y + ny * reach, point.z + GATE_LIFT_M);
  const b = toRenderFrame(point.x - nx * reach, point.y - ny * reach, point.z + GATE_LIFT_M);
  return [new THREE.Vector3(...a), new THREE.Vector3(...b)];
}

/** A line across the full width of the road at `stationM`, or null off-model. */
export function buildGate(
  track: TrackModel, stationM: number, opts: { colour: string; dashed: boolean; opacity: number },
): THREE.Line | null {
  const ends = crossPoints(track, stationM);
  if (!ends) return null;
  const geometry = new THREE.BufferGeometry().setFromPoints(ends);
  const material = opts.dashed
    ? new THREE.LineDashedMaterial({
        color: new THREE.Color(opts.colour), transparent: true, opacity: opts.opacity,
        dashSize: 1.2, gapSize: 0.9,
      })
    : new THREE.LineBasicMaterial({
        color: new THREE.Color(opts.colour), transparent: true, opacity: opts.opacity,
      });
  const line = new THREE.Line(geometry, material);
  // Required by LineDashedMaterial: without it the dash pattern never appears
  // and the "this is a proxy" signal silently degrades to a solid line.
  if (opts.dashed) line.computeLineDistances();
  return line;
}

/**
 * A translucent band along the road from `startM` to `endM`, wrapping the
 * start/finish line when the zone does. Null when the span is unusable.
 */
export function buildZoneBand(
  track: TrackModel, startM: number, endM: number, opacity: number,
): THREE.Mesh | null {
  const L = track.lengthMetres;
  if (!(L > 0)) return null;
  // A zone that wraps past the line has endM < startM; walking the signed span
  // rather than the raw difference is what keeps it one continuous band.
  const span = ((endM - startM) % L + L) % L;
  if (!(span > 0) || span > L * 0.9) return null;

  const steps = Math.max(2, Math.ceil(span / BAND_STEP_M));
  const positions: number[] = [];
  for (let i = 0; i <= steps; i++) {
    const station = startM + (span * i) / steps;
    const ends = crossPoints(track, station);
    if (!ends) return null;
    positions.push(ends[0].x, ends[0].y, ends[0].z, ends[1].x, ends[1].y, ends[1].z);
  }
  const index: number[] = [];
  for (let i = 0; i < steps; i++) {
    const a = i * 2, b = a + 1, c = a + 2, d = a + 3;
    index.push(a, b, c, b, d, c);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(index);
  geometry.computeVertexNormals();
  const material = new THREE.MeshBasicMaterial({
    color: new THREE.Color(HAAS.grey), transparent: true, opacity,
    depthWrite: false, side: THREE.DoubleSide,
  });
  return new THREE.Mesh(geometry, material);
}

/**
 * A gate that is actually visible at racing speed: a painted band across the
 * road plus a post at each end.
 *
 * The band is the part that reads on approach; the posts are what read from
 * behind, once the car is past it. Both are drawn from the same two edge points
 * as the hairline, so they cannot disagree about where the line is.
 */
export function buildGateMarker(
  track: TrackModel, stationM: number,
  opts: { colour: string; opacity: number; posts: boolean },
): THREE.Group | null {
  const band = buildZoneBand(track, stationM - LINE_BAND_M / 2, stationM + LINE_BAND_M / 2, opts.opacity);
  if (!band) return null;
  (band.material as THREE.MeshBasicMaterial).color = new THREE.Color(opts.colour);
  const group = new THREE.Group();
  group.add(band);

  if (opts.posts) {
    const ends = crossPoints(track, stationM);
    if (ends) {
      const material = new THREE.LineBasicMaterial({
        color: new THREE.Color(opts.colour), transparent: true, opacity: opts.opacity,
      });
      for (const end of ends) {
        const top = end.clone();
        // Render frame is [x, z, -y], so "up" out of the road is +y here.
        top.y += POST_HEIGHT_M;
        const geometry = new THREE.BufferGeometry().setFromPoints([end, top]);
        group.add(new THREE.Line(geometry, material));
      }
    }
  }
  return group;
}

/**
 * Every gate and band this circuit's geometry supports, as one group.
 *
 * Returns null when the geometry carries nothing drawable, so the caller adds
 * nothing rather than an empty group nobody disposes.
 */
export function buildOvertakeLayer(
  track: TrackModel, geometry: OvertakeGeometry | null,
): THREE.Group | null {
  if (!geometry) return null;
  const group = new THREE.Group();
  group.name = "overtake-geometry";

  if (geometry.detectionM !== null && Number.isFinite(geometry.detectionM)) {
    // Solid, bright, banded and posted: a measured position of a stated rule,
    // and the one line a viewer most needs to see coming.
    const gate = buildGateMarker(track, geometry.detectionM, {
      colour: HAAS.white, opacity: 0.75, posts: true,
    });
    if (gate) {
      gate.name = "detection-line";
      gate.userData = { kind: "DETECTION", stationM: geometry.detectionM,
                        source: geometry.detectionSource };
      group.add(gate);
    }
  }

  for (const zone of geometry.zones) {
    if (zone.activationM === null || !Number.isFinite(zone.activationM)) continue;
    // Dimmer and unposted: a historical-DRS proxy, not a surveyed 2026 line, and
    // it must not carry the same visual weight as the measured one.
    const gate = buildGateMarker(track, zone.activationM, {
      colour: HAAS.red, opacity: 0.45, posts: false,
    });
    if (gate) {
      gate.name = `activation-line-${zone.zone}`;
      gate.userData = { kind: "ACTIVATION", zone: zone.zone,
                        stationM: zone.activationM, source: zone.source };
      group.add(gate);
    }
    if (zone.endM !== null && Number.isFinite(zone.endM)) {
      const band = buildZoneBand(track, zone.activationM, zone.endM, 0.12);
      if (band) {
        band.name = `activation-zone-${zone.zone}`;
        band.userData = { kind: "ZONE", zone: zone.zone, source: zone.source };
        group.add(band);
      }
    }
  }

  return group.children.length ? group : null;
}

/**
 * Reads the geometry out of a `/rules/{event}` response body.
 *
 * The service wraps each number as `{value, value_source, ...}`; a bare number
 * is accepted too so a future flattened payload does not silently read as
 * absent. Anything that is not a finite number becomes null -- the config
 * writes null deliberately where a line has not been sourced, and that must
 * survive to the renderer rather than becoming a 0 at the start/finish line.
 */
export function overtakeGeometryFromRules(body: unknown): OvertakeGeometry | null {
  if (!body || typeof body !== "object") return null;
  const overtake = (body as { overtake?: unknown }).overtake;
  if (!overtake || typeof overtake !== "object") return null;

  const read = (node: unknown): { value: number | null; source: string | null } => {
    if (typeof node === "number") return { value: Number.isFinite(node) ? node : null, source: null };
    if (node && typeof node === "object") {
      const value = (node as { value?: unknown }).value;
      const source = (node as { value_source?: unknown }).value_source;
      return {
        value: typeof value === "number" && Number.isFinite(value) ? value : null,
        source: typeof source === "string" ? source : null,
      };
    }
    return { value: null, source: null };
  };

  // The Detection Line is carried PER ZONE by config/rules/2026/*.yaml, not once
  // at the top of the overtake block. Reading it at the top level found nothing
  // on every circuit, and the panel reported "not sourced" for four lines that
  // are sourced, measured and in the file. The top-level read is kept only as a
  // fallback for a config that does hoist it.
  const topDetection = read((overtake as { detection_line_m?: unknown }).detection_line_m);
  const rawZones = (overtake as { zones?: unknown }).zones;
  const zones: OvertakeZone[] = [];
  if (Array.isArray(rawZones)) {
    for (const entry of rawZones) {
      if (!entry || typeof entry !== "object") continue;
      const detection = read((entry as { detection_line_m?: unknown }).detection_line_m);
      const activation = read((entry as { activation_line_m?: unknown }).activation_line_m);
      const end = read((entry as { zone_end_m?: unknown }).zone_end_m);
      const zoneNumber = (entry as { zone?: unknown }).zone;
      zones.push({
        zone: typeof zoneNumber === "number" ? zoneNumber : zones.length + 1,
        label: typeof zoneNumber === "string" && zoneNumber
          ? zoneNumber : String(zones.length + 1),
        detectionM: detection.value ?? topDetection.value,
        detectionSource: detection.source ?? topDetection.source,
        activationM: activation.value,
        endM: end.value,
        source: activation.source,
      });
    }
  }
  // The first sourced detection line, for the consumers that draw a single one.
  const firstDetection = zones.find((z) => z.detectionM !== null) ?? null;
  const detectionM = firstDetection?.detectionM ?? topDetection.value;
  const detectionSource = firstDetection?.detectionSource ?? topDetection.source;
  if (detectionM === null && zones.every((z) => z.activationM === null)) return null;
  return { detectionM, detectionSource, zones };
}
