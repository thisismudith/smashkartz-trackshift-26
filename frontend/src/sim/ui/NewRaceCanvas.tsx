"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { SimStore } from "../store/simStore";
import { SimRenderer, type CameraMode } from "../render/scene";
import { parseTrackModel, type RawTrackModel } from "../data/manifest";
import { defaultSimSource, type RuleSet } from "../data/source";
import { DriverPanel } from "./DriverPanel";
import { GridBuilder } from "./GridBuilder";
import { RaceControlFeed } from "./RaceControlFeed";
import { TimelineScrubber } from "./TimelineScrubber";
import styles from "./sim.module.css";

interface SimIndex {
  catalogue: string;
  params: string;
  tracks: Record<string, string>;
}

interface CatalogueDriver {
  code: string; team: string | null; colour: string | null; number: string | null;
  firstName?: string | null; lastName?: string | null;
  /** Sessions this exact (code, team) pairing was seen in -- see driver_registry's own
   * docstring in scripts/simdata/catalogue.py. Used only to pick ONE row per code below. */
  sessions?: number;
}
interface CatalogueTeam { team: string; colour: string }
interface WeatherBand { min: number; median: number; max: number }
interface CatalogueEntry {
  code: string; number: string | null; team: string | null; colour: string | null;
  firstName: string | null; lastName: string | null;
}
interface CatalogueTrack {
  event: string;
  slug: string;
  year: number | null;
  raceLaps: number | null;
  /** The cars that actually started this Grand Prix; absent in pre-entries artifacts. */
  entries?: CatalogueEntry[];
  weather?: { airTempC: WeatherBand | null; trackTempC: WeatherBand | null } | null;
  tyres?: { compounds: string[] } | null;
}
interface Catalogue { drivers: CatalogueDriver[]; teams: CatalogueTeam[]; tracks: CatalogueTrack[] }

const CAMERA_MODES: CameraMode[] = ["broadcast", "onboard", "helicopter", "orbit"];

/** One row per driver code, keeping whichever (code, team) pairing has the most
 * sessions -- see the registry's own (code, team) keying, documented where this is
 * called. Stable in the input's own first-seen order, so the roster reads the same
 * way run to run. */
function dedupeByCode(drivers: CatalogueDriver[]): CatalogueDriver[] {
  const best = new Map<string, CatalogueDriver>();
  const order: string[] = [];
  for (const d of drivers) {
    const prior = best.get(d.code);
    if (!prior) order.push(d.code);
    if (!prior || (d.sessions ?? 0) > (prior.sessions ?? 0)) best.set(d.code, d);
  }
  return order.map((code) => best.get(code)!);
}

/**
 * The New Race configurator: bounded entirely by the catalogue (plan section 9) --
 * track, lap count, field size and seed. Conditions are limited to what the
 * catalogue actually offers rather than free text, so a run can never ask the engine
 * for something outside its fitted parameters' reach.
 */
export default function NewRaceCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [store] = useState(() => new SimStore());
  const rendererRef = useRef<SimRenderer | null>(null);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  // the armed run: set by startRace, handed to the renderer once the stage has mounted
  const [race, setRace] = useState<{
    track: ReturnType<typeof parseTrackModel>;
    drivers: string[];
    colours: (string | null)[];
  } | null>(null);
  const [cameraMode, setCameraMode] = useState<CameraMode>("broadcast");
  const [selectedDriver, setSelectedDriver] = useState<string | null>(null);
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [rules, setRules] = useState<RuleSet | null>(null);
  const [indexData, setIndexData] = useState<SimIndex | null>(null);
  const [trackSlug, setTrackSlug] = useState<string>("");
  const [laps, setLaps] = useState<number>(20);
  // Held as the cars taken OUT of the race rather than the ones left in: the default is
  // always "the whole entry list", so a freshly loaded catalogue or a switch to another
  // Grand Prix needs no effect to re-seat the selection -- an empty exclusion set
  // already means every car that started that race.
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [seed, setSeed] = useState<number>(1);
  const [started, setStarted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const state = useSyncExternalStore(store.subscribe, store.getState, store.getState);
  const dashboard = useSyncExternalStore(store.subscribe, store.getDashboard, store.getDashboard);
  const meta = useSyncExternalStore(store.subscribe, store.getMeta, store.getMeta);

  const selectedRow = selectedDriver
    ? dashboard?.leaderboard.find((r) => r.driver === selectedDriver) ?? null
    : null;

  useEffect(() => {
    let disposed = false;
    async function load() {
      try {
        const idxPointer = await fetch("/sim/index.json").then((r) => r.json());
        const index: SimIndex = await fetch(`/sim/${idxPointer.latest}`).then((r) => r.json());
        const cat: Catalogue = await fetch(`/sim/${index.catalogue}`).then((r) => r.json());
        if (disposed) return;
        setIndexData(index);
        setCatalogue(cat);
        // NOTE: the fetches above predate the data seam and talk to /sim directly.
        // This one goes through defaultSimSource, which is where they all belong.
        setRules(await defaultSimSource.rules());
        const first = Object.keys(index.tracks)[0];
        setTrackSlug(first);
        const trackMeta = cat.tracks.find((t) => t.slug === first);
        if (trackMeta?.raceLaps) setLaps(trackMeta.raceLaps);
      } catch (err) {
        if (!disposed) setError(err instanceof Error ? err.message : String(err));
      }
    }
    void load();
    return () => { disposed = true; };
  }, []);

  useEffect(() => () => { rendererRef.current?.dispose(); store.dispose(); }, [store]);
  useEffect(() => { rendererRef.current?.setCameraMode(cameraMode); }, [cameraMode]);

  /**
   * The renderer is attached here rather than inside startRace because the <canvas>
   * only exists once the configurator has been swapped out for the stage: reading
   * canvasRef during the click handler always found null, so the run was armed in the
   * store and then never shown. Attaching in an effect runs after that swap, and is
   * what an effect is for -- handing React state to an external system (WebGL).
   */
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!race || !canvas) return;
    if (!rendererRef.current) {
      rendererRef.current = new SimRenderer(canvas, store);
      rendererRef.current.start();
      rendererRef.current.setCameraMode(cameraMode);
      const ro = new ResizeObserver(() => rendererRef.current?.resize());
      ro.observe(canvas);
      resizeObserverRef.current = ro;
    }
    rendererRef.current.setTrack(race.track);
    rendererRef.current.setDrivers(race.drivers.length, race.colours, race.drivers);
    // cameraMode is read once, on creation; its own effect keeps it in step after that
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [race, store]);

  useEffect(() => () => { resizeObserverRef.current?.disconnect(); }, []);

  async function startRace() {
    if (!indexData || !catalogue) return;
    setError(null);
    try {
      const trackFile = indexData.tracks[trackSlug];
      const trackUrl = `/sim/${trackFile}`;
      const paramsUrl = `/sim/${indexData.params}`;
      const teamColourBySlug = new Map(catalogue.teams.map((t) => [t.team, `#${t.colour}`]));
      const grid = entries
        .filter((d) => !excluded.has(d.code))
        .map((d) => ({ driver: d.code, team: d.team, colour: d.colour }));
      if (grid.length < 2) throw new Error("select at least 2 drivers");

      store.startGenerated({ trackUrl, paramsUrl, entries: grid, totalLaps: laps, seed });

      const trackRaw: RawTrackModel = await fetch(trackUrl).then((r) => r.json());
      setRace({
        track: parseTrackModel(trackRaw),
        drivers: grid.map((d) => d.driver),
        colours: grid.map((d) =>
          (d.colour ? `#${d.colour}` : null) ?? (d.team ? teamColourBySlug.get(d.team) ?? null : null)),
      });
      setStarted(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const builtTracks = useMemo(
    () => (catalogue ? catalogue.tracks.filter((t) => indexData?.tracks[t.slug]) : []),
    [catalogue, indexData],
  );

  const track = useMemo(
    () => builtTracks.find((t) => t.slug === trackSlug) ?? null,
    [builtTracks, trackSlug],
  );

  /**
   * The field on offer is the Grand Prix's own entry list, not the season-wide driver
   * registry: that registry is the union over every session, so it carries reserve and
   * rookie drivers who only ran a Friday practice. Artifacts built before the catalogue
   * carried `entries` fall back to the registry so an old build still runs.
   *
   * The registry itself is keyed (code, team), DELIBERATELY carrying two rows for a
   * driver who changed team mid-season (measured: LAW Racing Bulls -> Red Bull, ARO
   * Audi -> Alpine, IWA Racing Bulls -> Red Bull) -- see driver_registry's docstring.
   * A grid needs exactly one car per driver CODE, so the fallback collapses to the
   * row with the most sessions (this driver's most representative team this season)
   * before anything downstream keys a list or a race entry by `code` alone. Without
   * this, React saw two rows sharing one key and one driver's row would silently
   * freeze or vanish -- indistinguishable, from the grid, from a car stuck on GRID.
   */
  const entries: CatalogueEntry[] = track?.entries?.length
    ? track.entries
    : dedupeByCode(catalogue?.drivers ?? []).map((d) => ({
        code: d.code, number: d.number, team: d.team, colour: d.colour,
        firstName: d.firstName ?? null, lastName: d.lastName ?? null,
      }));

  const selectedDrivers = new Set(
    entries.filter((e) => !excluded.has(e.code)).map((e) => e.code),
  );

  if (!started) {
    return catalogue ? (
      <GridBuilder
        entries={entries}
        teams={catalogue.teams}
        tracks={builtTracks}
        track={track}
        trackSlug={trackSlug}
        laps={laps}
        seed={seed}
        selected={selectedDrivers}
        onTrackChange={(slug) => {
          setTrackSlug(slug);
          const meta = catalogue.tracks.find((t) => t.slug === slug);
          if (meta?.raceLaps) setLaps(meta.raceLaps);
          // a different race is a different entry list: start it as the full field
          setExcluded(new Set());
        }}
        onLapsChange={setLaps}
        onSeedChange={setSeed}
        onToggleDriver={(code) => setExcluded((prev) => {
          const next = new Set(prev);
          if (next.has(code)) next.delete(code); else next.add(code);
          return next;
        })}
        onToggleTeam={(codes, on) => setExcluded((prev) => {
          const next = new Set(prev);
          for (const code of codes) { if (on) next.delete(code); else next.add(code); }
          return next;
        })}
        onSelectAll={() => setExcluded(new Set())}
        onClear={() => setExcluded(new Set(entries.map((e) => e.code)))}
        onStart={() => void startRace()}
        error={error}
      />
    ) : (
      <div className={styles.stage}>
        <div className={styles.provenance}>{error ?? "Loading catalogue…"}</div>
      </div>
    );
  }

  return (
    <div className={styles.stage}>
      <canvas ref={canvasRef} className={styles.canvas} />
      <div className={styles.hud}>
        <div className={styles.provenance}>
          {error ? (
            <span className={styles.error}>{error}</span>
          ) : (
            <span style={{ color: "var(--haas-red)" }}>
              Simulated finish — seed {seed}
            </span>
          )}
        </div>

        <div className={styles.controls}>
          <button type="button" onClick={() => (state.playing ? store.pause() : store.play())}>
            {state.playing ? "Pause" : state.atEnd ? "Replay" : "Play"}
          </button>
          <button type="button" onClick={() => store.pause()}>Pause</button>
          <button type="button" onClick={() => store.setSpeed(1)}>1x</button>
          <button type="button" onClick={() => store.setSpeed(4)}>4x</button>
          <button type="button" onClick={() => store.setSpeed(20)}>20x</button>
          <button type="button" onClick={() => setSeed((s) => s + 1)}>New seed</button>
          <button type="button" onClick={() => void startRace()}>Re-roll</button>
          {CAMERA_MODES.map((m) => (
            <button key={m} type="button" data-active={cameraMode === m} onClick={() => setCameraMode(m)}>
              {m}
            </button>
          ))}
        </div>

        {dashboard ? (
          <>
            <TimelineScrubber
              sessionTime={dashboard.sessionTime}
              duration={state.duration}
              neutralisations={meta?.neutralisations ?? []}
              onSeek={(t) => store.seek(t)}
            />
            <div className={styles.row}>
              <table className={styles.leaderboard}>
                <tbody>
                  {dashboard.leaderboard.map((row) => (
                    <tr key={row.driver} data-selected={row.driver === selectedDriver} onClick={() => setSelectedDriver(row.driver)}>
                      <td>{row.position}</td>
                      <td>{row.driver}</td>
                      <td>{row.gapToLeader}</td>
                      <td>{row.compound ?? "—"}</td>
                      <td>{row.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className={styles.side}>
                <DriverPanel row={selectedRow} rules={rules} />
                <RaceControlFeed events={meta?.events ?? []} sessionTime={dashboard.sessionTime} />
              </div>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
