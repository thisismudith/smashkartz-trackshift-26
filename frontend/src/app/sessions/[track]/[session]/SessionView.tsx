/**
 * One session, three views over the same manifest.
 *
 * All three read ONLY the session manifest -- per-lap timing, stints, positions and the energy
 * twin's per-lap estimates are all already in it, so the whole page is one 300-750 KB fetch
 * with no binary decode. The telemetry-trace and battle views are not here because they need
 * the sample blob and the gap channel respectively (UI.md sections 4.5, 4.6, 6.1, 6.2).
 *
 * Energy values are SIMULATED estimates from the twin, never measurements. They are labelled
 * "estimated" everywhere and never called battery or SOC (API.md section 8).
 */
"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { defaultSimSource } from "@/sim/data/source";
import type { RawSessionManifest, RawDriverEntry, RawLapEntry } from "@/sim/data/manifest";
import type { Catalogue } from "@/sim/data/catalogue";
import { driverStyles, teamColour } from "@/sim/data/catalogue";
import {
  ChartFrame, NoData, Plot, Grid, XAxis, YAxis, Line, Band, Dots, XRegion, RefLine,
  niceTicks, extent, useSeriesToggle,
} from "@/sim/charts";
import { CHART } from "@/lib/palette";
import { MultiSelect, type SelectOption } from "@/components/filters";
import s from "./session.module.css";

type View = "pace" | "energy" | "overtake";

interface DriverStyle {
  colour: string;
  dashed: boolean;
}

const VIEWS: { key: View; label: string }[] = [
  { key: "energy", label: "Energy & ERS" },
  { key: "overtake", label: "Overtaking" },
  { key: "pace", label: "Pace & progression" },
];

export default function SessionView({
  track,
  session,
  initialView = "energy",
}: {
  track: string;
  session: string;
  initialView?: View;
}) {
  const [manifest, setManifest] = useState<RawSessionManifest | null>(null);
  const [cat, setCat] = useState<Catalogue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setViewState] = useState<View>(initialView);

  // ?view= keeps a view linkable without making each one its own route, which would re-fetch the
  // manifest on every tab change. The initial value is resolved on the server and passed in, so
  // there is no client-side URL read to go out of step with the server render; subsequent changes
  // only rewrite the address bar.
  const setView = (v: View) => {
    setViewState(v);
    const url = new URL(window.location.href);
    url.searchParams.set("view", v);
    window.history.replaceState(null, "", url);
  };
  const [selected, setSelected] = useState<string[]>([]);

  useEffect(() => {
    let live = true;
    Promise.all([
      defaultSimSource.sessionManifest({ trackSlug: track, session }),
      defaultSimSource.catalogue<Catalogue>(),
    ])
      .then(([m, c]) => {
        if (!live) return;
        setManifest(m);
        setCat(c);
        // default to the five classified highest, which is the comparison a reader wants first
        const byFinish = [...m.drivers].sort((a, b) => finishPos(a) - finishPos(b));
        setSelected(byFinish.slice(0, 5).map((d) => d.driver));
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [track, session]);

  const drivers = useMemo(
    () => (manifest ? [...manifest.drivers].sort((a, b) => finishPos(a) - finishPos(b)) : []),
    [manifest],
  );

  const shown = useMemo(
    () => drivers.filter((d) => selected.includes(d.driver)),
    [drivers, selected],
  );

  const [teamPick, setTeamPick] = useState<string[]>([]);

  // Options come from the MANIFEST, not the catalogue: this page is about who actually ran in
  // this session, and a driver on the entry list who never took the start should not be offered.
  const driverOptions = useMemo<SelectOption[]>(() => {
    if (!cat) return [];
    const st = driverStyles(cat.teams, drivers);
    return drivers.map((d) => {
      const laps = d.laps.filter((l) => l.time !== null).length;
      return {
        value: d.driver,
        label: d.driver,
        group: d.team ?? "Unattached",
        colour: st[d.driver]?.colour,
        dashed: st[d.driver]?.dashed,
        meta: `${laps} laps`,
        keywords: d.team ?? "",
      };
    });
  }, [drivers, cat]);

  const teamOptions = useMemo<SelectOption[]>(() => {
    if (!cat) return [];
    const names = [...new Set(drivers.map((d) => d.team).filter(Boolean) as string[])].sort();
    return names.map((name) => ({
      value: name,
      label: name.replace(/\s+F1 Team$/i, ""),
      keywords: name,
      colour: teamColour(cat.teams, name),
      meta: `${drivers.filter((d) => d.team === name).length} cars`,
    }));
  }, [drivers, cat]);

  if (error) {
    return (
      <main className={s.main}>
        <p className={s.lede}>
          Could not load this session: {error}. <Link href="/sessions" className={s.back}>Back to sessions</Link>
        </p>
      </main>
    );
  }
  if (!manifest || !cat) {
    return (
      <main className={s.main}>
        <p className={s.lede}>Loading session…</p>
      </main>
    );
  }

  // Two cars from the same team share a colour; the second is drawn dashed so the pair is
  // separable without adding a hue the palette has not validated.
  const styles = driverStyles(cat.teams, drivers);
  const styleOf = (d: RawDriverEntry): DriverStyle =>
    styles[d.driver] ?? { colour: "#AEAEAE", dashed: false };

  return (
    <main className={s.main}>
      <header className={s.head}>
        <p className={s.kicker}>
          <Link href="/sessions" className={s.back}>
            Sessions
          </Link>{" "}
          · {manifest.session} · {manifest.drivers.length} cars
        </p>
        <h1 className={s.title}>{manifest.event.replace(/\s+Grand Prix$/i, "")}</h1>
      </header>

      <div className={s.controls}>
        <div className={s.segmented} role="tablist" aria-label="View">
          {VIEWS.map((v) => (
            <button
              key={v.key}
              type="button"
              role="tab"
              aria-selected={v.key === view}
              className={s.segment}
              onClick={() => setView(v.key)}
            >
              {v.label}
            </button>
          ))}
        </div>

        <MultiSelect
          label="Drivers"
          options={driverOptions}
          selected={selected}
          onChange={setSelected}
          placeholder="None selected"
        />

        <MultiSelect
          label="Teams"
          options={teamOptions}
          selected={teamPick}
          onChange={(next) => {
            setTeamPick(next);
            // Picking a team SETS the driver selection to that team's cars rather than merely
            // narrowing the list -- "show me Ferrari" should put Ferrari on the chart, not leave
            // the chart unchanged and quietly shorten a dropdown the user is not looking at.
            if (next.length > 0) {
              const want = new Set(next);
              setSelected(drivers.filter((d) => d.team && want.has(d.team)).map((d) => d.driver));
            }
          }}
          placeholder="Any"
          showChips={false}
        />
      </div>

      {shown.length === 0 ? (
        <NoData title="No drivers selected" reason="Pick at least one driver above." />
      ) : view === "pace" ? (
        <PaceView manifest={manifest} shown={shown} styleOf={styleOf} />
      ) : view === "overtake" ? (
        <OvertakeView manifest={manifest} shown={shown} styleOf={styleOf} />
      ) : (
        <EnergyView shown={shown} styleOf={styleOf} />
      )}
    </main>
  );
}

/* ------------------------------------------------------------------ pace --- */

function PaceView({
  manifest,
  shown,
  styleOf,
}: {
  manifest: RawSessionManifest;
  shown: RawDriverEntry[];
  styleOf: (d: RawDriverEntry) => DriverStyle;
}) {
  const [excludeNeutralised, setExcludeNeutralised] = useState(true);
  const { hidden, toggle, visible } = useSeriesToggle();

  const allSeries = shown.map((d) => {
    const laps = d.laps.filter((l) => isRacingLap(l, manifest, excludeNeutralised));
    return { driver: d.driver, colour: styleOf(d).colour, dashed: styleOf(d).dashed, x: laps.map((l) => l.lap), y: laps.map((l) => l.time as number) };
  });

  const series = visible(allSeries, (sr) => sr.driver);
  const yExt = extent(series.flatMap((sr) => sr.y));
  const xExt = extent(series.flatMap((sr) => sr.x));

  const allPosSeries = shown.map((d) => ({
    driver: d.driver,
    colour: styleOf(d).colour,
    dashed: styleOf(d).dashed,
    x: d.laps.filter((l) => l.pos !== null).map((l) => l.lap),
    y: d.laps.filter((l) => l.pos !== null).map((l) => l.pos as number),
  }));
  const posSeries = visible(allPosSeries, (sr) => sr.driver);
  const maxPos = Math.max(...posSeries.flatMap((p) => p.y), 1);

  return (
    <div className={s.views}>
      <label className={s.toggle}>
        <input
          type="checkbox"
          checked={excludeNeutralised}
          onChange={(e) => setExcludeNeutralised(e.target.checked)}
        />
        Exclude in/out laps, deleted laps and laps run under SC/VSC
      </label>

      {yExt && xExt ? (
        <ChartFrame
          title="Lap time"
          units="seconds"
          provenance="OBSERVED"
          legend={allSeries.map((sr) => ({ label: sr.driver, colour: sr.colour, dashed: sr.dashed }))}
          hidden={hidden}
          onToggleSeries={toggle}
          note={
            excludeNeutralised
              ? "Pit in/out laps, stewards-deleted laps and any lap overlapping a safety-car or VSC window are removed — they are not pace. Neutralisation windows are shaded so the removal is visible rather than silent."
              : "Every lap is shown, including pit and neutralised laps. The spikes are pit stops and safety cars, not pace."
          }
        >
          <Plot
            xDomain={[xExt[0], xExt[1]]}
            yDomain={[yExt[0] - 0.5, niceTicks(yExt[0], yExt[1], 5).niceMax]}
            height={300}
            margin={{ left: 56, right: 44 }}
            ariaLabel="Lap time by lap number for the selected drivers"
            hover={{
              series: series.map((sr) => ({
                label: sr.driver,
                colour: sr.colour,
                x: sr.x,
                y: sr.y,
                format: (v: number) => `${v.toFixed(3)} s`,
              })),
              xLabel: "lap",
              xFormat: (v: number) => String(Math.round(v)),
            }}
          >
            <Grid />
            {manifest.neutralisation.map((n, i) => {
              const from = lapAtSessionTime(manifest, n.start);
              const to = lapAtSessionTime(manifest, n.end);
              return from !== null && to !== null ? (
                <XRegion key={i} from={from} to={Math.max(to, from + 0.6)} colour="#AEAEAE" opacity={0.14} />
              ) : null;
            })}
            <XAxis label="lap" />
            <YAxis label="lap time s" format={(v) => v.toFixed(0)} />
            {series.map((sr, i) => (
              <Line
                key={sr.driver}
                x={sr.x}
                y={sr.y}
                colour={sr.colour}
                dashed={sr.dashed}
                label={series.length <= 4 ? sr.driver : undefined}
                labelIndex={sr.x.length - 1 - i}
              />
            ))}
          </Plot>
        </ChartFrame>
      ) : (
        <NoData title="Lap time" reason="No timed laps survive the current filters for these drivers." />
      )}

      <ChartFrame
        title="Position"
        units="race order by lap"
        provenance="OBSERVED"
        legend={allPosSeries.map((sr) => ({ label: sr.driver, colour: sr.colour, dashed: sr.dashed }))}
        hidden={hidden}
        onToggleSeries={toggle}
        note="Official classification at the end of each lap. A vertical step is an overtake, a pit cycle, or a retirement ahead."
      >
        <Plot
          xDomain={[1, Math.max(...posSeries.flatMap((p) => p.x), 2)]}
          yDomain={[maxPos + 0.5, 0.5]}
          height={260}
          margin={{ left: 40, right: 44 }}
          ariaLabel="Race position by lap for the selected drivers"
          hover={{
            series: posSeries.map((sr) => ({
              label: sr.driver,
              colour: sr.colour,
              x: sr.x,
              y: sr.y,
              format: (v: number) => `P${Math.round(v)}`,
            })),
            xLabel: "lap",
            xFormat: (v: number) => String(Math.round(v)),
          }}
        >
          <Grid />
          <XAxis label="lap" />
          <YAxis label="position" format={(v) => v.toFixed(0)} />
          {posSeries.map((sr, i) => (
            <Line
              key={sr.driver}
              x={sr.x}
              y={sr.y}
              colour={sr.colour}
              dashed={sr.dashed}
              step
              label={posSeries.length <= 4 ? sr.driver : undefined}
              labelIndex={sr.x.length - 1 - i}
            />
          ))}
        </Plot>
      </ChartFrame>
    </div>
  );
}

/* ------------------------------------------------------------- overtaking --- */

/**
 * Overtaking, from what the session manifest can honestly support.
 *
 * The hard caveat, stated on screen rather than buried: a change in end-of-lap classification is
 * NOT the same thing as an on-track overtake. Pit cycles, retirements ahead and lapped traffic all
 * move the classification without anyone passing anyone. Laps where the driver was in the pit lane
 * are excluded, which removes the largest source of false positives but not all of them.
 *
 * The chart that earns this page its place is the last one: estimated deployment on the laps where
 * a driver gained a place, against their own lap-by-lap line. That is the Energy x Overtake
 * question in its cheapest honest form -- does getting past cost energy?
 */
function OvertakeView({
  manifest,
  shown,
  styleOf,
}: {
  manifest: RawSessionManifest;
  shown: RawDriverEntry[];
  styleOf: (d: RawDriverEntry) => DriverStyle;
}) {
  const changes = useMemo(() => positionChanges(manifest), [manifest]);
  const maxLap = Math.max(...manifest.drivers.flatMap((d) => d.laps.map((l) => l.lap)), 1);

  // OVERTAKE ENABLED / DISABLED comes from race control, which is the only place the 2026
  // overtake system's availability is recorded -- the raw drs channel is dead (all zeros).
  const windows = useMemo(() => overtakeWindows(manifest), [manifest]);

  const perLap: number[] = [];
  for (let l = 1; l <= maxLap; l++) perLap.push(changes.filter((c) => c.lap === l).length);

  return (
    <div className={s.views}>
      <p className={s.simWarning}>
        A change in end-of-lap classification is <strong>not the same thing as an overtake</strong>.
        Pit cycles, retirements ahead and lapped traffic all move the order without a pass. Laps
        where the driver was in the pit lane are excluded here, which removes the largest source of
        false positives but not all of them. Read these as position changes, not as a pass count.
      </p>

      <ChartFrame
        title="Position changes per lap"
        units={`${changes.length} events across the race`}
        provenance="DERIVED"
        note="Derived from official end-of-lap classification. Grey bands are safety-car and VSC windows, where the order is neutralised and passing is not permitted. Gold bands are periods when race control had the overtake system DISABLED — the only record of its availability, since the raw DRS channel is dead for 2026."
        table={{
          columns: ["lap", "driver", "from", "to"],
          rows: changes.map((c) => [c.lap, c.driver, c.from, c.to]),
        }}
      >
        <Plot
          xDomain={[1, maxLap]}
          yDomain={[0, Math.max(...perLap, 1) + 1]}
          height={230}
          margin={{ left: 44, right: 20 }}
          ariaLabel="Number of position changes on each lap"
          hover={{
            series: [
              {
                label: "changes",
                colour: CHART.series[0],
                x: perLap.map((_, i) => i + 1),
                y: perLap,
                format: (v: number) => String(Math.round(v)),
              },
            ],
            xLabel: "lap",
            xFormat: (v: number) => String(Math.round(v)),
          }}
        >
          <Grid />
          {manifest.neutralisation.map((n, i) => {
            const a = lapAtSessionTime(manifest, n.start);
            const b = lapAtSessionTime(manifest, n.end);
            return a !== null && b !== null ? (
              <XRegion key={`n${i}`} from={a} to={Math.max(b, a + 0.6)} colour="#AEAEAE" opacity={0.16} />
            ) : null;
          })}
          {windows.disabled.map((w, i) => {
            const a = lapAtSessionTime(manifest, w.from);
            const b = w.to === null ? maxLap : lapAtSessionTime(manifest, w.to);
            return a !== null && b !== null ? (
              <XRegion
                key={`d${i}`}
                from={a}
                to={Math.max(b, a + 0.6)}
                colour="#A88606"
                opacity={0.18}
                label={i === 0 ? "overtake disabled" : undefined}
              />
            ) : null;
          })}
          <XAxis label="lap" />
          <YAxis label="position changes" format={(v) => v.toFixed(0)} />
          <Line x={perLap.map((_, i) => i + 1)} y={perLap} colour={CHART.series[0]} step />
        </Plot>
      </ChartFrame>

      <ChartFrame
        title="Estimated deployment on laps where a place was gained"
        units="MJ per lap"
        provenance="SIMULATED"
        legend={shown.map((d) => ({ label: d.driver, colour: styleOf(d).colour }))}
        note="The line is each driver's estimated deployment lap by lap; the ringed points are laps on which they gained a position. If getting past costs energy, those points sit above the driver's own line. Deployment is an estimate from the energy twin, never a measurement."
      >
        <DeployAtPass shown={shown} styleOf={styleOf} changes={changes} maxLap={maxLap} />
      </ChartFrame>
    </div>
  );
}

function DeployAtPass({
  shown,
  styleOf,
  changes,
  maxLap,
}: {
  shown: RawDriverEntry[];
  styleOf: (d: RawDriverEntry) => DriverStyle;
  changes: { lap: number; driver: string; from: number; to: number }[];
  maxLap: number;
}) {
  const series = shown
    .map((d) => {
      const laps = d.laps.filter((l) => l.energy && Number.isFinite(l.energy.ersEnergyUsedMj));
      // position 1 is the front, so a gain is a DECREASE
      const gains = new Set(
        changes.filter((c) => c.driver === d.driver && c.to < c.from).map((c) => c.lap),
      );
      const gainLaps = laps.filter((l) => gains.has(l.lap));
      return {
        driver: d.driver,
        colour: styleOf(d).colour, dashed: styleOf(d).dashed,
        x: laps.map((l) => l.lap),
        y: laps.map((l) => l.energy!.ersEnergyUsedMj),
        gx: gainLaps.map((l) => l.lap),
        gy: gainLaps.map((l) => l.energy!.ersEnergyUsedMj),
      };
    })
    .filter((sr) => sr.x.length > 0);

  const ext = extent(series.flatMap((sr) => sr.y));
  if (!ext) {
    return (
      <NoData
        title="Deployment at a position gain"
        reason="No lap in this session carries an energy estimate, so there is nothing to compare."
      />
    );
  }

  return (
    <Plot
      xDomain={[1, maxLap]}
      yDomain={[Math.max(0, ext[0] - 0.3), niceTicks(ext[0], ext[1], 4).niceMax]}
      height={300}
      margin={{ left: 52, right: 44 }}
      ariaLabel="Estimated energy deployed per lap, with laps where a position was gained marked"
      hover={{
        series: series.map((sr) => ({
          label: sr.driver,
          colour: sr.colour,
          x: sr.x,
          y: sr.y,
          format: (v: number) => `${v.toFixed(2)} MJ`,
        })),
        xLabel: "lap",
        xFormat: (v: number) => String(Math.round(v)),
      }}
    >
      <Grid />
      <XAxis label="lap" />
      <YAxis label="estimated deploy MJ" format={(v) => v.toFixed(1)} />
      {series.map((sr, i) => (
        <Line
          key={sr.driver}
          x={sr.x}
          y={sr.y}
          colour={sr.colour}
          dashed={sr.dashed}
          width={1.4}
          label={series.length <= 4 ? sr.driver : undefined}
          labelIndex={sr.x.length - 1 - i}
        />
      ))}
      {series.map((sr) => (
        <Dots key={sr.driver + "g"} x={sr.gx} y={sr.gy} colour={sr.colour} r={4.5} />
      ))}
    </Plot>
  );
}

/** Every end-of-lap classification change, excluding laps where the driver was in the pit lane.
 * Position 1 is the front, so `to < from` is a gain. */
function positionChanges(m: RawSessionManifest) {
  const out: { lap: number; driver: string; from: number; to: number }[] = [];
  for (const d of m.drivers) {
    let prev: number | null = null;
    for (const l of d.laps) {
      const pitted = l.pin !== null || l.pout !== null;
      if (l.pos === null) continue;
      if (prev !== null && l.pos !== prev && !pitted) {
        out.push({ lap: l.lap, driver: d.driver, from: prev, to: l.pos });
      }
      prev = l.pos;
    }
  }
  return out.sort((a, b) => a.lap - b.lap);
}

/** Reconstruct OVERTAKE ENABLED / DISABLED windows from race control. The 2026 overtake system's
 * availability is not in any telemetry channel -- the raw drs field is all zeros for 2026 -- so
 * race control is the only source. An unclosed DISABLED window runs to the end of the session. */
function overtakeWindows(m: RawSessionManifest) {
  const msgs = m.raceControl
    .filter((r) => r.kind === "overtakeMode")
    .sort((a, b) => a.sessionTime - b.sessionTime);
  const disabled: { from: number; to: number | null }[] = [];
  let open: number | null = null;
  for (const r of msgs) {
    const off = /DISABL/i.test(r.message);
    if (off && open === null) open = r.sessionTime;
    else if (!off && open !== null) {
      disabled.push({ from: open, to: r.sessionTime });
      open = null;
    }
  }
  if (open !== null) disabled.push({ from: open, to: null });
  return { disabled, messages: msgs };
}

/* ---------------------------------------------------------------- energy --- */

/**
 * Energy, led by its own calibration state.
 *
 * The twin's longitudinal power balance does not currently close: across this session the median
 * per-lap balance is strongly negative, so the modelled store drains to its floor and stays there
 * on most laps. A store trace drawn without that context reads as "these cars ran the whole race
 * empty", which is a claim about the cars rather than what it really is -- a claim about our
 * model. So the calibration state is stated first, the balance itself is the headline chart, and
 * the store trace is placed after both and labelled as not yet usable as a state estimate.
 *
 * This is AGENTS.md's final principle applied literally: a simpler result with honest limitations
 * is stronger than a more complicated one built on numbers that do not hold up.
 */
function EnergyView({
  shown,
  styleOf,
}: {
  shown: RawDriverEntry[];
  styleOf: (d: RawDriverEntry) => DriverStyle;
}) {
  const withEnergy = shown.filter((d) => d.laps.some((l) => l.energy));
  const cal = useMemo(() => calibration(withEnergy), [withEnergy]);
  const { hidden, toggle, visible } = useSeriesToggle();

  if (withEnergy.length === 0) {
    return (
      <NoData
        title="Energy & ERS"
        reason="No lap for the selected drivers carries an energy estimate. The twin runs over telemetry, so a session without usable samples produces none."
      />
    );
  }

  const maxLap = Math.max(...withEnergy.flatMap((d) => d.laps.map((l) => l.lap)), 1);

  const balanceSeries = withEnergy.map((d) => {
    const laps = d.laps.filter((l) => l.energy && Number.isFinite(l.energy.energyBalanceMj));
    const st = styleOf(d);
    return {
      driver: d.driver,
      colour: st.colour,
      dashed: st.dashed,
      x: laps.map((l) => l.lap),
      y: laps.map((l) => l.energy!.energyBalanceMj),
    };
  });
  // the domain is computed from the VISIBLE series, so hiding an outlier rescales the chart
  const balShown = visible(balanceSeries, (sr) => sr.driver);
  const balExt = extent(balShown.flatMap((sr) => sr.y));

  const socSeries = withEnergy.map((d) => {
    const laps = d.laps.filter((l) => l.energy && Number.isFinite(l.energy.socEndMj));
    const st = styleOf(d);
    return {
      driver: d.driver,
      colour: st.colour,
      dashed: st.dashed,
      x: laps.map((l) => l.lap),
      y: laps.map((l) => l.energy!.socEndMj),
      lo: laps.map((l) => l.energy!.socEndMj - (l.energy!.socUncertaintyMj ?? 0)),
      hi: laps.map((l) => l.energy!.socEndMj + (l.energy!.socUncertaintyMj ?? 0)),
    };
  });
  const socShown = visible(socSeries, (sr) => sr.driver);
  const socExt = extent(socShown.flatMap((sr) => [...sr.lo, ...sr.hi]));

  const scatter = withEnergy.flatMap((d) =>
    d.laps
      .filter((l) => l.energy && l.time !== null && !l.del && l.pin === null && l.pout === null)
      .map((l) => ({ mj: l.energy!.ersEnergyUsedMj, t: l.time as number, colour: styleOf(d).colour })),
  );
  const mjExt = extent(scatter.map((p) => p.mj));
  const tExt = extent(scatter.map((p) => p.t));

  return (
    <div className={s.views}>
      <p className={s.simWarning}>
        Every number below is an <strong>estimate from the energy twin</strong>, tagged SIMULATED —
        reconstructed from a longitudinal power balance over public telemetry. No car publishes its
        electrical state. These are not measurements, and they are not a battery readout.
      </p>

      <section className={s.quality}>
        <h2 className={s.qualityHead}>Calibration state — read this first</h2>
        <p className={s.qualityBody}>
          The twin&apos;s power balance <strong>does not currently close</strong> on this session.
          Median per-lap balance is <strong>{cal.medianBalance.toFixed(2)} MJ</strong>, so the
          modelled store drains to its floor and stays there on{" "}
          <strong>
            {cal.floorLaps} of {cal.totalLaps} laps ({Math.round((100 * cal.floorLaps) / Math.max(1, cal.totalLaps))}%)
          </strong>
          . The most likely cause is an uncalibrated effective ICE power map, which is a known open
          item — it has deliberately not been tuned to make the charts look right.
        </p>
        <p className={s.qualityBody}>
          Consequence for reading this page: the <em>balance</em> and the <em>deploy/harvest</em>{" "}
          traces below are informative, because they are what the twin actually estimates per lap.
          The <em>store</em> trace is not yet usable as a state estimate, and is shown last, marked
          as such, rather than removed.
        </p>
        {cal.violationLaps > 0 ? (
          <p className={s.qualityBody}>
            On <strong>{cal.violationLaps} of {cal.totalLaps}</strong> laps the estimate wanted more
            electrical power than the (unverified) regulation envelope allows. That is a fault in
            our model, <strong>not a car exceeding a limit</strong>: the envelope is used as a
            calibration diagnostic and never as a silent clamp, so the raw estimate is preserved and
            the disagreement is reported rather than hidden.
          </p>
        ) : null}
      </section>

      {balExt ? (
        <ChartFrame
          title="Estimated energy balance per lap"
          units="MJ recovered minus MJ deployed"
          provenance="SIMULATED"
          legend={balanceSeries.map((sr) => ({ label: sr.driver, colour: sr.colour, dashed: sr.dashed }))}
          hidden={hidden}
          onToggleSeries={toggle}
          note="Above the line the lap put energy back; below it the lap spent more than it recovered. This is the twin's core per-lap output and the quantity its calibration is judged on — a balance that sits persistently below zero is the signature of the open calibration above, not of a car running itself flat."
          table={{
            columns: ["driver", "lap", "balance MJ"],
            rows: balanceSeries.flatMap((sr) => sr.x.map((lap, i) => [sr.driver, lap, sr.y[i].toFixed(2)])),
          }}
        >
          <Plot
            xDomain={[1, maxLap]}
            yDomain={[balExt[0] - 0.4, balExt[1] + 0.4]}
            height={300}
            margin={{ left: 52, right: 44 }}
            ariaLabel="Estimated per-lap energy balance for the selected drivers"
            hover={{
              series: balShown.map((sr) => ({
                label: sr.driver,
                colour: sr.colour,
                x: sr.x,
                y: sr.y,
                format: (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)} MJ`,
              })),
              xLabel: "lap",
              xFormat: (v: number) => String(Math.round(v)),
            }}
          >
            <Grid />
            <XAxis label="lap" />
            <YAxis label="balance MJ" format={(v) => v.toFixed(1)} />
            <RefLine y={0} label="break even" />
            {balShown.map((sr, i) => (
              <Line
                key={sr.driver}
                x={sr.x}
                y={sr.y}
                colour={sr.colour}
                dashed={sr.dashed}
                label={balShown.length <= 4 ? sr.driver : undefined}
                labelIndex={sr.x.length - 1 - i}
              />
            ))}
          </Plot>
        </ChartFrame>
      ) : null}

      <div className={s.pair}>
        <ChartFrame
          title="Estimated deploy and harvest"
          units="MJ per lap"
          provenance="SIMULATED"
          legend={[
            { label: "deployed", colour: CHART.diverging.negative },
            { label: "harvested", colour: CHART.diverging.positive },
          ]}
          note="Diverging around zero: energy spent below the line, energy recovered above it. They are separate quantities with separate per-lap budgets, so they are drawn against a shared baseline rather than netted into one number."
        >
          <DeployHarvest driver={withEnergy[0]} maxLap={maxLap} />
        </ChartFrame>

        {mjExt && tExt ? (
          <ChartFrame
            title="Energy-normalised pace"
            units="lap time vs estimated energy deployed"
            provenance="SIMULATED"
            note="One point per racing lap. Until the shadow price exists this is the closest available answer to what a joule bought — a descending cloud means more deployment is buying lap time, a flat one means it is not."
          >
            <Plot
              xDomain={[Math.max(0, mjExt[0] - 0.3), mjExt[1] + 0.3]}
              yDomain={[tExt[0] - 0.3, niceTicks(tExt[0], Math.min(tExt[1], tExt[0] * 1.1), 4).niceMax]}
              height={260}
              margin={{ left: 56, right: 20 }}
              ariaLabel="Lap time against estimated energy deployed"
            >
              <Grid />
              <XAxis label="estimated deploy MJ" format={(v) => v.toFixed(1)} />
              <YAxis label="lap time s" format={(v) => v.toFixed(0)} />
              {[...new Set(scatter.map((p) => p.colour))].map((c) => {
                const sub = scatter.filter((p) => p.colour === c);
                return <Dots key={c} x={sub.map((p) => p.mj)} y={sub.map((p) => p.t)} colour={c} r={2.6} />;
              })}
            </Plot>
          </ChartFrame>
        ) : null}
      </div>

      {socExt ? (
        <ChartFrame
          title="Estimated energy store — not yet usable as a state estimate"
          units="MJ at the end of each lap"
          provenance="SIMULATED"
          legend={socSeries.map((sr) => ({ label: sr.driver, colour: sr.colour, dashed: sr.dashed }))}
          hidden={hidden}
          onToggleSeries={toggle}
          note="Shown for completeness and for calibration work, not as a claim about the cars. Because the balance above does not close, this trace spends most of the race clamped at its lower bound; the band is the twin's own uncertainty, which is why it extends below a physically possible zero. When the balance closes, this becomes the chart the rest of the system is built on."
        >
          <Plot
            xDomain={[1, maxLap]}
            yDomain={[Math.min(0, socExt[0]), niceTicks(0, Math.max(socExt[1], 4), 4).niceMax]}
            height={260}
            margin={{ left: 52, right: 44 }}
            ariaLabel="Estimated stored electrical energy by lap, with uncertainty"
          >
            <Grid />
            <XAxis label="lap" />
            <YAxis label="estimated store MJ" format={(v) => v.toFixed(1)} />
            <RefLine y={4} label="store capacity 4.0 MJ (unverified)" />
            {socShown.map((sr) => (
              <Band key={sr.driver + "band"} x={sr.x} lo={sr.lo} hi={sr.hi} colour={sr.colour} opacity={0.14} />
            ))}
            {socShown.map((sr, i) => (
              <Line
                key={sr.driver}
                x={sr.x}
                y={sr.y}
                colour={sr.colour}
                dashed={sr.dashed}
                label={socShown.length <= 4 ? sr.driver : undefined}
                labelIndex={sr.x.length - 1 - i}
              />
            ))}
          </Plot>
        </ChartFrame>
      ) : null}
    </div>
  );
}

/** Session-level calibration facts, computed rather than asserted, so the banner cannot drift
 * away from the artifact it describes. */
function calibration(drivers: RawDriverEntry[]) {
  const balances: number[] = [];
  let floorLaps = 0;
  let violationLaps = 0;
  let totalLaps = 0;
  for (const d of drivers) {
    for (const l of d.laps) {
      const e = l.energy;
      if (!e) continue;
      totalLaps++;
      if (Number.isFinite(e.energyBalanceMj)) balances.push(e.energyBalanceMj);
      if (e.socEndMj === 0) floorLaps++;
      if ((e.envelopeCapViolations ?? 0) > 0) violationLaps++;
    }
  }
  balances.sort((a, b) => a - b);
  const medianBalance = balances.length
    ? balances[Math.floor(balances.length / 2)]
    : 0;
  return { medianBalance, floorLaps, violationLaps, totalLaps };
}

function DeployHarvest({ driver, maxLap }: { driver: RawDriverEntry; maxLap: number }) {
  const laps = driver.laps.filter((l) => l.energy);
  const dep = laps.map((l) => -(l.energy!.ersEnergyUsedMj ?? 0));
  const har = laps.map((l) => l.energy!.ersEnergyHarvestedMj ?? 0);
  const ext = extent([...dep, ...har]);
  if (!ext) return <NoData title="Deploy and harvest" reason="No per-lap energy on this driver." />;
  const bound = Math.max(Math.abs(ext[0]), Math.abs(ext[1]));
  return (
    <Plot
      xDomain={[1, maxLap]}
      yDomain={[-bound * 1.1, bound * 1.1]}
      height={260}
      margin={{ left: 52, right: 20 }}
      ariaLabel={`Estimated deploy and harvest per lap for ${driver.driver}`}
    >
      <Grid />
      <XAxis label={`lap · ${driver.driver}`} />
      <YAxis label="MJ" format={(v) => v.toFixed(1)} />
      <RefLine y={0} />
      <Line x={laps.map((l) => l.lap)} y={dep} colour={CHART.diverging.negative} step />
      <Line x={laps.map((l) => l.lap)} y={har} colour={CHART.diverging.positive} step />
    </Plot>
  );
}

/* ----------------------------------------------------------------- utils --- */

function finishPos(d: RawDriverEntry): number {
  for (let i = d.laps.length - 1; i >= 0; i--) {
    if (d.laps[i].pos !== null) return d.laps[i].pos as number;
  }
  return 99;
}

/** A lap that is actually a measure of pace: timed, accurate, not deleted, not a pit in/out lap,
 * and (optionally) not overlapping a neutralisation window. */
function isRacingLap(l: RawLapEntry, m: RawSessionManifest, excludeNeutralised: boolean): boolean {
  if (l.time === null || !Number.isFinite(l.time)) return false;
  if (!excludeNeutralised) return true;
  if (l.del || !l.iacc) return false;
  if (l.pin !== null || l.pout !== null) return false;
  if (l.lST === null || l.sesT === null) return true;
  return !m.neutralisation.some((n) => (l.lST as number) < n.end && (l.sesT as number) > n.start);
}

/**
 * Which lap a session-absolute time falls in, for placing a neutralisation or overtake-mode
 * window on a lap axis.
 *
 * Two things this must not do. It must not key off `manifest.drivers[0]`, whose array position is
 * arbitrary and who may have retired early -- a window after their last lap then silently
 * disappeared from the chart. And it must not return null for a time that merely falls in a gap
 * between two recorded laps (a pit cycle leaves one), because a dropped band reads as "nothing
 * happened here".
 *
 * So: use the driver with the most laps as the clock, and snap to the nearest lap boundary rather
 * than giving up. Returns null only when the session has no usable lap timing at all.
 */
function lapAtSessionTime(m: RawSessionManifest, t: number): number | null {
  let clock: RawDriverEntry | null = null;
  for (const d of m.drivers) {
    const n = d.laps.filter((l) => l.lST !== null && l.sesT !== null).length;
    if (!clock || n > clock.laps.filter((l) => l.lST !== null && l.sesT !== null).length) clock = d;
  }
  const laps = clock ? clock.laps.filter((l) => l.lST !== null && l.sesT !== null) : [];
  if (laps.length === 0) return null;

  for (const l of laps) {
    if (t >= (l.lST as number) && t <= (l.sesT as number)) return l.lap;
  }
  // outside every recorded lap: snap to the nearest boundary rather than dropping the band
  const first = laps[0];
  const last = laps[laps.length - 1];
  if (t < (first.lST as number)) return first.lap;
  if (t > (last.sesT as number)) return last.lap;
  let best = laps[0];
  let bestGap = Infinity;
  for (const l of laps) {
    const gap = Math.min(Math.abs(t - (l.lST as number)), Math.abs(t - (l.sesT as number)));
    if (gap < bestGap) {
      bestGap = gap;
      best = l;
    }
  }
  return best.lap;
}
