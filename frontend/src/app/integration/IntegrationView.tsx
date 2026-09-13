/**
 * The integration console: one page that exercises every route the UI consumes, in either
 * delivery mode, against whatever the backend actually returns.
 *
 * It exists because "the UI works against the replay bundle" and "the UI works against the
 * live service" are claims that need somewhere to be true and visible. A source toggle sits
 * at the top; everything below re-reads through it. The same components render both, which
 * is the point of API.md 1.6 — one schema, two delivery modes — and the only way to be sure
 * of that is to be able to flip between them on one screen.
 *
 * Load order follows the integration brief: /meta, /validation, /battles, then
 * /battles/{battle_id}/timeline using the id that /battles returned.
 *
 * On state shape: switching source resets eleven responses at once. Rather than clearing
 * them one by one inside an effect — which is a cascading render and which React now warns
 * about — the content is REMOUNTED under `key={mode}`, so "fresh state for a new source" is
 * expressed as a new component instance. For the same reason a fetched result is stored
 * together with the key it was fetched for (`{ forId, result }`); "still loading" is then
 * derived by comparing that key against the current one, instead of being a null someone has
 * to remember to write back.
 */
"use client";

import { useCallback, useEffect, useState } from "react";
import { ProvenanceLegend } from "@/components/provenance/Provenance";
import {
  fetchBattles,
  fetchMeta,
  fetchPolicies,
  fetchPowerEnvelope,
  fetchRules,
  fetchTimeline,
  fetchTrack,
  fetchValidation,
  postPassPredict,
  postPlan,
  postSimulate,
} from "@/serve/client";
import { assessEvidence } from "@/serve/evidence";
import type { Result } from "@/serve/guards";
import { CORE_TAGS } from "@/serve/provenance";
import { makeSource, type SourceMode } from "@/serve/source";
import type {
  BattlesResponse,
  MetaResponse,
  PassPredictResponse,
  PlanResponse,
  PoliciesResponse,
  PowerEnvelopeResponse,
  RulesResponse,
  SimulateResponse,
  TimelineResponse,
  TrackResponse,
  ValidationResponse,
} from "@/serve/types";
import BattlePanel from "./BattlePanel";
import EvidencePanel from "./EvidencePanel";
import PlanPanel from "./PlanPanel";
import SimulationPanel, { PassProbabilityPanel } from "./SimulationPanel";
import TrackRulePanel from "./TrackRulePanel";
import { ErrorState } from "./panels";
import s from "./integration.module.css";

/** Used only until /battles names an event. Never used to identify a battle. */
const FALLBACK_EVENT = "british_grand_prix";

/** A result plus the key it was fetched for, so staleness is derivable rather than stored. */
type Keyed<T> = { forId: string; result: Result<T> } | null;

function currentOr<T>(keyed: Keyed<T>, id: string | null): Result<T> | null {
  if (!keyed || !id) return null;
  return keyed.forId === id ? keyed.result : null;
}

export default function IntegrationView() {
  const [mode, setMode] = useState<SourceMode>("live");

  return (
    <main className={s.main}>
      <header className={s.head}>
        <p className={s.kicker}>Model API integration</p>
        <h1 className={s.title}>
          One schema, <em>two</em> delivery modes
        </h1>
        <p className={s.lede}>
          Every route the UI consumes, read through a single source toggle. The replay bundle and the
          live service return the same bodies, so the panels below are written once and rendered from
          whichever is selected. Nothing on this page fabricates a value: a field the backend does not
          return reads <em>Unavailable</em> with the reason, and a response this build cannot render
          becomes a visible error rather than a blank panel.
        </p>

        <div className={s.sourceToggle} role="radiogroup" aria-label="Data source">
          {(["live", "replay"] as const).map((m) => (
            <label key={m} className={`${s.sourceOption} ${mode === m ? s.sourceOptionOn : ""}`}>
              <input
                type="radio"
                name="source"
                value={m}
                checked={mode === m}
                onChange={() => setMode(m)}
                className={s.policyRadio}
              />
              <span className={s.sourceName}>{m === "live" ? "Live service" : "Replay bundle"}</span>
              <span className={s.sourceWhere}>
                {m === "live" ? makeSource("live").label : "TRACKSHIFT_REPLAY_DIR"}
              </span>
            </label>
          ))}
        </div>
      </header>

      {/* Remounting on `mode` is the reset: a new source gets a new component instance. */}
      <IntegrationContent key={mode} mode={mode} />
    </main>
  );
}

function IntegrationContent({ mode }: { mode: SourceMode }) {
  const [meta, setMeta] = useState<Result<MetaResponse> | null>(null);
  const [validation, setValidation] = useState<Result<ValidationResponse> | null>(null);
  const [battles, setBattles] = useState<Result<BattlesResponse> | null>(null);
  const [policies, setPolicies] = useState<Result<PoliciesResponse> | null>(null);
  const [plan, setPlan] = useState<Result<PlanResponse> | null>(null);
  const [pass, setPass] = useState<Result<PassPredictResponse> | null>(null);
  const [simulate, setSimulate] = useState<Result<SimulateResponse> | null>(null);

  const [timeline, setTimeline] = useState<Keyed<TimelineResponse>>(null);
  const [track, setTrack] = useState<Keyed<TrackResponse>>(null);
  const [rules, setRules] = useState<Keyed<RulesResponse>>(null);
  const [envelope, setEnvelope] = useState<Keyed<PowerEnvelopeResponse>>(null);

  /** User's explicit pick. Null means "whatever /battles listed first". */
  const [picked, setPicked] = useState<string | null>(null);
  const [policy, setPolicy] = useState("DEFEND_CONSERVE");
  const [seed, setSeed] = useState(7);
  const [running, setRunning] = useState(false);

  // Steps 1-3 of the brief, plus the routes that need no battle id. Independent, so together.
  useEffect(() => {
    let alive = true;
    const src = makeSource(mode);
    const guard = <T,>(f: (r: Result<T>) => void) => (r: Result<T>) => { if (alive) f(r); };

    fetchMeta(src).then(guard(setMeta));
    fetchValidation(src).then(guard(setValidation));
    fetchBattles(src).then(guard(setBattles));
    fetchPolicies(src).then(guard(setPolicies));

    // The synthetic service fills the decision state itself when the body omits it. Sending a
    // hand-written state here would be testing our fixture rather than theirs.
    postPlan(src, { include_baselines: true }).then(guard(setPlan));
    postSimulate(src, { n_episodes: 8, seed: 17, rival_policy: "DEFEND_CONSERVE" }).then(guard(setSimulate));
    postPassPredict(src, { gap_s: 0.72, deploy_level: 0.5, checkpoint: "DETECTION" }).then(guard(setPass));

    return () => { alive = false; };
  }, [mode]);

  // Derived, not stored: the id comes from /battles unless the reader picked another.
  const listed = battles?.ok ? battles.data.battles : [];
  const battleId = picked ?? listed[0]?.battle_id ?? null;
  const event = listed.find((b) => b.battle_id === battleId)?.event ?? FALLBACK_EVENT;

  // Step 4: the timeline, for the id /battles actually returned.
  useEffect(() => {
    if (!battleId) return;
    let alive = true;
    fetchTimeline(makeSource(mode), battleId).then((r) => {
      if (alive) setTimeline({ forId: battleId, result: r });
    });
    return () => { alive = false; };
  }, [battleId, mode]);

  useEffect(() => {
    let alive = true;
    const src = makeSource(mode);
    fetchTrack(src, event).then((r) => { if (alive) setTrack({ forId: event, result: r }); });
    fetchRules(src, event).then((r) => { if (alive) setRules({ forId: event, result: r }); });
    fetchPowerEnvelope(src, event).then((r) => { if (alive) setEnvelope({ forId: event, result: r }); });
    return () => { alive = false; };
  }, [event, mode]);

  const runSimulation = useCallback(async () => {
    setRunning(true);
    const r = await postSimulate(makeSource(mode), {
      n_episodes: 8,
      seed,
      rival_policy: policy,
      our_policy: "beam_dp",
    });
    setSimulate(r);
    setRunning(false);
  }, [mode, policy, seed]);

  const evidence = assessEvidence(validation?.ok ? validation.data : null);

  return (
    <>
      {meta && !meta.ok ? <ErrorState result={meta} what="Could not read /meta from this source." /> : null}
      {meta?.ok ? (
        <dl className={s.metaStrip}>
          <div><dt>mode</dt><dd>{meta.data.mode}</dd></div>
          <div><dt>api</dt><dd>{meta.data.api_version}</dd></div>
          <div><dt>commit</dt><dd><code>{meta.data.git_commit}</code></dd></div>
          <div><dt>release ready</dt><dd>{String(meta.data.release_ready ?? "not stated")}</dd></div>
          <div><dt>fixture</dt><dd><code>{meta.data.synthetic_fixture ?? "none declared"}</code></dd></div>
          <div><dt>stubs</dt><dd>{meta.data.stubs.length === 0 ? "none" : meta.data.stubs.join(", ")}</dd></div>
        </dl>
      ) : null}

      <ProvenanceLegend tags={CORE_TAGS} />

      <BattlePanel
        battles={battles}
        timeline={currentOr(timeline, battleId)}
        selected={battleId}
        onSelect={setPicked}
      />

      <TrackRulePanel
        track={currentOr(track, event)}
        rules={currentOr(rules, event)}
        envelope={currentOr(envelope, event)}
      />

      <PlanPanel plan={plan} />

      <SimulationPanel
        policies={policies}
        simulate={running ? null : simulate}
        selectedPolicy={policy}
        onSelectPolicy={setPolicy}
        seed={seed}
        onSeed={setSeed}
        onRun={runSimulation}
        running={running}
        replayMode={mode === "replay"}
      />

      <PassProbabilityPanel pass={pass} evidence={evidence} />

      <EvidencePanel validation={validation} evidence={evidence} />
    </>
  );
}
