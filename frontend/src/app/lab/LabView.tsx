/**
 * The parameter lab.
 *
 * What is real here today: every input the race model takes, with the value it was fitted to,
 * its standard error, its 95% interval, its sample count and the note explaining what was
 * actually estimated. That inventory is the honest half of "parameters you can change" — you
 * can see exactly what there is to change and how well the data pins each one down.
 *
 * What is NOT here yet: the answers. Moving a slider and getting a stint curve back means
 * evaluating the lap model, and that model lives in Python (AGENTS.md section 25 — the browser
 * never computes physics). The route from here is a precomputed grid emitted by
 * scripts/simdata/whatif.py, which the UI reads as a lookup rather than a calculation
 * (UI.md section 6.3). Until that ships, the output panels state what they are waiting for
 * instead of showing a number nobody computed.
 */
"use client";

import { useEffect, useMemo, useState } from "react";
import { defaultSimSource } from "@/sim/data/source";
import type { FittedParams, Leaf } from "@/sim/engine/params";
import { AwaitingModel } from "@/sim/charts";
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
        blurb:
          "How lap time moves through a session independently of the tyre. Fuel burn and track evolution are collinear in lap number and cannot be separated from lap data alone, so the artifact ships their sum and says so.",
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
        blurb:
          "The dirty-air term, measured on the gap at the start of the lap. It doubles as the per-circuit overtaking-difficulty index.",
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
        blurb:
          "Two components: a Normal core for ordinary lap-to-lap scatter, and a rare incident tail. A single Gaussian would understate how often a lap goes badly wrong.",
        rows: [
          { label: "Core sigma", leaf: params.noise.coreSigma, unit: "s" },
          { label: "Incident probability", leaf: params.noise.incidentProbability, unit: "per lap" },
          { label: "Incident mean excess", leaf: params.noise.incidentMeanExcessSeconds, unit: "s" },
        ],
      },
    ];
    return g.map((grp) => ({ ...grp, rows: grp.rows.filter((r) => r.leaf && Number.isFinite(r.leaf.value)) }));
  }, [params, track]);

  if (error) {
    return (
      <main className={s.main}>
        <p className={s.lede}>Could not load fitted parameters: {error}</p>
      </main>
    );
  }
  if (!params) {
    return (
      <main className={s.main}>
        <p className={s.lede}>Loading fitted parameters…</p>
      </main>
    );
  }

  return (
    <main className={s.main}>
      <header className={s.head}>
        <p className={s.kicker}>What-if · inputs</p>
        <h1 className={s.title}>
          Parameter <em>Lab</em>
        </h1>
        <p className={s.lede}>
          Every input the race model takes, with the spread it was fitted to. A parameter whose
          interval is wide is one the data barely pins down — worth knowing before you lean on an
          answer that depends on it.
        </p>
      </header>

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

      <section className={s.gated}>
        <h2 className={s.sectionTitle}>Answers</h2>
        <p className={s.sectionLede}>
          These are the questions the lab is meant to answer instantly. Each needs the lap model
          evaluated, which happens in Python — the browser never computes physics. The delivery
          route is a precomputed grid the UI reads as a lookup table, so a slider still responds
          immediately and offline.
        </p>
        <div className={s.gatedGrid}>
          <AwaitingModel
            title="Envelope calculator"
            model="sampled power-envelope table"
            route="GET /rules/{event}/power_envelope"
            detail={
              <>
                Reading the cap at an arbitrary speed means interpolating between breakpoints, and
                there is exactly one implementation of that curve in the system — <code>rules.py</code>
                &apos;s <code>max_electrical_power_kw</code>. Sampling it into the rules artifact keeps
                the number the UI shows provably the number the optimiser saw.
              </>
            }
          />
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
