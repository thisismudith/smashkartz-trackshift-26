/**
 * Parameter controls for the decision-state variables API.md's routes actually take as
 * input (energy, gap, eligibility, tyre, rival belief, horizon). "Save" does not fake a
 * response: it builds the exact request body API.md defines and calls the real client in
 * ./api-contract/client.ts. Today that always fails with a network error, because no
 * backend implements these routes yet — the failure is shown, not hidden, so this stays
 * honest about what is and isn't wired up. See DecisionView.tsx's own stance on that.
 */
"use client";

import { useState } from "react";
import { fetchShadowPrice, postPlan } from "@/api-contract/client";
import type { ApiCallResult, PlanRequestBody, PlanResponse, ShadowPriceResponse } from "@/api-contract/types";
import s from "./scenario.module.css";

const RIVAL_PRESETS = {
  BALANCED: { CONSERVING: 0.12, BALANCED: 0.31, DEPLOYING: 0.49, DERATING: 0.08 },
  CONSERVING: { CONSERVING: 0.55, BALANCED: 0.3, DEPLOYING: 0.1, DERATING: 0.05 },
  DEPLOYING: { CONSERVING: 0.05, BALANCED: 0.15, DEPLOYING: 0.7, DERATING: 0.1 },
  DERATING: { CONSERVING: 0.1, BALANCED: 0.2, DEPLOYING: 0.15, DERATING: 0.55 },
} as const;

function StatusBlock<T>({ label, result }: { label: string; result: ApiCallResult<T> | "pending" | null }) {
  if (result === null) return null;
  if (result === "pending") {
    return <p className={s.statusPending}>{label}: sending…</p>;
  }
  if (result.ok) {
    return (
      <div className={s.statusOk}>
        <p>{label}: {result.status} OK</p>
        <pre className={s.pre}>{JSON.stringify(result.data, null, 2)}</pre>
      </div>
    );
  }
  return (
    <div className={s.statusError}>
      <p>
        {label}: not reachable — {result.error}
      </p>
      <p className={s.statusErrorNote}>
        Nothing is faked here. This is the request that would have been sent, once{" "}
        <code>src/trackshift/serve/</code> exists and <code>NEXT_PUBLIC_TRACKSHIFT_API_BASE</code>{" "}
        points at it.
      </p>
      <pre className={s.pre}>
        {result.request.method} {result.request.url}
        {result.request.body ? `\n${JSON.stringify(result.request.body, null, 2)}` : ""}
      </pre>
    </div>
  );
}

export function ScenarioControls() {
  const [energyKj, setEnergyKj] = useState(1420);
  const [timeGapS, setTimeGapS] = useState(0.78);
  const [eligibility, setEligibility] = useState<"NOT_ARMED" | "ARMED">("NOT_ARMED");
  const [tyreCompound, setTyreCompound] = useState("MEDIUM");
  const [tyreLife, setTyreLife] = useState(12);
  const [horizonLaps, setHorizonLaps] = useState<1 | 2>(2);
  const [rivalPreset, setRivalPreset] = useState<keyof typeof RIVAL_PRESETS>("BALANCED");

  const [shadowPriceResult, setShadowPriceResult] = useState<ApiCallResult<ShadowPriceResponse> | "pending" | null>(
    null,
  );
  const [planResult, setPlanResult] = useState<ApiCallResult<PlanResponse> | "pending" | null>(null);

  const planRequest: PlanRequestBody = {
    state: {
      ref: { year: 2026, event: "british_grand_prix", session: "Race", lap: 31, segment_id: 22, distance_m: 3140.0, lap_fraction: 0.53 },
      energy: { energy_kj: energyKj, deployed_kj: 0, harvested_kj: 0 },
      tyre: { compound: tyreCompound, life_laps: tyreLife },
      gap: { time_gap_s: timeGapS, distance_gap_m: null, relative_speed_mps: null, relative_acceleration_mps2: null, gap_rate_s_per_s: null },
      overtake_state: eligibility,
      race_control: { overtake_disabled: false },
      power_envelope: { regime: "NORMAL" },
    },
    rival_state: { p: RIVAL_PRESETS[rivalPreset] },
    horizon_laps: horizonLaps,
    include_baselines: true,
    risk: { cvar_alpha: 0.2 },
  };

  async function saveShadowPrice() {
    setShadowPriceResult("pending");
    const result = await fetchShadowPrice("british_grand_prix", { energy_kj: energyKj, time_gap_s: timeGapS, eligibility });
    setShadowPriceResult(result);
  }

  async function savePlan() {
    setPlanResult("pending");
    const result = await postPlan(planRequest);
    setPlanResult(result);
  }

  return (
    <div className={s.panel}>
      <h2 className={s.heading}>Scenario parameters</h2>
      <p className={s.sub}>
        These are the StrategicState fields that API.md&apos;s routes take as input (§3.4, §5.12, §5.13).
        Moving a control changes only the request below — nothing on this page recomputes a
        result locally, since that would be exactly the fabricated number the contract forbids.
      </p>

      <div className={s.grid}>
        <label className={s.field}>
          <span>Energy (kJ)</span>
          <input type="range" min={0} max={4000} step={50} value={energyKj} onChange={(e) => setEnergyKj(Number(e.target.value))} />
          <span className={s.value}>{energyKj}</span>
        </label>

        <label className={s.field}>
          <span>Time gap (s)</span>
          <input type="range" min={0} max={3} step={0.05} value={timeGapS} onChange={(e) => setTimeGapS(Number(e.target.value))} />
          <span className={s.value}>{timeGapS.toFixed(2)}</span>
        </label>

        <label className={s.field}>
          <span>Eligibility</span>
          <select value={eligibility} onChange={(e) => setEligibility(e.target.value as "NOT_ARMED" | "ARMED")}>
            <option value="NOT_ARMED">NOT_ARMED</option>
            <option value="ARMED">ARMED</option>
          </select>
        </label>

        <label className={s.field}>
          <span>Tyre compound</span>
          <select value={tyreCompound} onChange={(e) => setTyreCompound(e.target.value)}>
            <option>SOFT</option>
            <option>MEDIUM</option>
            <option>HARD</option>
          </select>
        </label>

        <label className={s.field}>
          <span>Tyre life (laps)</span>
          <input type="range" min={0} max={40} step={1} value={tyreLife} onChange={(e) => setTyreLife(Number(e.target.value))} />
          <span className={s.value}>{tyreLife}</span>
        </label>

        <label className={s.field}>
          <span>Rival belief preset</span>
          <select value={rivalPreset} onChange={(e) => setRivalPreset(e.target.value as keyof typeof RIVAL_PRESETS)}>
            {Object.keys(RIVAL_PRESETS).map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
        </label>

        <label className={s.field}>
          <span>Horizon (laps)</span>
          <select value={horizonLaps} onChange={(e) => setHorizonLaps(Number(e.target.value) as 1 | 2)}>
            <option value={1}>1</option>
            <option value={2}>2</option>
          </select>
        </label>
      </div>

      <div className={s.actions}>
        <button type="button" className={s.saveButton} onClick={saveShadowPrice}>
          Save → GET /value/british_grand_prix/shadow_price
        </button>
        <button type="button" className={s.saveButton} onClick={savePlan}>
          Save → POST /plan
        </button>
      </div>

      <StatusBlock label="shadow_price" result={shadowPriceResult} />
      <StatusBlock label="plan" result={planResult} />
    </div>
  );
}
