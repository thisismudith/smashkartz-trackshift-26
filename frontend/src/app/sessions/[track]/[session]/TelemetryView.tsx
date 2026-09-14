/**
 * Two laps, one shared distance axis (UI.md section 4.5).
 *
 * The x axis is the RING STATION in metres — never the telemetry `distance` channel, which is
 * per-driver integrated wheel speed and disagrees between cars at the same circuit by up to
 * 4.14% (UI.md 1.4). Putting both laps on the ring is what makes "at the apex of turn 9" the
 * same x for both drivers instead of two different ones.
 *
 * Panels are stacked and never share a y. Speed, throttle, brake and gear are OBSERVED
 * channels; the cumulative delta between them is DERIVED. A dual axis would let a reader
 * compare a km/h against a percent by their height on the page, which is exactly the
 * comparison neither number supports.
 *
 * The honesty this view has to carry is about its own x axis. Three circuits have measured
 * position faults, and the station channel is derived from positions, so a lap can look
 * perfectly smooth while its distance axis is fiction. Every selected lap is therefore
 * checked — position frame, station-vs-speed credibility, ring coverage — and a lap that
 * fails is refused with the measurement that failed it, rather than drawn.
 */
"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { defaultSimSource } from "@/sim/data/source";
import type {
  RawDriverEntry,
  RawLapEntry,
  RawSessionManifest,
  RawTrackModel,
} from "@/sim/data/manifest";
import { lapPositionFrame, parseCorners, type LapPositionFrame } from "@/sim/data/manifest";
import {
  STATION_ANOMALY_LIMIT,
  alongLapDistance,
  cumulativeDelta,
  loadSessionSamples,
  resampleByStation,
  sampleLapFromBuffer,
  stationGrid,
  type AlongLap,
  type ResampledLap,
} from "@/sim/data/lapSamples";
import {
  AwaitingModel,
  Band,
  BandLabels,
  ChartFrame,
  Grid,
  Line,
  NoData,
  Plot,
  RefLine,
  XAxis,
  YAxis,
  decimate,
  extent,
  niceTicks,
} from "@/sim/charts";
import s from "./telemetry.module.css";

interface DriverStyle {
  colour: string;
  dashed: boolean;
}

interface Pick {
  driver: string;
  lap: number;
}

/**
 * The measured position faults from UI.md section 1.4, in the wording the session browser
 * uses, narrowed to what they do to a distance axis. They live here rather than being fetched
 * because the build pipeline does not yet emit a per-session quality block; when it does, this
 * table is replaced by that data and not before.
 *
 * These are the SESSION-level statements. They never gate on their own: the per-lap checks
 * below decide what is actually drawn, and on Monaco most laps fail while a hundred pass.
 */
const POSITION_FAULTS: Record<
  string,
  { session: string; severity: "blocking" | "caution"; text: string }[]
> = {
  "monaco-grand-prix": [
    {
      session: "Race",
      severity: "blocking",
      text:
        "1324 of 1452 laps carry no measured position: their station comes from normalising the distance channel onto the ring, not from projecting coordinates. Those laps are refused here. The 128 laps that do carry positions are accurate and can be compared normally.",
    },
  ],
  "hungarian-grand-prix": [
    {
      session: "Race",
      severity: "blocking",
      text:
        "Laps 15–70 are a stale-anchor sample-and-hold: only 53.6% of position samples per lap are distinct and the integrated path runs 10% long, so the station axis teleports. Because the fastest laps are late-race, a naive fastest-lap pick lands inside the corrupt window — the default below therefore picks the fastest lap whose station steps agree with the speed channel, which is usually outside it.",
    },
  ],
  "japanese-grand-prix": [
    {
      session: "Race",
      severity: "caution",
      text:
        "The circuit's ring self-crosses: 529 vertex pairs lie within 12 m of each other but more than 150 m apart in station, so a stateless projection can alias across the crossover. Station steps that disagree with the speed channel are broken out of the traces rather than drawn through.",
    },
  ],
};

/** Grid resolution for the shared axis. 5 m is finer than the ~6-9 m native sample spacing, so
 * the grid is not what limits detail; decimation to the chart's pixel width is. */
const GRID_STEP_M = 5;

/** Panel geometry. Left and right MUST be identical across every panel or the shared x axis
 * stops being shared — that is the one thing this view exists to guarantee. */
const MARGIN = { left: 54, right: 18 };

export default function TelemetryView({
  track,
  session,
  manifest,
  drivers,
  styles,
}: {
  track: string;
  session: string;
  manifest: RawSessionManifest;
  /** Already ordered by finishing classification — the default pair is the first two. */
  drivers: RawDriverEntry[];
  /**
   * driver code -> colour, as `driverStyles` builds it. A PLAIN RECORD, deliberately:
   * this used to be a `styleOf` lookup FUNCTION, which Next cannot serialise across the
   * server/client boundary, so the page threw on every render and only ever displayed its
   * "Telemetry is unavailable" catch branch. Data crosses; behaviour is rebuilt here.
   */
  styles: Record<string, DriverStyle>;
}) {
  /** The lookup the rest of this component was already written against. */
  const styleOf = (d: RawDriverEntry): DriverStyle =>
    styles[d.driver] ?? { colour: "#AEAEAE", dashed: false };

  const trackLengthM = manifest.trackLengthMetres;

  const [buf, setBuf] = useState<ArrayBuffer | null>(null);
  const [trackModel, setTrackModel] = useState<RawTrackModel | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pick, setPick] = useState<{ a: Pick; b: Pick } | null>(null);

  // The blob is 3-10 MB, so it is loaded ONCE per session and cached by URL in lapSamples.ts;
  // every driver and lap change after this is a decode of ~9 KB out of memory.
  useEffect(() => {
    let live = true;
    (async () => {
      const urls = await defaultSimSource.sessionUrls({ trackSlug: track, session });
      const [b, tm] = await Promise.all([
        loadSessionSamples(urls.binUrl),
        defaultSimSource.track(track),
      ]);
      if (!live) return;
      setBuf(b);
      setTrackModel(tm);
    })().catch((e: unknown) => live && setLoadError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [track, session]);

  useEffect(() => {
    if (!buf || drivers.length === 0) return;
    const a = drivers[0];
    const b = drivers[1] ?? drivers[0];
    setPick({
      a: { driver: a.driver, lap: defaultLap(buf, a, trackLengthM) },
      b: { driver: b.driver, lap: defaultLap(buf, b, trackLengthM) },
    });
  }, [buf, drivers, trackLengthM]);

  // The decimation budget is the chart's own pixel width, not the lap length, so a phone draws
  // a phone's worth of points (UI.md section 7.4).
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [widthPx, setWidthPx] = useState(720);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      if (w > 0) setWidthPx(Math.round(w / 8) * 8);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const grid = useMemo(() => stationGrid(trackLengthM, GRID_STEP_M), [trackLengthM]);

  const prepared = useMemo(() => {
    if (!buf || !pick) return null;
    try {
      const a = prepare(buf, drivers, pick.a, grid, trackLengthM);
      const b = prepare(buf, drivers, pick.b, grid, trackLengthM);
      if (!a || !b) return null;
      const delta = cumulativeDelta(a.lap, b.lap, grid, trackLengthM, a.along, b.along);
      return { a, b, delta, error: null as string | null };
    } catch (e: unknown) {
      return { a: null, b: null, delta: null, error: e instanceof Error ? e.message : String(e) };
    }
  }, [buf, pick, drivers, grid, trackLengthM]);

  const faults = (POSITION_FAULTS[track] ?? []).filter((f) => f.session === session);

  const corners = useMemo(
    () => (trackModel ? parseCorners(trackModel).corners : []),
    [trackModel],
  );

  const entryOf = (code: string) => drivers.find((d) => d.driver === code);
  const styleFor = (code: string): DriverStyle => {
    const d = entryOf(code);
    return d ? styleOf(d) : { colour: "#AEAEAE", dashed: false };
  };

  const body = () => {
    if (loadError) {
      return (
        <NoData
          title="Telemetry traces"
          reason={`The sample blob for this session could not be loaded: ${loadError}`}
        />
      );
    }
    if (!buf || !pick || !prepared) {
      return <p className={s.panelNote}>Loading sample blob (3–10 MB, fetched once per session)…</p>;
    }
    if (prepared.error || !prepared.a || !prepared.b || !prepared.delta) {
      return (
        <NoData
          title="Telemetry traces"
          reason={`This lap could not be decoded: ${prepared.error ?? "the manifest points outside the sample blob"}.`}
        />
      );
    }
    return (
      <Traces
        a={prepared.a}
        b={prepared.b}
        delta={prepared.delta}
        grid={grid}
        trackLengthM={trackLengthM}
        corners={corners}
        widthPx={widthPx}
      />
    );
  };

  return (
    <div className={s.wrap} ref={wrapRef}>
      {faults.map((f, i) => (
        <p key={i} className={`${s.caution} ${f.severity === "blocking" ? s.blocking : ""}`}>
          <span className={s.cautionHead}>
            Measured position fault — {f.severity === "blocking" ? "traces are gated" : "read with care"}
          </span>
          {f.text}
        </p>
      ))}

      <div className={s.pickers}>
        <DriverLapPicker
          side="A"
          drivers={drivers}
          buf={buf}
          trackLengthM={trackLengthM}
          value={pick?.a}
          colour={pick ? styleFor(pick.a.driver).colour : "#AEAEAE"}
          onChange={(next) => setPick((p) => (p ? { ...p, a: next } : p))}
        />
        <DriverLapPicker
          side="B"
          drivers={drivers}
          buf={buf}
          trackLengthM={trackLengthM}
          value={pick?.b}
          colour={pick ? styleFor(pick.b.driver).colour : "#AEAEAE"}
          onChange={(next) => setPick((p) => (p ? { ...p, b: next } : p))}
        />
        <div className={s.field}>
          <span className={s.fieldLabel}>Pair</span>
          <button
            type="button"
            className={s.swap}
            disabled={!pick}
            onClick={() => setPick((p) => (p ? { a: p.b, b: p.a } : p))}
          >
            Swap A / B
          </button>
        </div>
      </div>

      {prepared?.a && prepared?.b ? (
        <div className={s.facts}>
          <LapFacts side="A" prep={prepared.a} trackLengthM={trackLengthM} />
          <LapFacts side="B" prep={prepared.b} trackLengthM={trackLengthM} />
        </div>
      ) : null}

      {body()}

      <AwaitingModel
        title="Estimated ERS deploy along the lap"
        model="per-sample energy series"
        route="scripts/simdata/twin.py — one row per LAP today"
        detail="The energy twin emits per-lap estimates (deploy, harvest, balance, store), not a per-sample power series, so there is no along-lap deploy trace to place under these panels and no power envelope to overlay on it. This is UI.md section 6.1 gap 1, not an omission from this view."
      />
    </div>
  );
}

/* ------------------------------------------------------------- the panels --- */

interface Prepared {
  code: string;
  entry: RawLapEntry;
  lap: import("@/sim/data/codec").DecodedLap;
  along: AlongLap;
  re: ResampledLap;
  style: DriverStyle;
  frame: LapPositionFrame;
  problems: string[];
}

function Traces({
  a,
  b,
  delta,
  grid,
  trackLengthM,
  corners,
  widthPx,
}: {
  a: Prepared;
  b: Prepared;
  delta: Float32Array;
  grid: Float32Array;
  trackLengthM: number;
  corners: { number: number; station: number }[];
  widthPx: number;
}) {
  const labelA = `${a.code} L${a.entry.lap}`;
  const labelB = `${b.code} L${b.entry.lap}`;

  // Two cars from the same team — or the same driver on two laps — share a colour, so the
  // second is dashed. Identity is never colour alone: both lines also carry a direct label.
  const cA = a.style;
  const cB =
    a.style.colour === b.style.colour && a.style.dashed === b.style.dashed
      ? { ...b.style, dashed: true }
      : b.style;

  if (a.problems.length > 0 || b.problems.length > 0) {
    return (
      <NoData
        title="Telemetry traces"
        reason={
          <>
            The distance axis of at least one selected lap is not usable, so no trace is drawn.{" "}
            {a.problems.length > 0 ? `${labelA}: ${a.problems.join("; ")}. ` : ""}
            {b.problems.length > 0 ? `${labelB}: ${b.problems.join("; ")}. ` : ""}
            Pick another lap — the fault is per lap, not per session.
          </>
        }
      />
    );
  }

  const budget = Math.max(200, Math.min(2000, Math.round(widthPx * 2)));
  const xDomain: [number, number] = [0, trackLengthM];

  const speedA = decimateRuns(grid, a.re.speedKph, budget);
  const speedB = decimateRuns(grid, b.re.speedKph, budget);
  const thrA = decimateRuns(grid, a.re.throttlePct, budget);
  const thrB = decimateRuns(grid, b.re.throttlePct, budget);
  const gearA = decimateRuns(grid, a.re.gear, budget);
  const gearB = decimateRuns(grid, b.re.gear, budget);
  const dl = decimateRuns(grid, delta, budget);

  const spExt = extent([...speedA.y, ...speedB.y]);
  const gearExt = extent([...gearA.y, ...gearB.y]);
  const thrMax = Math.max(100, extent([...thrA.y, ...thrB.y])?.[1] ?? 100);
  const dExt = extent(dl.y);
  const dBound = Math.max(0.05, Math.abs(dExt?.[0] ?? 0), Math.abs(dExt?.[1] ?? 0));

  const step = trackLengthM / grid.length;
  const brakeA = runsOf(grid, a.re.brake, 1, step);
  const brakeB = runsOf(grid, b.re.brake, 1, step);
  const gapA = runsOf(grid, a.re.brake, null, step);
  const gapB = runsOf(grid, b.re.brake, null, step);

  const legend = [
    { label: labelA, colour: cA.colour, dashed: cA.dashed },
    { label: labelB, colour: cB.colour, dashed: cB.dashed },
  ];
  const xFormat = (v: number) => `${Math.round(v)} m`;
  // Corner numbers crowd below ~520 px; the rules stay, the labels drop.
  const showCornerLabels = widthPx >= 520;

  return (
    <>
      <ChartFrame
        title="Speed, throttle, brake and gear"
        units="against distance along the lap"
        provenance="OBSERVED"
        legend={legend}
        note={
          <>
            Four channels, four y axes, one x. The channels are measured; the x axis is derived —
            each sample&apos;s ring station comes from projecting its measured coordinates onto the
            circuit centreline, never from the telemetry distance channel, which is per-driver
            integrated wheel speed and disagrees between cars by up to 4.14%. Vertical rules on the
            speed panel are corner numbers from the track model. A break in a line is a station step
            the speed channel contradicts, left as a gap rather than drawn through.
          </>
        }
      >
        <div className={s.stack}>
          <Plot
            xDomain={xDomain}
            yDomain={[0, niceTicks(0, spExt?.[1] ?? 300, 4).niceMax]}
            height={210}
            margin={{ ...MARGIN, top: 16, bottom: 6 }}
            ariaLabel={`Speed against distance along the lap for ${labelA} and ${labelB}`}
            hover={{
              series: [
                { label: labelA, colour: cA.colour, x: speedA.x, y: speedA.y, format: kph },
                { label: labelB, colour: cB.colour, x: speedB.x, y: speedB.y, format: kph },
              ],
              xLabel: "station",
              xFormat,
            }}
          >
            <Grid />
            {corners.map((c) => (
              <RefLine
                key={c.number}
                x={c.station}
                label={showCornerLabels ? String(c.number) : undefined}
              />
            ))}
            <YAxis label="speed km/h" count={4} format={(v) => v.toFixed(0)} />
            <Line x={speedA.x} y={speedA.y} colour={cA.colour} dashed={cA.dashed} width={1.6} label={labelA} />
            <Line
              x={speedB.x}
              y={speedB.y}
              colour={cB.colour}
              dashed={cB.dashed}
              width={1.6}
              label={labelB}
              labelIndex={Math.max(0, Math.floor(speedB.x.length * 0.9))}
            />
          </Plot>

          <Plot
            xDomain={xDomain}
            yDomain={[0, thrMax + 6]}
            height={120}
            margin={{ ...MARGIN, top: 8, bottom: 6 }}
            ariaLabel={`Throttle against distance along the lap for ${labelA} and ${labelB}`}
            hover={{
              series: [
                { label: labelA, colour: cA.colour, x: thrA.x, y: thrA.y, format: pct },
                { label: labelB, colour: cB.colour, x: thrB.x, y: thrB.y, format: pct },
              ],
              xLabel: "station",
              xFormat,
            }}
          >
            <Grid count={3} />
            <YAxis label="throttle %" count={3} format={(v) => v.toFixed(0)} />
            <Line x={thrA.x} y={thrA.y} colour={cA.colour} dashed={cA.dashed} width={1.3} />
            <Line x={thrB.x} y={thrB.y} colour={cB.colour} dashed={cB.dashed} width={1.3} />
          </Plot>

          <Plot
            xDomain={xDomain}
            yDomain={[0, 1]}
            height={78}
            margin={{ ...MARGIN, top: 8, bottom: 6 }}
            ariaLabel={`Brake application against distance along the lap, ${labelA} on the upper lane and ${labelB} on the lower`}
          >
            {/* one lane per driver: brake is on or off, so a filled band says it without a y scale */}
            <BandLabels labels={[a.code, b.code]} />
            {gapA.map((r, i) => (
              <Band key={`ga${i}`} x={[r.from, r.to]} lo={[0.56, 0.56]} hi={[0.96, 0.96]} colour="#5A5A5A" opacity={0.3} />
            ))}
            {gapB.map((r, i) => (
              <Band key={`gb${i}`} x={[r.from, r.to]} lo={[0.04, 0.04]} hi={[0.44, 0.44]} colour="#5A5A5A" opacity={0.3} />
            ))}
            {brakeA.map((r, i) => (
              <Band key={`a${i}`} x={[r.from, r.to]} lo={[0.56, 0.56]} hi={[0.96, 0.96]} colour={cA.colour} opacity={0.85} />
            ))}
            {brakeB.map((r, i) => (
              <Band key={`b${i}`} x={[r.from, r.to]} lo={[0.04, 0.04]} hi={[0.44, 0.44]} colour={cB.colour} opacity={0.85} />
            ))}
          </Plot>

          <Plot
            xDomain={xDomain}
            yDomain={[0, (gearExt?.[1] ?? 8) + 0.6]}
            height={118}
            margin={{ ...MARGIN, top: 8, bottom: 26 }}
            ariaLabel={`Gear against distance along the lap for ${labelA} and ${labelB}`}
            hover={{
              series: [
                { label: labelA, colour: cA.colour, x: gearA.x, y: gearA.y, format: gearFmt },
                { label: labelB, colour: cB.colour, x: gearB.x, y: gearB.y, format: gearFmt },
              ],
              xLabel: "station",
              xFormat,
            }}
          >
            <Grid count={3} />
            <XAxis count={6} format={(v) => String(Math.round(v))} />
            <YAxis label="gear" count={4} format={(v) => v.toFixed(0)} />
            <Line x={gearA.x} y={gearA.y} colour={cA.colour} dashed={cA.dashed} width={1.3} step />
            <Line x={gearB.x} y={gearB.y} colour={cB.colour} dashed={cB.dashed} width={1.3} step />
          </Plot>
        </div>
      </ChartFrame>

      <ChartFrame
        title="Cumulative time delta"
        units={`${labelA} minus ${labelB}, seconds`}
        provenance="DERIVED"
        note={
          <>
            Positive means <strong>{a.code} is behind</strong> — it took longer to travel from the
            start/finish line to that point on the circuit. Zeroed at the first station both laps
            cover, because the two laps&apos; first samples sit a few metres apart on the ring and
            reading that offset as a gap would open the trace on a number nobody drove. This is a
            comparison of two laps on the same piece of road, not a measured on-track gap: the two
            cars were not there at the same time.
          </>
        }
      >
        <Plot
          xDomain={xDomain}
          yDomain={[-dBound * 1.18, dBound * 1.18]}
          height={168}
          margin={{ ...MARGIN, top: 10, bottom: 40 }}
          ariaLabel={`Cumulative time delta between ${labelA} and ${labelB} against distance along the lap`}
          hover={{
            series: [
              {
                label: `${labelA} − ${labelB}`,
                colour: cA.colour,
                x: dl.x,
                y: dl.y,
                format: (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(3)} s`,
              },
            ],
            xLabel: "station",
            xFormat,
          }}
        >
          <Grid />
          <XAxis label="distance along lap · ring station m" count={6} format={(v) => String(Math.round(v))} />
          <YAxis label="delta s" count={4} format={(v) => v.toFixed(2)} />
          <RefLine y={0} label={`level · ${a.code} behind above`} />
          <Line x={dl.x} y={dl.y} colour={cA.colour} width={1.8} />
        </Plot>
      </ChartFrame>
    </>
  );
}

/* ------------------------------------------------------------- the facts --- */

function LapFacts({
  side,
  prep,
  trackLengthM,
}: {
  side: "A" | "B";
  prep: Prepared;
  trackLengthM: number;
}) {
  const anomaly = prep.along.anomalyFraction;
  const coverage = trackLengthM > 0 ? prep.along.coveredM / trackLengthM : 0;
  return (
    <div className={s.fact}>
      <span className={s.factHead}>
        <span className={s.factSwatch} style={{ borderTopColor: prep.style.colour, borderTopStyle: prep.style.dashed ? "dashed" : "solid" }} />
        <span className={s.factCode}>
          {side} · {prep.code} lap {prep.entry.lap}
        </span>
      </span>
      <span className={s.factRow}>
        <span>lap time</span>
        <b>{prep.entry.time === null ? "—" : formatLapTime(prep.entry.time)}</b>
      </span>
      <span className={s.factRow}>
        <span>position frame</span>
        <b className={prep.frame === "A" ? undefined : s.bad}>
          {prep.frame === "A" ? "A · projected from measured coordinates" : prep.frame}
        </b>
      </span>
      <span className={s.factRow}>
        <span>ring covered</span>
        <b className={coverage < 0.9 ? s.bad : undefined}>{(coverage * 100).toFixed(1)}%</b>
      </span>
      <span className={s.factRow}>
        <span>station steps the speed channel contradicts</span>
        <b className={anomaly !== null && anomaly > STATION_ANOMALY_LIMIT ? s.bad : undefined}>
          {anomaly === null ? "—" : `${prep.along.anomalies} (${(anomaly * 100).toFixed(2)}%)`}
        </b>
      </span>
      {prep.along.absent > 0 ? (
        <span className={s.factRow}>
          <span>samples with no position at all</span>
          <b className={s.bad}>{prep.along.absent}</b>
        </span>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------- the picker --- */

function DriverLapPicker({
  side,
  drivers,
  buf,
  trackLengthM,
  value,
  colour,
  onChange,
}: {
  side: "A" | "B";
  drivers: RawDriverEntry[];
  buf: ArrayBuffer | null;
  trackLengthM: number;
  value: Pick | undefined;
  colour: string;
  onChange: (next: Pick) => void;
}) {
  const entry = value ? drivers.find((d) => d.driver === value.driver) : undefined;
  const laps = entry ? entry.laps.filter((l) => l.sampleCount > 0) : [];

  return (
    <>
      <div className={s.field}>
        <span className={s.fieldLabel} style={{ color: colour }}>
          Driver {side}
        </span>
        <span className={s.selectWrap}>
          <select
            className={s.select}
            value={value?.driver ?? ""}
            disabled={!value}
            aria-label={`Driver ${side}`}
            onChange={(e) => {
              const d = drivers.find((x) => x.driver === e.target.value);
              // changing driver re-picks their own best credible lap; carrying the old lap
              // number across would silently compare two unrelated laps
              if (d && buf) onChange({ driver: d.driver, lap: defaultLap(buf, d, trackLengthM) });
            }}
          >
            {drivers.map((d) => (
              <option key={d.driver} value={d.driver}>
                {d.driver} · {d.team ?? "unattached"}
              </option>
            ))}
          </select>
        </span>
      </div>

      <div className={s.field}>
        <span className={s.fieldLabel}>Lap {side}</span>
        <span className={s.selectWrap}>
          <select
            className={s.select}
            value={value?.lap ?? ""}
            disabled={!value || laps.length === 0}
            aria-label={`Lap for driver ${side}`}
            onChange={(e) => value && onChange({ ...value, lap: Number(e.target.value) })}
          >
            {laps.map((l) => (
              <option key={l.lap} value={l.lap}>
                {`L${l.lap} · ${l.time === null ? "no time" : formatLapTime(l.time)}${lapTag(l)}`}
              </option>
            ))}
          </select>
        </span>
      </div>
    </>
  );
}

/* ----------------------------------------------------------------- utils --- */

/** A lap that is a measure of pace: timed, accurate, not deleted, not a pit in/out lap. */
function isCleanLap(l: RawLapEntry): boolean {
  return (
    l.time !== null &&
    Number.isFinite(l.time) &&
    !l.del &&
    l.iacc &&
    l.pin === null &&
    l.pout === null &&
    l.sampleCount > 0
  );
}

/** Short suffix naming what makes a lap unusable as pace, so the picker never hides it. */
function lapTag(l: RawLapEntry): string {
  const tags: string[] = [];
  if (l.pin !== null) tags.push("pit in");
  if (l.pout !== null) tags.push("pit out");
  if (l.del) tags.push("deleted");
  if (!l.iacc) tags.push("inaccurate");
  if (lapPositionFrame(l) !== "A") tags.push(`frame ${lapPositionFrame(l)}`);
  return tags.length > 0 ? ` · ${tags.join(", ")}` : "";
}

/** How many candidate laps the default pick will decode before giving up. Bounds the work on a
 * session where every lap is corrupt (Hungary) without capping it so low that the scan misses
 * the clean laps outside the corrupt window. */
const DEFAULT_SCAN_LIMIT = 48;

/**
 * This driver's fastest clean lap whose distance axis is actually usable.
 *
 * Not simply the fastest clean lap: at Hungary the fastest laps are late-race and therefore
 * inside the stale-anchor window, so a fastest-lap default lands on corrupt geometry every time
 * (UI.md 1.4). Walking the clean laps in time order and taking the first that passes the same
 * checks the view enforces costs a handful of 9 KB decodes and makes the default honest.
 *
 * When nothing passes, the fastest clean lap is returned anyway — the view then explains which
 * check it failed, which is more useful than an empty picker.
 */
function defaultLap(buf: ArrayBuffer, driver: RawDriverEntry, trackLengthM: number): number {
  const clean = driver.laps.filter(isCleanLap).sort((x, y) => (x.time as number) - (y.time as number));
  const fallback = clean[0] ?? driver.laps.find((l) => l.sampleCount > 0);
  for (const l of clean.slice(0, DEFAULT_SCAN_LIMIT)) {
    if (lapPositionFrame(l) !== "A") continue;
    try {
      const along = alongLapDistance(sampleLapFromBuffer(buf, l), trackLengthM);
      if (checkLap(along, trackLengthM).length === 0) return l.lap;
    } catch {
      // a lap the blob cannot serve is simply not a candidate
    }
  }
  return fallback?.lap ?? driver.laps[0]?.lap ?? 1;
}

/** What is measurably wrong with this lap's distance axis. Empty means it can be drawn. */
function checkLap(along: AlongLap, trackLengthM: number): string[] {
  const out: string[] = [];
  const f = along.anomalyFraction;
  if (f !== null && f > STATION_ANOMALY_LIMIT) {
    out.push(
      `${along.anomalies} of ${along.positioned} station steps disagree with the speed channel (${(f * 100).toFixed(1)}%, limit ${(STATION_ANOMALY_LIMIT * 100).toFixed(0)}%)`,
    );
  }
  if (along.positioned < 2) out.push("no positioned samples");
  else if (along.coveredM < 0.8 * trackLengthM) {
    out.push(`the samples cover only ${((100 * along.coveredM) / trackLengthM).toFixed(0)}% of the ring`);
  }
  return out;
}

function prepare(
  buf: ArrayBuffer,
  drivers: RawDriverEntry[],
  pick: Pick,
  grid: Float32Array,
  trackLengthM: number,
): Prepared | null {
  const d = drivers.find((x) => x.driver === pick.driver);
  const entry = d?.laps.find((l) => l.lap === pick.lap);
  if (!d || !entry) return null;
  const lap = sampleLapFromBuffer(buf, entry);
  const along = alongLapDistance(lap, trackLengthM);
  const frame = lapPositionFrame(entry);
  const problems = checkLap(along, trackLengthM);
  if (frame !== "A") {
    problems.unshift(
      frame === "B"
        ? "position frame B — station was normalised from the distance channel, not projected from measured coordinates"
        : `position frame ${frame} — this lap's positions are not measured`,
    );
  }
  return {
    code: d.driver,
    entry,
    lap,
    along,
    re: resampleByStation(lap, grid, trackLengthM, along),
    style: { colour: "#AEAEAE", dashed: false },
    frame,
    problems,
  };
}

/**
 * Decimates each contiguous run of finite samples separately and rejoins them with a NaN.
 *
 * decimate() drops non-finite points, which would silently CLOSE every gap: a lap with a
 * broken station step would come back as one continuous line straight across the hole, which is
 * the opposite of what the hole means. Splitting first keeps a gap a gap, and the budget is
 * shared between runs in proportion to their length so the total stays bounded by pixel width.
 */
function decimateRuns(
  xs: Float32Array,
  ys: Float32Array,
  budget: number,
): { x: number[]; y: number[] } {
  const runs: [number, number][] = [];
  let start = -1;
  for (let i = 0; i < ys.length; i++) {
    const ok = Number.isFinite(xs[i]) && Number.isFinite(ys[i]);
    if (ok && start < 0) start = i;
    else if (!ok && start >= 0) {
      runs.push([start, i]);
      start = -1;
    }
  }
  if (start >= 0) runs.push([start, ys.length]);

  const total = runs.reduce((n, [p, q]) => n + (q - p), 0);
  const x: number[] = [];
  const y: number[] = [];
  for (const [p, q] of runs) {
    const share = total > 0 ? Math.max(3, Math.round((budget * (q - p)) / total)) : 0;
    const d = decimate(xs.subarray(p, q), ys.subarray(p, q), share);
    if (x.length > 0) {
      x.push(NaN);
      y.push(NaN);
    }
    for (let i = 0; i < d.x.length; i++) {
      x.push(d.x[i]);
      y.push(d.y[i]);
    }
  }
  return { x, y };
}

/** Contiguous x ranges where `values` equals `want`; `want: null` finds the unknown (NaN) runs.
 * Ranges are widened by half a grid step so a single-sample run is still a visible band. */
function runsOf(
  grid: Float32Array,
  values: Float32Array,
  want: number | null,
  stepM: number,
): { from: number; to: number }[] {
  const out: { from: number; to: number }[] = [];
  const hit = (v: number) => (want === null ? !Number.isFinite(v) : v === want);
  let start = -1;
  for (let i = 0; i < values.length; i++) {
    if (hit(values[i]) && start < 0) start = i;
    else if (!hit(values[i]) && start >= 0) {
      out.push({ from: grid[start] - stepM / 2, to: grid[i - 1] + stepM / 2 });
      start = -1;
    }
  }
  if (start >= 0) {
    out.push({ from: grid[start] - stepM / 2, to: grid[values.length - 1] + stepM / 2 });
  }
  return out;
}

function formatLapTime(t: number): string {
  const m = Math.floor(t / 60);
  const sec = t - m * 60;
  return m > 0 ? `${m}:${sec.toFixed(3).padStart(6, "0")}` : `${sec.toFixed(3)}`;
}

const kph = (v: number) => `${v.toFixed(0)} km/h`;
const pct = (v: number) => `${v.toFixed(0)}%`;
const gearFmt = (v: number) => (v === 0 ? "N" : String(Math.round(v)));
