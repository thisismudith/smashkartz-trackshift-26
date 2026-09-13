import { describe, expect, it } from "vitest";
import { describeShape, findDrsViolations, isDrsClean, shapes } from "./guards";

describe("the 2026 DRS request boundary", () => {
  it("rejects a raw `drs` key anywhere in the payload", () => {
    const v = findDrsViolations({ state: { era: { drs: 0 } } });
    expect(v).toHaveLength(1);
    expect(v[0].path).toBe("state.era.drs");
  });

  it("rejects any historical_drs_* key", () => {
    const v = findDrsViolations({ historical_drs_open: 1, historical_drs_eligible: 0 });
    expect(v.map((x) => x.path).sort()).toEqual(["historical_drs_eligible", "historical_drs_open"]);
  });

  it("rejects the PROXY_HISTORICAL_DRS sentinel as a VALUE, not just as a key", () => {
    // The backend rejects this form separately; a UI that only checked keys would send it.
    const v = findDrsViolations({ state: { overtake_state_source: "PROXY_HISTORICAL_DRS" } });
    expect(v).toHaveLength(1);
    expect(v[0].path).toBe("state.overtake_state_source");
  });

  it("finds violations inside arrays", () => {
    const v = findDrsViolations({ segments: [{ ok: 1 }, { drs: 1 }] });
    expect(v[0].path).toBe("segments[1].drs");
  });

  it("says an all-zero DRS channel is unavailable, not closed", () => {
    // The wording matters: this is the sentence that stops someone reading 0 as "closed".
    const [v] = findDrsViolations({ drs: 0 });
    expect(v.reason).toContain("unavailable, not closed");
  });

  it("passes a clean 2026 payload", () => {
    expect(
      isDrsClean({
        state: { ref: { year: 2026 }, overtake_state: { state: "NOT_ARMED" } },
        our_policy: "beam_dp",
      }),
    ).toBe(true);
  });

  it("does not hang on a cyclic payload", () => {
    const a: Record<string, unknown> = { name: "a" };
    a.self = a;
    expect(findDrsViolations(a)).toEqual([]);
  });

  it("treats a key merely containing 'drs' as fine", () => {
    // `drs_zone_count` is not one of the three forbidden forms; over-blocking would be its
    // own bug, refusing legitimate requests.
    expect(isDrsClean({ drs_zone_count: 4 })).toBe(true);
  });
});

describe("response shape narrowing", () => {
  it("accepts the timeline the service actually sends (`segments`)", () => {
    expect(shapes.timeline.check({ battle_id: "b", segments: [] })).toBe(true);
  });

  it("rejects the timeline API.md documents (`steps`), rather than rendering nothing", () => {
    // API.md 5.5 says `steps`; the service says `segments`. If that ever flips, this must
    // become a visible error, not an empty panel.
    expect(shapes.timeline.check({ battle_id: "b", steps: [] })).toBe(false);
  });

  it("rejects a battles body whose `battles` is not an array", () => {
    expect(shapes.battles.check({ battles: null })).toBe(false);
    expect(shapes.battles.check({ battles: [] })).toBe(true);
  });

  it("rejects a simulate body with no summary", () => {
    expect(shapes.simulate.check({ episodes: [] })).toBe(false);
  });

  it("rejects HTML served in place of JSON", () => {
    expect(shapes.meta.check("<!doctype html>")).toBe(false);
  });
});

describe("describeShape", () => {
  it("summarises an object by its keys, for the error state", () => {
    expect(describeShape({ a: 1, b: 2 })).toBe("object{a, b}");
  });

  it("truncates a wide object", () => {
    const wide = Object.fromEntries("abcdefghij".split("").map((k) => [k, 1]));
    expect(describeShape(wide)).toBe("object{a, b, c, d, e, f, +4 more}");
  });

  it("distinguishes null from an object", () => {
    expect(describeShape(null)).toBe("null");
    expect(describeShape([1, 2, 3])).toBe("array(3)");
  });
});
