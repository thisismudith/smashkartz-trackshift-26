/**
 * Track landmarks and the power envelope: GET /track/{event}, GET /rules/{event},
 * GET /rules/{event}/power_envelope.
 *
 * Three things this panel is careful about.
 *
 * 1. LANDMARK PROVENANCE IS PER-LANDMARK. `app.py` copies each rule's `value_source` into
 *    the line's `provenance`, so the Detection Lines here arrive as `DERIVED_TELEMETRY` —
 *    positioned by aligning the FIA circuit map to corner distances measured in our own
 *    telemetry. That is a different and weaker claim than a figure quoted from an article,
 *    and a different claim again from the Detection *Gap*, which is `UNVERIFIED`: a
 *    carried-over 1.0 s with no 2026 citation. Flattening all three to "RULE" would erase
 *    exactly the distinction the reader needs, so each line shows its own tag and its own
 *    source string.
 *
 * 2. NO DRS ANYWHERE. There is no DRS row in this panel and no way to produce one. The
 *    explainer states the rule positively — an all-zero 2026 DRS channel means the channel
 *    is unavailable, not that a flap is closed — because "closed" is the reading someone
 *    arrives at on their own when they see a column of zeros.
 *
 * 3. THE ENVELOPE IS A CURVE. API.md 20.1 forbids presenting a peak kW as "the" power
 *    limit. The live route samples the curve with the same C3 evaluator the optimiser uses.
 *    The replay bundle has no such file, so replay mode falls back to the rule breakpoints
 *    and says so — the breakpoints define the piecewise-linear curve exactly, but they are
 *    labelled as breakpoints so nobody mistakes the fallback for the sampled route.
 */
"use client";

import { ProvenanceBadge, Value } from "@/components/provenance/Provenance";
import { formatRuleValue } from "@/serve/format";
import type { Result } from "@/serve/guards";
import type { EnvelopeCurveSpec, PowerEnvelopeResponse, RulesResponse, TrackResponse } from "@/serve/types";
import { Callout, ErrorState, Loading, Panel } from "./panels";
import s from "./integration.module.css";

interface CurvePoint {
  speed_kmh: number;
  max_power_kw: number;
}

/** Straight lines between breakpoints — the exact shape a piecewise-linear curve has. */
function fromBreakpoints(c: EnvelopeCurveSpec | undefined): CurvePoint[] {
  if (!c?.breakpoints_kmh || !c.max_power_kw) return [];
  return c.breakpoints_kmh.map((speed_kmh, i) => ({ speed_kmh, max_power_kw: c.max_power_kw[i] ?? 0 }));
}

function EnvelopePlot({ curves }: { curves: Record<string, CurvePoint[]> }) {
  const all = Object.values(curves).flat();
  if (all.length === 0) return null;
  const maxSpeed = Math.max(...all.map((p) => p.speed_kmh), 1);
  const maxPower = Math.max(...all.map((p) => p.max_power_kw), 1);
  const W = 560;
  const H = 200;
  const PAD = { l: 46, r: 12, t: 12, b: 30 };
  const x = (v: number) => PAD.l + (v / maxSpeed) * (W - PAD.l - PAD.r);
  const y = (v: number) => H - PAD.b - (v / maxPower) * (H - PAD.t - PAD.b);
  const colours: Record<string, string> = { normal: "#3A63D6", override: "#DA291C" };

  return (
    <figure className={s.figure}>
      <svg viewBox={`0 0 ${W} ${H}`} className={s.svg} role="img" aria-label="Maximum electrical power against speed, for normal and Overtake modes">
        <line x1={PAD.l} y1={H - PAD.b} x2={W - PAD.r} y2={H - PAD.b} stroke="#5A5A5A" strokeWidth="1" />
        <line x1={PAD.l} y1={PAD.t} x2={PAD.l} y2={H - PAD.b} stroke="#5A5A5A" strokeWidth="1" />
        {[0, 0.5, 1].map((f) => (
          <text key={f} x={PAD.l - 6} y={y(maxPower * f) + 4} textAnchor="end" className={s.axisText}>
            {Math.round(maxPower * f)}
          </text>
        ))}
        {[0, 0.5, 1].map((f) => (
          <text key={f} x={x(maxSpeed * f)} y={H - PAD.b + 16} textAnchor="middle" className={s.axisText}>
            {Math.round(maxSpeed * f)}
          </text>
        ))}
        {Object.entries(curves).map(([name, pts]) => (
          <polyline
            key={name}
            points={pts.map((p) => `${x(p.speed_kmh)},${y(p.max_power_kw)}`).join(" ")}
            fill="none"
            stroke={colours[name] ?? "#AEAEAE"}
            strokeWidth="2"
            strokeDasharray={name === "override" ? "5 3" : undefined}
          />
        ))}
        <text x={W - PAD.r} y={H - 4} textAnchor="end" className={s.axisText}>km/h</text>
        <text x={4} y={PAD.t + 4} className={s.axisText}>kW</text>
      </svg>
      <figcaption className={s.figcaption}>
        {Object.keys(curves).map((name) => (
          <span key={name} className={s.legendChip}>
            <span className={s.legendSwatch} style={{ background: colours[name] ?? "#AEAEAE" }} />
            {name}
          </span>
        ))}
        <span className={s.figNote}>
          Power is a function of speed, not a single number. The two curves coincide below the
          separation speed, where Overtake confers no power advantage and the mode is not observable.
        </span>
      </figcaption>
    </figure>
  );
}

export function TrackRulePanel({
  track,
  rules,
  envelope,
}: {
  track: Result<TrackResponse> | null;
  rules: Result<RulesResponse> | null;
  envelope: Result<PowerEnvelopeResponse> | null;
}) {
  const sampled = envelope?.ok
    ? Object.fromEntries(
        Object.entries(envelope.data.curves).map(([k, v]) => [k, v.map((p) => ({ speed_kmh: p.speed_kmh, max_power_kw: p.max_power_kw }))]),
      )
    : null;

  const fallback =
    !sampled && rules?.ok
      ? {
          normal: fromBreakpoints(rules.data.power_envelope?.normal),
          override: fromBreakpoints(rules.data.power_envelope?.override),
        }
      : null;

  const lines = track?.ok ? (track.data.lines ?? []) : [];

  return (
    <Panel
      id="track"
      kicker="Area 3 · GET /track/{event} · GET /rules/{event}/power_envelope"
      title="Track and rule panel"
    >
      <Callout tone="warn">
        <strong>No DRS is rendered as an Overtake state.</strong> 2026 Overtake comes only from the
        rule engine; <code>drs</code> and <code>historical_drs_*</code> are refused both ways.
      </Callout>

      {track === null ? <Loading what="track geometry" /> : null}
      {track && !track.ok ? <ErrorState result={track} what="Could not load track geometry." /> : null}

      {track?.ok ? (
        <>
          <h3 className={s.subhead}>Detection and Activation landmarks</h3>
          {lines.length === 0 ? (
            <p className={s.empty}>The response carried no lines, so no landmark is drawn.</p>
          ) : (
            <div className={s.tableWrap}>
              <table className={s.table}>
                <caption className={s.caption}>
                  Provenance per landmark, citation on hover — they are not the same kind of claim.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Kind</th>
                    <th scope="col">Zone</th>
                    <th scope="col">Distance</th>
                    <th scope="col">Provenance</th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((l, i) => (
                    <tr key={`${l.kind}-${l.zone}-${i}`}>
                      <th scope="row">{l.kind}</th>
                      <td>{l.zone ?? "—"}</td>
                      <td>
                        <Value
                          d={formatRuleValue({ value: l.distance_m, value_source: undefined }, { unit: "m", digits: 1 })}
                          source={l.source}
                        />
                      </td>
                      <td>
                        <ProvenanceBadge tag={l.provenance} source={l.source} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : null}

      {rules?.ok ? (
        <>
          <h3 className={s.subhead}>Overtake thresholds</h3>
          <dl className={s.fields}>
            <div className={s.field}>
              <dt className={s.label}>Detection gap</dt>
              <dd className={s.data}>
                <Value
                  d={formatRuleValue(rules.data.overtake?.detection_gap_s, { unit: "s", digits: 2 })}
                  source={rules.data.overtake?.detection_gap_s?.source}
                />
                {rules.data.overtake?.detection_gap_s?.note ? (
                  <span className={s.note}>{rules.data.overtake.detection_gap_s.note}</span>
                ) : null}
              </dd>
            </div>
            <div className={s.field}>
              <dt className={s.label}>Overtake enabled</dt>
              <dd className={s.data}>
                {rules.data.overtake?.enabled === undefined ? (
                  <abbr className={s.na} title="The configuration does not state this; it is never defaulted to enabled.">Unavailable</abbr>
                ) : (
                  <>
                    {String(rules.data.overtake.enabled)}
                    <ProvenanceBadge tag="RULE" />
                  </>
                )}
              </dd>
            </div>
            <div className={s.field}>
              <dt className={s.label}>Configuration version</dt>
              <dd className={s.data}>
                <code>{rules.data.config_version ?? "Unavailable"}</code>
              </dd>
            </div>
            {rules.data.regulation_snapshot ? (
              <div className={s.field}>
                <dt className={s.label}>Regulation snapshot</dt>
                <dd className={s.data}>
                  effective {rules.data.regulation_snapshot.effective_date ?? "?"}, retrieved{" "}
                  {rules.data.regulation_snapshot.retrieved_at ?? "?"}
                  <span className={s.note}>{rules.data.regulation_snapshot.note}</span>
                </dd>
              </div>
            ) : null}
          </dl>

          <h3 className={s.subhead}>Zones</h3>
          <div className={s.tableWrap}>
            <table className={s.table}>
              <thead>
                <tr>
                  <th scope="col">Zone</th>
                  <th scope="col">Detection line</th>
                  <th scope="col">Activation line</th>
                  <th scope="col">Zone end</th>
                </tr>
              </thead>
              <tbody>
                {(rules.data.overtake?.zones ?? []).map((z) => (
                  <tr key={String(z.zone)}>
                    <th scope="row">
                      {z.zone}
                      {z.name ? <span className={s.zoneName}>{z.name}</span> : null}
                    </th>
                    <td>
                      <Value d={formatRuleValue(z.detection_line_m, { unit: "m", digits: 1 })} source={z.detection_line_m?.source} />
                    </td>
                    <td>
                      <Value d={formatRuleValue(z.activation_line_m, { unit: "m", digits: 1 })} source={z.activation_line_m?.source} />
                    </td>
                    <td>
                      <Value d={formatRuleValue(z.zone_end_m, { unit: "m", digits: 1 })} source={z.zone_end_m?.source} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      <h3 className={s.subhead}>Power envelope</h3>
      {envelope === null ? <Loading what="the sampled power envelope" /> : null}
      {envelope && !envelope.ok ? (
        <>
          <ErrorState result={envelope} what="Could not load the sampled power envelope." />
          {fallback && (fallback.normal.length > 0 || fallback.override.length > 0) ? (
            <>
              <Callout>
                Drawn from the <strong>rule breakpoints</strong> in <code>/rules/{"{event}"}</code>:
                the same shape, but not the sampled route.
              </Callout>
              <EnvelopePlot curves={fallback} />
            </>
          ) : null}
        </>
      ) : null}
      {envelope?.ok && sampled ? (
        <>
          <EnvelopePlot curves={sampled} />
          <p className={s.meta}>
            Sampled by the C3 evaluator the DP uses — the line the optimiser saw.{" "}
            <ProvenanceBadge tag={envelope.data.provenance} />
            {envelope.data.rule_configuration_version ? <code className={s.inlineCode}>{envelope.data.rule_configuration_version}</code> : null}
          </p>
        </>
      ) : null}
      {rules?.ok && rules.data.power_envelope?.normal?.source ? (
        <p className={s.sourceNote}>
          <strong>Source:</strong> {rules.data.power_envelope.normal.source}
        </p>
      ) : null}
    </Panel>
  );
}

export default TrackRulePanel;
