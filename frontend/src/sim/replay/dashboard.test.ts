import { describe, expect, it } from "vitest";
import type { CarState, RaceEvent } from "../contract/types";
import { buildDashboardSnapshot } from "./dashboard";

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
