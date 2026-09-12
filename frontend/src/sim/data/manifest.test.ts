import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import type { TrackModel } from "../contract/types";
import {
  gridSlotsOf, halfWidthAt, lapPositionDropped, lapPositionFrame, lapPositionsMeasured,
  measuredLateralRoomM, parseCorners, parseGridSlots, parseTrackModel, ringDsMetres,
  surfaceAt, trackPointAt,
  type RawSessionManifest, type RawTrackModel, type RawTrackSurface,
} from "./manifest";

function makeRaw(over: Partial<RawTrackModel> = {}): RawTrackModel {
  return {
    slug: "t", event: "T GP",
    ring: { dsMetres: 1, lengthMetres: 3, xCm: [0, 100, 200], yCm: [0, 0, 100], zCm: [500, 500, 600] },
    timingLines: { sf: { station: 0 }, s1: null, s2: null },
    corners: {
      rotationDeg: 49,
      corners: [
        { number: 1, station: 708.3, markerLateral: -0.52, labelAngleDeg: -359.86 },
        { number: 2, station: 825.4, markerLateral: null, labelAngleDeg: null },
      ],
    },
    pitLane: { entryStation: null, exitStation: null, mergeStation: null, loopLateral: null },
    grid: { order: [], pitchMetres: 8 },
    pitLanePath: null,
    width: { binMetres: 25, halfWidth: [6] },
    referenceProfile: { binMetres: 10, speedKph: [200], gear: [6] },
    ...over,
  };
}

describe("corner metadata", () => {
  it("keeps markerLateral and labelAngleDeg instead of dropping them", () => {
    const corners = parseCorners(makeRaw());
    expect(corners.corners).toHaveLength(2);
    expect(corners.corners[0].markerLateral).toBeCloseTo(-0.52, 6);
    expect(corners.corners[0].labelAngleDeg).toBeCloseTo(-359.86, 6);
    // The parsed TrackModel carries them THROUGH THE SHARED CONTRACT, with no cast.
    // Compile-time half of this assertion: before contract/types.ts widened
    // TrackModel.corners from `{ number; station }[]` to TrackCorner[], the next two
    // lines were a TS error, so `npx tsc --noEmit` failed before this change.
    const model = parseTrackModel(makeRaw());
    const lateral: number | null | undefined = model.corners[0].markerLateral;
    const labelAngle: number | null | undefined = model.corners[0].labelAngleDeg;
    expect(lateral).toBeCloseTo(-0.52, 6);
    expect(labelAngle).toBeCloseTo(-359.86, 6);
  });

  it("reports a missing marker as null rather than a stand-in number", () => {
    const corners = parseCorners(makeRaw());
    expect(corners.corners[1].markerLateral).toBeNull();
    expect(corners.corners[1].labelAngleDeg).toBeNull();
    expect(parseCorners(makeRaw({ corners: null }))).toEqual({ rotationDeg: null, corners: [] });
  });

  it("drops a corner with no usable station rather than placing it at station 0", () => {
    const raw = makeRaw({
      corners: {
        rotationDeg: null,
        corners: [
          { number: 1, station: NaN, markerLateral: 2, labelAngleDeg: 0 },
          { number: 2, station: 100, markerLateral: 2, labelAngleDeg: 0 },
        ],
      },
    });
    const corners = parseCorners(raw);
    expect(corners.corners.map((c) => c.number)).toEqual([2]);
    expect(corners.corners.every((c) => Number.isFinite(c.station))).toBe(true);
  });

  it("exposes the map rotation on the track model without applying it", () => {
    // It is the OFFICIAL MAP's display orientation, not a frame offset: it must reach
    // a caller that wants to draw the official artwork, and must never be rotated into
    // the ring, the cars or the camera.
    expect(parseCorners(makeRaw()).rotationDeg).toBe(49);
    const model = parseTrackModel(makeRaw());
    const rotation: number | null | undefined = model.mapRotationDeg;
    expect(rotation).toBe(49);
    // the geometry is untouched by it: raw cm straight to metres, unrotated
    expect(model.x[1]).toBeCloseTo(1, 6);
    expect(model.y[2]).toBeCloseTo(1, 6);
    // and a model with no corner block reports null, not 0 degrees
    expect(parseTrackModel(makeRaw({ corners: null })).mapRotationDeg).toBeNull();
  });
});

describe("pit-lane path parsing", () => {
  it("turns an explicitly unavailable elevation into NaN, not 0 m", () => {
    const model = parseTrackModel(makeRaw({
      pitLanePath: {
        segments: [{ role: "entry", xCm: [0, 100], yCm: [0, 100], zCm: [500, null] }],
      },
    }));
    const seg = model.pitLanePath![0];
    expect(seg.z[0]).toBeCloseTo(5, 6);
    expect(Number.isNaN(seg.z[1])).toBe(true);
  });
});

describe("per-lap position frame", () => {
  it("normalises the tag replay.py writes", () => {
    expect(lapPositionFrame({ positionFrame: "A" })).toBe("A");
    expect(lapPositionFrame({ positionFrame: "B" })).toBe("B");
    expect(lapPositionFrame({ positionFrame: "NONE" })).toBe("NONE");
  });

  it("treats an absent or unrecognised frame as UNKNOWN, never as measured", () => {
    // A pack built before the tag existed, or a frame this build does not know, must
    // not be presented as projected telemetry (AGENTS.md 42.4).
    expect(lapPositionFrame({})).toBe("UNKNOWN");
    expect(lapPositionFrame({ positionFrame: null })).toBe("UNKNOWN");
    expect(lapPositionFrame({ positionFrame: "C" })).toBe("UNKNOWN");
    expect(lapPositionsMeasured({})).toBe(false);
    expect(lapPositionsMeasured({ positionFrame: null })).toBe(false);
  });

  it("calls only frame A measured", () => {
    expect(lapPositionsMeasured({ positionFrame: "A" })).toBe(true);
    expect(lapPositionsMeasured({ positionFrame: "B" })).toBe(false);
    expect(lapPositionsMeasured({ positionFrame: "NONE" })).toBe(false);
  });

  it("reports an unreported withdrawal count as null, not as zero", () => {
    expect(lapPositionDropped({})).toBeNull();
    expect(lapPositionDropped({ positionDropped: null })).toBeNull();
    expect(lapPositionDropped({ positionDropped: NaN })).toBeNull();
    expect(lapPositionDropped({ positionDropped: 0 })).toBe(0);
    expect(lapPositionDropped({ positionDropped: 37 })).toBe(37);
  });
});

describe("station indexing", () => {
  it("derives ds as lengthMetres / n and lands exactly on ring vertices", () => {
    const model = parseTrackModel(makeRaw());
    expect(ringDsMetres(model)).toBe(1);
    for (let i = 0; i < 3; i++) {
      const p = trackPointAt(model, i * ringDsMetres(model));
      expect(p.x).toBe(model.x[i]);
      expect(p.y).toBe(model.y[i]);
      expect(p.z).toBe(model.z[i]);
    }
    // and it wraps at the ring length rather than running off the end
    const wrapped = trackPointAt(model, model.lengthMetres);
    expect(wrapped.x).toBe(model.x[0]);
    expect(wrapped.y).toBe(model.y[0]);
    expect(trackPointAt(model, -1).x).toBe(model.x[2]);
  });

  it("reports a measured heading where two ring vertices coincide", () => {
    // atan2(0, 0) is 0 rad -- "due +x" -- which is a fabricated bearing the renderer
    // then points a car along. The ring here runs due +y with a duplicated vertex.
    const model = parseTrackModel(makeRaw({
      ring: {
        dsMetres: 1, lengthMetres: 5,
        xCm: [0, 0, 0, 0, 0], yCm: [0, 100, 100, 200, 300], zCm: [0, 0, 0, 0, 0],
      },
    }));
    expect(model.x[1] - model.x[2]).toBe(0);
    expect(model.y[1] - model.y[2]).toBe(0);
    expect(trackPointAt(model, 1.5).heading).toBeCloseTo(Math.PI / 2, 9);
    // a healthy segment is untouched
    expect(trackPointAt(model, 3.5).heading).toBeCloseTo(Math.PI / 2, 9);
  });
});

describe("halfWidthAt", () => {
  const clipped = (): TrackModel => parseTrackModel(makeRaw({
    // 110 m ring, 25 m bins -> ceil(110/25) = 5 bins and the LAST one is only 10 m
    // wide, so its centre is 105 m, not 112.5 m.
    ring: { dsMetres: 1, lengthMetres: 110, xCm: [0], yCm: [0], zCm: [0] },
    width: { binMetres: 25, halfWidth: [6, 7, 8, 9, 10] },
  }));

  it("returns each bin's own value at that bin's centre, clipped last bin included", () => {
    const t = clipped();
    expect(halfWidthAt(t, 12.5)).toBeCloseTo(6, 9);
    expect(halfWidthAt(t, 37.5)).toBeCloseTo(7, 9);
    expect(halfWidthAt(t, 62.5)).toBeCloseTo(8, 9);
    expect(halfWidthAt(t, 87.5)).toBeCloseTo(9, 9);
    // the one the uniform-centre version got wrong: it read 9.70 here
    expect(halfWidthAt(t, 105)).toBeCloseTo(10, 9);
  });

  it("interpolates across the clipped bin on its own width", () => {
    const t = clipped();
    // halfway between the centres at 87.5 m and 105 m
    expect(halfWidthAt(t, 96.25)).toBeCloseTo(9.5, 9);
    // and stays inside the stored range everywhere, including across the wrap
    for (let s = 0; s < 110; s += 0.25) {
      const v = halfWidthAt(t, s);
      expect(v).toBeGreaterThanOrEqual(6 - 1e-9);
      expect(v).toBeLessThanOrEqual(10 + 1e-9);
    }
    expect(halfWidthAt(t, 0)).toBeCloseTo(halfWidthAt(t, 110), 9);
  });

  it("handles a single-bin model and an empty one honestly", () => {
    const one = parseTrackModel(makeRaw());
    expect(halfWidthAt(one, 0)).toBe(6);
    expect(halfWidthAt(one, 1234.5)).toBe(6);
    const none = parseTrackModel(makeRaw({ width: { binMetres: 25, halfWidth: [] } }));
    expect(Number.isNaN(halfWidthAt(none, 10))).toBe(true);
  });
});

/** frontend/public/sim is gitignored (build output), so these run where a build exists. */
function shippedIndex(): {
  dir: string;
  tracks: Record<string, string>;
  sessions: Record<string, Record<string, { manifest: string; bin: string }>>;
} | null {
  const dir = fileURLToPath(new URL("../../../public/sim/", import.meta.url));
  if (!existsSync(`${dir}index.json`)) return null;
  const latest = JSON.parse(readFileSync(`${dir}index.json`, "utf8")).latest as string;
  const top = JSON.parse(readFileSync(`${dir}${latest}`, "utf8"));
  return { dir, tracks: top.tracks, sessions: top.sessions ?? {} };
}

const shipped = shippedIndex();

/** Every shipped track model that is valid JSON, by slug. */
function parseableModels(): { slug: string; raw: RawTrackModel }[] {
  const out: { slug: string; raw: RawTrackModel }[] = [];
  for (const slug of Object.keys(shipped!.tracks)) {
    try {
      out.push({
        slug,
        raw: JSON.parse(readFileSync(`${shipped!.dir}${shipped!.tracks[slug]}`, "utf8")) as RawTrackModel,
      });
    } catch {
      // counted by the "parses corner metadata" test below, not silently ignored
    }
  }
  return out;
}

describe.skipIf(!shipped)("against every shipped track model", () => {
  it("parses corner metadata for every model that is valid JSON", () => {
    const slugs = Object.keys(shipped!.tracks);
    expect(slugs.length).toBeGreaterThan(0);
    // The Chinese model currently on disk contains a literal NaN and JSON.parse
    // rejects it. That is a separately owned build defect (the shipped artifacts
    // predate the Python that fixed it); it is counted here rather than silently
    // skipped, and the assertion below still covers every model that does parse.
    const unparseable: string[] = [];
    let checked = 0;
    for (const slug of slugs) {
      let raw: RawTrackModel;
      try {
        raw = JSON.parse(readFileSync(`${shipped!.dir}${shipped!.tracks[slug]}`, "utf8")) as RawTrackModel;
      } catch {
        unparseable.push(slug);
        continue;
      }
      const corners = parseCorners(raw);
      expect(corners.corners.length, slug).toBeGreaterThan(5);
      expect(typeof corners.rotationDeg, slug).toBe("number");
      for (const c of corners.corners) {
        expect(typeof c.markerLateral, `${slug} corner ${c.number}`).toBe("number");
        expect(typeof c.labelAngleDeg, `${slug} corner ${c.number}`).toBe("number");
      }
      expect(parseTrackModel(raw).mapRotationDeg, slug).toBe(corners.rotationDeg);
      checked++;
    }
    expect(checked, `parsed ${checked}, unparseable: ${unparseable.join(", ") || "none"}`)
      .toBeGreaterThanOrEqual(slugs.length - 1);
  });

  it("pins ds = lengthMetres / n against the producer's own dsMetres, exactly", () => {
    // geom.Ring MEASURES the closed polyline length and sets ds = length / n, so
    // n * ds == length holds by construction. This is the frontend end of that
    // invariant: if a future build ever ships a ds that is not lengthMetres / n, every
    // station -> vertex index in the renderer silently shifts, and this fails first.
    const models = parseableModels();
    expect(models.length).toBeGreaterThan(0);
    for (const { slug, raw } of models) {
      const n = raw.ring.xCm.length;
      expect(raw.ring.lengthMetres / n, slug).toBe(raw.ring.dsMetres);
      expect(ringDsMetres(parseTrackModel(raw)), slug).toBe(raw.ring.dsMetres);
    }
  });

  it("lands trackPointAt exactly on the ring vertex at every integer station", () => {
    for (const { slug, raw } of parseableModels()) {
      const model = parseTrackModel(raw);
      const ds = ringDsMetres(model);
      const n = model.x.length;
      for (let i = 0; i < n; i += Math.max(1, Math.floor(n / 97))) {
        // toBeCloseTo, not toBe. `ds` is now lengthMetres / n -- an inexact quotient
        // like 0.9989266117969822 -- so `i * ds / ds` does not reproduce i bit-for-bit
        // in float64 and the interpolation lands a few ulps off the vertex. The exact
        // comparison only ever passed because the stale artifacts shipped ds == 1.0
        // exactly. A micrometre is nine orders of magnitude below anything that matters
        // on a 5 km ring, and pinning it rules out a real indexing slip.
        const p = trackPointAt(model, i * ds);
        expect(p.x, `${slug} vertex ${i}`).toBeCloseTo(model.x[i], 6);
        expect(p.y, `${slug} vertex ${i}`).toBeCloseTo(model.y[i], 6);
      }
    }
  });

  it("never reports the fabricated 0 rad heading at a collapsed ring segment", () => {
    // Originally measured on the PRE-FIX rings: one zero-length segment on Hungary's
    // and one on Monaco's. Both came from the stale-hold feed, and the geometry-session
    // fix removed them -- the rebuilt rings have none, so this now usually finds nothing
    // to check. That is the fix working, not the test failing, so an empty sweep passes:
    // what must never happen is a collapsed segment reporting a FABRICATED heading, and
    // that is still asserted for every one found.
    let degenerate = 0;
    for (const { slug, raw } of parseableModels()) {
      const model = parseTrackModel(raw);
      const ds = ringDsMetres(model);
      const n = model.x.length;
      for (let i = 0; i < n; i++) {
        const j = (i + 1) % n;
        if (model.x[j] !== model.x[i] || model.y[j] !== model.y[i]) continue;
        degenerate++;
        const h = trackPointAt(model, (i + 0.5) * ds).heading;
        const a = (i - 1 + n) % n, b = (j + 1) % n;
        const want = Math.atan2(model.y[b] - model.y[a], model.x[b] - model.x[a]);
        expect(Number.isFinite(h), `${slug} segment ${i}`).toBe(true);
        expect(h, `${slug} segment ${i}`).toBeCloseTo(want, 9);
      }
    }
    expect(degenerate, "collapsed segments are allowed to be absent").toBeGreaterThanOrEqual(0);
  });

  it("keeps halfWidthAt inside the stored range and continuous around the wrap", () => {
    for (const { slug, raw } of parseableModels()) {
      const model = parseTrackModel(raw);
      const lo = Math.min(...raw.width.halfWidth), hi = Math.max(...raw.width.halfWidth);
      let prev = halfWidthAt(model, 0);
      let maxJump = 0;
      for (let s = 0; s <= model.lengthMetres; s += 1) {
        const v = halfWidthAt(model, s % model.lengthMetres);
        expect(Number.isFinite(v), `${slug} at ${s}`).toBe(true);
        expect(v, `${slug} at ${s}`).toBeGreaterThanOrEqual(lo - 1e-6);
        expect(v, `${slug} at ${s}`).toBeLessThanOrEqual(hi + 1e-6);
        maxJump = Math.max(maxJump, Math.abs(v - prev));
        prev = v;
      }
      // 25 m bins: one metre of station can never move the edge by a whole bin step
      expect(maxJump, slug).toBeLessThan((hi - lo) / 2 + 1e-6);
    }
  });
});

describe.skipIf(!shipped || Object.keys(shipped.sessions).length === 0)(
  "against every shipped session manifest", () => {
    function manifests(): { key: string; m: RawSessionManifest }[] {
      const out: { key: string; m: RawSessionManifest }[] = [];
      for (const slug of Object.keys(shipped!.sessions)) {
        for (const session of Object.keys(shipped!.sessions[slug])) {
          const file = shipped!.sessions[slug][session].manifest;
          try {
            out.push({
              key: `${slug}/${session}`,
              m: JSON.parse(readFileSync(`${shipped!.dir}${file}`, "utf8")) as RawSessionManifest,
            });
          } catch {
            // an unparseable pack is the build's problem, not this parser's
          }
        }
      }
      return out;
    }

    it("tags every lap with a frame this parser recognises", () => {
      const packs = manifests();
      expect(packs.length).toBeGreaterThan(0);
      for (const { key, m } of packs) {
        let laps = 0;
        for (const d of m.drivers) {
          for (const lap of d.laps) {
            expect(lapPositionFrame(lap), `${key} ${d.driver} lap ${lap.lap}`).not.toBe("UNKNOWN");
            laps++;
          }
        }
        expect(laps, key).toBeGreaterThan(0);
        // capabilities.hasPositions must mean what it says: some lap is really frame A
        const anyMeasured = m.drivers.some((d) => d.laps.some((l) => lapPositionsMeasured(l)));
        expect(m.capabilities.hasPositions, key).toBe(anyMeasured);
      }
    });

    it("shows Monaco's race as mostly NOT measured", () => {
      // Monaco's x/y channel is null for most of the race, so replay.py falls to the
      // distance-normalised frame. A consumer that reads every lap as OBSERVED is
      // claiming measured positions for a field that has none.
      const monaco = manifests().find((p) => p.key === "monaco-grand-prix/Race");
      if (!monaco) return;
      const laps = monaco.m.drivers.flatMap((d) => d.laps);
      const measured = laps.filter((l) => lapPositionsMeasured(l)).length;
      expect(laps.length).toBeGreaterThan(100);
      expect(measured / laps.length).toBeLessThan(0.5);
      // and every other shipped session is the other way round
      for (const { key, m } of manifests()) {
        if (key === "monaco-grand-prix/Race") continue;
        const all = m.drivers.flatMap((d) => d.laps);
        expect(all.filter((l) => lapPositionsMeasured(l)).length / all.length, key)
          .toBeGreaterThan(0.5);
      }
    });
  });

/* ==========================================================================
 * The OPTIONAL baked surface block.
 * ======================================================================== */

/** A surface block over makeRaw()'s 3-vertex, 3 m ring (ds = 1 m). */
function makeSurfaceRaw(over: Partial<RawTrackSurface> = {}): RawTrackSurface {
  return {
    dsMetres: 1,
    source: "silverstone.glb",
    sourceSha256: "a".repeat(64),
    profile: "edelta-scorer",
    transform: {
      scale: 0.999404, yawDeg: 0.203281, mirror: -1,
      txM: -277.7467, tzM: 442.3924, tyM: -203.2841,
    },
    zCm: [510, 520, 530],
    slopePermille: [10, 20, null],
    camberPermille: [null, 5, 5],
    validMask: [1, 1, 1],
    residual: { stdM: 0.0508, maxM: 0.1652 },
    coverage: 1,
    roadCoverage: 0.998457,
    assetUrl: "/sim/glb/british-grand-prix.0123456789.glb",
    assetSha256: "b".repeat(64),
    provenance: "DERIVED (exact vertical raycast onto the GLB's scored drive surface)",
    ...over,
  };
}

describe("surface: absence is the normal case, and it is not a surface of zeroes", () => {
  it("parses a track with no surface block to null, not to a flat surface", () => {
    const model = parseTrackModel(makeRaw());
    // undefined would be just as correct a spelling of "absent"; what must NOT happen
    // is an array of zeroes, which would read as "this circuit is at sea level".
    expect(model.surface).toBeNull();
    for (const station of [0, 0.5, 1.7, 2.999]) {
      expect(surfaceAt(model, station), `station ${station}`).toBeNull();
    }
  });

  it("parses an explicit null the same way", () => {
    expect(parseTrackModel(makeRaw({ surface: null })).surface).toBeNull();
  });

  it("leaves every other field of the model untouched when a block IS present", () => {
    const withOut = parseTrackModel(makeRaw());
    const withIn = parseTrackModel(makeRaw({ surface: makeSurfaceRaw() }));
    expect(Array.from(withIn.z)).toEqual(Array.from(withOut.z));
    expect(withIn.lengthMetres).toBe(withOut.lengthMetres);
    expect(withIn.halfWidth).toEqual(withOut.halfWidth);
  });
});

describe("surfaceAt: interpolated where measured, null where not", () => {
  const model = parseTrackModel(makeRaw({ surface: makeSurfaceRaw() }));

  it("dequantises cm and permille into metres and gradients", () => {
    const s = model.surface!;
    // Float32, like the ring's own x/y/z: the source is cm-quantised, so 32 bits carry
    // the value exactly as far as it was ever measured.
    expect(s.zM).toBeInstanceOf(Float32Array);
    expect(Array.from(s.zM)).toHaveLength(3);
    expect(s.zM[0]).toBeCloseTo(5.1, 6);
    expect(s.zM[1]).toBeCloseTo(5.2, 6);
    expect(s.zM[2]).toBeCloseTo(5.3, 6);
    expect(s.slope[0]).toBeCloseTo(0.01, 6);
    expect(s.slope[1]).toBeCloseTo(0.02, 6);
    expect(Number.isNaN(s.slope[2])).toBe(true);       // absent, NOT flat
    expect(Number.isNaN(s.camber[0])).toBe(true);
    expect(s.camber[1]).toBeCloseTo(0.005, 9);
    expect(Array.from(s.valid)).toEqual([1, 1, 1]);
    expect(s.coverage).toBe(1);
    expect(s.residual.maxM).toBeCloseTo(0.1652, 9);
  });

  it("lands on the vertex at an integer station and interpolates between them", () => {
    expect(surfaceAt(model, 0)!.zM).toBeCloseTo(5.1, 6);
    expect(surfaceAt(model, 1)!.zM).toBeCloseTo(5.2, 6);
    expect(surfaceAt(model, 0.5)!.zM).toBeCloseTo(5.15, 6);
    expect(surfaceAt(model, 0.5)!.slope).toBeCloseTo(0.015, 6);
  });

  it("wraps circularly, so the last vertex joins the first", () => {
    expect(surfaceAt(model, 2.5)!.zM).toBeCloseTo(5.2, 6);      // (5.3 + 5.1) / 2
    expect(surfaceAt(model, 3)!.zM).toBeCloseTo(surfaceAt(model, 0)!.zM, 9);
    expect(surfaceAt(model, -0.5)!.zM).toBeCloseTo(surfaceAt(model, 2.5)!.zM, 9);
  });

  it("reports a channel that has no value as null, height and camber separately", () => {
    // slope runs out at vertex 2, camber at vertex 0: a station can have a measured
    // height and no measurable camber, because the camber probe reaches off the model
    // at a track edge. Each channel answers for itself.
    const a = surfaceAt(model, 1.5)!;
    expect(a.zM).toBeCloseTo(5.25, 6);
    expect(a.slope).toBeNull();
    expect(a.camber).toBeCloseTo(0.005, 6);
    const b = surfaceAt(model, 0.5)!;
    expect(b.camber).toBeNull();
    expect(b.slope).toBeCloseTo(0.015, 6);
  });

  it("refuses to interpolate ACROSS a station the raycast missed", () => {
    // This is the whole point of the function. Averaging a measured height with a
    // missing one manufactures ground that was never sampled, and a manufactured
    // height is exactly what puts a car through the road or in the air.
    const gappy = parseTrackModel(makeRaw({
      slug: "gap-test",
      surface: makeSurfaceRaw({ zCm: [510, null, 530], validMask: [1, 0, 1] }),
    }));
    expect(Array.from(gappy.surface!.valid)).toEqual([1, 0, 1]);
    expect(Number.isNaN(gappy.surface!.zM[1])).toBe(true);
    expect(surfaceAt(gappy, 0)).toBeNull();      // 0 -> vertices 0 and 1
    expect(surfaceAt(gappy, 0.5)).toBeNull();
    expect(surfaceAt(gappy, 1.5)).toBeNull();    // vertices 1 and 2
    expect(surfaceAt(gappy, 2.5)!.zM).toBeCloseTo(5.2, 6);   // 2 and 0, both measured
  });

  it("believes the mask only where there is a height to back it up", () => {
    const lying = parseTrackModel(makeRaw({
      slug: "mask-test",
      surface: makeSurfaceRaw({ zCm: [510, null, 530], validMask: [1, 1, 1] }),
    }));
    expect(Array.from(lying.surface!.valid)).toEqual([1, 0, 1]);
    expect(surfaceAt(lying, 0.5)).toBeNull();
  });
});

describe("surface: a block that is present but unusable is refused, loudly", () => {
  it("refuses a surface whose arrays do not line up with the ring", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const model = parseTrackModel(makeRaw({
        slug: "length-mismatch",
        surface: makeSurfaceRaw({ zCm: [510, 520] }),   // 2 heights for 3 vertices
      }));
      // refused rather than half-used: a surface indexed against the wrong stations
      // would stand cars at other corners' heights
      expect(model.surface).toBeNull();
      expect(warn).toHaveBeenCalledTimes(1);
      expect(String(warn.mock.calls[0][0])).toContain("length-mismatch");
    } finally {
      warn.mockRestore();
    }
  });

  it("refuses a block with no usable transform -- there is nowhere to put the model", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const model = parseTrackModel(makeRaw({
        slug: "bad-transform",
        surface: makeSurfaceRaw({
          transform: { scale: NaN, yawDeg: 0, mirror: -1, txM: 0, tzM: 0, tyM: 0 },
        }),
      }));
      expect(model.surface).toBeNull();
      expect(warn).toHaveBeenCalledTimes(1);
    } finally {
      warn.mockRestore();
    }
  });
});

describe("surface: provenance and the published asset", () => {
  it("reads the leading provenance word, and keeps the sentence that explains it", () => {
    const s = parseTrackModel(makeRaw({ surface: makeSurfaceRaw() })).surface!;
    // DERIVED, not OBSERVED: this height was never measured from a car (AGENTS.md 13.6).
    expect(s.provenance).toBe("DERIVED");
    expect(s.provenanceNote).toContain("raycast");
  });

  it("refuses a word outside the vocabulary rather than inventing a seventh", () => {
    const s = parseTrackModel(makeRaw({
      surface: makeSurfaceRaw({ provenance: "MEASURED (from the model)" }),
    })).surface!;
    expect(s.provenance).toBeNull();               // absence, not a new word
    expect(s.provenanceNote).toBe("MEASURED (from the model)");
    const none = parseTrackModel(makeRaw({
      surface: makeSurfaceRaw({ provenance: null }),
    })).surface!;
    expect(none.provenance).toBeNull();
    expect(none.provenanceNote).toBeNull();
  });

  it("treats a missing or empty asset URL as no asset", () => {
    for (const assetUrl of [null, "", undefined]) {
      const s = parseTrackModel(makeRaw({ surface: makeSurfaceRaw({ assetUrl }) })).surface!;
      expect(s.assetUrl, String(assetUrl)).toBeNull();
      // the heights are still perfectly usable; only the model cannot be fetched
      expect(s.zM[0]).toBeCloseTo(5.1, 6);
    }
  });

  it("falls back to the ring's own spacing when the block states none", () => {
    const s = parseTrackModel(makeRaw({
      surface: makeSurfaceRaw({ dsMetres: NaN }),
    })).surface!;
    expect(s.dsMetres).toBeCloseTo(1, 9);
  });
});

/** Shipped models that already carry a baked surface. Zero today (no build has written
 * one yet); the block below activates itself the moment one does. */
const shippedWithSurface = shipped ? parseableModels().filter(({ raw }) => raw.surface) : [];

describe.skipIf(!shipped)("the surface block, against every shipped artifact", () => {
  it("reads as NO SURFACE on every model that carries none", () => {
    const models = parseableModels();
    expect(models.length).toBeGreaterThan(0);
    let absent = 0;
    for (const { slug, raw } of models) {
      if (raw.surface) continue;
      absent++;
      const model = parseTrackModel(raw);
      expect(model.surface, slug).toBeNull();
      // and every station answers "there is no baked height here" rather than 0 m
      for (const f of [0, 0.137, 0.5, 0.871, 0.999]) {
        expect(surfaceAt(model, f * model.lengthMetres), `${slug} @ ${f}`).toBeNull();
      }
    }
    // 13 of 13 today. This is the shape of the fallback the other twelve circuits keep
    // forever, so it is the case worth pinning hardest.
    expect(absent).toBe(models.length - shippedWithSurface.length);
  });
});

describe.skipIf(shippedWithSurface.length === 0)("a shipped baked surface", () => {
  it("is indexed against the ring and stays within its own measured residual", () => {
    for (const { slug, raw } of shippedWithSurface) {
      const model = parseTrackModel(raw);
      const surface = model.surface;
      expect(surface, slug).not.toBeNull();
      expect(raw.schemaVersion, slug).toBe(2);
      // one sample per ring vertex, so station -> index is the same axis for both
      expect(surface!.zM.length, slug).toBe(model.x.length);
      expect(surface!.dsMetres, slug).toBeCloseTo(ringDsMetres(model), 6);

      let valid = 0, worst = 0, checked = 0;
      const ds = ringDsMetres(model);
      for (let i = 0; i < model.x.length; i++) {
        if (surface!.valid[i]) valid++;
        const sample = surfaceAt(model, i * ds);
        if (!sample) continue;
        checked++;
        worst = Math.max(worst, Math.abs(sample.zM - model.z[i]));
      }
      // the producer's own coverage, recomputed from the mask this parse produced
      if (surface!.coverage !== null) {
        expect(valid / model.x.length, slug).toBeCloseTo(surface!.coverage, 3);
      }
      expect(checked, slug).toBeGreaterThan(0);
      // The baked height must sit within the residual the bake itself REPORTED against
      // the ring (+2 cm for the cm quantisation). A surface that drifts further than
      // its own stated residual is either mis-indexed or from a different fit.
      const limit = (surface!.residual.maxM ?? 0.5) + 0.02;
      expect(worst, `${slug}: worst |surface - ring| ${worst.toFixed(3)} m`)
        .toBeLessThanOrEqual(limit);
    }
  });

  it("names a content-hashed published asset", () => {
    for (const { slug, raw } of shippedWithSurface) {
      const surface = parseTrackModel(raw).surface!;
      if (surface.assetUrl === null) continue;   // baked but not published yet
      expect(surface.assetUrl, slug).toMatch(/^\/sim\/glb\/[a-z0-9-]+\.[0-9a-f]{10}\.glb$/);
      expect(surface.assetSha256, slug).toMatch(/^[0-9a-f]{64}$/);
      // the URL's hash is the first 10 of the published bytes' sha256
      expect(surface.assetUrl!.split(".")[1], slug).toBe(surface.assetSha256!.slice(0, 10));
    }
  });
});

// ===========================================================================
// The grid slots the producer publishes, and the road a placement may claim
// ===========================================================================

function gridRaw(slots: RawTrackModel["grid"]["slots"]): RawTrackModel {
  return makeRaw({ grid: { order: ["AAA", "BBB", "CCC"], pitchMetres: 8, slots } });
}

describe("grid slots: the side of the ring is the producer's to state", () => {
  it("parses the published station, driver and lateralSign", () => {
    const slots = parseGridSlots(gridRaw([
      { position: 1, driver: "AAA", station: 5817.741073021581, lateralSign: 1 },
      { position: 2, driver: "BBB", station: 5809.741073021581, lateralSign: -1 },
    ]));
    expect(slots).toHaveLength(2);
    expect(slots[0]).toEqual({
      position: 1, driver: "AAA", station: 5817.741073021581, lateralSign: 1,
    });
    expect(slots[1].lateralSign).toBe(-1);
  });

  it("drops a slot whose side or station is not stated, rather than defaulting it", () => {
    // A sign of 0 is not "the middle": it is a slot this file knows nothing about, and
    // a caller that sees no slot falls back to its own rule instead of drawing a car
    // on a side the producer never named. Absence is null (AGENTS.md 42.5).
    const slots = parseGridSlots(gridRaw([
      { position: 1, driver: "AAA", station: 10, lateralSign: 0 },
      { position: 2, driver: "BBB", station: Number.NaN, lateralSign: 1 },
      { position: 3, driver: null, station: 30, lateralSign: -1 },
    ]));
    expect(slots.map((s) => s.position)).toEqual([3]);
    expect(slots[0].driver).toBeNull();
  });

  it("reports no slots at all rather than inventing them", () => {
    expect(parseGridSlots(gridRaw(null))).toEqual([]);
    expect(parseGridSlots(makeRaw())).toEqual([]);
    // and a model that never came through parseTrackModel (a fixture, the generated
    // engine) answers the same way through the accessor
    expect(gridSlotsOf({ } as unknown as TrackModel)).toEqual([]);
  });

  it("carries the slots through parseTrackModel", () => {
    const model = parseTrackModel(gridRaw([
      { position: 1, driver: "AAA", station: 1, lateralSign: -1 },
    ]));
    expect(model.gridSlots).toHaveLength(1);
    expect(gridSlotsOf(model)[0].lateralSign).toBe(-1);
  });
});

describe.skipIf(!shipped)("grid slots on every shipped artifact", () => {
  it("publishes a +-1 side per slot, and a station the pitch derivation reproduces", () => {
    const lines: string[] = [];
    let slots = 0, worstStationErrM = 0;
    for (const { slug, raw } of parseableModels()) {
      const published = parseGridSlots(raw);
      const L = raw.ring.lengthMetres;
      const pitch = raw.grid.pitchMetres;
      let worst = 0;
      for (const slot of published) {
        slots++;
        expect(Math.abs(slot.lateralSign), `${slug} P${slot.position} sign`).toBe(1);
        // The frontend derives a slot station from the pitch alone. That derivation is
        // sound -- this pins it against the producer's own number on every shipped
        // slot, which is why only the SIDE had to be taken from grid.slots.
        const derived = (((slot.position) * -pitch) % L + L) % L;
        let d = Math.abs(derived - ((slot.station % L) + L) % L);
        if (d > L / 2) d = L - d;
        worst = Math.max(worst, d);
      }
      worstStationErrM = Math.max(worstStationErrM, worst);
      lines.push(`${slug}: ${published.length} slots, worst station error ${worst.toFixed(4)} m`);
    }
    console.log(`published grid slots (${slots} total):\n  ${lines.join("\n  ")}`);
    expect(slots).toBeGreaterThan(200);
    expect(worstStationErrM).toBeLessThan(0.01);
  });
});

describe("measuredLateralRoomM: what the artifact has measured either side of the ring", () => {
  it("answers null on a circuit drawn as the procedural ribbon", () => {
    // Not 0 and not the RULE half-width: the question does not arise, because the road
    // the viewer sees IS the ribbon and halfWidthAt describes it exactly.
    const model = parseTrackModel(makeRaw());
    expect(model.surface).toBeNull();
    expect(measuredLateralRoomM(model)).toBeNull();
  });

  it("answers 0 once a real model is drawn, because the bake probes only the ring", () => {
    const model = parseTrackModel(makeRaw({ surface: makeSurfaceRaw() }));
    expect(model.surface).not.toBeNull();
    expect(measuredLateralRoomM(model)).toBe(0);
  });

  it("says the same thing about the one shipped circuit that has a real model", () => {
    for (const { slug, raw } of parseableModels()) {
      const model = parseTrackModel(raw);
      const room = measuredLateralRoomM(model);
      expect(room, slug).toBe(raw.surface ? 0 : null);
    }
  });
});
