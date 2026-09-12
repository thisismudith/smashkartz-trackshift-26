import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { CarState, Provenance, RaceEvent, TrackModel } from "../contract/types";
import { parseTrackModel } from "../data/manifest";
import type { RawSessionManifest, RawTrackModel } from "../data/manifest";
import {
  buildDashboardSnapshot, describePositionIntegrity, describePositionProvenance,
  meterState, positionIntegrityUnknown,
} from "./dashboard";
import { ReplayTimeline } from "./timeline";

function makeCar(overrides: Partial<CarState> = {}): CarState {
  return {
    driver: "AAA", team: "Team A", stationM: 0, lateralM: 0, elevationM: 0,
    headingRad: 0, speedKph: 0, gear: 1, throttlePct: 0, brake: false,
    tyreCompound: "MEDIUM", tyreLife: 1, lapsDone: 0, lapProgress: 0, position: 1,
    gapToLeaderS: null, lapsDownFromLeader: 0, intervalS: null, inPit: false,
    status: "track", provenance: "OBSERVED", positionProvenance: "OBSERVED", energy: null, ...overrides,
  };
}

describe("buildDashboardSnapshot", () => {
  it("sorts the leaderboard by position and labels the leader", () => {
    const states = new Map([
      ["BBB", makeCar({ driver: "BBB", position: 2, gapToLeaderS: 3.4 })],
      ["AAA", makeCar({ driver: "AAA", position: 1 })],
    ]);
    const snap = buildDashboardSnapshot(states, 100, [], []);
    expect(snap.leaderboard.map((r) => r.driver)).toEqual(["AAA", "BBB"]);
    expect(snap.leaderboard[0].gapToLeader).toBe("LEADER");
    expect(snap.leaderboard[1].gapToLeader).toBe("+3.4");
  });

  it("shows lapped cars as +N LAP(S) rather than a numeric gap", () => {
    const states = new Map([
      ["AAA", makeCar({ position: 1 })],
      ["BBB", makeCar({ driver: "BBB", position: 2, lapsDownFromLeader: 2 })],
    ]);
    const snap = buildDashboardSnapshot(states, 100, [], []);
    expect(snap.leaderboard[1].gapToLeader).toBe("+2 LAPS");
  });

  it("shows PIT instead of a gap while a car is between pit entry and exit", () => {
    const states = new Map([
      ["AAA", makeCar({ position: 1 })],
      ["BBB", makeCar({ driver: "BBB", position: 2, inPit: true, gapToLeaderS: 12 })],
    ]);
    const snap = buildDashboardSnapshot(states, 100, [], []);
    expect(snap.leaderboard[1].gapToLeader).toBe("PIT");
    expect(snap.leaderboard[1].interval).toBe("PIT");
  });

  it("finds the neutralisation interval active at the current session time", () => {
    const states = new Map([["AAA", makeCar()]]);
    const snap = buildDashboardSnapshot(states, 55, [], [{ kind: "VSC", start: 50, end: 80 }]);
    expect(snap.activeNeutralisation).toEqual({ kind: "VSC", start: 50, end: 80 });
    const before = buildDashboardSnapshot(states, 40, [], [{ kind: "VSC", start: 50, end: 80 }]);
    expect(before.activeNeutralisation).toBeNull();
  });

  it("carries the order source through so the UI can label a provisional row", () => {
    const states = new Map([
      ["AAA", { ...makeCar({ position: 1 }), orderSource: "OFFICIAL" as const }],
      ["BBB", { ...makeCar({ driver: "BBB", position: 2 }), orderSource: "MEASURED" as const }],
    ]);
    const snap = buildDashboardSnapshot(states, 100, [], []);
    expect(snap.leaderboard.map((r) => r.orderSource)).toEqual(["OFFICIAL", "MEASURED"]);
  });

  it("labels rows honestly when the source does not report an order source", () => {
    // The generated New Race timeline emits a plain CarState. A measured position means
    // the row was placed by measurement; a placed one by rule. Neither is claimed to be
    // an official classification the feed never supplied.
    const states = new Map([
      ["AAA", makeCar({ position: 1 })],
      ["BBB", makeCar({ driver: "BBB", position: 2, positionProvenance: "RULE", status: "grid" })],
    ]);
    const snap = buildDashboardSnapshot(states, 100, [], []);
    expect(snap.leaderboard.map((r) => r.orderSource)).toEqual(["MEASURED", "RULE"]);
  });

  it("returns only recent events, newest first", () => {
    const events: RaceEvent[] = [
      { sessionTime: 10, kind: "vsc", message: "old", drivers: [], provenance: "OBSERVED" },
      { sessionTime: 95, kind: "sc", message: "recent1", drivers: [], provenance: "OBSERVED" },
      { sessionTime: 98, kind: "sc", message: "recent2", drivers: [], provenance: "OBSERVED" },
    ];
    const snap = buildDashboardSnapshot(new Map(), 100, events, [], 30);
    expect(snap.recentEvents.map((e) => e.message)).toEqual(["recent2", "recent1"]);
  });
});

describe("position provenance on the timing columns", () => {
  it("carries the position provenance onto every row", () => {
    const states = new Map([
      ["AAA", makeCar({ position: 1 })],
      ["BBB", makeCar({ driver: "BBB", position: 2, positionProvenance: "DERIVED" })],
    ]);
    const snap = buildDashboardSnapshot(states, 100, [], []);
    expect(snap.leaderboard.map((r) => r.positionProvenance)).toEqual(["OBSERVED", "DERIVED"]);
  });

  it("never prints a numeric gap for a RULE placement, even when one is supplied", () => {
    // A grid slot, a pit-lane start and a parked queue are placements. The distance
    // between two placements is not a time, so no seconds may be printed from one.
    const states = new Map([
      ["AAA", makeCar({ position: 1 })],
      ["BBB", makeCar({
        driver: "BBB", position: 2, positionProvenance: "RULE",
        gapToLeaderS: 12.3, intervalS: 4.5, status: "finished",
      })],
    ]);
    const row = buildDashboardSnapshot(states, 100, [], []).leaderboard[1];
    expect(row.gapToLeader).toBe("PLACED");
    expect(row.interval).toBe("PLACED");
    expect(row.gapProvenance).toBeNull();
  });

  it("does not grow a lap deficit for a car that has stopped", () => {
    // The leader keeps circulating after a retirement, so lapsDownFromLeader keeps
    // climbing: measured on the built Monaco race pack, parked cars reach "+78 LAPS" at
    // the flag of a 78-lap race. The car is placed by rule and is not losing laps to
    // anyone; it is out.
    const states = new Map([
      ["AAA", makeCar({ position: 1 })],
      ["BBB", makeCar({
        driver: "BBB", position: 2, positionProvenance: "RULE", status: "retired",
        lapsDownFromLeader: 78,
      })],
    ]);
    const row = buildDashboardSnapshot(states, 100, [], []).leaderboard[1];
    expect(row.gapToLeader).toBe("OUT");
    expect(row.interval).toBe("OUT");
    expect(row.gapProvenance).toBeNull();
  });

  it("never prints a numeric gap for a DERIVED (position frame B) lap", () => {
    // Monaco: 1324 of 1452 race laps have no usable x/y and are placed by stretching the
    // wheel-speed distance channel onto the ring. A gap measured off that is not timing.
    const states = new Map([
      ["AAA", makeCar({ position: 1 })],
      ["BBB", makeCar({
        driver: "BBB", position: 2, positionProvenance: "DERIVED",
        gapToLeaderS: 8.1, intervalS: 8.1,
      })],
    ]);
    const row = buildDashboardSnapshot(states, 100, [], []).leaderboard[1];
    expect(row.gapToLeader).toBe("DERIVED");
    expect(row.interval).toBe("DERIVED");
    expect(row.gapProvenance).toBeNull();
  });

  it("says GRID rather than a bare dash while the field is on its slots", () => {
    // "—" reads as "no data yet". The truth is stronger: the car is on a RULE slot and
    // there is no gap to measure.
    const states = new Map([
      ["AAA", makeCar({ position: 1, status: "grid", positionProvenance: "RULE" })],
      ["BBB", makeCar({ driver: "BBB", position: 2, status: "grid", positionProvenance: "RULE" })],
    ]);
    const snap = buildDashboardSnapshot(states, 0, [], []);
    expect(snap.leaderboard.map((r) => r.gapToLeader)).toEqual(["GRID", "GRID"]);
    expect(snap.leaderboard.map((r) => r.interval)).toEqual(["GRID", "GRID"]);
  });

  it("keeps the New Race engine's own gaps, which are not measurements but are consistent", () => {
    // The generated timeline positions AND times every car from the same engine and the
    // whole timeline is flagged SIMULATED. Suppressing the number there would hide a
    // self-consistent quantity rather than expose a dishonest one.
    const states = new Map([
      ["AAA", makeCar({ position: 1, provenance: "SIMULATED", positionProvenance: "SIMULATED" })],
      ["BBB", makeCar({
        driver: "BBB", position: 2, provenance: "SIMULATED", positionProvenance: "SIMULATED",
        gapToLeaderS: 2.25, intervalS: 2.25,
      })],
    ]);
    const row = buildDashboardSnapshot(states, 100, [], []).leaderboard[1];
    expect(row.gapToLeader).toBe("+2.3");
    expect(row.gapProvenance).toBe("SIMULATED");
  });

  it("tags a measured gap OBSERVED and an absent one null", () => {
    const states = new Map([
      ["AAA", makeCar({ position: 1 })],
      ["BBB", makeCar({ driver: "BBB", position: 2, gapToLeaderS: 1.5, intervalS: 1.5 })],
      ["CCC", makeCar({ driver: "CCC", position: 3 })],
    ]);
    const snap = buildDashboardSnapshot(states, 100, [], []);
    expect(snap.leaderboard.map((r) => r.gapProvenance)).toEqual([null, "OBSERVED", null]);
    expect(snap.leaderboard[2].gapToLeader).toBe("—");
  });

  it("describes every provenance the contract defines, and only OBSERVED as measured", () => {
    const all: Provenance[] = ["OBSERVED", "DERIVED", "INFERRED", "SIMULATED", "RULE", "DEFAULT"];
    for (const p of all) {
      const d = describePositionProvenance(p);
      expect(d.label.length).toBeGreaterThan(0);
      expect(d.note.length).toBeGreaterThan(0);
    }
    expect(describePositionProvenance("OBSERVED").note).toContain("measured");
    for (const p of all.filter((q) => q !== "OBSERVED" && q !== "SIMULATED")) {
      expect(describePositionProvenance(p).note.toLowerCase())
        .toContain("no gap is computed from it");
    }
  });
});

describe("energy meters against unverified regulation placeholders", () => {
  // scripts/simdata/rules.py ships deploy_budget = 8.5 MJ with verified:false and
  // citation "unsourced placeholder". A reconstruction that runs past it is the model
  // failing to close, not a competitor breaking a rule, and must not be painted in the
  // same red as a breach.
  const DEPLOY_MJ = 8.5;

  it("does not report an overshoot of an UNVERIFIED limit as a violation", () => {
    const m = meterState(12.43, DEPLOY_MJ, { verified: false });
    expect(m.level).toBe("unclosed");
    expect(m.level).not.toBe("over");
    expect(m.modelDoesNotClose).toBe(true);
    expect(m.excess).toBeCloseTo(3.93, 6);
    expect(m.fillPct).toBe(100);
  });

  it("still reports an overshoot of a VERIFIED limit as a violation", () => {
    const m = meterState(12.43, DEPLOY_MJ, { verified: true });
    expect(m.level).toBe("over");
    expect(m.modelDoesNotClose).toBe(false);
    expect(m.excess).toBeCloseTo(3.93, 6);
  });

  it("keeps the four in-limit bands unchanged", () => {
    expect(meterState(1.0, DEPLOY_MJ, { verified: false }).level).toBe("low");
    expect(meterState(4.0, DEPLOY_MJ, { verified: false }).level).toBe("mid");
    expect(meterState(7.0, DEPLOY_MJ, { verified: false }).level).toBe("high");
    expect(meterState(8.5, DEPLOY_MJ, { verified: false }).level).toBe("high");
    expect(meterState(2.0, DEPLOY_MJ, { verified: false }).fillPct).toBeCloseTo(23.529, 3);
  });

  it("omits the grading entirely when there is no limit to grade against", () => {
    for (const limit of [null, 0, -1, Number.NaN]) {
      const m = meterState(700, limit, {});
      expect(m.level).toBeUndefined();
      expect(m.fraction).toBeNull();
      expect(m.excess).toBeNull();
    }
  });

  it("does not paint an over-capacity store as comfortable when inverted", () => {
    // The inverted scale exists so a nearly EMPTY store reads as the alarming end. The
    // old mapping sent "over" to "low", so a store holding more than its own capacity --
    // the clearest possible sign the reconstruction does not close -- came out blue.
    const empty = meterState(0.2, 4.0, { verified: false, invert: true });
    expect(empty.level).toBe("over");
    const overfull = meterState(5.0, 4.0, { verified: false, invert: true });
    expect(overfull.level).toBe("unclosed");
    expect(overfull.excess).toBeCloseTo(1.0, 6);
    const overfullVerified = meterState(5.0, 4.0, { verified: true, invert: true });
    expect(overfullVerified.level).toBe("over");
  });
});

describe("per-session position integrity", () => {
  it("reports an unreported count as unknown, never as zero", () => {
    const lines = describePositionIntegrity(undefined);
    expect(lines.map((l) => l.value)).toEqual(["unknown", "unknown"]);
    expect(lines.every((l) => !l.alert)).toBe(true);
    expect(positionIntegrityUnknown(undefined)).toBe(true);
    expect(positionIntegrityUnknown(null)).toBe(true);
  });

  it("distinguishes a reported zero from an unreported count", () => {
    const lines = describePositionIntegrity({
      positionsWithdrawn: 0, positionSamples: 614258, derivedFrameLaps: 0, totalLaps: 1452,
    });
    expect(lines.map((l) => l.value)).toEqual(["none", "none"]);
    expect(positionIntegrityUnknown({
      positionsWithdrawn: 0, positionSamples: 1, derivedFrameLaps: 0, totalLaps: 1,
    })).toBe(false);
  });

  it("states a real withdrawal as a count and a share of the session", () => {
    // Chinese Qualifying: rawio.SentinelIndex masks 40.26% of samples there.
    const lines = describePositionIntegrity({
      positionsWithdrawn: 402_600, positionSamples: 1_000_000,
      derivedFrameLaps: 1324, totalLaps: 1452,
    });
    expect(lines[0].value).toBe("402 600 samples (40.3%)");
    expect(lines[0].alert).toBe(true);
    expect(lines[1].value).toBe("1 324 laps (91.2%)");
    expect(lines[1].alert).toBe(true);
  });

  it("reports a half-known session honestly rather than filling the hole", () => {
    const lines = describePositionIntegrity({
      positionsWithdrawn: null, positionSamples: null, derivedFrameLaps: 1324, totalLaps: 1452,
    });
    expect(lines[0].value).toBe("unknown");
    expect(lines[1].value).toBe("1 324 laps (91.2%)");
  });
});

// ===========================================================================
// Against a real 2026 race pack. The artifacts under frontend/public/sim are
// git-ignored, so this suite skips when they are not present rather than failing.
// ===========================================================================

interface RealPack {
  slug: string; session: string; track: TrackModel; manifest: RawSessionManifest; bin: ArrayBuffer;
}

/** Packs the index points at but which do not parse. Reported rather than hidden. */
const unreadable: string[] = [];

/** Every built pack on disk, or [] when the artifacts are not present. */
function loadPacks(): RealPack[] {
  const dir = join(process.cwd(), "public", "sim");
  const indexPath = join(dir, "index.json");
  if (!existsSync(indexPath)) return [];
  const latest = (JSON.parse(readFileSync(indexPath, "utf-8")) as { latest: string }).latest;
  if (!existsSync(join(dir, latest))) return [];
  const index = JSON.parse(readFileSync(join(dir, latest), "utf-8")) as {
    tracks: Record<string, string>;
    sessions: Record<string, Record<string, { manifest: string; bin: string }>>;
  };
  const out: RealPack[] = [];
  for (const slug of Object.keys(index.sessions)) {
    const trackFile = index.tracks[slug];
    if (!trackFile) continue;
    for (const [session, files] of Object.entries(index.sessions[slug])) {
      const paths = [trackFile, files.manifest, files.bin].map((f) => join(dir, f));
      if (!paths.every(existsSync)) continue;
      try {
        const raw = readFileSync(paths[2]);
        out.push({
          slug, session,
          track: parseTrackModel(JSON.parse(readFileSync(paths[0], "utf-8")) as RawTrackModel),
          manifest: JSON.parse(readFileSync(paths[1], "utf-8")) as RawSessionManifest,
          bin: raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength) as ArrayBuffer,
        });
      } catch (err) {
        // A shipped artifact that will not parse is reported, not silently skipped:
        // chinese-grand-prix's track model currently contains a literal NaN, which
        // JSON.parse rejects, and the browser fails the same way.
        unreadable.push(`${slug}/${session}: ${(err as Error).message.slice(0, 80)}`);
      }
    }
  }
  return out;
}

const packs = loadPacks();
const NUMERIC = /^\+\d+(\.\d+)?$/;
const LAP_DEFICIT = /^\+(\d+) LAPS?$/;

describe.skipIf(packs.length === 0)("built packs never print measured timing for an unmeasured position", () => {
  it("prints no +s.s gap, and no bare dash, for any car whose position is not measured", () => {
    if (unreadable.length) {
      console.log(`[packs] ${unreadable.length} shipped artifact(s) unreadable: ${unreadable.join("; ")}`);
    }
    let totalRows = 0, totalUnmeasured = 0, totalNumeric = 0, totalDash = 0;
    for (const pack of packs) {
      const timeline = new ReplayTimeline(pack.manifest, pack.track, pack.bin);
      let rows = 0, unmeasured = 0, numeric = 0, dash = 0, maxDeficit = 0;
      const printed = new Map<string, number>();
      for (let t = 0; t <= timeline.duration; t += 30) {
        for (const row of buildDashboardSnapshot(timeline.sampleAt(t), t, [], []).leaderboard) {
          rows++;
          const deficit = LAP_DEFICIT.exec(row.gapToLeader);
          if (deficit) maxDeficit = Math.max(maxDeficit, Number(deficit[1]));
          if (row.positionProvenance === "OBSERVED") continue;
          unmeasured++;
          if (NUMERIC.test(row.gapToLeader) || NUMERIC.test(row.interval)) numeric++;
          // The leader's own interval is legitimately "—": there is nobody ahead of it.
          // Every other dash on an unmeasured row would be hiding the reason.
          if (row.gapToLeader === "—" || (row.position > 1 && row.interval === "—")) dash++;
          const token = NUMERIC.test(row.gapToLeader) ? "+s.s" : row.gapToLeader;
          printed.set(token, (printed.get(token) ?? 0) + 1);
          expect(row.gapProvenance).toBeNull();
        }
      }
      totalRows += rows; totalUnmeasured += unmeasured; totalNumeric += numeric; totalDash += dash;
      console.log(
        `[${pack.slug}/${pack.session}] rows=${rows} unmeasured=${unmeasured}`
        + ` (${(unmeasured / rows * 100).toFixed(1)}%) numeric-on-unmeasured=${numeric}`
        + ` dash-on-unmeasured=${dash} maxLapDeficitPrinted=${maxDeficit}`
        + ` tokens=${JSON.stringify([...printed].sort((a, b) => b[1] - a[1]).slice(0, 6))}`,
      );
      // No pack may print a number that implies measurement for a position that was not
      // measured, and none may hide the reason behind a bare dash either.
      expect(numeric).toBe(0);
      expect(dash).toBe(0);
      // A parked car cannot go on losing laps to a leader still circulating. Measured on
      // HEAD: Monaco printed +78 LAPS, Hungary +56, Australia +48.
      expect(maxDeficit).toBeLessThan(20);
    }
    expect(totalRows).toBeGreaterThan(10000);
    expect(totalUnmeasured).toBeGreaterThan(0);
    expect(totalNumeric).toBe(0);
    expect(totalDash).toBe(0);
  });
});

// Monaco is the frame-B case: 1324 of its 1452 race laps carry no usable x/y.
const monaco = packs.find((p) => p.slug === "monaco-grand-prix" && p.session === "Race") ?? null;

describe.skipIf(!monaco)("Monaco still shows the timing it really measured", () => {
  const pack = monaco!;
  const timeline = new ReplayTimeline(pack.manifest, pack.track, pack.bin);
  const instants: number[] = [];
  for (let t = 0; t <= timeline.duration; t += 30) instants.push(t);

  it("keeps measured gaps on the 128 laps that do have an x/y trace", () => {
    // The guard must not simply blank the timing screen.
    let observedNumeric = 0;
    for (const t of instants) {
      for (const row of buildDashboardSnapshot(timeline.sampleAt(t), t, [], []).leaderboard) {
        if (row.positionProvenance === "OBSERVED" && NUMERIC.test(row.gapToLeader)) {
          observedNumeric++;
          expect(row.gapProvenance).toBe("OBSERVED");
        }
      }
    }
    console.log(`[monaco] measured +s.s gaps still printed=${observedNumeric}`);
    expect(observedNumeric).toBeGreaterThan(0);
  });
});
