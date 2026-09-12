/**
 * The regulation panel.
 *
 * Two obligations from API.md section 8 shape this page more than anything else:
 *   "Draw the power envelope as a curve against speed; never present peak kW as 'the' power limit"
 *   "Badge every number derived from an envelope key whose `verified` is false, and never claim
 *    legality by construction while `unverified_keys` is non-empty"
 *
 * So the curves are the content, the unverified state is stated at the top rather than buried,
 * and `describe_compliance()`'s own statement is rendered verbatim instead of paraphrased.
 *
 * The curves are drawn straight from the breakpoints, which are the polyline vertices of a
 * piecewise-linear function -- that is reproduction, not evaluation. Reading the cap at an
 * arbitrary speed is evaluation and belongs to rules.py (UI.md section 6.4); the calculator
 * that needs it is gated until that sampled table ships.
 */
"use client";

import { useEffect, useState } from "react";
import { CHART } from "@/lib/palette";
import { defaultSimSource, type RuleSet } from "@/sim/data/source";
import { ChartFrame, Plot, Grid, XAxis, YAxis, Line, RefLine, XRegion, niceTicks } from "@/sim/charts";
import s from "./rules.module.css";

const MODE_COLOUR: Record<string, string> = {
  normal: CHART.series[0],
  override: CHART.series[1],
};

export default function RulesView() {
  const [rules, setRules] = useState<RuleSet | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    defaultSimSource
      .rules()
      .then((r) => {
        if (!live) return;
        if (!r) setError("The built artifacts predate the rule engine, so no thresholds are available.");
        else setRules(r);
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, []);

  if (error) {
    return (
      <main className={s.main}>
        <p className={s.lede}>{error}</p>
      </main>
    );
  }
  if (!rules) {
    return (
      <main className={s.main}>
        <p className={s.lede}>Loading rule configuration…</p>
      </main>
    );
  }

  const modes = Object.keys(rules.power_envelope);
  const allSpeeds = modes.flatMap((m) => rules.power_envelope[m].breakpoints_kmh);
  const allPower = modes.flatMap((m) => rules.power_envelope[m].max_power_kw);
  const maxSpeed = Math.max(...allSpeeds);
  // end the y axis on a tick above the data: a plateau drawn exactly on the frame reads as
  // clipped, and the reader cannot tell the curve from the chart border
  const powerTop = niceTicks(0, Math.max(...allPower), 4).niceMax;

  // Where the two curves stop coinciding. Below it, override confers no power advantage and
  // the mode is not observable at all (API.md section 3.11 / 20.2) -- that region is shaded
  // rather than left looking like ordinary chart space.
  const separation = separationSpeed(rules);

  const budgets = [
    { key: "ers_store_capacity", label: "Energy store", q: rules.energy_budget.ers_store_capacity },
    { key: "deploy_budget", label: "Deploy budget", q: rules.energy_budget.deploy_budget },
    { key: "harvest_budget", label: "Harvest budget", q: rules.energy_budget.harvest_budget },
  ];

  return (
    <main className={s.main}>
      <header className={s.head}>
        <p className={s.kicker}>Regulation · {rules.competition.configuration_version}</p>
        <h1 className={s.title}>
          2026 <em>Envelope</em>
        </h1>
        <p className={s.lede}>
          Maximum electrical deployment is a function of speed, not a constant. These are the two
          piecewise-linear curves the rule engine evaluates, and the three energy quantities it
          accounts against — drawn from the one configuration every model in the system reads.
        </p>
      </header>

      {!rules.compliance.all_values_verified ? (
        <section className={s.warning} role="note">
          <h2 className={s.warningHead}>Unverified regulation values</h2>
          <p className={s.warningBody}>{rules.compliance.statement}</p>
          <p className={s.warningBody}>
            <strong>{rules.compliance.unverified_keys.length} keys</strong> carry{" "}
            <code>verified: false</code>:{" "}
            {rules.compliance.unverified_keys.map((k) => (
              <code key={k} className={s.keyChip}>
                {k}
              </code>
            ))}
          </p>
          <p className={s.warningBody}>
            <code>legal_by_construction</code> is{" "}
            <strong>{String(rules.compliance.legal_by_construction)}</strong>. Nothing on this page
            should be read as a statement of what the regulations require.
          </p>
        </section>
      ) : null}

      <section className={s.block}>
        <ChartFrame
          title="Speed-dependent power envelope"
          units="kW vs km/h"
          provenance="RULE"
          legend={modes.map((m) => ({ label: m, colour: MODE_COLOUR[m] ?? CHART.series[2] }))}
          note={
            <>
              The curves coincide below{" "}
              {separation === null ? "the separation speed" : `${separation.toFixed(0)} km/h`}: there,
              override confers no power advantage and the mode is not observable from the outside.
              Every value is an unverified reported figure, derived from the formulas transcribed in{" "}
              <code>Math.md §7.2</code> rather than typed in.
            </>
          }
          table={{
            columns: ["mode", "speed km/h", "max power kW"],
            rows: modes.flatMap((m) =>
              rules.power_envelope[m].breakpoints_kmh.map((v, i) => [
                m,
                v,
                rules.power_envelope[m].max_power_kw[i],
              ]),
            ),
          }}
        >
          <Plot
            xDomain={[0, Math.ceil(maxSpeed / 20) * 20]}
            yDomain={[0, powerTop]}
            height={300}
            ariaLabel="Maximum electrical power against car speed, for normal and override modes"
          >
            <Grid />
            {separation !== null ? (
              <XRegion from={0} to={separation} label="mode not discriminable" />
            ) : null}
            <XAxis label="car speed km/h" />
            <YAxis label="max electrical power kW" />
            {separation !== null ? (
              <RefLine x={separation} label={`${separation.toFixed(0)} km/h`} />
            ) : null}
            {modes.map((m) => (
              <Line
                key={m}
                x={rules.power_envelope[m].breakpoints_kmh}
                y={rules.power_envelope[m].max_power_kw}
                colour={MODE_COLOUR[m] ?? CHART.series[2]}
                label={m}
                labelIndex={Math.max(0, rules.power_envelope[m].breakpoints_kmh.length - 2)}
              />
            ))}
          </Plot>
        </ChartFrame>

        <div className={s.derivations}>
          {modes.map((m) => (
            <details key={m} className={s.details}>
              <summary className={s.summary}>
                <span className={s.swatch} style={{ background: MODE_COLOUR[m] ?? CHART.series[2] }} />
                {m} — how this curve was derived
              </summary>
              <p className={s.detailBody}>{rules.power_envelope[m].derivation}</p>
              <p className={s.detailMeta}>
                <span className={s.metaKey}>source</span> {rules.power_envelope[m].source}
              </p>
              <p className={s.detailMeta}>
                <span className={s.metaKey}>citation</span> {rules.power_envelope[m].citation}
              </p>
            </details>
          ))}
        </div>
      </section>

      <section className={s.block}>
        <h2 className={s.sectionTitle}>Three energy quantities</h2>
        <p className={s.sectionLede}>
          Deliberately three different numbers with three different accounting windows, so they
          cannot be silently interchanged. A store capacity is an instantaneous bound; a budget is
          spent over a lap.
        </p>
        <div className={s.quantities}>
          {budgets.map(({ key, label, q }) => (
            <article key={key} className={s.quantity}>
              <h3 className={s.quantityLabel}>{label}</h3>
              <p className={s.quantityValue}>
                {q.value_mj.toFixed(1)} <span className={s.quantityUnit}>MJ</span>
              </p>
              <p className={s.quantityWindow}>{q.accounting_window.replace(/_/g, " ")}</p>
              {!q.verified ? <span className={s.unverified}>unverified</span> : null}
              <p className={s.quantityNote}>{q.note}</p>
            </article>
          ))}
        </div>
      </section>

      <section className={s.block}>
        <h2 className={s.sectionTitle}>Provenance</h2>
        <dl className={s.provList}>
          <div className={s.provRow}>
            <dt>Season</dt>
            <dd>{rules.regulation_snapshot.season}</dd>
          </div>
          <div className={s.provRow}>
            <dt>Configuration</dt>
            <dd>{rules.competition.configuration_version}</dd>
          </div>
          <div className={s.provRow}>
            <dt>Retrieved</dt>
            <dd>{rules.regulation_snapshot.retrieved_at ?? "— never independently retrieved"}</dd>
          </div>
          <div className={s.provRow}>
            <dt>Models speed-dependent envelope</dt>
            <dd>{String(rules.compliance.models_speed_dependent_envelope)}</dd>
          </div>
        </dl>
        <p className={s.sectionLede}>{rules.regulation_snapshot.note}</p>
        <ul className={s.sources}>
          {rules.regulation_snapshot.source_documents.map((d, i) => (
            <li key={i}>{d}</li>
          ))}
        </ul>
      </section>
    </main>
  );
}

/**
 * The lowest speed at which the two curves stop agreeing.
 *
 * Read off the breakpoints rather than computed by evaluating both envelopes across a sweep:
 * evaluating the cap is rules.py's job, and a second implementation here is exactly what
 * AGENTS.md section 32 forbids. Returns null when there are not two modes to compare, in which
 * case the page simply omits the marker rather than inventing one.
 */
function separationSpeed(rules: RuleSet): number | null {
  const normal = rules.power_envelope.normal;
  const override = rules.power_envelope.override;
  if (!normal || !override) return null;
  // Both curves start flat at the same ceiling; they part where the lower one begins to
  // taper, so the separation speed is the last breakpoint still at full power.
  const taper = normal.max_power_kw.findIndex((p, i) => i > 0 && p < normal.max_power_kw[i - 1]);
  return taper > 0 ? normal.breakpoints_kmh[taper - 1] : null;
}
