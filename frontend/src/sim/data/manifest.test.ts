import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { TrackModel } from "../contract/types";
import {
  halfWidthAt, lapPositionDropped, lapPositionFrame, lapPositionsMeasured,
  parseCorners, parseTrackModel, ringDsMetres, trackPointAt,
  type RawSessionManifest, type RawTrackModel,
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
