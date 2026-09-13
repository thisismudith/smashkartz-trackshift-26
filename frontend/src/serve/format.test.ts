import { describe, expect, it } from "vitest";
import { formatProbability, formatQuantity, formatRuleValue, rankDistribution } from "./format";

describe("formatQuantity", () => {
  it("renders a missing value as Unavailable and keeps the backend's reason", () => {
    const d = formatQuantity({ value: null, provenance: "OBSERVED", unit: "s", reason: "field not published for 2023" });
    expect(d.kind).toBe("unavailable");
    if (d.kind !== "unavailable") throw new Error("unreachable");
    expect(d.text).toBe("Unavailable");
    expect(d.reason).toBe("field not published for 2023");
  });

  it("NEVER renders a missing value as zero", () => {
    const d = formatQuantity({ value: null, provenance: "DERIVED", unit: "s" });
    expect(d.text).not.toContain("0");
  });

  it("does render a real zero as zero", () => {
    // The inverse error: a measured 0.0 turned into "Unavailable" is equally wrong.
    const d = formatQuantity({ value: 0, provenance: "OBSERVED", unit: "kW" });
    expect(d.kind).toBe("value");
    expect(d.text).toBe("0.00 kW");
  });

  it("treats NaN as missing rather than printing NaN", () => {
    const d = formatQuantity({ value: Number.NaN, provenance: "SIMULATED" });
    expect(d.kind).toBe("unavailable");
  });

  it("handles an absent field entirely", () => {
    const d = formatQuantity(undefined);
    expect(d.kind).toBe("unavailable");
    if (d.kind !== "unavailable") throw new Error("unreachable");
    expect(d.reason).toContain("not present");
  });

  it("keeps the unit from the payload and the provenance with it", () => {
    const d = formatQuantity({ value: 2.4, provenance: "SIMULATED", unit: "MJ" });
    expect(d.text).toBe("2.40 MJ");
    expect(d.provenance).toBe("SIMULATED");
  });

  it("switches to exponential for the very small numbers the DP produces", () => {
    // Rival-state probabilities come back as 6.7e-92; "0.00" would be a lie.
    const d = formatQuantity({ value: 6.699e-92, provenance: "INFERRED" });
    expect(d.text).toBe("6.70e-92");
  });
});

describe("formatRuleValue", () => {
  it("uses an UNRESOLVED rule's source text as the reason it is unavailable", () => {
    const d = formatRuleValue({
      value: null,
      value_source: "UNRESOLVED",
      source: "UNRESOLVED: the public regulation does not state its event value here",
    });
    expect(d.kind).toBe("unavailable");
    if (d.kind !== "unavailable") throw new Error("unreachable");
    expect(d.reason).toContain("does not state its event value");
    expect(d.provenance).toBe("UNRESOLVED");
  });

  it("carries value_source through as the provenance of a present rule value", () => {
    const d = formatRuleValue({ value: 5789.3, value_source: "DERIVED_TELEMETRY" }, { unit: "m", digits: 1 });
    expect(d.text).toBe("5789.3 m");
    expect(d.provenance).toBe("DERIVED_TELEMETRY");
  });
});

describe("formatProbability", () => {
  it("keeps a genuine zero probability visible", () => {
    const d = formatProbability(0);
    expect(d.kind).toBe("value");
    expect(d.text).toBe("0.0%");
  });

  it("reports a null probability as unavailable", () => {
    expect(formatProbability(null).kind).toBe("unavailable");
  });
});

describe("rankDistribution", () => {
  it("orders states by probability, descending", () => {
    const r = rankDistribution({ BALANCED: 0.31, CONSERVING: 0.12, DEPLOYING: 0.49, DERATING: 0.08 });
    expect(r.map((x) => x.state)).toEqual(["DEPLOYING", "BALANCED", "CONSERVING", "DERATING"]);
  });

  it("keeps zero-probability states in the distribution", () => {
    // Dropping them would misrepresent the support of the distribution.
    const r = rankDistribution({ A: 1, B: 0 });
    expect(r).toHaveLength(2);
  });

  it("returns empty for a missing distribution instead of throwing", () => {
    expect(rankDistribution(undefined)).toEqual([]);
  });
});
