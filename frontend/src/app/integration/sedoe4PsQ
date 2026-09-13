/**
 * Battle playback: /battles, then /battles/{battle_id}/timeline.
 *
 * The battle id is taken from the `/battles` response and never typed in. API.md's example
 * id (`2026_GBR_Race_HAM_ANT_Battle03`) does not exist in the running service, which serves
 * `synthetic_2026_GBR_Race_HAM_ANT`; a hard-coded id would 404 against the fixture and,
 * worse, would quietly keep working against a stale bundle after the real one changed.
 *
 * What the service returns per segment is gap, estimated energy and a rival-state
 * distribution — not the full API.md 5.5 step. The missing blocks are listed as missing
 * rather than omitted, because a panel that simply does not mention `race_control` reads
 * as "the gate is fine" rather than "nobody asked".
 *
 * Energy and rival state are both modelled. The words here follow API.md 58: "estimated
 * electrical energy", never battery or SOC, and the rival state is drawn as the
 * distribution it is, never collapsed to its most likely label.
 */
"use client";

import { useMemo } from "react";
import { ProvenanceBadge, Value } from "@/components/provenance/Provenance";
import { formatQuantity, formatProbability, rankDistribution } from "@/serve/format";
import type { Result } from "@/serve/guards";
import type { BattlesResponse, TimelineResponse } from "@/serve/types";
import { Callout, ErrorState, Loading, Panel } from "./panels";
import s from "./integration.module.css";

/** Blocks API.md 5.5 documents that this service does not yet join into the timeline. */
const ABSENT_BLOCKS: Array<[string, string]> = [
  ["race_control", "the C7 eligibility gate (SC / VSC / pit state). Without it the UI cannot grey model panels on non-eligible steps."],
  ["era", "the regulation-era block. 2026 Overtake state must come from the rule engine, never from a DRS channel."],
  ["pass", "per-checkpoint pass probabilities (DETECTION / ACTIVATION / BRAKING)."],
  ["eligibility", "P(eligible), eligibility margin and energy-to-unlock."],
  ["baseline_residuals", "C2 residuals against driver / team / field medians."],
];

export function BattlePanel({
  battles,
  timeline,
  selected,
  onSelect,
}: {
  battles: Result<BattlesResponse> | null;
  timeline: Result<TimelineResponse> | null;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const rows = useMemo(() => (timeline?.ok ? timeline.data.segments : []), [timeline]);
  const cutoff = timeline?.ok ? timeline.data.causal_cutoff : undefined;
  const ruleVersion = timeline?.ok ? timeline.data.versions?.models?.rules : undefined;

  return (
    <Panel
      id="battle"
      kicker="Area 2 · GET /battles → GET /battles/{battle_id}/timeline"
      title="Battle playback"
      aside={
        battles?.ok && battles.data.battles.length > 0 ? (
          <label className={s.selectLabel}>
            Battle
            <select
              className={s.select}
              value={selected ?? ""}
              onChange={(e) => onSelect(e.target.value)}
            >
              {battles.data.battles.map((b) => (
                <option key={b.battle_id} value={b.battle_id}>
                  {b.battle_id}
                </option>
              ))}
            </select>
          </label>
        ) : null
      }
    >
      <Callout>
        Ids come from <code>/battles</code>. Nothing here hard-codes the example id in API.md —
        the development fixture is synthetic and uses its own.
      </Callout>

      {battles === null ? <Loading what="the battle index" /> : null}
      {battles && !battles.ok ? <ErrorState result={battles} what="Could not list battles." /> : null}
      {battles?.ok && battles.data.battles.length === 0 ? (
        <p className={s.empty}>
          The source returned no battles.{" "}
          {battles.data.reason ? <em>{battles.data.reason}</em> : "No reason was given."}
        </p>
      ) : null}

      {battles?.ok && battles.data.battles.length > 0 ? (
        <dl className={s.fields}>
          {(() => {
            const b = battles.data.battles.find((x) => x.battle_id === selected) ?? battles.data.battles[0];
            return (
              <>
                <div className={s.field}>
                  <dt className={s.label}>Battle id</dt>
                  <dd className={s.data}>
                    <code>{b.battle_id}</code>
                    <ProvenanceBadge tag={b.provenance} />
                  </dd>
                </div>
                <div className={s.field}>
                  <dt className={s.label}>Pairing</dt>
                  <dd className={s.data}>
                    {b.attacker ?? "?"} attacking {b.defender ?? "?"} · {b.event ?? "?"} {b.year ?? ""} {b.session ?? ""}
                  </dd>
                </div>
              </>
            );
          })()}
        </dl>
      ) : null}

      {timeline === null && selected ? <Loading what="the timeline" /> : null}
      {timeline && !timeline.ok ? (
        <ErrorState result={timeline} what={`Could not load the timeline for ${selected}.`} />
      ) : null}

      {timeline?.ok ? (
        <>
          <div className={s.metaRow}>
            <span>
              <strong>{rows.length}</strong> segment{rows.length === 1 ? "" : "s"}
            </span>
            <span>
              Causal cutoff:{" "}
              {cutoff?.segment_index !== undefined ? (
                <>
                  segment index <strong>{cutoff.segment_index}</strong>
                  <ProvenanceBadge tag={cutoff.provenance} />
                </>
              ) : (
                <abbr title="The response carried no causal_cutoff, so the information horizon behind these values is not stated.">
                  Unavailable
                </abbr>
              )}
            </span>
            <span>
              Rule version:{" "}
              {ruleVersion ? <code>{ruleVersion}</code> : <abbr title="No rule model version in versions.models.rules.">Unavailable</abbr>}
            </span>
          </div>

          <Callout>
            Everything below the cutoff index used only information available at or before that
            point. Estimated electrical energy is a twin output tagged <strong>SIMULATED</strong> —
            it is not a battery reading, and the rival state is a model&apos;s belief, not an
            observation.
          </Callout>

          <div className={s.tableWrap}>
            <table className={s.table}>
              <caption className={s.caption}>
                Per-segment joined model output. Rival state is shown as a full distribution
                (API.md 8) rather than its most likely label.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Segment</th>
                  <th scope="col">Gap</th>
                  <th scope="col">Estimated electrical energy</th>
                  <th scope="col">Rival state distribution (inferred)</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((seg, i) => {
                  const dist = rankDistribution(seg.rival_state?.p);
                  const beyond = cutoff?.segment_index !== undefined && i > cutoff.segment_index;
                  return (
                    <tr key={seg.segment_id} className={beyond ? s.beyondCutoff : undefined}>
                      <th scope="row" className={s.segCell}>
                        {seg.segment_id}
                        {beyond ? <span className={s.beyondTag} title="Past the declared causal cutoff.">past cutoff</span> : null}
                      </th>
                      <td>
                        <Value d={formatQuantity(seg.gap_s, { digits: 3 })} />
                      </td>
                      <td>
                        <Value d={formatQuantity(seg.energy_mj, { digits: 3 })} />
                      </td>
                      <td>
                        {dist.length === 0 ? (
                          <abbr className={s.na} title="No rival-state distribution was returned for this segment.">
                            Unavailable
                          </abbr>
                        ) : (
                          <>
                            <ul className={s.dist}>
                              {dist.map(({ state, p }) => (
                                <li key={state} className={s.distRow}>
                                  <span className={s.distName}>{state}</span>
                                  <span className={s.distBar} aria-hidden="true">
                                    <span className={s.distFill} style={{ width: `${Math.max(p * 100, p > 0 ? 0.6 : 0)}%` }} />
                                  </span>
                                  <span className={s.distP}>{formatProbability(p).text}</span>
                                </li>
                              ))}
                            </ul>
                            <span className={s.distMeta}>
                              <ProvenanceBadge tag={seg.rival_state?.provenance} />
                              {seg.rival_state?.model_version ? <code className={s.inlineCode}>{seg.rival_state.model_version}</code> : null}
                              {seg.rival_state?.merged && seg.rival_state.merged.length > 0 ? (
                                <span className={s.merged}>merged: {seg.rival_state.merged.join(", ")}</span>
                              ) : null}
                            </span>
                          </>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <details className={s.details}>
            <summary className={s.summary}>
              What this timeline does not carry ({ABSENT_BLOCKS.length} blocks API.md 5.5 documents)
            </summary>
            <p className={s.detailsBody}>
              These are listed rather than omitted. A panel that silently leaves out the
              race-control gate reads as &ldquo;the gate is fine&rdquo;, which is a claim nobody made.
            </p>
            <dl className={s.fields}>
              {ABSENT_BLOCKS.map(([key, why]) => (
                <div className={s.field} key={key}>
                  <dt className={s.label}>
                    <code>{key}</code>
                  </dt>
                  <dd className={s.data}>
                    <abbr className={s.na} title="Not present in this API version's timeline response.">Unavailable</abbr>
                    <span className={s.note}>{why}</span>
                  </dd>
                </div>
              ))}
            </dl>
          </details>
        </>
      ) : null}
    </Panel>
  );
}

export default BattlePanel;
