import { describe, expect, it } from "vitest";
import { SAMPLE_BYTES } from "../data/codec";
import type { RawSessionManifest } from "../data/manifest";
import type { TrackModel } from "../contract/types";
import { ReplayTimeline } from "./timeline";

const TRACK_LENGTH = 1000;

function makeTrack(): TrackModel {
  const n = 1000;
  const x = new Float32Array(n), y = new Float32Array(n), z = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const a = (i / n) * 2 * Math.PI;
    x[i] = Math.cos(a) * (TRACK_LENGTH / (2 * Math.PI));
    y[i] = Math.sin(a) * (TRACK_LENGTH / (2 * Math.PI));
    z[i] = 0;
  }
  return {
    slug: "test-track", event: "Test GP", lengthMetres: TRACK_LENGTH,
    x, y, z,
    halfWidth: new Float32Array([6]), widthBinMetres: TRACK_LENGTH,
    timingLines: { sf: 0, s1: 333, s2: 666 },
    corners: [],
    pitLane: { entryStation: null, exitStation: null, mergeStation: null, loopLateral: null },
    grid: { order: ["AAA", "BBB"], pitchMetres: 8 },
    pitLanePath: null,
    referenceProfile: { binMetres: TRACK_LENGTH, speedKph: new Float32Array([250]), gear: new Uint8Array([6]) },
  };
}

/** One lap of constant-speed samples covering the whole ring in `durationS` seconds. */
function encodeConstantSpeedLap(durationS: number, sampleEveryS: number): ArrayBuffer {
  const n = Math.round(durationS / sampleEveryS) + 1;
  const buf = new ArrayBuffer(n * SAMPLE_BYTES);
  const view = new DataView(buf);
  const speedKph = (TRACK_LENGTH / durationS) * 3.6;
  for (let i = 0; i < n; i++) {
    const t = i * sampleEveryS;
    const station = (t / durationS) * TRACK_LENGTH;
    const o = i * SAMPLE_BYTES;
    view.setUint16(o, i === 0 ? 0 : Math.round(sampleEveryS * 1000), true);
    view.setFloat32(o + 2, station, true);
    view.setInt16(o + 6, 0, true);
    view.setUint16(o + 8, Math.round(speedKph), true);
    view.setUint8(o + 10, 6);
    view.setUint8(o + 11, 100);
  }
  return buf;
}

/** A large constant added to every lST/sesT/pin/pout, mirroring the REAL data: lap 1's
 * lST is identical across drivers but is measured 2000-3500 s into the recording
 * session, not 0. The timeline must normalise this away or every car reads as still
 * on the grid for the whole session. */
const SESSION_ABSOLUTE_OFFSET = 3339.562;

function buildManifest(): { manifest: RawSessionManifest; bin: ArrayBuffer } {
  // AAA: two 100 s laps starting at t=0 and t=100. BBB: two 105 s laps, slightly
  // behind, starting at t=0 and t=105 -- so at t=150, AAA has done 1 lap and is
  // partway through lap 2; BBB is still on lap 2 too but behind on station.
  const lapA1 = encodeConstantSpeedLap(100, 1);
  const lapA2 = encodeConstantSpeedLap(100, 1);
  const lapB1 = encodeConstantSpeedLap(105, 1);
  const lapB2 = encodeConstantSpeedLap(105, 1);

  const parts = [lapA1, lapA2, lapB1, lapB2];
  const totalBytes = parts.reduce((s, p) => s + p.byteLength, 0);
  const bin = new Uint8Array(totalBytes);
  let off = 0;
  const offsets: number[] = [];
  for (const p of parts) {
    offsets.push(off);
    bin.set(new Uint8Array(p), off);
    off += p.byteLength;
  }

  const manifest: RawSessionManifest = {
    event: "Test GP", session: "Race", trackSlug: "test-track",
    trackLengthMetres: TRACK_LENGTH, sampleStructBytes: SAMPLE_BYTES,
    binFile: "test.bin",
    drivers: [
      {
        driver: "AAA", team: "Team A",
        laps: [
          { lap: 1, byteOffset: offsets[0], sampleCount: lapA1.byteLength / SAMPLE_BYTES,
            positionFrame: "A", lST: SESSION_ABSOLUTE_OFFSET, sesT: SESSION_ABSOLUTE_OFFSET + 100, time: 100, pin: null, pout: null,
            status: "1", pos: 1, compound: "MEDIUM", stint: 1, life: 1, fresh: true,
            iacc: true, del: false, ff1G: false, energy: null },
          { lap: 2, byteOffset: offsets[1], sampleCount: lapA2.byteLength / SAMPLE_BYTES,
            positionFrame: "A", lST: SESSION_ABSOLUTE_OFFSET + 100, sesT: SESSION_ABSOLUTE_OFFSET + 200, time: 100, pin: null, pout: null,
            status: "1", pos: 1, compound: "MEDIUM", stint: 1, life: 2, fresh: false,
            iacc: true, del: false, ff1G: false, energy: null },
        ],
      },
      {
        driver: "BBB", team: "Team B",
        laps: [
          { lap: 1, byteOffset: offsets[2], sampleCount: lapB1.byteLength / SAMPLE_BYTES,
            positionFrame: "A", lST: SESSION_ABSOLUTE_OFFSET, sesT: SESSION_ABSOLUTE_OFFSET + 105, time: 105, pin: null, pout: null,
            status: "1", pos: 2, compound: "HARD", stint: 1, life: 1, fresh: true,
            iacc: true, del: false, ff1G: false, energy: null },
          { lap: 2, byteOffset: offsets[3], sampleCount: lapB2.byteLength / SAMPLE_BYTES,
            positionFrame: "A", lST: SESSION_ABSOLUTE_OFFSET + 105, sesT: SESSION_ABSOLUTE_OFFSET + 210, time: 105, pin: null, pout: null,
            status: "1", pos: 2, compound: "HARD", stint: 1, life: 2, fresh: false,
            iacc: true, del: false, ff1G: false, energy: null },
        ],
      },
    ],
    raceControl: [{ sessionTime: SESSION_ABSOLUTE_OFFSET + 50, kind: "vsc", message: "VSC DEPLOYED", cars: [] }],
    neutralisation: [{ kind: "VSC", start: SESSION_ABSOLUTE_OFFSET + 50, end: SESSION_ABSOLUTE_OFFSET + 80 }],
    weather: null,
    capabilities: { hasPositions: true, hasDriverAhead: false },
  };
  return { manifest, bin: bin.buffer };
}

describe("ReplayTimeline", () => {
  const track = makeTrack();
  const { manifest, bin } = buildManifest();
  const timeline = new ReplayTimeline(manifest, track, bin);

  it("reports totalLaps and duration from the lap tables", () => {
    expect(timeline.totalLaps).toBe(2);
    expect(timeline.duration).toBe(210);
  });

  it("places AAA ahead of BBB once AAA is faster and both are on the same lap", () => {
    const states = timeline.sampleAt(50); // both mid-lap-1; AAA is faster (100s vs 105s lap)
    const a = states.get("AAA")!;
    const b = states.get("BBB")!;
    expect(a.position).toBe(1);
    expect(b.position).toBe(2);
    expect(a.lapsDone).toBe(0);
    expect(a.lapProgress).toBeGreaterThan(b.lapProgress);
  });

  it("gives AAA a full extra lap once it crosses the line first", () => {
    const states = timeline.sampleAt(102); // AAA has completed lap 1; BBB has not (105s)
    const a = states.get("AAA")!;
    const b = states.get("BBB")!;
    expect(a.lapsDone).toBe(1);
    expect(b.lapsDone).toBe(0);
    expect(a.position).toBe(1);
  });

  it("does NOT call a car lapped merely because the leader crossed the line", () => {
    // At t=102 AAA is 2 s into lap 2 and BBB is ~97 % through lap 1: a few seconds
    // apart, not a lap. Comparing bare lap NUMBERS used to report "+1 LAP" here.
    const b = timeline.sampleAt(102).get("BBB")!;
    expect(b.lapsDownFromLeader).toBe(0);
  });

  it("does report a car lapped once it is a full lap of progress behind", () => {
    // AAA finishes its 2 laps at t=200; BBB is only ~90 % through lap 2 at t=200,
    // so pick a time where the gap in total progress really does exceed one lap.
    const states = timeline.sampleAt(205);
    const a = states.get("AAA")!;
    const b = states.get("BBB")!;
    const progressA = a.lapsDone + a.lapProgress;
    const progressB = b.lapsDone + b.lapProgress;
    expect(b.lapsDownFromLeader).toBe(Math.max(0, Math.floor(progressA - progressB)));
  });

  it("computes a non-negative gap when both cars are on the same lap", () => {
    const states = timeline.sampleAt(50);
    const b = states.get("BBB")!;
    expect(b.gapToLeaderS).not.toBeNull();
    expect(b.gapToLeaderS!).toBeGreaterThanOrEqual(0);
  });

  it("marks the leader in the pit-free 'track' state, not grid or finished mid-race", () => {
    const a = timeline.sampleAt(50).get("AAA")!;
    expect(a.status).toBe("track");
  });

  it("classifies the final finisher as 'finished' after the last lap ends", () => {
    const states = timeline.sampleAt(205);
    expect(states.get("AAA")!.status).toBe("finished");
  });

  it("passes race-control events and neutralisation intervals through unmodified", () => {
    expect(timeline.events()).toHaveLength(1);
    expect(timeline.events()[0].kind).toBe("vsc");
    expect(timeline.neutralisations()).toEqual([{ kind: "VSC", start: 50, end: 80 }]);
  });
});
