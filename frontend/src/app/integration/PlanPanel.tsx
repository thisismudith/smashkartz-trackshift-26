/**
 * The planner: POST /plan.
 *
 * The load-bearing distinction here is between an action that scored badly and an action
 * that was never scorable. API.md 1.9 and 31 say illegal actions never appear in an action
 * list — the rule engine filters before scoring. So this panel keeps two separate lists and
 * never merges them:
 *
 *   Legal actions      ranked, with expected value and regret
 *   Excluded actions   shown with the RULE that removed them, and no score at all
 *
 * Rendering an excluded action with a score — even a very bad one — would say the optimiser
 * considered it and disliked it. It did not consider it. Giving it a number invites the
 * reader to imagine the policy might pick it under other conditions, which is exactly the
 * claim the rule engine exists to make impossible. Excluded rows therefore carry the rule
 * name where the score would be.
 *
 * `rule_violations` must be 0 for a plan; it is displayed rather than assumed, so a
 * non-zero value is visible as a failure instead of passing silently.
 */
"use client";

import { ProvenanceBadge, Value } from "@/components/provenance/Provenance";
import { formatNumber } from "@/serve/format";
import type { Result } from "@/serve/guards";
import type { PlanAction, PlanAlternative, PlanResponse } from "@/serve/types";
import { Callout, ErrorState, Loading, Panel, RuleViolations } from "./panels";
import s from "./integration.module.css";

function actionLabel(a: PlanAction): string {
  const d = `deploy ${(a.deploy_level * 100).toFixed(0)}%`;
  const l = a.lift_amount ? `, lift ${(a.lift_amount * 100).toFixed(0)}%` : "";
  return `${d}${l}`;
}

function ActionRow({ a, alt }: { a: PlanAction; alt?: PlanAlternative }) {
  return (
    <tr>
      <th scope="row" className={s.actionCell}>
        {actionLabel(a)}
        {a.label ? <span className={s.actionTag}>{a.label}</span> : null}
        {a.upper_bound_only ? (
          <span className={s.upperBound} title="An upper bound, not a deployable policy.">upper bound only</span>
        ) : null}
      </th>
      <td>
        <Value d={formatNumber(a.cap_kw, { unit: "kW", digits: 0, provenance: "RULE" })} />
      </td>
      <td>{a.applicable_mode ?? "—"}</td>
      <td>
        <Value d={formatNumber(a.delivered_power_kw, { unit: "kW", digits: 0, provenance: "DERIVED" })} />
      </td>
      <td>
        <Value d={formatNumber(alt?.expected_value ?? null, { digits: 3, provenance: "DERIVED", fallbackReason: "the DP could not evaluate this action — its segment transition did not complete. Not the same as scoring zero." })} />
      </td>
      <td>
        <Value d={formatNumber(alt?.regret ?? null, { digits: 3, provenance: "DERIVED", fallbackReason: "no expected value for this action, so there is no gap to the best one to report" })} />
      </td>
    </tr>
  );
}

export function PlanPanel({ plan }: { plan: Result<PlanResponse> | null }) {
  if (plan === null) return <Panel id="plan" kicker="Area 4 · POST /plan" title="Plan"><Loading what="the plan" /></Panel>;

  if (!plan.ok) {
    return (
      <Panel id="plan" kicker="Area 4 · POST /plan" title="Plan">
        <ErrorState result={plan} what="Could not compute a plan." />
      </Panel>
    );
  }

  const d = plan.data;
  const alts = d.decision?.alternatives ?? d.alternatives ?? [];
  const byKey = new Map(alts.map((x) => [`${x.action.deploy_level}:${x.action.lift_amount}`, x]));
  const legal = d.legal_actions ?? [];
  const excluded = d.excluded_actions ?? [];

  return (
    <Panel
      id="plan"
      kicker="Area 4 · POST /plan"
      title="Plan"
      aside={<RuleViolations n={d.rule_violations} context="the planner" />}
    >
      <dl className={s.fields}>
        <div className={s.field}>
          <dt className={s.label}>Recommended action</dt>
          <dd className={s.data}>
            {d.recommended_action ? (
              <>
                <strong>{actionLabel(d.recommended_action)}</strong>
                <ProvenanceBadge tag={d.provenance} />
                <span className={s.note}>
                  Cap at this state {d.recommended_action.cap_kw ?? "?"} kW under{" "}
                  {d.recommended_action.applicable_mode ?? "?"} mode — the cap moves with speed, it is
                  not a constant.
                </span>
              </>
            ) : (
              <abbr className={s.na} title="The planner returned no recommended action.">Unavailable</abbr>
            )}
          </dd>
        </div>
        <div className={s.field}>
          <dt className={s.label}>Status</dt>
          <dd className={s.data}>
            {d.status ?? "—"}
            {d.reason ? <span className={s.note}>{d.reason}</span> : null}
          </dd>
        </div>
        <div className={s.field}>
          <dt className={s.label}>Dominant mechanism</dt>
          <dd className={s.data}>{d.decision?.dominant_mechanism ?? "—"}</dd>
        </div>
        <div className={s.field}>
          <dt className={s.label}>Primary constraint</dt>
          <dd className={s.data}>{d.decision?.primary_constraint ?? "—"}</dd>
        </div>
        <div className={s.field}>
          <dt className={s.label}>Decision stability</dt>
          <dd className={s.data}>
            <Value d={formatNumber(d.decision?.decision_stability ?? null, { digits: 3, provenance: "DERIVED" })} />
          </dd>
        </div>
        <div className={s.field}>
          <dt className={s.label}>Model / rule versions</dt>
          <dd className={s.data}>
            <code>{d.rule_configuration_version ?? "rule version unavailable"}</code>
            {d.schema_version ? <code className={s.inlineCode}>{d.schema_version}</code> : null}
            {d.versions?.models
              ? Object.entries(d.versions.models).map(([k, v]) => (
                  <code className={s.inlineCode} key={k}>
                    {k}: {v}
                  </code>
                ))
              : null}
          </dd>
        </div>
        {d.input_provenance ? (
          <div className={s.field}>
            <dt className={s.label}>Input provenance</dt>
            <dd className={s.data}>
              {Object.entries(d.input_provenance).map(([k, v]) => (
                <span key={k} className={s.inputProv}>
                  {k}
                  <ProvenanceBadge tag={v} />
                </span>
              ))}
            </dd>
          </div>
        ) : null}
      </dl>

      <h3 className={s.subhead}>Legal actions ({legal.length})</h3>
      <Callout>
        What the rule engine permitted here. <strong>Expected value</strong> is the DP&apos;s value
        for taking that action now and continuing optimally; <strong>regret</strong> is what it
        costs against the best one. Illegal actions carry no score at all — they are below, with the
        rule that removed them.
      </Callout>
      <div className={s.tableWrap}>
        <table className={s.table}>
          <thead>
            <tr>
              <th scope="col">Action</th>
              <th scope="col">Cap</th>
              <th scope="col">Mode</th>
              <th scope="col">Delivered</th>
              <th scope="col">Expected value</th>
              <th scope="col">Regret</th>
            </tr>
          </thead>
          <tbody>
            {legal.map((a, i) => (
              <ActionRow key={i} a={a} alt={byKey.get(`${a.deploy_level}:${a.lift_amount}`)} />
            ))}
          </tbody>
        </table>
      </div>

      <h3 className={s.subhead}>Excluded actions ({excluded.length})</h3>
      {excluded.length === 0 ? (
        <p className={s.empty}>
          The rule engine excluded nothing at this state — every action in the grid was legal here.
          That is a statement about this state, not a general one.
        </p>
      ) : (
        <div className={s.tableWrap}>
          <table className={s.table}>
            <caption className={s.caption}>
              Excluded actions carry no score on purpose: they were removed before scoring, so there
              is no value to report.
            </caption>
            <thead>
              <tr>
                <th scope="col">Action</th>
                <th scope="col">Removed by rule</th>
                <th scope="col">Source</th>
              </tr>
            </thead>
            <tbody>
              {excluded.map((e, i) => (
                <tr key={i} className={s.excludedRow}>
                  <th scope="row">{e.action ? actionLabel(e.action) : "—"}</th>
                  <td>
                    <code>{e.rule ?? e.reason ?? "rule not named"}</code>
                  </td>
                  <td className={s.sourceCell}>{e.source ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {d.baselines?.baselines ? (
        <>
          <h3 className={s.subhead}>Baselines</h3>
          <Callout tone="warn">
            <code>oracle_rival_state</code> is an <strong>upper bound, not a deployable policy</strong>
            — it is told the rival&apos;s hidden state. {d.baselines.oracle_note ?? ""}
          </Callout>
          <div className={s.tableWrap}>
            <table className={s.table}>
              <thead>
                <tr>
                  <th scope="col">Baseline</th>
                  <th scope="col">First action</th>
                  <th scope="col">Segments</th>
                  <th scope="col">Deployable</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(d.baselines.baselines).map(([name, actions]) => {
                  const first = actions[0];
                  const upper = first?.upper_bound_only === true || first?.deployable === false;
                  return (
                    <tr key={name}>
                      <th scope="row">
                        <code>{name}</code>
                      </th>
                      <td>{first ? actionLabel(first) : "—"}</td>
                      <td>{actions.length}</td>
                      <td>
                        {upper ? (
                          <span className={s.upperBound}>No — upper bound only</span>
                        ) : (
                          "Yes"
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className={s.meta}>
            <RuleViolations n={d.baselines.rule_violations} context="the baseline set" />
          </p>
        </>
      ) : null}
    </Panel>
  );
}

export default PlanPanel;
