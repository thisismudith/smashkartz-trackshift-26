import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { SAMPLE_BYTES } from "../data/codec";
import type { RawLapEntry, RawSessionManifest, RawTrackModel } from "../data/manifest";
import type { GridSlot } from "../data/manifest";
import { gridSlotsOf, halfWidthAt, measuredLateralRoomM, parseTrackModel } from "../data/manifest";
import type { CarState, TrackModel } from "../contract/types";
import { CAR_RENDER_WIDTH_M } from "../render/presentation";
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
/**
 * The lateral every sample of this synthetic race carries, centimetres.
 *
 * Deliberately absurd, and deliberately NOT zero. A stationary car's telemetry lateral
 * is a projection of whatever coordinate the feed emitted while it sat still, and on the
 * real packs that is a frozen or absent position: the median |lateral| measured on cars
 * the board had already PLACED on a grid slot was 35.55 m at Monaco, 19.00 m at Monza,
 * 18.91 m at the Silverstone sprint, 13.83 m at Hungary. Parked cars were worse -- 327.00 m
 * at Hungary (replay.py's own clip limit), 60.19 m at Miami. A fixture whose lateral is 0
 * cannot tell a placement that owns its lateral from one that is being overwritten.
 */
const BOGUS_LATERAL_CM = 3000; // 30.00 m, well outside the 6 m half-width of makeTrack()

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
    view.setInt16(o + 6, BOGUS_LATERAL_CM, true);
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

// ===========================================================================
// PLACEMENTS OWN THEIR LATERAL, AND A DERIVED POSITION IS NOT A MEASURED ONE.
//
// Two defects that used to share one line of sampleAt():
//
//  (1) `lateralM = s.lateralM` ran unconditionally, so a car the board had already
//      PLACED -- grid slot, pit-lane start, parked queue -- kept its placed station but
//      was painted with the telemetry lateral of a car that is not where the telemetry
//      says it is. That is the diagonal line of cars beside the circuit at Monaco, and
//      it also deleted the two-column grid stagger on every pack.
//  (2) `positionFrame` was carried into RawLapEntry and read by nobody, so a frame-B lap
//      (no x/y at all; station = rescaled wheel-speed integral, lateral = 0) was tagged
//      OBSERVED and handed measured gaps.
//
// Not the same bug and not the same fix, so they are tested separately.
// ===========================================================================

/** Does a car placed at `stationM` with `lateralM` fit inside the road the track model
 * MEASURED there? Asserted as a property rather than by recomputing the production
 * formula, so these tests cannot drift into agreeing with a wrong implementation. */
function roadFits(track: TrackModel, stationM: number, lateralM: number): boolean {
  return Math.abs(lateralM) + CAR_RENDER_WIDTH_M / 2 <= halfWidthAt(track, stationM) + 1e-6;
}

describe("RULE placements own their lateral", () => {
  const track = raceTrack();
  const { manifest, bin } = buildRaceManifest();
  const timeline = new ReplayTimeline(manifest, track, bin);
  const BOGUS_M = BOGUS_LATERAL_CM / 100;

  it("the fixture really does feed a bogus telemetry lateral to every car", () => {
    // Positive control: a car that is genuinely being MEASURED still reports the
    // telemetry lateral, bogus or not. The fix must not blanket-zero laterals.
    const running = [...timeline.sampleAt(300).values()].filter((c) => c.status === "track");
    expect(running.length).toBeGreaterThan(4);
    for (const c of running) expect(c.lateralM).toBeCloseTo(BOGUS_M, 2);
  });

  it("never paints a placed car with the telemetry lateral", () => {
    let placed = 0;
    for (let t = 0; t <= timeline.duration; t += 1) {
      for (const car of timeline.sampleAt(t, false).values()) {
        if (car.positionProvenance === "OBSERVED") continue;
        placed++;
        expect(Math.abs(car.lateralM), `${car.driver} @${t} (${car.status})`)
          .toBeLessThan(BOGUS_M);
      }
    }
    expect(placed).toBeGreaterThan(500);
  });

  it("keeps the grid in two staggered columns that fit the road", () => {
    // the standing start: every car bar the pit-lane starter is on a RULE grid slot
    const grid = [...timeline.sampleAt(2).values()].filter((c) => c.status === "grid");
    expect(grid.length).toBe(RACE_DRIVERS.length - 1); // D06 is held in the pit lane
    const bySlot = grid.sort((a, b) => b.stationM - a.stationM); // pole is nearest the line
    for (const car of bySlot) {
      expect(roadFits(track, car.stationM, car.lateralM), `${car.driver} off the road`).toBe(true);
      expect(Math.abs(car.lateralM)).toBeGreaterThan(0);
    }
    // Two columns, and which column a car is in follows its SLOT, not its rank in this
    // list -- D06 vacated one slot to start from the lane, so consecutive survivors are
    // not necessarily consecutive slots.
    const slotOf = (c: CarState) =>
      Math.round((TRACK_LENGTH - c.stationM) / track.grid.pitchMetres) - 1;
    for (const car of bySlot) {
      expect(Math.sign(car.lateralM), `${car.driver} slot ${slotOf(car)}`)
        .toBe(slotOf(car) % 2 === 0 ? -1 : 1);
    }
    // WIDER than the flat 1.8 m constant this replaced: the columns are separated by the
    // road's own measured half-width (6 m here), not by 3.6 m.
    const left = bySlot.find((c) => c.lateralM < 0)!;
    const right = bySlot.find((c) => c.lateralM > 0)!;
    const separation = right.lateralM - left.lateralM;
    expect(separation).toBeGreaterThan(2 * 1.8);
    expect(separation).toBeCloseTo(halfWidthAt(track, left.stationM), 6);
  });

  it("parks finished and retired cars in the queue, not beside it", () => {
    const end = [...timeline.sampleAt(timeline.duration, false).values()]
      .filter((c) => c.status === "finished" || c.status === "retired");
    // every car bar the one whose own last lap ends exactly at `duration` (it has not
    // crossed yet at that instant, so it is still being measured)
    expect(end.length).toBeGreaterThanOrEqual(RACE_DRIVERS.length - 1);
    for (const car of end) {
      expect(car.positionProvenance).toBe("RULE");
      expect(roadFits(track, car.stationM, car.lateralM), `${car.driver} parked off the road`)
        .toBe(true);
    }
  });

  it("clamps the columns onto a road too narrow to hold them", () => {
    // A 1.6 m half-width leaves only 0.6 m of room for a 2.0 m car, which is LESS than
    // the half-of-half-width the columns want. The clamp, not the fraction, must win.
    const narrow: TrackModel = { ...raceTrack(), halfWidth: new Float32Array([1.6]) };
    const tl = new ReplayTimeline(manifest, narrow, bin);
    const grid = [...tl.sampleAt(2).values()].filter((c) => c.status === "grid");
    expect(grid.length).toBeGreaterThan(4);
    for (const car of grid) {
      expect(roadFits(narrow, car.stationM, car.lateralM)).toBe(true);
      expect(Math.abs(car.lateralM)).toBeCloseTo(1.6 - CAR_RENDER_WIDTH_M / 2, 6);
    }
  });
});

// ---------------------------------------------------------------------------
// Position frame B
// ---------------------------------------------------------------------------

/** The same race with two drivers' laps written in position-frame B -- the state
 * monaco-grand-prix/Race is in for 91.2 % of its laps. */
function frameBManifest(base: RawSessionManifest, derived: string[]): RawSessionManifest {
  return {
    ...base,
    drivers: base.drivers.map((d) => (derived.includes(d.driver)
      ? { ...d, laps: d.laps.map((l) => ({ ...l, positionFrame: "B" as const })) }
      : d)),
  };
}

describe("position-frame B is DERIVED, not OBSERVED", () => {
  const track = raceTrack();
  const { manifest, bin } = buildRaceManifest();
  const DERIVED_DRIVERS = ["D02", "D05"];
  const timeline = new ReplayTimeline(frameBManifest(manifest, DERIVED_DRIVERS), track, bin);
  const control = new ReplayTimeline(manifest, track, bin);
  const instants: number[] = [];
  for (let t = 0; t <= timeline.duration; t += 2) instants.push(t);

  it("labels a frame-B position DERIVED and a frame-A position OBSERVED", () => {
    let derived = 0, observed = 0;
    for (const t of instants) {
      for (const car of timeline.sampleAt(t, false).values()) {
        if (car.positionProvenance === "RULE") { expect(car.positionFrame).toBeNull(); continue; }
        if (DERIVED_DRIVERS.includes(car.driver)) {
          expect(car.positionFrame, `${car.driver} @${t}`).toBe("B");
          expect(car.positionProvenance).toBe("DERIVED");
          derived++;
        } else {
          expect(car.positionFrame).toBe("A");
          expect(car.positionProvenance).toBe("OBSERVED");
          observed++;
        }
      }
    }
    expect(derived).toBeGreaterThan(500);
    expect(observed).toBeGreaterThan(2000);
  });

  it("never publishes a gap or an interval computed from a rescaled wheel-speed integral", () => {
    let suppressed = 0;
    for (const t of instants) {
      for (const car of timeline.sampleAt(t).values()) {
        if (car.positionFrame !== "B") continue;
        expect(car.gapToLeaderS, `${car.driver} @${t}`).toBeNull();
        expect(car.intervalS).toBeNull();
        expect(car.orderSource).not.toBe("MEASURED");
        suppressed++;
      }
    }
    // ...and the same cars DID carry published gaps before the frame tag was read.
    let before = 0;
    for (const t of instants) {
      for (const car of control.sampleAt(t).values()) {
        if (DERIVED_DRIVERS.includes(car.driver) && car.gapToLeaderS !== null) before++;
      }
    }
    console.log(`frame-B gap suppression: ${before} gaps were published from a derived`
      + ` position, ${suppressed} car-instants now report none`);
    expect(before).toBeGreaterThan(400);
  });

  it("still gives every car a distinct position and a monotone gap column", () => {
    let worstDrop = 0, checked = 0;
    for (const t of instants) {
      const rows = [...timeline.sampleAt(t).values()].sort((a, b) => a.position - b.position);
      expect(rows.map((r) => r.position)).toEqual(rows.map((_, i) => i + 1));
      let previous = -Infinity;
      for (const r of rows) {
        if (r.gapToLeaderS === null) continue;
        checked++;
        worstDrop = Math.max(worstDrop, previous - r.gapToLeaderS);
        previous = r.gapToLeaderS;
      }
    }
    expect(checked).toBeGreaterThan(1000);
    expect(worstDrop).toBeLessThanOrEqual(1e-6);
  });
});

// ===========================================================================
// The same two rules, measured against every real 2026 pack on disk.
// ===========================================================================

interface PackFiles {
  slug: string; session: string; track: TrackModel; manifest: RawSessionManifest; bin: ArrayBuffer;
}

function loadAllPacks(): PackFiles[] {
  const dir = join(process.cwd(), "public", "sim");
  if (!existsSync(join(dir, "index.json"))) return [];
  const latest = (JSON.parse(readFileSync(join(dir, "index.json"), "utf-8")) as { latest: string }).latest;
  if (!existsSync(join(dir, latest))) return [];
  const index = JSON.parse(readFileSync(join(dir, latest), "utf-8")) as {
    tracks: Record<string, string>;
    sessions: Record<string, Record<string, { manifest: string; bin: string }>>;
  };
  const out: PackFiles[] = [];
  for (const slug of Object.keys(index.sessions)) {
    for (const session of Object.keys(index.sessions[slug])) {
      const s = index.sessions[slug][session];
      const files = [index.tracks[slug], s.manifest, s.bin];
      if (!files.every((f) => f && existsSync(join(dir, f)))) continue;
      try {
        const raw = readFileSync(join(dir, s.bin));
        out.push({
          slug,
          session,
          // the shipped Chinese track model contains a literal NaN and is not valid JSON;
          // that is a pack-build defect owned elsewhere, so it is skipped, not failed.
          track: parseTrackModel(JSON.parse(readFileSync(join(dir, files[0]), "utf-8")) as RawTrackModel),
          manifest: JSON.parse(readFileSync(join(dir, s.manifest), "utf-8")) as RawSessionManifest,
          bin: raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength) as ArrayBuffer,
        });
      } catch { /* unreadable artifact */ }
    }
  }
  return out;
}

const allPacks = loadAllPacks();

describe.skipIf(allPacks.length === 0)("placement and frame on every real 2026 pack", () => {
  it("draws every placed car on the road, at the start and at the flag", () => {
    const lines: string[] = [];
    let checked = 0;
    for (const pack of allPacks) {
      const tl = new ReplayTimeline(pack.manifest, pack.track, pack.bin);
      const gridLat: number[] = [];
      let worstOff = -Infinity;
      let worstWho = "-";
      const check = (car: CarState) => {
        // A pit-lane START is placed on the pit road, which is legitimately tens of
        // metres off the racing line, so it is not expected to fit the track ribbon.
        if (car.positionProvenance === "OBSERVED" || car.status === "pit") return;
        checked++;
        // A surfaced circuit's road is NOT halfWidthAt -- that RULE scale describes the
        // ribbon, which is not what is drawn once a real model is on screen. This must
        // use the same measured, per-side room the placement itself was fitted to,
        // never a symmetric width: british-grand-prix's own asphalt runs 1.5-3.5 m one
        // side of the grid and 17.0-17.5 m the other.
        const surfaced = Boolean(pack.track.surface);
        const sign = car.lateralM >= 0 ? 1 : -1;
        const half = surfaced
          ? (measuredLateralRoomM(pack.track, car.stationM, sign) ?? 0)
          : halfWidthAt(pack.track, car.stationM);
        const over = Math.abs(car.lateralM) + CAR_RENDER_WIDTH_M / 2 - half;
        if (over > worstOff) { worstOff = over; worstWho = `${car.driver}/${car.status}`; }
        expect(over, `${pack.slug}/${pack.session} ${car.driver} (${car.status}) lateral`
          + ` ${car.lateralM.toFixed(2)} m on a ${half.toFixed(2)} m ${surfaced ? "measured" : "half-width"} road`)
          .toBeLessThanOrEqual(1e-6);
      };
      for (let t = 0; t <= 90; t += 0.25) {
        for (const car of tl.sampleAt(t, false).values()) {
          check(car);
          if (car.status === "grid") gridLat.push(Math.abs(car.lateralM));
        }
      }
      const parked: number[] = [];
      for (let t = Math.max(0, tl.duration - 240); t <= tl.duration; t += 2) {
        for (const car of tl.sampleAt(t, false).values()) {
          check(car);
          if (car.status === "finished" || car.status === "retired") parked.push(Math.abs(car.lateralM));
        }
      }
      gridLat.sort((a, b) => a - b);
      parked.sort((a, b) => a - b);
      const med = gridLat.length ? gridLat[Math.floor(gridLat.length / 2)] : NaN;
      lines.push(`${pack.slug}/${pack.session}: grid |lat| median ${med.toFixed(2)} m over`
        + ` ${gridLat.length} car-instants; parked |lat| max`
        + ` ${(parked[parked.length - 1] ?? NaN).toFixed(2)} m;`
        + ` worst overhang ${worstOff.toFixed(3)} m (${worstWho})`);
    }
    console.log(`placed cars on the road (${checked} placed car-instants checked):\n  `
      + lines.join("\n  "));
    expect(checked).toBeGreaterThan(10000);
  }, 300_000);

  it("never publishes a gap for a frame-B car, and says so on every pack", () => {
    const lines: string[] = [];
    let bTotal = 0, aTotal = 0;
    for (const pack of allPacks) {
      const tl = new ReplayTimeline(pack.manifest, pack.track, pack.bin);
      let a = 0, b = 0, rule = 0, aGaps = 0;
      for (let t = 0; t <= tl.duration; t += 5) {
        for (const car of tl.sampleAt(t).values()) {
          if (car.positionFrame === null) {
            rule++;
            expect(car.positionProvenance).toBe("RULE");
            continue;
          }
          if (car.positionFrame === "B") {
            b++;
            expect(car.positionProvenance, `${pack.slug} ${car.driver}`).toBe("DERIVED");
            expect(car.gapToLeaderS).toBeNull();
            expect(car.intervalS).toBeNull();
            expect(car.orderSource).not.toBe("MEASURED");
          } else {
            a++;
            expect(car.positionProvenance).toBe("OBSERVED");
            if (car.gapToLeaderS !== null) aGaps++;
          }
        }
      }
      aTotal += a;
      bTotal += b;
      if (b > 0) {
        lines.push(`${pack.slug}/${pack.session}: frame A ${a} car-instants (${aGaps} gaps),`
          + ` frame B ${b} (0 gaps), placed ${rule}`);
      }
    }
    console.log(`frame-B car-instants across ${allPacks.length} packs: A ${aTotal}, B ${bTotal}\n  `
      + (lines.join("\n  ") || "(no frame-B laps on disk)"));
    expect(aTotal).toBeGreaterThan(100000);
  }, 300_000);
});

// ===========================================================================
// A RULE placement may only claim road the artifact has MEASURED
// ===========================================================================

/** The same synthetic race track, but drawn from a real circuit model: it carries a
 * baked `surface`, so the ribbon's RULE half-width is no longer what the viewer sees.
 * Heights are flat and valid everywhere -- nothing here depends on them; what matters
 * is that the bake, like every real bake, measures ONE point per station (the ring) and
 * therefore measures no lateral extent at all. */
function surfacedTrack(): TrackModel {
  const base = raceTrack();
  const n = base.x.length;
  return {
    ...base,
    surface: {
      dsMetres: base.lengthMetres / n,
      source: "test.glb", sourceSha256: null, profile: "test",
      transform: { scale: 1, yawDeg: 0, mirror: -1, txM: 0, tzM: 0, tyM: 0 },
      zM: new Float32Array(n), slope: new Float32Array(n), camber: new Float32Array(n),
      // No measured road edge either side, on purpose: this fixture pins the fallback
      // for a surfaced circuit whose road-edge walk has nothing to report, which the
      // single-file collapse below depends on.
      edgeLeftM: new Float32Array(n).fill(NaN), edgeRightM: new Float32Array(n).fill(NaN),
      valid: new Uint8Array(n).fill(1),
      residual: { stdM: 0.05, maxM: 0.17 },
      coverage: 1, roadCoverage: 0.9985,
      assetUrl: "/sim/glb/test.glb", assetSha256: null,
      provenance: "DERIVED", provenanceNote: "DERIVED (test fixture)",
    },
  };
}

describe("a placement claims only the road that has been measured", () => {
  const { manifest, bin } = buildRaceManifest();

  it("keeps the two columns on a circuit drawn as the procedural ribbon", () => {
    // Positive control. The ribbon IS halfWidthAt, so a car placed inside it is on the
    // road it is drawn on, and nothing about this path changes.
    const track = raceTrack();
    const grid = [...new ReplayTimeline(manifest, track, bin).sampleAt(2).values()]
      .filter((c) => c.status === "grid");
    expect(grid.length).toBeGreaterThan(4);
    for (const car of grid) {
      expect(Math.abs(car.lateralM), car.driver).toBeCloseTo(3, 6); // half of 6 m
      expect(roadFits(track, car.stationM, car.lateralM)).toBe(true);
    }
  });

  it("collapses the columns once the drawn road is a real model", () => {
    // THE FIX. surface_block() bakes one probe per station -- the ring itself -- so the
    // artifact measures no lateral road whatsoever, and a RULE stagger has nothing to
    // stand on. Measured on the real british-grand-prix model before this rule existed:
    // the placement claimed +/-3.17..3.52 m while the GLB asphalt at the front of the
    // grid ends 1.75-2.00 m to the LEFT of the ring, putting ten of the 21 cars
    // 1.2-1.8 m out on the grass.
    const track = surfacedTrack();
    const tl = new ReplayTimeline(manifest, track, bin);
    let placed = 0;
    for (let t = 0; t <= tl.duration; t += 0.5) {
      for (const car of tl.sampleAt(t, false).values()) {
        if (car.positionProvenance !== "RULE" || car.status === "pit") continue;
        placed++;
        expect(car.lateralM, `${car.driver} @${t} (${car.status})`).toBe(0);
      }
    }
    expect(placed).toBeGreaterThan(500);
  });

  it("still refuses to paint a placed car with the telemetry lateral", () => {
    // The collapse must not be achieved by letting telemetry win: a placed car's
    // lateral is still OWNED by the placement, it is just now zero.
    const tl = new ReplayTimeline(manifest, surfacedTrack(), bin);
    const grid = [...tl.sampleAt(2, false).values()].filter((c) => c.status === "grid");
    expect(grid.length).toBeGreaterThan(4);
    for (const car of grid) expect(car.lateralM).toBe(0);
  });
});

// ===========================================================================
// The side of a grid slot belongs to the producer
// ===========================================================================

/** `raceTrack()` plus the grid slots an artifact publishes, pole on the LEFT (+1) --
 * which is the opposite of the parity rule this file used to apply. */
function slottedTrack(signs: number[]): TrackModel & { gridSlots: GridSlot[] } {
  return {
    ...raceTrack(),
    gridSlots: signs.map((lateralSign, i) => ({
      position: i + 1,
      driver: RACE_DRIVERS[i],
      station: TRACK_LENGTH - (i + 1) * 8,
      lateralSign: lateralSign as -1 | 1,
    })),
  };
}

describe("grid slot side comes from the producer, not from slot parity", () => {
  const { manifest, bin } = buildRaceManifest();

  it("puts each car on the side the artifact published", () => {
    // Every shipped artifact publishes pole on +1 and alternates from there; the parity
    // rule here was `slot % 2 === 0 ? -1 : +1`, i.e. the mirror image, on all 232
    // published slots across the 13 shipped models.
    const signs = RACE_DRIVERS.map((_, i) => (i % 2 === 0 ? 1 : -1));
    const track = slottedTrack(signs);
    const grid = [...new ReplayTimeline(manifest, track, bin).sampleAt(2).values()]
      .filter((c) => c.status === "grid");
    expect(grid.length).toBeGreaterThan(4);
    for (const car of grid) {
      const slot = RACE_DRIVERS.indexOf(car.driver);
      expect(Math.sign(car.lateralM), `${car.driver} slot ${slot}`).toBe(signs[slot]);
    }
  });

  it("falls back to alternating sides only for a driver the producer did not place", () => {
    // Monaco publishes 2 slots for a 22-car field and China 0; the rest of the field
    // still has to go somewhere, and a made-up side is the honest fallback -- but only
    // where there is no published one to honour.
    const track = slottedTrack([1, -1]); // D01, D02 only
    const grid = [...new ReplayTimeline(manifest, track, bin).sampleAt(2).values()]
      .filter((c) => c.status === "grid");
    const side = (d: string) => Math.sign(grid.find((c) => c.driver === d)!.lateralM);
    expect(side("D01")).toBe(1);
    expect(side("D02")).toBe(-1);
    expect(side("D03")).toBe(-1);  // slot 2, parity
    expect(side("D04")).toBe(1);   // slot 3, parity
  });
});

describe.skipIf(allPacks.length === 0)("placements against the real 2026 packs", () => {
  it("stands every placed car on the side the producer published, ribbon circuits", () => {
    const lines: string[] = [];
    let checked = 0, agreed = 0;
    for (const pack of allPacks) {
      if (pack.track.surface) continue;      // no side is claimed at all there
      const slots = new Map(gridSlotsOf(pack.track)
        .filter((s) => s.driver !== null).map((s) => [s.driver as string, s.lateralSign]));
      if (slots.size === 0) continue;
      const tl = new ReplayTimeline(pack.manifest, pack.track, pack.bin);
      let n = 0, ok = 0;
      for (let t = 0; t <= 4; t += 0.05) {
        for (const car of tl.sampleAt(t, false).values()) {
          const want = slots.get(car.driver);
          if (car.status !== "grid" || want === undefined) continue;
          n++;
          if (Math.sign(car.lateralM) === want) ok++;
        }
      }
      checked += n;
      agreed += ok;
      lines.push(`${pack.slug}/${pack.session}: ${ok}/${n} grid car-instants on the published side`);
      expect(ok, `${pack.slug}/${pack.session}`).toBe(n);
    }
    console.log(`published grid side honoured (${agreed}/${checked}):\n  ${lines.join("\n  ")}`);
    expect(checked).toBeGreaterThan(500);
  }, 300_000);

  it("claims only the road that is actually measured on the circuit drawn from a real model", () => {
    // british-grand-prix is the only shipped circuit with a baked surface. This must
    // hold REGARDLESS of whether the shipped artifact carries a road-edge walk yet:
    //   - no walk (every artifact before it landed): measuredLateralRoomM is null on
    //     both sides everywhere, so columnLateral collapses to single file -- 0, exactly
    //     the "claims nothing" answer this test used to hard-code.
    //   - a walk that measured this side at this station: the car must fit strictly
    //     inside that measured room, on the correct side of the ring's own normal
    //     (sign>0 => left, i.e. lateralM>=0; sign<0 => right, lateralM<=0).
    // What must NEVER happen, in either state: the ribbon's RULE half-width used as if
    // it were a measurement of this road. Measured before the walk existed: the old
    // +/-3.17..3.52 m placement stood ten of 21 cars on the grass, because the asphalt
    // at the grid ends +1.75..+2.00 m from the ring on that side. The feed agrees -- the
    // largest lateral in 1,108,923 real position samples anywhere in that 168 m stretch
    // is +2.79 m.
    const surfaced = allPacks.filter((p) => p.track.surface);
    expect(surfaced.length).toBeGreaterThan(0);
    for (const pack of surfaced) {
      const tl = new ReplayTimeline(pack.manifest, pack.track, pack.bin);
      let placed = 0, nonZero = 0;
      const check = (car: CarState, where: string) => {
        if (car.positionProvenance !== "RULE" || car.status === "pit") return;
        placed++;
        if (car.lateralM !== 0) nonZero++;
        const sign = car.lateralM >= 0 ? 1 : -1;
        const room = measuredLateralRoomM(pack.track, car.stationM, sign);
        const label = `${pack.slug}/${pack.session} ${car.driver} (${where})`;
        if (room === null) {
          expect(car.lateralM, label).toBe(0);
        } else {
          expect(Math.abs(car.lateralM) + CAR_RENDER_WIDTH_M / 2, label)
            .toBeLessThanOrEqual(room + 1e-6);
        }
      };
      for (let t = 0; t <= 60; t += 0.25) {
        for (const car of tl.sampleAt(t, false).values()) check(car, car.status);
      }
      for (let t = Math.max(0, tl.duration - 120); t <= tl.duration; t += 1) {
        for (const car of tl.sampleAt(t, false).values()) check(car, "parked");
      }
      console.log(`${pack.slug}/${pack.session}: ${placed} placed car-instants, `
        + `${nonZero} at a measured nonzero lateral`);
      expect(placed).toBeGreaterThan(100);
    }
  }, 300_000);
});
