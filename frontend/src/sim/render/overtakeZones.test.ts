import { describe, expect, it } from "vitest";
import type { TrackModel } from "../contract/types";
import { buildGate, buildOvertakeLayer, buildZoneBand, overtakeGeometryFromRules } from "./overtakeZones";

/** A closed square-ish ring so a gate has a real heading to be perpendicular to. */
function ringTrack(lengthMetres = 4000, n = 400): TrackModel {
  const x = new Float32Array(n);
  const y = new Float32Array(n);
  const z = new Float32Array(n);
  const r = lengthMetres / (2 * Math.PI);
  for (let i = 0; i < n; i++) {
    const t = (i / n) * Math.PI * 2;
    x[i] = Math.cos(t) * r;
    y[i] = Math.sin(t) * r;
    z[i] = 0;
  }
  return {
    slug: "ring", event: "Ring GP", lengthMetres,
    x, y, z,
    halfWidth: new Float32Array([6]), widthBinMetres: lengthMetres,
    timingLines: { sf: 0, s1: null, s2: null },
    corners: [], grid: { order: [], pitchMetres: 8 },
    pitLanePath: null,
    pitLane: { entryStation: null, exitStation: null, mergeStation: null, loopLateral: null },
    referenceProfile: {
      binMetres: 10, speedKph: new Float32Array(400).fill(200), gear: new Uint8Array(400).fill(6),
    },
  };
}

describe("overtakeGeometryFromRules", () => {
  it("reads the wrapped {value, value_source} shape the service sends", () => {
    const geometry = overtakeGeometryFromRules({
      overtake: {
        detection_line_m: { value: 5520, value_source: "DERIVED_TELEMETRY" },
        zones: [{
          zone: 1,
          activation_line_m: { value: 1300, value_source: "PROXY_HISTORICAL_DRS" },
          zone_end_m: { value: 1800, value_source: "PROXY_HISTORICAL_DRS" },
        }],
      },
    });
    expect(geometry).not.toBeNull();
    expect(geometry!.detectionM).toBe(5520);
    expect(geometry!.detectionSource).toBe("DERIVED_TELEMETRY");
    expect(geometry!.zones).toHaveLength(1);
    expect(geometry!.zones[0]).toMatchObject({
      zone: 1, activationM: 1300, endM: 1800, source: "PROXY_HISTORICAL_DRS",
    });
  });

  it("keeps an unsourced line NULL rather than reading it as the start line", () => {
    // A null detection_line_m read as 0 would draw the Detection Line across
    // start/finish on every circuit that has not been sourced -- confidently,
    // and wrong, which is the one failure mode worth a test of its own.
    const geometry = overtakeGeometryFromRules({
      overtake: {
        detection_line_m: { value: null, value_source: "UNVERIFIED" },
        zones: [{ zone: 1, activation_line_m: { value: 1300 }, zone_end_m: { value: 1800 } }],
      },
    });
    expect(geometry).not.toBeNull();
    expect(geometry!.detectionM).toBeNull();
  });

  it("accepts a bare number so a future flattened payload is not read as absent", () => {
    const geometry = overtakeGeometryFromRules({
      overtake: { detection_line_m: 5520, zones: [] },
    });
    expect(geometry!.detectionM).toBe(5520);
  });

  it("returns null when nothing at all is sourced", () => {
    expect(overtakeGeometryFromRules({
      overtake: { detection_line_m: { value: null }, zones: [{ zone: 1, activation_line_m: { value: null } }] },
    })).toBeNull();
    expect(overtakeGeometryFromRules({})).toBeNull();
    expect(overtakeGeometryFromRules(null)).toBeNull();
  });
});

describe("buildGate", () => {
  it("spans the road plus an overhang on both sides", () => {
    const track = ringTrack();
    const gate = buildGate(track, 1000, { colour: "#EFEFEF", dashed: false, opacity: 1 });
    expect(gate).not.toBeNull();
    const pos = gate!.geometry.getAttribute("position");
    expect(pos.count).toBe(2);
    const dx = pos.getX(0) - pos.getX(1);
    const dy = pos.getY(0) - pos.getY(1);
    const dz = pos.getZ(0) - pos.getZ(1);
    const width = Math.sqrt(dx * dx + dy * dy + dz * dz);
    // half-width 6 m plus 2.5 m overhang each side
    expect(width).toBeGreaterThan(16);
    expect(width).toBeLessThan(18);
  });

  it("computes line distances for a dashed gate, or the dashes never appear", () => {
    const track = ringTrack();
    const dashed = buildGate(track, 1000, { colour: "#DA291C", dashed: true, opacity: 1 });
    expect(dashed!.geometry.getAttribute("lineDistance")).toBeDefined();
  });
});

describe("buildZoneBand", () => {
  it("builds a band over the span between two stations", () => {
    const band = buildZoneBand(ringTrack(), 1000, 1400, 0.12);
    expect(band).not.toBeNull();
    expect(band!.geometry.getIndex()!.count).toBeGreaterThan(0);
  });

  it("wraps a zone that crosses the start/finish line", () => {
    const track = ringTrack(4000);
    const wrapped = buildZoneBand(track, 3900, 200, 0.12);
    expect(wrapped).not.toBeNull();
    // 300 m of span at 12 m per step, two vertices per step
    const pos = wrapped!.geometry.getAttribute("position");
    expect(pos.count).toBeGreaterThan(10);
  });

  it("refuses a span that would wrap almost the whole lap", () => {
    expect(buildZoneBand(ringTrack(4000), 100, 50, 0.12)).toBeNull();
  });
});

describe("buildOvertakeLayer", () => {
  it("draws the detection line solid and each activation line dashed", () => {
    const layer = buildOvertakeLayer(ringTrack(), {
      detectionM: 3000, detectionSource: "DERIVED_TELEMETRY",
      zones: [{ zone: 1, activationM: 1000, endM: 1500, source: "PROXY_HISTORICAL_DRS" }],
    });
    expect(layer).not.toBeNull();
    const names = layer!.children.map((c) => c.name);
    expect(names).toContain("detection-line");
    expect(names).toContain("activation-line-1");
    expect(names).toContain("activation-zone-1");
    const detection = layer!.children.find((c) => c.name === "detection-line")!;
    expect(detection.userData.kind).toBe("DETECTION");
    expect(detection.userData.source).toBe("DERIVED_TELEMETRY");
  });

  it("draws nothing for a circuit with no sourced geometry", () => {
    expect(buildOvertakeLayer(ringTrack(), {
      detectionM: null, detectionSource: null, zones: [],
    })).toBeNull();
    expect(buildOvertakeLayer(ringTrack(), null)).toBeNull();
  });

  it("draws the detection line even when no zone is sourced", () => {
    const layer = buildOvertakeLayer(ringTrack(), {
      detectionM: 3000, detectionSource: "DERIVED_TELEMETRY",
      zones: [{ zone: 1, activationM: null, endM: null, source: null }],
    });
    expect(layer!.children.map((c) => c.name)).toEqual(["detection-line"]);
  });
});
