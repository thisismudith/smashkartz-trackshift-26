/**
 * Renders the four /decision gates against sampleFixtures instead of AwaitingModel.
 * Only reachable via ?preview=1 (DecisionView.tsx). SAMPLE DATA — see
 * frontend/src/api-contract/fixtures/README.md. Never the default view.
 */
"use client";

import { ChartFrame } from "@/sim/charts";
import { CHART } from "@/lib/palette";
import { sampleFixtures } from "@/api-contract";
import { ScenarioControls } from "./ScenarioControls";
import s from "./preview.module.css";

const timeline = sampleFixtures.timeline["2026_GBR_Race_HAM_ANT_Battle03"];
const step = timeline.steps[timeline.steps.length - 1]; // the step with an opportunity
const shadowPrice = sampleFixtures.shadowPrice.british_grand_prix;
const plan = sampleFixtures.plan;

function ShadowPricePanel() {
  const maxLambda = Math.max(...shadowPrice.profile.map((p) => p.lambda_s_per_kj));
  return (
    <ChartFrame
      title="Energy shadow price · λ_E"
      units="s / kJ"
      provenance="DERIVED"
      note={`Spike at segment ${shadowPrice.spikes[0]?.segment_id} — ${shadowPrice.spikes[0]?.cause}, before the zone's Detection Line.`}
    >
      <div className={s.bars}>
        {shadowPrice.profile.map((p) => (
          <div key={p.segment_id} className={s.barRow}>
            <span className={s.barLabel}>seg {p.segment_id}</span>
            <div className={s.barTrack}>
              <div
                className={s.barFill}
                style={{
                  width: `${(p.lambda_s_per_kj / maxLambda) * 100}%`,
                  background: CHART.series[0],
                }}
              />
            </div>
            <span className={s.barValue}>{p.lambda_s_per_kj.toFixed(4)}</span>
          </div>
        ))}
      </div>
    </ChartFrame>
  );
}

function PassProbabilityPanel() {
  const pass = step.pass;
  if (!pass) return null;
  return (
    <ChartFrame title="Pass probability" units="P(pass)" provenance="INFERRED">
      <div className={s.checkpoints}>
        {(["DETECTION", "ACTIVATION", "BRAKING"] as const).map((cp) => {
          const c = pass.checkpoints[cp];
          return (
            <div key={cp} className={s.checkpoint} data-reached={c.reached}>
              <span className={s.checkpointLabel}>{cp}</span>
              <span className={s.checkpointValue}>
                {c.p_pass_by_outcome_horizon !== null
                  ? `${Math.round(c.p_pass_by_outcome_horizon * 100)}%`
                  : "not reached"}
              </span>
              {c.ensemble_spread !== null ? (
                <span className={s.checkpointSpread}>± {c.ensemble_spread.toFixed(2)}</span>
              ) : null}
            </div>
          );
        })}
      </div>
    </ChartFrame>
  );
}

function RivalBeliefPanel() {
  const rival = step.rival_state;
  if (!rival) return null;
  const entries = Object.entries(rival.p) as [string, number][];
  return (
    <ChartFrame title="Rival belief" units="P(state)" provenance="INFERRED">
      <div className={s.bars}>
        {entries.map(([label, value], i) => (
          <div key={label} className={s.barRow}>
            <span className={s.barLabel}>{label}</span>
            <div className={s.barTrack}>
              <div
                className={s.barFill}
                style={{ width: `${value * 100}%`, background: CHART.series[i % CHART.series.length] }}
              />
            </div>
            <span className={s.barValue}>{Math.round(value * 100)}%</span>
          </div>
        ))}
      </div>
    </ChartFrame>
  );
}

function RecommendationPanel() {
  return (
    <ChartFrame
      title="Recommendation"
      provenance="INFERRED"
      note={`Dominant mechanism: ${plan.decision.dominant_mechanism} · constraint: ${plan.decision.primary_constraint}`}
      table={{
        columns: ["Policy", "P(ahead)", "Final energy (kJ)"],
        rows: (plan.baselines ?? []).map((b) => [
          b.name,
          b.p_ahead_at_horizon.toFixed(2),
          b.final_energy_kj.toFixed(0),
        ]),
      }}
    >
      <div className={s.recommendation}>
        <span className={s.recommendationAction}>{plan.plan[0]?.action.label}</span>
        <span className={s.recommendationMeta}>
          deploy {plan.plan[0]?.action.deploy_level} · P(ahead) at horizon{" "}
          {(plan.p_ahead_at_horizon.mean * 100).toFixed(0)}% [{(plan.p_ahead_at_horizon.low * 100).toFixed(0)}–
          {(plan.p_ahead_at_horizon.high * 100).toFixed(0)}]
        </span>
      </div>
    </ChartFrame>
  );
}

export function PreviewPanels() {
  return (
    <>
      <div className={s.banner}>SAMPLE DATA — not a model output. Fixtures under frontend/src/api-contract/fixtures/.</div>
      <ScenarioControls />
      <div className={s.grid}>
        <ShadowPricePanel />
        <PassProbabilityPanel />
        <RivalBeliefPanel />
        <RecommendationPanel />
      </div>
    </>
  );
}
