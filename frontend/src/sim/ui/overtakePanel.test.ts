import { describe, expect, it } from "vitest";
import type { DashboardRow, DashboardSnapshot } from "../contract/types";
import { battlesToShow, closestBattle, driverBattles, intervalSeconds, readableError, rulesEventKey } from "./OvertakePanel";

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
    expect(battle).toEqual({ attacker: "CCC", defender: "BBB", gapS: 0.4, relation: "closest" });
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


describe("driverBattles", () => {
  const board = snapshot([
    row("AAA", "LEADER"), row("BBB", "+1.2"), row("CCC", "+0.4"), row("DDD", "+3.0"),
  ]);

  it("returns the car the focused driver is chasing AND the one chasing it", () => {
    const battles = driverBattles(board, "CCC");
    expect(battles).toEqual([
      { attacker: "CCC", defender: "BBB", gapS: 0.4, relation: "ahead" },
      { attacker: "DDD", defender: "CCC", gapS: 3.0, relation: "behind" },
    ]);
  });

  it("gives the leader only the car chasing it", () => {
    expect(driverBattles(board, "AAA")).toEqual([
      { attacker: "BBB", defender: "AAA", gapS: 1.2, relation: "behind" },
    ]);
  });

  it("gives the last car only the one it is chasing", () => {
    expect(driverBattles(board, "DDD")).toEqual([
      { attacker: "DDD", defender: "CCC", gapS: 3.0, relation: "ahead" },
    ]);
  });

  it("is empty for a driver not on the board, or none selected", () => {
    expect(driverBattles(board, "ZZZ")).toEqual([]);
    expect(driverBattles(board, null)).toEqual([]);
  });
});

describe("battlesToShow", () => {
  const board = snapshot([
    row("AAA", "LEADER"), row("BBB", "+1.2"), row("CCC", "+0.4"), row("DDD", "+3.0"),
  ]);

  it("follows the focused driver rather than the tightest gap in the field", () => {
    // The tightest gap is CCC->BBB at 0.4 s, but AAA is focused, so the panel
    // must answer about AAA -- the whole point of the fix.
    const shown = battlesToShow(board, "AAA");
    expect(shown).toHaveLength(1);
    expect(shown[0]).toMatchObject({ attacker: "BBB", defender: "AAA", relation: "behind" });
  });

  it("falls back to the closest battle when nothing is focused, and says so", () => {
    const shown = battlesToShow(board, null);
    expect(shown).toHaveLength(1);
    expect(shown[0].relation).toBe("closest");
    expect(shown[0]).toMatchObject({ attacker: "CCC", defender: "BBB" });
  });

  it("is empty when a focused driver is in clear air and the field has no gaps", () => {
    const strung = snapshot([row("AAA", "LEADER"), row("BBB", "—")]);
    expect(battlesToShow(strung, "AAA")).toEqual([]);
  });
});
