/**
 * Validation and evidence status: GET /validation.
 *
 * The grade chip is the headline because it is the thing most likely to be dropped when
 * these numbers are copied into a slide. `folds.py` puts it plainly: an INTERIM run is
 * evidence that the pipeline works, not evidence that the checkpoint passed — so the grade
 * travels with the numbers, and here it travels above them.
 *
 * Three assertions are shown as assertions, with their current value, rather than as prose:
 *
 *   British GP held out          must be true; it is demo/replay only
 *   Historical 2022-25 complete  currently false; never claim otherwise
 *   Historical DRS != Overtake   must be true
 *
 * Writing "British GP is held out" as a sentence would keep saying so after the flag
 * flipped. Rendering the flag means the page stops claiming it the moment it stops being
 * true, which is the only version of the claim worth making.
 */
"use client";

import { ProvenanceBadge } from "@/components/provenance/Provenance";
import { assessEvidence, gradeMeaning, type EvidenceAssessment } from "@/serve/evidence";
import type { Result } from "@/serve/guards";
import type { ValidationResponse } from "@/serve/types";
import { Callout, ErrorState, Loading, Panel } from "./panels";
import s from "./integration.module.css";

function Assertion({
  label,
  value,
  expected,
  whenTrue,
  whenFalse,
  whenUnknown,
}: {
  label: string;
  value: boolean | null | undefined;
  expected: boolean;
  whenTrue: string;
  whenFalse: string;
  whenUnknown: string;
}) {
  const known = value === true || value === false;
  const holds = known && value === expected;
  return (
    <div className={`${s.assertion} ${!known ? s.assertUnknown : holds ? s.assertOk : s.assertBad}`}>
      <span className={s.assertLabel}>{label}</span>
      <span className={s.assertValue}>{known ? String(value) : "not reported"}</span>
      <span className={s.assertText}>{!known ? whenUnknown : value ? whenTrue : whenFalse}</span>
    </div>
  );
}

export function EvidencePanel({
  validation,
  evidence,
}: {
  validation: Result<ValidationResponse> | null;
  evidence: EvidenceAssessment;
}) {
  const era = validation?.ok ? validation.data.era_evaluation : undefined;

  return (
    <Panel id="evidence" kicker="Area 7 · GET /validation" title="Validation and evidence status">
      {validation === null ? <Loading what="validation metrics" /> : null}
      {validation && !validation.ok ? <ErrorState result={validation} what="Could not load validation metrics." /> : null}

      <div className={`${s.gradeBox} ${s[`grade_${evidence.grade}`] ?? ""}`}>
        <div className={s.gradeChipRow}>
          <span className={s.gradeChip}>{evidence.grade}</span>
          <span className={s.gradeSource}>
            {evidence.fromBackend ? "graded by the backend" : "derived by this UI from the era report"}
          </span>
        </div>
        <p className={s.gradeMeaning}>{gradeMeaning(evidence.grade)}</p>
        <ul className={s.gradeReasons}>
          {evidence.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      </div>

      <Callout tone="warn">
        Current 2026-only work is <strong>INTERIM</strong>, not a final regulation-era result.
        Historical 2022&ndash;25 validation is <strong>not complete</strong>.
      </Callout>

      {validation?.ok ? (
        <>
          <h3 className={s.subhead}>Assertions</h3>
          <div className={s.assertions}>
            <Assertion
              label="British GP used for training or calibration"
              value={era?.british_gp_used_for_training_or_calibration}
              expected={false}
              whenFalse="Held out, as required. It is demo and replay material only, never training or calibration input."
              whenTrue="The British GP has entered training or calibration. The hold-out is broken and results involving it are not valid."
              whenUnknown="The era report does not state this, so the hold-out cannot be confirmed from this response."
            />
            <Assertion
              label="Real historical 2022-25 evidence joined"
              value={era?.real_historical_evidence}
              expected={true}
              whenTrue="Real historical seasons are joined."
              whenFalse="No real 2022-25 seasons are joined. This is a 2026-only run; historical validation is not complete and must not be described as such."
              whenUnknown="Not reported; historical completeness cannot be claimed."
            />
            <Assertion
              label="Historical DRS treated as distinct from 2026 Overtake"
              value={era?.historical_drs_is_not_2026_overtake}
              expected={true}
              whenTrue="Historical DRS is kept distinct from the 2026 Overtake state, as required."
              whenFalse="Historical DRS is being conflated with 2026 Overtake. Any 2026 eligibility result from this run is invalid."
              whenUnknown="Not reported."
            />
          </div>

          <h3 className={s.subhead}>Per-component status</h3>
          <div className={s.tableWrap}>
            <table className={s.table}>
              <caption className={s.caption}>
                Sample size per component. No single overall figure — it would hide the weak one.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Component</th>
                  <th scope="col">Model version</th>
                  <th scope="col">Status</th>
                  <th scope="col">n</th>
                  <th scope="col">Provenance</th>
                </tr>
              </thead>
              <tbody>
                {(["rival", "value", "planner", "simulator"] as const).map((key) => {
                  const c = validation.data[key] as Record<string, unknown> | undefined;
                  if (!c) return null;
                  const n = c.n ?? c.n_episodes ?? c.n_synthetic_rows;
                  return (
                    <tr key={key}>
                      <th scope="row">{key}</th>
                      <td>
                        <code>{String(c.model_version ?? "—")}</code>
                      </td>
                      <td>{String(c.status ?? c.real_state_calibration ?? "—")}</td>
                      <td>{n === undefined ? <abbr className={s.na} title="No sample size was reported for this component.">Unavailable</abbr> : String(n)}</td>
                      <td>
                        <ProvenanceBadge tag={c.provenance as string | undefined} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {validation.data.reason ? <p className={s.sourceNote}>{validation.data.reason}</p> : null}

          {era?.metrics ? (
            <details className={s.details}>
              <summary className={s.summary}>Era strategies ({Object.keys(era.metrics).length})</summary>
              <p className={s.detailsBody}>
                Selected strategy: <code>{era.selected ?? "not stated"}</code>. Split version{" "}
                <code>{era.split_version ?? "not stated"}</code>.
              </p>
              <div className={s.tableWrap}>
                <table className={s.table}>
                  <thead>
                    <tr>
                      <th scope="col">Strategy</th>
                      <th scope="col">n</th>
                      <th scope="col">Held-out n</th>
                      <th scope="col">Years</th>
                      <th scope="col">Calibration</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(era.metrics).map(([name, m]) => (
                      <tr key={name} className={name === era.selected ? s.selectedRow : undefined}>
                        <th scope="row">
                          <code>{name}</code>
                        </th>
                        <td>{m.n ?? "—"}</td>
                        <td>{m.held_out_evidence_n ?? "—"}</td>
                        <td>{m.year_coverage?.join(", ") ?? "—"}</td>
                        <td className={s.sourceCell}>{m.calibration ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          ) : null}
        </>
      ) : null}
    </Panel>
  );
}

export { assessEvidence };
export default EvidencePanel;
