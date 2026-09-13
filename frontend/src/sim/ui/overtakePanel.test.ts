import { describe, expect, it } from "vitest";
import type { DashboardRow, DashboardSnapshot } from "../contract/types";
import {
  battlesToShow, closestBattle, driverBattles, intervalSeconds, provenanceMeaning,
  readQuantity, readServiceCode, readableError, refusalHeadline, rulesEventKey,
} from "./OvertakePanel";

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

  it("pulls the message out of the UNWRAPPED envelope the service actually raises", () => {
    // The live service answers `{detail: {code, message}}`; only the older shape
    // nests under `detail.error`. Reading just the nested one is why a 422
    // reached the panel as its own raw JSON.
    const body = JSON.stringify({
      detail: { code: "ILLEGAL_STATE", message: "no time gap in the request. Send state.gap" },
    });
    expect(readableError(body)).toBe("no time gap in the request");
  });

  it("falls back to the raw text when it is not the service's envelope", () => {
    expect(readableError("connection refused")).toBe("connection refused");
  });

  it("has something to say when there is no error text at all", () => {
    expect(readableError(undefined)).toContain("unreachable");
  });
});

describe("readServiceCode", () => {
  it("reads code and message from both envelope nestings", () => {
    expect(readServiceCode(JSON.stringify({
      detail: { code: "NOT_MODEL_ELIGIBLE", message: "the pass model is fitted on green-flag rows" },
    }))).toEqual({
      code: "NOT_MODEL_ELIGIBLE", message: "the pass model is fitted on green-flag rows",
    });
    expect(readServiceCode(JSON.stringify({
      detail: { error: { code: "UNKNOWN_EVENT", message: "rule file not found" } },
    }))).toEqual({ code: "UNKNOWN_EVENT", message: "rule file not found" });
  });

  it("is null for anything that is not a coded body, including bad JSON", () => {
    expect(readServiceCode(undefined)).toBeNull();
    expect(readServiceCode("connection refused")).toBeNull();
    expect(readServiceCode(JSON.stringify({ detail: "plain string detail" }))).toBeNull();
    expect(readServiceCode(JSON.stringify({ detail: {} }))).toBeNull();
  });
});

describe("refusalHeadline", () => {
  it("names the two 422s that are answers rather than failures", () => {
    // INTEGRATION.md section 4: both render as an explanatory state. The sim
    // meets NOT_MODEL_ELIGIBLE on every Safety Car, VSC and pit sequence.
    expect(refusalHeadline("NOT_MODEL_ELIGIBLE")).toBe("the model is not defined here");
    expect(refusalHeadline("CHECKPOINT_VIOLATION")).toContain("DETECTION checkpoint cannot have");
  });

  it("returns null for a genuine failure, so it keeps the failure path", () => {
    for (const code of ["ILLEGAL_STATE", "UNKNOWN_EVENT", "", "500"]) {
      expect(refusalHeadline(code), code).toBeNull();
    }
  });
});

describe("readQuantity", () => {
  it("reads the Quantity's value AND the provenance tag beside it", () => {
    expect(readQuantity({ value: 0.6, unit: "s", provenance: "SIMULATED" }))
      .toEqual({ value: 0.6, provenance: "SIMULATED" });
  });

  it("keeps the tag when the value is absent — absence is still attributable", () => {
    expect(readQuantity({ value: null, provenance: "DERIVED", reason: "not published" }))
      .toEqual({ value: null, provenance: "DERIVED" });
  });

  it("reads the bare-float shape without inventing a tag for it", () => {
    expect(readQuantity(0.42)).toEqual({ value: 0.42, provenance: null });
  });

  it("rejects a non-finite number rather than rendering NaN%", () => {
    expect(readQuantity(Number.NaN)).toEqual({ value: null, provenance: null });
    expect(readQuantity({ value: Number.POSITIVE_INFINITY, provenance: "SIMULATED" }))
      .toEqual({ value: null, provenance: "SIMULATED" });
    expect(readQuantity(undefined)).toEqual({ value: null, provenance: null });
  });
});

describe("the bodies the live service actually returns", () => {
  // Captured verbatim from the running dev service (POST /api/v1/rules/eligibility
  // and POST /api/v1/pass/predict). Pinned as bytes because every bug this block
  // covers was a shape mismatch that typechecked perfectly.
  const ELIGIBILITY = '{"p_eligible":{"value":1.0,"provenance":"SIMULATED"},'
    + '"eligibility_margin_s":{"value":0.6,"unit":"s","provenance":"SIMULATED"},'
    + '"margin_s":{"value":0.6,"unit":"s","provenance":"SIMULATED"},'
    + '"gap_s":{"value":0.4,"unit":"s","provenance":"DERIVED"}}';
  const NOT_ELIGIBLE = '{"detail":{"code":"NOT_MODEL_ELIGIBLE","message":'
    + '"normal_race_model_eligible is false. The pass model is fitted on green-flag '
    + 'normal-race rows only (CP-13), so under a Safety Car, VSC or pit sequence its '
    + 'output would be an extrapolation, not a prediction."}}';
  const VIOLATION = '{"detail":{"code":"CHECKPOINT_VIOLATION","message":'
    + '"[\'gap_at_activation_s\'] cannot be known at DETECTION."}}';

  it("carries the SIMULATED tag off both eligibility numbers", () => {
    const body = JSON.parse(ELIGIBILITY) as Record<string, unknown>;
    expect(readQuantity(body.eligibility_margin_s ?? body.margin_s))
      .toEqual({ value: 0.6, provenance: "SIMULATED" });
    expect(readQuantity(body.p_eligible)).toEqual({ value: 1, provenance: "SIMULATED" });
  });

  it("routes both 422s to the explanatory state, never to the failure path", () => {
    for (const body of [NOT_ELIGIBLE, VIOLATION]) {
      const coded = readServiceCode(body);
      expect(coded).not.toBeNull();
      expect(refusalHeadline(coded!.code)).not.toBeNull();
      expect(coded!.message).not.toBe("");
    }
  });
});

describe("provenanceMeaning", () => {
  it("says what SIMULATED means for a number on this panel", () => {
    // P(eligible) is the arming rule run forward over a projected gap. A bare
    // "97%" reads as a measurement of the race, which API.md section 2 forbids.
    expect(provenanceMeaning(["SIMULATED"]))
      .toBe("simulated — produced by a model of the race, not measured in it");
  });

  it("collapses a repeated tag but keeps two different ones separable", () => {
    expect(provenanceMeaning(["SIMULATED", "SIMULATED"]).split(" · ")).toHaveLength(1);
    const both = provenanceMeaning(["SIMULATED", "DERIVED"]);
    expect(both).toContain("simulated —");
    expect(both).toContain("derived —");
  });

  it("names an untagged or unknown tag instead of explaining one it does not define", () => {
    expect(provenanceMeaning([null])).toContain("without a provenance");
    expect(provenanceMeaning(["DERIVED_TELEMETRY"]))
      .toBe("derived_telemetry — vocabulary this panel does not define");
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
