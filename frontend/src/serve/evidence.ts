/**
 * What this run's numbers are worth — FULL / INTERIM / REDUCED.
 *
 * `src/trackshift/pass_model/folds.py::grade_evidence` is the authority. It grades a
 * benchmark run against CP-14's documented split (train 2022-2024, validate 2025, test
 * 2026 excluding the British GP, grouped by `battle_id`) and returns FULL only for that
 * exact design. The comment in that file is the rule this module exists to honour:
 *
 *   "What must not happen is an INTERIM run being written up as a pass."
 *
 * `GET /validation` does not currently carry the grade — `grade_evidence` lives in the
 * pass-model package and the synthetic service never calls it. So the UI has two honest
 * options and exactly one dishonest one. The dishonest one is to print the metrics with no
 * grade at all, which reads as a pass. What this does instead:
 *
 *   - If the backend ever sends a grade, that grade wins. Nothing here second-guesses it.
 *   - Otherwise the grade is derived from the flags the era report DOES carry, and it is
 *     labelled as derived-by-the-UI, with the signals it used shown alongside. A reader can
 *     therefore check the reasoning rather than trust it.
 *   - FULL is never derived. A UI cannot know that the CP-14 year table ran; only the
 *     backend can say so. Absent an explicit grade the ceiling here is INTERIM, which is
 *     the right way round: under-claiming costs a caveat, over-claiming costs the result.
 */
import type { EraEvaluation, ValidationResponse } from "./types";
import { isRecord } from "./guards";

export type EvidenceGrade = "FULL" | "INTERIM" | "REDUCED" | "UNKNOWN";

export interface EvidenceAssessment {
  grade: EvidenceGrade;
  /** True when the backend stated the grade; false when this module derived it. */
  fromBackend: boolean;
  /** Why this grade, in the reader's language. Always non-empty. */
  reasons: string[];
  /** Whether a headline accuracy/calibration number may be shown as a result. */
  mayClaimCalibration: boolean;
  /** True while no real 2022-25 evidence is joined. */
  historicalValidationComplete: boolean;
  /** Must stay false. British GP is held out; it is demo/replay only. */
  britishGpUsedForTraining: boolean | null;
}

const GRADES: EvidenceGrade[] = ["FULL", "INTERIM", "REDUCED"];

/** Finds an explicit grade on the era report, wherever the backend chooses to put it. */
function explicitGrade(era: EraEvaluation | undefined, root: ValidationResponse): EvidenceGrade | null {
  const candidates: unknown[] = [
    (era as Record<string, unknown> | undefined)?.grade,
    (era as Record<string, unknown> | undefined)?.evidence_grade,
    (root as Record<string, unknown>)?.evidence_grade,
  ];
  for (const c of candidates) {
    if (typeof c === "string" && GRADES.includes(c.toUpperCase() as EvidenceGrade)) {
      return c.toUpperCase() as EvidenceGrade;
    }
    // folds.grade_evidence returns a dict: {"grade": "...", "reasons": [...]}.
    if (isRecord(c) && typeof c.grade === "string" && GRADES.includes(c.grade.toUpperCase() as EvidenceGrade)) {
      return c.grade.toUpperCase() as EvidenceGrade;
    }
  }
  return null;
}

function explicitReasons(era: EraEvaluation | undefined): string[] {
  const node = (era as Record<string, unknown> | undefined)?.evidence_grade
    ?? (era as Record<string, unknown> | undefined)?.grade;
  if (isRecord(node) && Array.isArray(node.reasons)) {
    return node.reasons.filter((r): r is string => typeof r === "string");
  }
  return [];
}

export function assessEvidence(validation: ValidationResponse | null | undefined): EvidenceAssessment {
  if (!validation) {
    return {
      grade: "UNKNOWN",
      fromBackend: false,
      reasons: ["No /validation response has been read, so no grade can be stated."],
      mayClaimCalibration: false,
      historicalValidationComplete: false,
      britishGpUsedForTraining: null,
    };
  }

  const era = validation.era_evaluation;
  const stated = explicitGrade(era, validation);
  const britishGp = era?.british_gp_used_for_training_or_calibration ?? null;
  const realHistorical = era?.real_historical_evidence === true;

  if (stated) {
    const reasons = explicitReasons(era);
    return {
      grade: stated,
      fromBackend: true,
      reasons: reasons.length
        ? reasons
        : [`The backend graded this run ${stated}.`],
      mayClaimCalibration: stated === "FULL",
      historicalValidationComplete: realHistorical,
      britishGpUsedForTraining: britishGp,
    };
  }

  const reasons: string[] = [];

  // The strongest available signal, and the one that decides the ceiling: without a real
  // 2022-25 lake joined, this cannot be the CP-14 year table whatever else is true.
  if (era?.real_historical_evidence === false) {
    reasons.push(
      "The era report sets real_historical_evidence: false — no real 2022-25 seasons are joined, " +
        "so this is a 2026-only run and does not measure generalisation across regulation eras.",
    );
  } else if (!era) {
    reasons.push("The /validation response carries no era_evaluation block, so the split design is unknown.");
  }

  if (era?.synthetic_fixture) {
    reasons.push(
      `Numbers come from the synthetic fixture "${era.synthetic_fixture}", which is development ` +
        "evidence that the pipeline runs — not a measurement of real race outcomes.",
    );
  }

  if (typeof era?.held_out_2026_n === "number") {
    reasons.push(`Held-out 2026 rows: n = ${era.held_out_2026_n}.`);
  }

  reasons.push(
    "No FULL grade is asserted here: only the backend's grade_evidence() can confirm the CP-14 " +
      "year table ran, so this UI will not derive FULL.",
  );

  return {
    grade: "INTERIM",
    fromBackend: false,
    reasons,
    mayClaimCalibration: false,
    historicalValidationComplete: realHistorical,
    britishGpUsedForTraining: britishGp,
  };
}

/** One-line description shown beside the grade chip. */
export function gradeMeaning(grade: EvidenceGrade): string {
  switch (grade) {
    case "FULL":
      return "Ran on CP-14's documented split (train 2022-2024 / validate 2025 / test 2026 excluding the British GP, grouped by battle_id). Only this may be reported as CP-14 passing.";
    case "INTERIM":
      return "Leakage-safe and the numbers are real, but not CP-14's year table — it does not measure generalisation across regulation eras. Evidence that the pipeline works, not that the checkpoint passed.";
    case "REDUCED":
      return "The split unit is coarser than battle_id, so the no-battle-across-folds guarantee is weaker than CP-14 requires. These numbers must not be compared with a FULL run.";
    default:
      return "No grade has been established for this run.";
  }
}
