import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { SAMPLE_BYTES } from "../data/codec";
import type { RawLapEntry, RawSessionManifest, RawTrackModel } from "../data/manifest";
import { parseTrackModel } from "../data/manifest";
import type { CarState, TrackModel } from "../contract/types";
import { ReplayTimeline } from "./timeline";
import { buildDashboardSnapshot } from "./dashboard";

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

  it("does not call the runner-up lapped just because the winner has finished", () => {
    // AAA finished its 2 laps at t=200; at t=205 BBB is ~95 % through lap 2, i.e. about
    // five seconds behind, not a lap. Ranking used lapsDone + lapProgress, and a car
    // parked at the line reports lapProgress 1 on top of a lapsDone that already counts
    // that lap -- so the winner read as a full lap further on than it was and the
    // runner-up was labelled "+1 LAP" at the flag.
    const states = timeline.sampleAt(205);
    expect(states.get("AAA")!.status).toBe("finished");
    expect(states.get("BBB")!.lapsDownFromLeader).toBe(0);
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

  it("never reports a gap computed from a placed (non-measured) position", () => {
    // Grid slots, the parked queue and pit-lane starts are documented placements, not
    // telemetry. A gap derived from one would present a rule as a measurement.
    for (const t of [0, 1, 50, 102, 150, 205, 209]) {
      for (const car of timeline.sampleAt(t).values()) {
        if (car.positionProvenance !== "OBSERVED") {
          expect(car.gapToLeaderS).toBeNull();
          expect(car.intervalS).toBeNull();
        }
      }
    }
  });

  it("tags every car's position as measured or placed", () => {
    for (const t of [0, 50, 205]) {
      for (const car of timeline.sampleAt(t).values()) {
        expect(["OBSERVED", "RULE"]).toContain(car.positionProvenance);
      }
    }
  });

  it("passes race-control events and neutralisation intervals through unmodified", () => {
    expect(timeline.events()).toHaveLength(1);
    expect(timeline.events()[0].kind).toBe("vsc");
    expect(timeline.neutralisations()).toEqual([{ kind: "VSC", start: 50, end: 80 }]);
  });
});

// ===========================================================================
// A race-shaped synthetic session.
//
// Two cars on a ring are not enough to catch an ordering bug: every one of them
// showed up only once the field was big enough to contain a pit stop, a lapped car
// and a retirement at the same time. This builds a 12-lap, 8-car race with
//   - a standing start (4 s of stationary, station-stuck lap-1 samples, which is what
//     the real packs contain),
//   - one car released from the PIT LANE 12 s after the lights,
//   - one 25 s pit stop mid-race,
//   - one retirement after lap 5,
//   - one backmarker slow enough to be lapped before the flag,
//   - a `pos` hole on one lap (0.2 % of real lap rows have none),
//   - a final-lap `pos` that deliberately contradicts the order the cars crossed the
//     line in, so "official is authoritative for the classified cars" is actually
//     tested rather than being trivially true.
// ===========================================================================

const RACE_DRIVERS = ["D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08"];
const RACE_LAPS = 12;
const RETIRES_AFTER = 5; // D07
const PIT_LAP = 6; // D04
const PIT_LOSS_S = 25;
const PIT_START_DELAY_S = 12; // D06 is released from the lane after the field has gone
const STANDING_START_S = 4;
const RACE_SAMPLE_DT = 0.25; // the real packs sample at roughly 240 ms

/** Station along the lap for a 0..1 fraction of the lap's MOVING time. Deliberately
 * non-uniform (a car is quicker on some parts of the lap than others) so the gap
 * lookup, which searches the leader's lap for the nearest station, is exercised on a
 * non-linear time-station curve rather than a straight line. Monotone in u, and it
 * stops short of the line so a sample never wraps back to station 0. */
function stationOf(u: number): number {
  const c = Math.min(1, Math.max(0, u));
  const s = TRACK_LENGTH * (c + (0.15 * Math.sin(2 * Math.PI * c)) / (2 * Math.PI));
  return Math.min(s, TRACK_LENGTH - 0.5);
}

function lapSeconds(driver: string, lap: number): number {
  const i = RACE_DRIVERS.indexOf(driver);
  const base = driver === "D08" ? 112 : 100 + i * 0.7;
  const wobble = ((lap * 7 + i * 13) % 5) * 0.4;
  return base + wobble + (driver === "D04" && lap === PIT_LAP ? PIT_LOSS_S : 0);
}

interface PlanLap { lap: number; lST: number; sesT: number; dur: number; hold: number }

function planRace(): Map<string, PlanLap[]> {
  const out = new Map<string, PlanLap[]>();
  for (const d of RACE_DRIVERS) {
    const laps: PlanLap[] = [];
    let t = d === "D06" ? PIT_START_DELAY_S : 0;
    const total = d === "D07" ? RETIRES_AFTER : RACE_LAPS;
    for (let lap = 1; lap <= total; lap++) {
      const hold = lap === 1 && d !== "D06" ? STANDING_START_S : 0;
      const dur = lapSeconds(d, lap) + hold;
      laps.push({ lap, lST: t, sesT: t + dur, dur, hold });
      t += dur;
    }
    out.set(d, laps);
  }
  return out;
}

/** Ground truth: metres covered from the start/finish line at race-relative time t. */
function distanceAt(plan: Map<string, PlanLap[]>, driver: string, t: number): number {
  const laps = plan.get(driver)!;
  let done = 0;
  for (const l of laps) {
    if (t >= l.sesT) { done = l.lap * TRACK_LENGTH; continue; }
    if (t < l.lST) return done;
    return (l.lap - 1) * TRACK_LENGTH + stationOf((t - l.lST - l.hold) / (l.dur - l.hold));
  }
  return done;
}

/** When this car last crossed the line, at or before t. */
function lastCrossing(plan: Map<string, PlanLap[]>, driver: string, t: number): number {
  let last = -Infinity;
  for (const l of plan.get(driver)!) if (t >= l.sesT) last = l.sesT;
  return last;
}

/** The classification rule a real timing tower uses: most ground covered, and on equal
 * ground (two cars that have both taken the flag) whoever got there first. */
function classifiedAhead(plan: Map<string, PlanLap[]>, e: string, d: string, t: number): boolean {
  const de = distanceAt(plan, e, t), dd = distanceAt(plan, d, t);
  if (Math.abs(de - dd) > 1e-9) return de > dd;
  return lastCrossing(plan, e, t) < lastCrossing(plan, d, t);
}

function encodeRaceLap(p: PlanLap): ArrayBuffer {
  const n = Math.round(p.dur / RACE_SAMPLE_DT) + 1;
  const buf = new ArrayBuffer(n * SAMPLE_BYTES);
  const view = new DataView(buf);
  const moving = p.dur - p.hold;
  for (let i = 0; i < n; i++) {
    const t = i * RACE_SAMPLE_DT;
    const u = t <= p.hold ? 0 : (t - p.hold) / moving;
    const station = stationOf(u);
    const speedKph = t <= p.hold
      ? 0
      : (TRACK_LENGTH * (1 + 0.15 * Math.cos(2 * Math.PI * Math.min(1, u))) / moving) * 3.6;
    const o = i * SAMPLE_BYTES;
    view.setUint16(o, i === 0 ? 0 : Math.round(RACE_SAMPLE_DT * 1000), true);
    view.setFloat32(o + 2, station, true);
    view.setInt16(o + 6, 0, true);
    view.setUint16(o + 8, Math.round(speedKph), true);
    view.setUint8(o + 10, 6);
    view.setUint8(o + 11, 100);
  }
  return buf;
}

function buildRaceManifest(): { manifest: RawSessionManifest; bin: ArrayBuffer; plan: Map<string, PlanLap[]> } {
  const plan = planRace();
  const blocks: { driver: string; lap: number; buf: ArrayBuffer }[] = [];
  for (const d of RACE_DRIVERS) for (const p of plan.get(d)!) blocks.push({ driver: d, lap: p.lap, buf: encodeRaceLap(p) });
  const total = blocks.reduce((s, b) => s + b.buf.byteLength, 0);
  const bin = new Uint8Array(total);
  const offsets = new Map<string, number>();
  let off = 0;
  for (const b of blocks) {
    offsets.set(`${b.driver}/${b.lap}`, off);
    bin.set(new Uint8Array(b.buf), off);
    off += b.buf.byteLength;
  }

  const drivers = RACE_DRIVERS.map((d) => {
    const laps: RawLapEntry[] = plan.get(d)!.map((p) => {
      // The official classification: how many cars had covered more ground at the
      // instant this car crossed the line to complete the lap.
      const ahead = RACE_DRIVERS.filter((e) => e !== d && classifiedAhead(plan, e, d, p.sesT)).length;
      const isPitLap = (d === "D04" && p.lap === PIT_LAP) || (d === "D06" && p.lap === 1);
      return {
        lap: p.lap,
        byteOffset: offsets.get(`${d}/${p.lap}`)!,
        sampleCount: Math.round(p.dur / RACE_SAMPLE_DT) + 1,
        positionFrame: "A" as const,
        lST: SESSION_ABSOLUTE_OFFSET + p.lST,
        sesT: SESSION_ABSOLUTE_OFFSET + p.sesT,
        time: p.dur,
        pin: d === "D04" && p.lap === PIT_LAP ? SESSION_ABSOLUTE_OFFSET + p.lST + 55 : null,
        pout: isPitLap ? SESSION_ABSOLUTE_OFFSET + p.lST + (d === "D06" ? 0 : 80) : null,
        status: "1",
        // one hole, as the real feed has on crash laps
        pos: d === "D05" && p.lap === 3 ? null : ahead + 1,
        compound: "MEDIUM", stint: 1, life: p.lap, fresh: p.lap === 1,
        iacc: true, del: false, ff1G: false, energy: null,
      };
    });
    return { driver: d, team: `T${d}`, laps };
  });

  // The result is reclassified: D03 is given D02's final position and vice versa. Only
  // the official field says so -- they crossed the line in the other order -- so the
  // board can only get this right by treating the classification as authoritative once
  // a car is no longer being measured.
  const finalOf = (d: string) => {
    const rows = drivers.find((x) => x.driver === d)!.laps;
    return rows[rows.length - 1];
  };
  const a = finalOf("D02"), b = finalOf("D03");
  [a.pos, b.pos] = [b.pos, a.pos];

  return {
    manifest: {
      event: "Synthetic GP", session: "Race", trackSlug: "test-track",
      trackLengthMetres: TRACK_LENGTH, sampleStructBytes: SAMPLE_BYTES, binFile: "race.bin",
      drivers,
      raceControl: [], neutralisation: [], weather: null,
      capabilities: { hasPositions: true, hasDriverAhead: false },
    },
    bin: bin.buffer,
    plan,
  };
}

function raceTrack(): TrackModel {
  return { ...makeTrack(), grid: { order: [...RACE_DRIVERS], pitchMetres: 8 } };
}

/** Measured distance covered, in laps, as the board itself reports it. `lapProgress` is
 * 1 for a car sitting at the line (finished, or between two lap rows) on top of a
 * lapsDone that already counts that lap, so those states are read as exactly lapsDone. */
function reportedProgress(car: CarState): number {
  return car.lapsDone + (car.status === "gap" || car.status === "finished" || car.status === "retired"
    ? 0 : car.lapProgress);
}

describe("ReplayTimeline running order (race-shaped session)", () => {
  const track = raceTrack();
  const { manifest, bin } = buildRaceManifest();
  const timeline = new ReplayTimeline(manifest, track, bin);
  const instants: number[] = [];
  for (let t = 0; t <= timeline.duration; t += 2) instants.push(t);

  it("builds a race with the shape the rest of these tests assume", () => {
    expect(timeline.totalLaps).toBe(RACE_LAPS);
    expect(instants.length).toBeGreaterThan(600);
    const end = timeline.sampleAt(timeline.duration);
    expect(end.get("D07")!.status).toBe("retired");
    expect(end.get("D01")!.status).toBe("finished");
    // the backmarker really is a lap down while the leader is still running
    const late = timeline.sampleAt(1180);
    expect(late.get("D08")!.lapsDownFromLeader).toBeGreaterThanOrEqual(1);
    // the standing start really is stationary and really is a RULE placement
    expect(timeline.sampleAt(2).get("D01")!.positionProvenance).toBe("RULE");
    expect(timeline.sampleAt(2).get("D01")!.status).toBe("grid");
    // the pit-lane starter really is held in the lane
    expect(timeline.sampleAt(6).get("D06")!.status).toBe("pit");
  });

  it("gives every car a distinct position 1..N at every instant", () => {
    for (const t of instants) {
      const positions = [...timeline.sampleAt(t).values()].map((c) => c.position).sort((x, y) => x - y);
      expect(positions).toEqual(positions.map((_, i) => i + 1));
    }
  });

  it("NEVER lets the gap to the leader decrease as you read down the order", () => {
    let worstDrop = 0;
    let worstAt = -1;
    let checked = 0;
    for (const t of instants) {
      const rows = [...timeline.sampleAt(t).values()].sort((x, y) => x.position - y.position);
      let previous = -Infinity;
      for (const r of rows) {
        if (r.gapToLeaderS === null) continue;
        checked++;
        const drop = previous - r.gapToLeaderS;
        if (drop > worstDrop) { worstDrop = drop; worstAt = t; }
        previous = r.gapToLeaderS;
      }
    }
    console.log(`gap monotonicity: ${checked} reported gaps over ${instants.length} instants,`
      + ` worst decrease ${worstDrop.toFixed(4)} s (t=${worstAt})`);
    expect(checked).toBeGreaterThan(3000);
    expect(worstDrop).toBeLessThanOrEqual(1e-6);
  });

  it("never shows a negative interval, and the dashboard column agrees", () => {
    for (const t of instants) {
      const states = timeline.sampleAt(t);
      for (const car of states.values()) {
        if (car.intervalS !== null) expect(car.intervalS).toBeGreaterThanOrEqual(0);
      }
      const snap = buildDashboardSnapshot(states, t, [], []);
      let previous = -Infinity;
      for (const row of snap.leaderboard) {
        const printed = /^\+\d+(\.\d+)?$/.test(row.gapToLeader) ? Number(row.gapToLeader.slice(1)) : null;
        if (printed === null) continue;
        expect(printed).toBeGreaterThanOrEqual(previous);
        previous = printed;
      }
    }
  });

  it("orders every measured car by measured distance, which is what makes gaps monotone", () => {
    for (const t of instants) {
      const rows = [...timeline.sampleAt(t).values()]
        .sort((x, y) => x.position - y.position)
        .filter((c) => c.positionProvenance === "OBSERVED");
      for (let i = 1; i < rows.length; i++) {
        expect(reportedProgress(rows[i - 1])).toBeGreaterThanOrEqual(reportedProgress(rows[i]) - 1e-9);
      }
    }
  });

  it("reads the classification strictly from the past: future `pos` values change nothing", () => {
    // The strongest statement of causality available from outside: corrupt every `pos`
    // the field has not earned yet at time t and the board at t must be identical.
    // `pos` is the position at the END of a lap, so the lap a car is on carries a value
    // it has not reached; reading it was reading the future.
    for (const t of [40, 220, 600, 900, 1200]) {
      const abs = SESSION_ABSOLUTE_OFFSET + t;
      const poisoned: RawSessionManifest = {
        ...manifest,
        drivers: manifest.drivers.map((d) => ({
          ...d,
          laps: d.laps.map((l) => (l.sesT !== null && l.sesT > abs && l.pos !== null
            ? { ...l, pos: 99 - l.pos } : l)),
        })),
      };
      const poisonedTimeline = new ReplayTimeline(poisoned, raceTrack(), bin);
      const expected = [...timeline.sampleAt(t).values()]
        .sort((x, y) => x.position - y.position).map((c) => c.driver);
      const actual = [...poisonedTimeline.sampleAt(t).values()]
        .sort((x, y) => x.position - y.position).map((c) => c.driver);
      expect(actual).toEqual(expected);
    }
  });

  it("lets the official classification decide the finishing order, over the crossing times", () => {
    // D02 and D03 crossed the line in one order and were classified in the other. Once
    // a car is parked its position is a RULE placement, so nothing measured can rank it
    // and the classification is the only truth left.
    const end = [...timeline.sampleAt(timeline.duration).values()]
      .sort((a, b) => a.position - b.position).map((c) => c.driver);
    const finishTime = (d: string) => {
      const laps = manifest.drivers.find((x) => x.driver === d)!.laps;
      return laps[laps.length - 1].sesT!;
    };
    expect(finishTime("D02")).toBeLessThan(finishTime("D03")); // D02 crossed first
    expect(end.indexOf("D03")).toBeLessThan(end.indexOf("D02")); // D03 is classified ahead
    for (const c of timeline.sampleAt(timeline.duration).values()) {
      if (c.status === "finished") expect(c.orderSource).toBe("OFFICIAL");
    }
  });

  it("labels where every row's place came from", () => {
    const seen = new Set<string>();
    for (const t of instants) {
      for (const car of timeline.sampleAt(t).values()) {
        expect(["OFFICIAL", "MEASURED", "RULE"]).toContain(car.orderSource);
        // a placed car is never presented as a measured on-track order
        if (car.positionProvenance !== "OBSERVED") expect(car.orderSource).not.toBe("MEASURED");
        seen.add(car.orderSource);
      }
    }
    expect([...seen].sort()).toEqual(["MEASURED", "OFFICIAL", "RULE"]);
  });

  it("never reports a gap for a placed car, or for a car in the pit lane", () => {
    for (const t of instants) {
      for (const car of timeline.sampleAt(t).values()) {
        if (car.positionProvenance !== "OBSERVED" || car.inPit) {
          expect(car.gapToLeaderS).toBeNull();
          expect(car.intervalS).toBeNull();
        }
      }
    }
  });
});

// ===========================================================================
// The same checks against a real 2026 pack. The packs are build artifacts and are
// git-ignored, so this suite skips when they are not present rather than failing.
// ===========================================================================

interface RealPack {
  slug: string; track: TrackModel; manifest: RawSessionManifest; bin: ArrayBuffer;
}

function loadRealPack(): RealPack | null {
  const dir = join(process.cwd(), "public", "sim");
  const indexPath = join(dir, "index.json");
  if (!existsSync(indexPath)) return null;
  const latest = (JSON.parse(readFileSync(indexPath, "utf-8")) as { latest: string }).latest;
  if (!existsSync(join(dir, latest))) return null;
  const index = JSON.parse(readFileSync(join(dir, latest), "utf-8")) as {
    tracks: Record<string, string>;
    sessions: Record<string, Record<string, { manifest: string; bin: string }>>;
  };
  for (const slug of Object.keys(index.sessions)) {
    const race = index.sessions[slug]?.Race;
    const trackFile = index.tracks[slug];
    if (!race || !trackFile) continue;
    const paths = [trackFile, race.manifest, race.bin].map((f) => join(dir, f));
    if (!paths.every(existsSync)) continue;
    const raw = readFileSync(paths[2]);
    return {
      slug,
      track: parseTrackModel(JSON.parse(readFileSync(paths[0], "utf-8")) as RawTrackModel),
      manifest: JSON.parse(readFileSync(paths[1], "utf-8")) as RawSessionManifest,
      bin: raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength) as ArrayBuffer,
    };
  }
  return null;
}

const realPack = loadRealPack();

describe.skipIf(!realPack)("ReplayTimeline against a real 2026 race pack", () => {
  const pack = realPack!;
  const timeline = new ReplayTimeline(pack.manifest, pack.track, pack.bin);
  // the raw manifest is session-absolute; the timeline normalises by the earliest lap-1
  // start, so mirror that here to line the two clocks up.
  const offset = Math.min(
    ...pack.manifest.drivers.flatMap((d) => d.laps.map((l) => l.lST ?? Infinity)),
  );
  const instants: number[] = [];
  for (let t = 0; t <= timeline.duration; t += 5) instants.push(t);

  /** The official classification the car has actually earned at race-relative time t. */
  function officialAt(driver: string, t: number): number | null {
    const laps = pack.manifest.drivers.find((d) => d.driver === driver)?.laps ?? [];
    let pos: number | null = null;
    for (const l of [...laps].sort((a, b) => a.lap - b.lap)) {
      if (l.sesT === null || l.sesT - offset > t) break;
      if (typeof l.pos === "number") pos = l.pos;
    }
    return pos;
  }

  it("holds the gap invariant on EVERY pack on disk, not just this one", () => {
    // One track cannot cover a red flag, a Monaco-length session or a 44-lap sprint.
    // Every violation found while writing this was specific to one circuit's data.
    const dir = join(process.cwd(), "public", "sim");
    const latest = (JSON.parse(readFileSync(join(dir, "index.json"), "utf-8")) as { latest: string }).latest;
    const index = JSON.parse(readFileSync(join(dir, latest), "utf-8")) as {
      tracks: Record<string, string>;
      sessions: Record<string, Record<string, { manifest: string; bin: string }>>;
    };
    const lines: string[] = [];
    let sessions = 0, totalGaps = 0, unreadable = 0;
    for (const slug of Object.keys(index.sessions)) {
      for (const session of Object.keys(index.sessions[slug])) {
        const s = index.sessions[slug][session];
        const trackFile = index.tracks[slug];
        const files = [trackFile, s.manifest, s.bin];
        if (!files.every((f) => f && existsSync(join(dir, f)))) continue;
        let tl: ReplayTimeline;
        try {
          const raw = readFileSync(join(dir, s.bin));
          tl = new ReplayTimeline(
            JSON.parse(readFileSync(join(dir, s.manifest), "utf-8")) as RawSessionManifest,
            parseTrackModel(JSON.parse(readFileSync(join(dir, trackFile), "utf-8")) as RawTrackModel),
            raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength) as ArrayBuffer,
          );
        } catch (e) {
          // e.g. the 2026 Chinese pack currently ships NaN in its track JSON, which is
          // not valid JSON at all. That is a pack-build problem, not an ordering one.
          unreadable++;
          lines.push(`${slug}/${session}: UNREADABLE ${(e as Error).message.slice(0, 48)}`);
          continue;
        }
        sessions++;
        let worst = 0, worstAt = -1, gaps = 0, badPermutations = 0;
        for (let t = 0; t <= tl.duration; t += 5) {
          const rows = [...tl.sampleAt(t).values()].sort((a, b) => a.position - b.position);
          if (rows.some((r, i) => r.position !== i + 1)) badPermutations++;
          let previous = -Infinity;
          for (const r of rows) {
            if (r.gapToLeaderS === null) continue;
            gaps++;
            if (previous - r.gapToLeaderS > worst) { worst = previous - r.gapToLeaderS; worstAt = t; }
            previous = r.gapToLeaderS;
          }
        }
        totalGaps += gaps;
        lines.push(`${slug}/${session}: ${tl.driverList.length} drivers, ${tl.totalLaps} laps,`
          + ` ${tl.duration.toFixed(0)} s, ${gaps} gaps, worst decrease ${worst.toFixed(4)} s`
          + ` (t=${worstAt}), ${badPermutations} broken position sets`);
        expect(badPermutations, `${slug}/${session} position set`).toBe(0);
        expect(worst, `${slug}/${session} gap monotonicity`).toBeLessThanOrEqual(1e-6);
      }
    }
    console.log(`all packs (${sessions} readable, ${unreadable} unreadable,`
      + ` ${totalGaps} reported gaps):\n  ${lines.join("\n  ")}`);
    expect(sessions).toBeGreaterThan(5);
  }, 120_000);

  it("loads the pack", () => {
    console.log(`real pack: ${pack.slug} Race, ${timeline.driverList.length} drivers,`
      + ` ${timeline.totalLaps} laps, ${timeline.duration.toFixed(0)} s,`
      + ` ${instants.length} sampled instants`);
    expect(timeline.driverList.length).toBeGreaterThan(10);
  });

  it("gives every car a distinct position 1..N at every instant", () => {
    for (const t of instants) {
      const positions = [...timeline.sampleAt(t).values()].map((c) => c.position).sort((a, b) => a - b);
      expect(positions).toEqual(positions.map((_, i) => i + 1));
    }
  });

  it("NEVER lets the gap to the leader decrease as you read down the order", () => {
    let worstDrop = 0, worstAt = -1, checked = 0;
    for (const t of instants) {
      const rows = [...timeline.sampleAt(t).values()].sort((a, b) => a.position - b.position);
      let previous = -Infinity;
      for (const r of rows) {
        if (r.gapToLeaderS === null) continue;
        checked++;
        const drop = previous - r.gapToLeaderS;
        if (drop > worstDrop) { worstDrop = drop; worstAt = t; }
        previous = r.gapToLeaderS;
      }
    }
    console.log(`real pack gap monotonicity: ${checked} reported gaps, worst decrease`
      + ` ${worstDrop.toFixed(4)} s (t=${worstAt})`);
    expect(worstDrop).toBeLessThanOrEqual(1e-6);
  });

  it("agrees with the official classification wherever the classification is decisive", () => {
    // Adjacent pairs of cars whose earned official positions differ, split by what kind
    // of pair it is. Disagreement is only meaningful for cars that are still racing:
    //  - a RETIRED car keeps the classification it held when it stopped (P5, say) for
    //    the rest of the session; it belongs at the bottom of the board, not at P5, so
    //    disagreement there is the correct answer and counting it proves nothing.
    //  - two FINISHED cars are pure placements, so nothing measured can rank them and
    //    the classification must win every time.
    let racing = 0, racingAgree = 0;
    let classified = 0, classifiedAgree = 0;
    for (const t of instants) {
      const rows = [...timeline.sampleAt(t).values()].sort((a, b) => a.position - b.position);
      for (let i = 1; i < rows.length; i++) {
        const hi = rows[i - 1], lo = rows[i];
        const above = officialAt(hi.driver, t), below = officialAt(lo.driver, t);
        if (above === null || below === null || above === below) continue;
        if (hi.status === "finished" && lo.status === "finished") {
          classified++;
          if (above < below) classifiedAgree++;
        } else if (hi.status === "track" && lo.status === "track") {
          racing++;
          if (above < below) racingAgree++;
        }
      }
    }
    const pct = (100 * racingAgree) / racing;
    console.log(`real pack vs official classification: running cars ${racingAgree}/${racing}`
      + ` adjacent pairs agree (${pct.toFixed(2)} %); classified cars`
      + ` ${classifiedAgree}/${classified} (${(100 * classifiedAgree / classified).toFixed(2)} %)`);
    expect(racing).toBeGreaterThan(10000);
    expect(classified).toBeGreaterThan(20);
    // Once a car is parked there is nothing left to measure, so the classification is
    // the only truth and must be reproduced exactly.
    expect(classifiedAgree).toBe(classified);
    // While cars are running, `pos` is a snapshot from each car's own last line crossing,
    // so a live on-track order legitimately differs within a lap. What must not happen is
    // the board wandering away from the classification.
    expect(pct).toBeGreaterThan(95);
  });

  it("never reports a gap for a placed car, or for a car in the pit lane", () => {
    for (const t of instants) {
      for (const car of timeline.sampleAt(t).values()) {
        if (car.positionProvenance !== "OBSERVED" || car.inPit) {
          expect(car.gapToLeaderS).toBeNull();
          expect(car.intervalS).toBeNull();
        }
      }
    }
  });
});
