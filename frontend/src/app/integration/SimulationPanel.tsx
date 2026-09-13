/**
 * Simulation: GET /simulate/policies, POST /simulate — and the pass-probability readout.
 *
 * Two claims this panel is built to prevent.
 *
 * "WHAT ACTUALLY HAPPENED." A simulated episode is a counterfactual: it is what the model
 * thinks would follow from a state under an assumed rival policy. The heading, the badge on
 * every number and the mandatory `assumptions` list all say so, and the rival policy is
 * chosen explicitly rather than defaulted, because a hidden default is how an assumption
 * stops being visible.
 *
 * "A 58% CHANCE OF PASSING." `p_pass_by_outcome_horizon` is not that. It is the probability
 * of the pass being COMPLETE BY A DECLARED HORIZON — a versioned definition of the training
 * label, not an instantaneous event. The distinction matters because the intuitive reading
 * ("there is a 58% chance he gets by") is horizon-free and therefore unfalsifiable. The
 * panel spells the horizon out, names the checkpoint the probability belongs to, and shows
 * the calibration string verbatim: when the service says `synthetic-development`, that is
 * what appears, not a rounded accuracy figure.
 *
 * No accuracy or calibration claim is displayed at all unless the evidence grade is FULL
 * (see evidence.ts). The grade is passed in rather than re-derived so one assessment drives
 * every panel.
 */
"use client";

import { ProvenanceBadge, Value } from "@/components/provenance/Provenance";
import { formatNumber, formatProbability, formatQuantity } from "@/serve/format";
import type { EvidenceAssessment } from "@/serve/evidence";
import type { Result } from "@/serve/guards";
import type { PassPredictResponse, PoliciesResponse, SimulateResponse } from "@/serve/types";
import { Callout, ErrorState, Loading, Panel, RuleViolations } from "./panels";
import s from "./integration.module.css";

export function SimulationPanel({
  policies,
  simulate,
  selectedPolicy,
  onSelectPolicy,
  seed,
  onSeed,
  onRun,
  running,
  replayMode,
}: {
  policies: Result<PoliciesResponse> | null;
  simulate: Result<SimulateResponse> | null;
  selectedPolicy: string;
  onSelectPolicy: (name: string) => void;
  seed: number;
  onSeed: (n: number) => void;
  onRun: () => void;
  running: boolean;
  replayMode: boolean;
}) {
  const chosen = policies?.ok ? policies.data.policies.find((p) => p.name === selectedPolicy) : undefined;

  return (
    <Panel
      id="simulate"
      kicker="Area 5 · GET /simulate/policies · POST /simulate"
      title="Simulated counterfactual"
      aside={simulate?.ok ? <RuleViolations n={simulate.data.summary?.rule_violations} context="the simulator" /> : null}
    >
      <Callout tone="warn">
        Everything in this panel is a <strong>simulated counterfactual</strong> — what the model
        expects under an assumed rival policy. It is not what actually happened in any race.
      </Callout>

      {policies === null ? <Loading what="the rival policy list" /> : null}
      {policies && !policies.ok ? <ErrorState result={policies} what="Could not list rival policies." /> : null}

      {policies?.ok ? (
        <>
          <h3 className={s.subhead}>Rival policy (explicit)</h3>
          <div className={s.policyGrid} role="radiogroup" aria-label="Rival policy">
            {policies.data.policies.map((p) => (
              <label key={p.name} className={`${s.policyCard} ${p.name === selectedPolicy ? s.policyCardOn : ""}`}>
                <input
                  type="radio"
                  name="rival_policy"
                  value={p.name}
                  checked={p.name === selectedPolicy}
                  onChange={() => onSelectPolicy(p.name)}
                  className={s.policyRadio}
                />
                <span className={s.policyName}>{p.name}</span>
                {/* Shown verbatim: API.md 56 requires the simulator's assumptions be disclosed. */}
                <span className={s.policyDesc}>{p.description ?? "No description was returned for this policy."}</span>
                {p.parameters && Object.keys(p.parameters).length > 0 ? (
                  <span className={s.policyParams}>
                    {Object.entries(p.parameters).map(([k, v]) => (
                      <code key={k} className={s.inlineCode}>
                        {k}={String(v)}
                      </code>
                    ))}
                  </span>
                ) : null}
              </label>
            ))}
          </div>

          <div className={s.controls}>
            <label className={s.selectLabel}>
              Seed
              <input
                type="number"
                className={s.numberInput}
                value={seed}
                onChange={(e) => onSeed(Number(e.target.value))}
                disabled={replayMode}
              />
            </label>
            <button type="button" className={s.button} onClick={onRun} disabled={running || replayMode}>
              {running ? "Running…" : "Run simulation"}
            </button>
            {replayMode ? (
              <span className={s.controlNote}>
                Replay mode serves the precomputed episode set from the bundle, so the seed and policy
                are fixed to whatever it was built with. Switch to the live service to vary them.
              </span>
            ) : (
              <span className={s.controlNote}>
                Fixed seed, so the run is reproducible. {chosen ? `Rival plays ${chosen.name}.` : ""}
              </span>
            )}
          </div>
        </>
      ) : null}

      {simulate === null ? <Loading what="the simulation" /> : null}
      {simulate && !simulate.ok ? <ErrorState result={simulate} what="Could not run the simulation." /> : null}

      {simulate?.ok ? (
        <>
          <h3 className={s.subhead}>Result — simulated counterfactual</h3>
          <dl className={s.fields}>
            <div className={s.field}>
              <dt className={s.label}>P(ahead at horizon)</dt>
              <dd className={s.data}>
                <Value d={formatProbability(simulate.data.summary?.p_ahead_at_horizon)} />
                <ProvenanceBadge tag={simulate.data.provenance} />
                <span className={s.note}>
                  Share of simulated episodes ending ahead. A frequency over model runs, not a
                  measured outcome.
                </span>
              </dd>
            </div>
            <div className={s.field}>
              <dt className={s.label}>Mean final estimated energy</dt>
              <dd className={s.data}>
                <Value
                  d={formatNumber(simulate.data.summary?.mean_final_energy_mj ?? null, { unit: "MJ", digits: 3, provenance: "SIMULATED" })}
                />
                <span className={s.note}>Estimated electrical energy — not a battery or state-of-charge reading.</span>
              </dd>
            </div>
            <div className={s.field}>
              <dt className={s.label}>Episodes / seed</dt>
              <dd className={s.data}>
                {simulate.data.summary?.n_episodes ?? "?"} episodes, seed {simulate.data.summary?.seed ?? "?"}
              </dd>
            </div>
            <div className={s.field}>
              <dt className={s.label}>Status</dt>
              <dd className={s.data}>{simulate.data.status ?? "—"}</dd>
            </div>
          </dl>

          <h3 className={s.subhead}>Assumptions</h3>
          {simulate.data.assumptions && simulate.data.assumptions.length > 0 ? (
            <ul className={s.assumptions}>
              {simulate.data.assumptions.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
          ) : (
            <p className={s.errorInline} role="alert">
              The response carried no <code>assumptions</code>. API.md requires them to be shown, so
              their absence is reported rather than passed over.
            </p>
          )}
        </>
      ) : null}
    </Panel>
  );
}

export function PassProbabilityPanel({
  pass,
  evidence,
}: {
  pass: Result<PassPredictResponse> | null;
  evidence: EvidenceAssessment;
}) {
  return (
    <Panel id="pass" kicker="Area 6 · POST /pass/predict" title="Pass probability">
      <Callout>
        <strong>
          <code>p_pass_by_outcome_horizon</code> is the probability of the pass being complete by the
          declared outcome horizon
        </strong>{" "}
        — a fixed, versioned definition of the training label. It is not the chance of getting by
        right now, and it does not guarantee a pass at any moment.
      </Callout>

      {pass === null ? <Loading what="a pass probability" /> : null}
      {pass && !pass.ok ? <ErrorState result={pass} what="Could not obtain a pass probability." /> : null}

      {pass?.ok ? (
        <dl className={s.fields}>
          <div className={s.field}>
            <dt className={s.label}>P(pass by outcome horizon)</dt>
            <dd className={s.data}>
              <Value d={formatQuantity(pass.data.p_pass_by_outcome_horizon, { digits: 3 })} />
              <span className={s.note}>
                Probability of completion by the horizon below — not an instantaneous pass chance.
              </span>
            </dd>
          </div>
          <div className={s.field}>
            <dt className={s.label}>Decision checkpoint</dt>
            <dd className={s.data}>
              {pass.data.checkpoint ?? "Unavailable"}
              <span className={s.note}>
                Each probability belongs to exactly one checkpoint and used only information
                available at or before it. DETECTION, ACTIVATION and BRAKING are never merged into
                one number.
              </span>
            </dd>
          </div>
          <div className={s.field}>
            <dt className={s.label}>Outcome horizon</dt>
            <dd className={s.data}>
              {pass.data.outcome_horizon ?? (
                <abbr className={s.na} title="The response did not name the outcome horizon, so the window this probability refers to is not stated.">
                  Unavailable
                </abbr>
              )}
            </dd>
          </div>
          <div className={s.field}>
            <dt className={s.label}>Calibration</dt>
            <dd className={s.data}>
              {pass.data.calibration ? (
                <>
                  <code>{pass.data.calibration}</code>
                  {/^synthetic/i.test(pass.data.calibration) ? (
                    <span className={s.noteWarn}>
                      The service declares this a <strong>synthetic development calibration</strong>.
                      It is not calibrated against real race outcomes and must not be read as one.
                    </span>
                  ) : null}
                </>
              ) : (
                <abbr className={s.na} title="No calibration method was named.">Unavailable</abbr>
              )}
            </dd>
          </div>
        </dl>
      ) : null}

      {/* The gate: no headline accuracy/calibration claim until a FULL C4 artifact exists. */}
      <div className={evidence.mayClaimCalibration ? s.claimOk : s.claimBlocked}>
        {evidence.mayClaimCalibration ? (
          <p>
            Evidence grade <strong>FULL</strong> — accuracy and calibration figures for this model may
            be reported as results.
          </p>
        ) : (
          <p>
            <strong>No accuracy or calibration figure is displayed.</strong> The evidence grade is{" "}
            <strong>{evidence.grade}</strong>, and a headline accuracy number requires a FULL C4
            artifact. Reporting one now would present a pipeline test as a measured result.
          </p>
        )}
      </div>
    </Panel>
  );
}

export default SimulationPanel;
