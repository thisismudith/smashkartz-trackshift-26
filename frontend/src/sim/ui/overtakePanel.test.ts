import { describe, expect, it } from "vitest";
import type { DashboardRow, DashboardSnapshot } from "../contract/types";
import { closestBattle, intervalSeconds, readableError, rulesEventKey } from "./OvertakePanel";

function row(driver: string, interval: string, status: DashboardRow["status"] = "track"): DashboardRow {
  return {
    position: 1, driver, team: null, gapToLeader: "", interval,
    compound: null, tyreLife: null, status, lastLapS: null, energy: null,
    speedKph: 250, gear: 7, throttlePct: 100, brake: false, lapProgress: 0.5,
  };
}

function snapshot(rows: DashboardRow[]): DashboardSnapshot {
  return { sessionTime: 0, leaderboard: rows, activeNeutralisation: null, recentEvents: [] };
}

describe("intervalSeconds", () => {
  it("reads a numeric interval, with or without the leading plus", () => {
    expect(intervalSeconds("+1.234")).toBeCloseTo(1.234, 6);
    expect(intervalSeconds("0.5")).toBeCloseTo(0.5, 6);
  });

  it("treats every non-numeric display string as an ABSENCE, never as zero", () => {
    // Reading any of these as 0 would invent the tightest battle on track and
    // then score it, which is the failure this function exists to prevent.
    for (const text of ["LEADER", "PIT", "—", "-", "", "LAP 1", "+1 LAP"]) {
      expect(intervalSeconds(text), text).toBeNull();
    }
    expect(intervalSeconds(null)).toBeNull();
    expect(intervalSeconds(undefined)).toBeNull();
  });

  it("rejects a non-positive gap", () => {
    expect(intervalSeconds("0")).toBeNull();
    expect(intervalSeconds("+0.0")).toBeNull();
  });
});

describe("closestBattle", () => {
  it("finds the smallest interval and names both cars", () => {
    const battle = closestBattle(snapshot([
      row("AAA", "LEADER"), row("BBB", "+3.0"), row("CCC", "+0.4"), row("DDD", "+2.0"),
    ]));
    expect(battle).toEqual({ attacker: "CCC", defender: "BBB", gapS: 0.4 });
  });

  it("ignores a car in the pit lane, which is not racing the car ahead", () => {
    const battle = closestBattle(snapshot([
      row("AAA", "LEADER"), row("BBB", "+0.2", "pit"), row("CCC", "+1.5"),
    ]));
    // BBB's 0.2 s is discarded; CCC's 1.5 s is also discarded because the car
    // directly ahead of it is the one in the pits.
    expect(battle).toBeNull();
  });

  it("returns null for a field with no measured intervals", () => {
    expect(closestBattle(snapshot([row("AAA", "LEADER"), row("BBB", "—")]))).toBeNull();
  });

  it("returns null for an absent or single-car board", () => {
    expect(closestBattle(null)).toBeNull();
    expect(closestBattle(snapshot([row("AAA", "LEADER")]))).toBeNull();
  });
});

describe("rulesEventKey", () => {
  it("converts the sim's hyphenated slug to the rules service's underscored key", () => {
    // The two halves of the project spell the same circuit differently and the
    // service 404s on the wrong one; this is the seam that reconciles them.
    expect(rulesEventKey("british-grand-prix")).toBe("british_grand_prix");
    expect(rulesEventKey("monaco-grand-prix")).toBe("monaco_grand_prix");
  });

  it("leaves an already-underscored key alone", () => {
    expect(rulesEventKey("british_grand_prix")).toBe("british_grand_prix");
  });
});

describe("readableError", () => {
  it("pulls the message out of the service's JSON error envelope", () => {
    const body = JSON.stringify({
      detail: { error: { code: "UNKNOWN_EVENT", message: "no rule config for 'x'. Known: [...]" } },
    });
    expect(readableError(body)).toBe("no rule config for 'x'");
  });

  it("falls back to the raw text when it is not the service's envelope", () => {
    expect(readableError("connection refused")).toBe("connection refused");
  });

  it("has something to say when there is no error text at all", () => {
    expect(readableError(undefined)).toContain("unreachable");
  });
});
