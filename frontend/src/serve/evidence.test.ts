import { describe, expect, it } from "vitest";
import { assessEvidence, gradeMeaning } from "./evidence";
import type { ValidationResponse } from "./types";

/** The era block the running synthetic service actually returns. */
const SYNTHETIC: ValidationResponse = {
  release_ready: false,
  era_evaluation: {
    status: "MEASURED",
    real_historical_evidence: false,
    british_gp_used_for_training_or_calibration: false,
    historical_drs_is_not_2026_overtake: true,
    held_out_2026_n: 384,
    synthetic_fixture: "trackshift-synthetic-closure-v1",
  },
};

describe("assessEvidence", () => {
  it("grades the current 2026-only synthetic run INTERIM, not FULL", () => {
    const a = assessEvidence(SYNTHETIC);
    expect(a.grade).toBe("INTERIM");
    expect(a.fromBackend).toBe(false);
  });

  it("refuses to claim calibration on an INTERIM run", () => {
    expect(assessEvidence(SYNTHETIC).mayClaimCalibration).toBe(false);
  });

  it("reports historical validation as incomplete while real_historical_evidence is false", () => {
    expect(assessEvidence(SYNTHETIC).historicalValidationComplete).toBe(false);
  });

  it("explains the grade using the flag that drove it", () => {
    const a = assessEvidence(SYNTHETIC);
    expect(a.reasons.join(" ")).toContain("real_historical_evidence: false");
  });

  it("never derives FULL, however healthy the report looks", () => {
    // A UI cannot know the CP-14 year table ran. Only grade_evidence() can say that.
    const optimistic: ValidationResponse = {
      era_evaluation: {
        status: "MEASURED",
        real_historical_evidence: true,
        british_gp_used_for_training_or_calibration: false,
        held_out_2026_n: 100000,
      },
    };
    expect(assessEvidence(optimistic).grade).toBe("INTERIM");
  });

  it("defers to an explicit backend grade when one arrives", () => {
    const graded: ValidationResponse = {
      era_evaluation: {
        ...SYNTHETIC.era_evaluation,
        // Shape of folds.grade_evidence()'s return value.
        evidence_grade: { grade: "FULL", reasons: ["ran the documented year table"] },
      } as ValidationResponse["era_evaluation"],
    };
    const a = assessEvidence(graded);
    expect(a.grade).toBe("FULL");
    expect(a.fromBackend).toBe(true);
    expect(a.mayClaimCalibration).toBe(true);
    expect(a.reasons).toContain("ran the documented year table");
  });

  it("passes a REDUCED grade straight through and still blocks a calibration claim", () => {
    const graded: ValidationResponse = {
      era_evaluation: { evidence_grade: "REDUCED" } as ValidationResponse["era_evaluation"],
    };
    const a = assessEvidence(graded);
    expect(a.grade).toBe("REDUCED");
    expect(a.mayClaimCalibration).toBe(false);
  });

  it("surfaces the British GP hold-out flag so the panel can assert it", () => {
    expect(assessEvidence(SYNTHETIC).britishGpUsedForTraining).toBe(false);
  });

  it("returns UNKNOWN, not a guess, when there is no validation response", () => {
    const a = assessEvidence(null);
    expect(a.grade).toBe("UNKNOWN");
    expect(a.mayClaimCalibration).toBe(false);
  });
});

describe("gradeMeaning", () => {
  it("says only FULL may be reported as CP-14 passing", () => {
    expect(gradeMeaning("FULL")).toContain("CP-14 passing");
  });

  it("says an INTERIM run is not evidence the checkpoint passed", () => {
    expect(gradeMeaning("INTERIM")).toContain("not that the checkpoint passed");
  });
});
