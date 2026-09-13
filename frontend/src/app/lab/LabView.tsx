/**
 * The parameter lab.
 *
 * What is real here today: every input the race model takes, with the value it was fitted to,
 * its standard error, its 95% interval, its sample count and the note explaining what was
 * actually estimated. That inventory is the honest half of "parameters you can change" — you
 * can see exactly what there is to change and how well the data pins each one down.
 *
 * Calculator A — the power envelope — is live. It reads the sampled table that rules.py wrote
 * into the rules artifact and looks the cap up; it does not interpolate. That is the point: the
 * curve has exactly one implementation in the system (AGENTS.md section 32), so the kW on screen
 * is provably the kW the optimiser saw rather than a browser's second opinion about the same
 * regulation.
 *
 * What is NOT here yet: the rest of the answers. Moving a slider and getting a stint curve back
 * means evaluating the lap model, and that model lives in Python (AGENTS.md section 25 — the
 * browser never computes physics). The route from here is a precomputed grid emitted by
 * scripts/simdata/whatif.py, which the UI reads as a lookup rather than a calculation
 * (UI.md section 6.3). Until that ships, the output panels state what they are waiting for
 * instead of showing a number nobody computed.
 */
"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { defaultSimSource } from "@/sim/data/source";
import type { RuleSet } from "@/sim/data/source";
import type { FittedParams, Leaf } from "@/sim/engine/params";
import {
  AwaitingModel,
  ChartFrame,
  Dots,
  Grid,
  Line,
  NoData,
  Plot,
  RefLine,
  XAxis,
  XRegion,
  YAxis,
} from "@/sim/charts";
import { Segmented, type SegmentedOption } from "@/components/ui";
import s from "./lab.module.css";

interface Group {
  name: string;
  blurb: string;
  rows: { label: string; leaf: Leaf; unit: string }[];
}

export default function LabView() {
  const [params, setParams] = useState<FittedParams | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [track, setTrack] = useState<string>("British Grand Prix");

  useEffect(() => {
    let live = true;
    defaultSimSource
      .params()
      .then((p) => live && setParams(p))
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, []);

  const tracks = useMemo(
    () => (params ? Object.keys(params.sessionPaceTrendPerLap.perTrack).sort() : []),
    [params],
  );

  const groups = useMemo<Group[]>(() => {
    if (!params) return [];
    const t = track;
    const g: Group[] = [
      {
        name: "Pace",
        blurb: "Fuel burn + track evolution, inseparable in lap data.",
        rows: [
          {
            label: "Session pace trend",
            leaf: params.sessionPaceTrendPerLap.perTrack[t] ?? params.sessionPaceTrendPerLap.pooled,
            unit: "s per lap",
          },
        ],
      },
      {
        name: "Proximity & overtaking",
        blurb: "Dirty air, on the gap at lap start. Doubles as overtaking difficulty.",
        rows: [
          {
            label: "Dirty-air loss",
            leaf: params.dirtyAirLossPerSecondOfProximity[t],
            unit: "s per s of proximity",
          },
        ],
      },
      {
        name: "Noise",
        blurb: "Normal core + rare incident tail.",
        rows: [
          { label: "Core sigma", leaf: params.noise.coreSigma, unit: "s" },
          { label: "Incident probability", leaf: params.noise.incidentProbability, unit: "per lap" },
          { label: "Incident mean excess", leaf: params.noise.incidentMeanExcessSeconds, unit: "s" },
        ],
      },
    ];
    return g.map((grp) => ({ ...grp, rows: grp.rows.filter((r) => r.leaf && Number.isFinite(r.leaf.value)) }));
  }, [params, track]);

  return (
    <main className={s.main}>
      <header className={s.head}>
        <p className={s.kicker}>What-if · inputs</p>
        <h1 className={s.title}>
          Parameter <em>Lab</em>
        </h1>
        <p className={s.lede}>
          Every model input, with its fitted spread. A wide interval is a parameter the data
          barely pins down.
        </p>
      </header>

      {/* The fitted-parameter inventory and the envelope calculator come from different
          artifacts. They are rendered independently so a failure to load one never takes the
          other off the page -- the envelope needs no fitted parameter at all. */}
      {params ? (
        <>
          <div className={s.trackRow}>
            <label className={s.trackLabel} htmlFor="lab-track">
              Circuit
            </label>
            <select
              id="lab-track"
              className={s.trackSelect}
              value={track}
              onChange={(e) => setTrack(e.target.value)}
            >
              {tracks.map((t) => (
                <option key={t} value={t}>
                  {t.replace(/\s+Grand Prix$/i, "")}
                </option>
              ))}
            </select>
          </div>

          <section className={s.groups}>
            {groups.map((g) => (
              <article key={g.name} className={s.group}>
                <h2 className={s.groupName}>{g.name}</h2>
                <p className={s.groupBlurb}>{g.blurb}</p>
                <ul className={s.rows}>
                  {g.rows.map((r) => (
                    <ParamRow key={r.label} label={r.label} leaf={r.leaf} unit={r.unit} />
                  ))}
                </ul>
              </article>
            ))}
          </section>
        </>
      ) : (
        <p className={s.lede}>
          {error ? `Could not load fitted parameters: ${error}` : "Loading fitted parameters…"}
        </p>
      )}

      <EnvelopeCalculator />

      <section className={s.gated}>
        <h2 className={s.sectionTitle}>Answers</h2>
        <p className={s.sectionLede}>
          Waiting on Python. Each arrives as a lookup table, like the calculator above.
        </p>
        <div className={s.gatedGrid}>
          <AwaitingModel
            title="Eligibility & energy-to-unlock"
            model="M21 — eligibility probability (blocked on M18)"
            route="POST /rules/eligibility"
            detail={
              <>
                Given a gap and a closing rate, how much energy does it take to be inside the
                threshold at the Detection Line, and how fragile is that margin per kilojoule.
                Blocked on something no code fixes: the Detection and Activation line positions are
                not in the raw telemetry and must come from FIA event documents.
              </>
            }
          />
          <AwaitingModel
            title="Energy shadow price"
            model="M22 (dynamic programming)"
            route="GET /value/{event}/shadow_price"
            detail={
              <>
                What a joule is worth at each point on the lap. The track renderer already accepts
                a per-distance value and colours the ribbon with it; nothing produces the values yet.
              </>
            }
          />
        </div>
      </section>
    </main>
  );
}

function ParamRow({ label, leaf, unit }: { label: string; leaf: Leaf; unit: string }) {
  const ci = leaf.ci95;
  // An interval spanning zero means the data does not distinguish this effect from none.
  const crossesZero = ci ? ci[0] <= 0 && ci[1] >= 0 : false;
  return (
    <li className={s.row}>
      <div className={s.rowHead}>
        <span className={s.rowLabel}>{label}</span>
        <span className={s.rowValue}>
          {fmt(leaf.value)}
          <span className={s.rowUnit}>{unit}</span>
        </span>
      </div>
      <div className={s.rowMeta}>
        {ci ? (
          <span data-weak={crossesZero ? "true" : undefined} className={s.ci}>
            95% {fmt(ci[0])} … {fmt(ci[1])}
            {crossesZero ? " · crosses zero" : ""}
          </span>
        ) : (
          <span className={s.ci}>no interval fitted</span>
        )}
        {leaf.se !== undefined ? <span>se {fmt(leaf.se)}</span> : null}
        {leaf.n !== undefined ? <span>n={leaf.n.toLocaleString()}</span> : null}
        <span className={s.prov}>{leaf.provenance}</span>
      </div>
      {leaf.note ? <p className={s.rowNote}>{leaf.note}</p> : null}
    </li>
  );
}

function fmt(v: number): string {
  const a = Math.abs(v);
  if (a === 0) return "0";
  if (a < 0.001) return v.toExponential(2);
  if (a < 1) return v.toFixed(4);
  if (a < 100) return v.toFixed(3);
  return v.toFixed(1);
}

/* ------------------------------------------------------ envelope calculator --- */

/** Two series, two of the three validated chromatic hues (UI.md section 7.3). */
const NORMAL_COLOUR = "#3A63D6";
const OVERRIDE_COLOUR = "#DA291C";
const DEPLOY_COLOUR = "#A88606";

type Mode = "normal" | "override";

const MODE_OPTIONS: SegmentedOption<Mode>[] = [
  { value: "normal", label: "Normal", title: "The ordinary electrical power limit" },
  { value: "override", label: "Override", title: "The override (attack) electrical power limit" },
];

/**
 * Calculator A (UI.md section 6.4) — the modelled electrical power cap at a speed.
 *
 * Every kW here is LOOKED UP in the table rules.py sampled from `max_electrical_power_kw`.
 * Interpolating between two rows in the browser would be the second implementation of the
 * curve that AGENTS.md section 32 forbids, and the readout would stop being the number the
 * optimiser saw. The price is that the speed snaps to a sampled point, which the panel says
 * out loud whenever the typed speed is not one.
 */
function EnvelopeCalculator() {
  const [rules, setRules] = useState<RuleSet | null | undefined>(undefined);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("override");
  const [speed, setSpeed] = useState(310);
  const [speedText, setSpeedText] = useState("310");
  const [deployText, setDeployText] = useState("250");
  const speedId = useId();
  const deployId = useId();

  useEffect(() => {
    let live = true;
    defaultSimSource
      .rules()
      .then((r) => {
        if (live) setRules(r);
      })
      .catch((e: unknown) => {
        if (!live) return;
        setLoadError(e instanceof Error ? e.message : String(e));
        setRules(null);
      });
    return () => {
      live = false;
    };
  }, []);

  const table = rules?.sampled_curves ?? null;

  // Both modes share one speed grid, so the two caps at a speed are the same row index.
  const grid = useMemo(() => {
    const normal = table?.curves?.normal;
    const override = table?.curves?.override;
    if (!normal?.length || !override?.length || normal.length !== override.length) return null;
    const speeds = normal.map((p) => p.speed_kmh);
    const normalKw = normal.map((p) => p.max_power_kw);
    const overrideKw = override.map((p) => p.max_power_kw);
    return {
      speeds,
      normalKw,
      overrideKw,
      maxSpeed: speeds[speeds.length - 1],
      peakKw: Math.max(...normalKw, ...overrideKw),
    };
  }, [table]);

  if (rules === undefined) {
    return (
      <section className={s.calc}>
        <h2 className={s.sectionTitle}>Envelope calculator</h2>
        <p className={s.sectionLede}>Loading the sampled envelope table…</p>
      </section>
    );
  }
  if (!rules) {
    return (
      <section className={s.calc}>
        <h2 className={s.sectionTitle}>Envelope calculator</h2>
        <NoData
          title="Modelled electrical power cap"
          reason={
            loadError
              ? `The rules artifact could not be read: ${loadError}`
              : "The built artifacts predate the rule engine, so this build carries no envelope at all. No cap is shown rather than one invented here."
          }
        />
      </section>
    );
  }
  if (!table || !grid) {
    return (
      <section className={s.calc}>
        <h2 className={s.sectionTitle}>Envelope calculator</h2>
        <NoData
          title="Modelled electrical power cap"
          reason={
            <>
              The rules artifact carries the breakpoints but no sampled table, and reading a cap
              between breakpoints is rules.py&apos;s job, not the browser&apos;s. Rebuild with{" "}
              <code>python scripts/build_sim_data.py --catalogue-only</code>.
            </>
          }
        />
      </section>
    );
  }

  // --- lookup, never interpolation ---
  const i = nearestSampleIndex(grid.speeds, speed);
  const sampledSpeed = grid.speeds[i];
  const normalKw = grid.normalKw[i];
  const overrideKw = grid.overrideKw[i];
  const capKw = mode === "override" ? overrideKw : normalKw;
  const otherKw = mode === "override" ? normalKw : overrideKw;
  const otherLabel = mode === "override" ? "Normal" : "Override";
  const snapped = Math.abs(sampledSpeed - speed) > 1e-9;

  const deployKw = Number(deployText);
  const deployOk = deployText.trim() !== "" && Number.isFinite(deployKw) && deployKw >= 0;
  const headroomKw = deployOk ? capKw - deployKw : null;

  const separation = table.separation_speed_kmh;
  // Read the verdict off the table itself rather than off the speed, so the sentence can never
  // disagree with the two numbers printed beside it.
  const indistinguishable = Math.abs(overrideKw - normalKw) <= 1e-9;
  const envelopeKeys = rules.compliance.unverified_keys.filter((k) => k.startsWith("power_envelope"));
  const labelAt = nearestSampleIndex(grid.speeds, grid.maxSpeed * 0.88);

  return (
    <section className={s.calc}>
      <h2 className={s.sectionTitle}>Envelope calculator</h2>
      <p className={s.sectionLede}>
        Electrical power cap vs speed, read off Python&apos;s sampled table — never interpolated
        here, so this is the kW the optimiser saw. Grid: {fmtSpeed(table.step_kmh)} km/h steps to{" "}
        {fmtSpeed(grid.maxSpeed)}, plus breakpoints.
      </p>

      <div className={s.badges}>
        <span className={s.badge} data-kind="rule">
          RULE
        </span>
        <span className={s.badge} data-kind="unverified">
          UNVERIFIED
        </span>
        <span className={s.badgeNote}>
          {envelopeKeys.length > 0
            ? `${envelopeKeys.join(", ")} carry verified: false — reported values, not verified regulation`
            : "reported values, not verified regulation"}
        </span>
      </div>

      <div className={s.calcGrid}>
        <div className={s.calcControls}>
          <div className={s.control}>
            <span className={s.controlLabel}>Deployment mode</span>
            <Segmented label="Deployment mode" options={MODE_OPTIONS} value={mode} onChange={setMode} />
          </div>

          <div className={s.control}>
            <label className={s.controlLabel} htmlFor={speedId}>
              Speed · km/h
            </label>
            <div className={s.sliderRow}>
              <input
                type="range"
                className={s.slider}
                min={0}
                max={grid.maxSpeed}
                step={table.step_kmh}
                value={sampledSpeed}
                aria-label="Speed in kilometres per hour"
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setSpeed(v);
                  setSpeedText(fmtSpeed(v));
                }}
              />
              <input
                id={speedId}
                type="number"
                className={s.num}
                min={0}
                max={grid.maxSpeed}
                step={table.step_kmh}
                value={speedText}
                onChange={(e) => {
                  setSpeedText(e.target.value);
                  const v = Number(e.target.value);
                  if (e.target.value.trim() !== "" && Number.isFinite(v)) setSpeed(v);
                }}
                // On blur the box shows the speed actually looked up, so the field and the
                // readout stop disagreeing once the user has finished typing.
                onBlur={() => {
                  setSpeed(sampledSpeed);
                  setSpeedText(fmtSpeed(sampledSpeed));
                }}
              />
            </div>
            {snapped ? (
              <p className={s.snap}>
                Read at {fmtSpeed(sampledSpeed)} km/h — the nearest sampled speed. Nothing here
                interpolates between samples.
              </p>
            ) : null}
          </div>

          <div className={s.control}>
            <label className={s.controlLabel} htmlFor={deployId}>
              Deploy power · kW <span className={s.yours}>your input</span>
            </label>
            <input
              id={deployId}
              type="number"
              className={s.num}
              min={0}
              step={10}
              value={deployText}
              onChange={(e) => setDeployText(e.target.value)}
            />
          </div>
        </div>

        <div className={s.readouts}>
          <div className={s.readout}>
            <span className={s.readoutLabel}>
              {mode === "override" ? "Override" : "Normal"} cap at {fmtSpeed(sampledSpeed)} km/h
            </span>
            <span className={s.readoutValue}>
              {fmtKw(capKw)}
              <span className={s.readoutUnit}>kW</span>
            </span>
          </div>

          <div className={s.readout}>
            <span className={s.readoutLabel}>{otherLabel} cap, same speed</span>
            <span className={s.readoutValueSmall}>
              {fmtKw(otherKw)}
              <span className={s.readoutUnit}>kW</span>
            </span>
          </div>

          <div className={s.readout}>
            <span className={s.readoutLabel}>Headroom under the cap</span>
            {headroomKw === null ? (
              <span className={s.readoutMissing}>unavailable — enter a deploy power</span>
            ) : (
              <span className={s.readoutValueSmall} data-over={headroomKw < 0 ? "true" : undefined}>
                {headroomKw >= 0 ? "+" : "−"}
                {fmtKw(Math.abs(headroomKw))}
                <span className={s.readoutUnit}>kW</span>
              </span>
            )}
          </div>

          {headroomKw !== null ? (
            <p className={s.calcNote}>
              {headroomKw >= 0
                ? `Deploying ${fmtKw(deployKw)} kW here leaves ${fmtKw(headroomKw)} kW under the modelled cap.`
                : `Deploying ${fmtKw(deployKw)} kW here is ${fmtKw(-headroomKw)} kW above the modelled cap — the model would clip it.`}
            </p>
          ) : null}

          <p className={indistinguishable ? s.calcFlat : s.calcNote}>
            {indistinguishable ? (
              separation === null ? (
                <>
                  The two modelled curves never differ at any speed, so mode is not discriminable
                  anywhere and override confers no advantage.
                </>
              ) : sampledSpeed <= separation ? (
                <>
                  At {fmtSpeed(sampledSpeed)} km/h the two modes are{" "}
                  <strong>indistinguishable</strong>: both sample to {fmtKw(capKw)} kW. Override
                  confers no advantage at or below {fmtSpeed(separation)} km/h — the curves part
                  only above it.
                </>
              ) : (
                // Past the separation speed the curves can still meet -- both run out at the top
                // end -- and the shared-region sentence would be a lie here.
                <>
                  At {fmtSpeed(sampledSpeed)} km/h the two modes sample to the same{" "}
                  {fmtKw(capKw)} kW, so <strong>override confers nothing here either</strong>. This
                  is not the shared region, though: the curves do differ above{" "}
                  {fmtSpeed(separation)} km/h and merely meet again at this speed.
                </>
              )
            ) : (
              <>
                Override allows <strong>{fmtKw(overrideKw - normalKw)} kW</strong> more than normal
                here ({fmtKw(overrideKw)} against {fmtKw(normalKw)} kW).
              </>
            )}
          </p>
        </div>
      </div>

      <ChartFrame
        title="Modelled electrical power cap against speed"
        units="kW · km/h"
        provenance="RULE"
        legend={[
          { label: "Normal", colour: NORMAL_COLOUR },
          { label: "Override", colour: OVERRIDE_COLOUR },
        ]}
        note={
          <>
            The vertices are the configured breakpoints and the segments between them are what
            rules.py evaluates, so this is the curve the optimiser reads.{" "}
            {rules.compliance.statement} {rules.power_envelope[mode]?.derivation ?? ""}
          </>
        }
        table={{
          columns: ["km/h", "normal kW", "override kW"],
          rows: grid.speeds.map((v, k) => [
            fmtSpeed(v),
            fmtKw(grid.normalKw[k]),
            fmtKw(grid.overrideKw[k]),
          ]),
        }}
      >
        <Plot
          xDomain={[0, grid.maxSpeed]}
          yDomain={[0, grid.peakKw * 1.12]}
          height={260}
          ariaLabel={`Electrical power cap against speed for both deployment modes. At ${fmtSpeed(sampledSpeed)} kilometres per hour the ${mode} cap is ${fmtKw(capKw)} kilowatts.`}
        >
          <Grid />
          {separation !== null ? <XRegion from={0} to={separation} label="modes identical" /> : null}
          <Line
            x={grid.speeds}
            y={grid.overrideKw}
            colour={OVERRIDE_COLOUR}
            label="OVERRIDE"
            labelIndex={labelAt}
          />
          <Line
            x={grid.speeds}
            y={grid.normalKw}
            colour={NORMAL_COLOUR}
            label="NORMAL"
            labelIndex={labelAt}
          />
          {deployOk ? (
            <RefLine y={deployKw} colour={DEPLOY_COLOUR} label={`deploy ${fmtKw(deployKw)} kW`} />
          ) : null}
          <RefLine x={sampledSpeed} label={`${fmtSpeed(sampledSpeed)} km/h`} />
          <Dots
            x={[sampledSpeed]}
            y={[capKw]}
            colour={mode === "override" ? OVERRIDE_COLOUR : NORMAL_COLOUR}
          />
          <XAxis label="speed · km/h" />
          <YAxis label="cap · kW" />
        </Plot>
      </ChartFrame>
    </section>
  );
}

/** Index of the sampled speed nearest `v`. Linear scan over ~74 rows, once per render.
 * This is the ONLY way a cap is obtained here: no value between two rows is ever computed. */
function nearestSampleIndex(speeds: readonly number[], v: number): number {
  let best = 0;
  let bestD = Infinity;
  for (let k = 0; k < speeds.length; k++) {
    const d = Math.abs(speeds[k] - v);
    if (d < bestD) {
      bestD = d;
      best = k;
    }
  }
  return best;
}

function fmtKw(v: number): string {
  return v.toFixed(1);
}

/** Breakpoints are not all integers — the normal-mode cliff sits at 339.999 km/h — so a speed
 * prints at whatever precision it actually has rather than being rounded into a neighbour. */
function fmtSpeed(v: number): string {
  return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(3)));
}
