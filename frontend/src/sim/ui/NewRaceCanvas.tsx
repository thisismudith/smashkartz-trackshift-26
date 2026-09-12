"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { SimStore } from "../store/simStore";
import { SimRenderer, type CameraMode } from "../render/scene";
import { parseTrackModel, type RawTrackModel } from "../data/manifest";
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

interface CatalogueDriver { code: string; team: string | null; colour: string | null; number: string | null }
interface CatalogueTeam { team: string; colour: string }
interface CatalogueTrack { event: string; slug: string; raceLaps: number | null }
interface Catalogue { drivers: CatalogueDriver[]; teams: CatalogueTeam[]; tracks: CatalogueTrack[] }

const CAMERA_MODES: CameraMode[] = ["broadcast", "onboard", "helicopter", "orbit"];

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
  const [cameraMode, setCameraMode] = useState<CameraMode>("broadcast");
  const [selectedDriver, setSelectedDriver] = useState<string | null>(null);
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [indexData, setIndexData] = useState<SimIndex | null>(null);
  const [trackSlug, setTrackSlug] = useState<string>("");
  const [laps, setLaps] = useState<number>(20);
  const [selectedDrivers, setSelectedDrivers] = useState<Set<string>>(new Set());
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
        const first = Object.keys(index.tracks)[0];
        setTrackSlug(first);
        const trackMeta = cat.tracks.find((t) => t.slug === first);
        if (trackMeta?.raceLaps) setLaps(trackMeta.raceLaps);
        // default grid: every driver the catalogue knows, so a first-time visitor can
        // hit Start immediately; they can then thin the field out or start fresh.
        setSelectedDrivers(new Set(cat.drivers.map((d) => d.code)));
      } catch (err) {
        if (!disposed) setError(err instanceof Error ? err.message : String(err));
      }
    }
    void load();
    return () => { disposed = true; };
  }, []);

  useEffect(() => () => { rendererRef.current?.dispose(); store.dispose(); }, [store]);
  useEffect(() => { rendererRef.current?.setCameraMode(cameraMode); }, [cameraMode]);

  async function startRace() {
    if (!indexData || !catalogue) return;
    setError(null);
    try {
      const trackFile = indexData.tracks[trackSlug];
      const trackUrl = `/sim/${trackFile}`;
      const paramsUrl = `/sim/${indexData.params}`;
      const teamColourBySlug = new Map(catalogue.teams.map((t) => [t.team, `#${t.colour}`]));
      const grid = catalogue.drivers
        .filter((d) => selectedDrivers.has(d.code))
        .map((d) => ({ driver: d.code, team: d.team }));
      if (grid.length < 2) throw new Error("select at least 2 drivers");

      store.startGenerated({ trackUrl, paramsUrl, entries: grid, totalLaps: laps, seed });

      const trackRaw: RawTrackModel = await fetch(trackUrl).then((r) => r.json());
      if (!canvasRef.current) return;
      const track = parseTrackModel(trackRaw);

      if (!rendererRef.current) {
        rendererRef.current = new SimRenderer(canvasRef.current, store);
        rendererRef.current.start();
        const ro = new ResizeObserver(() => rendererRef.current?.resize());
        ro.observe(canvasRef.current);
      }
      rendererRef.current.setTrack(track);
      const teamColours = grid.map((d) => (d.team ? teamColourBySlug.get(d.team) ?? null : null));
      rendererRef.current.setDrivers(grid.length, teamColours, grid.map((d) => d.driver));
      setStarted(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const builtTracks = useMemo(
    () => (catalogue ? catalogue.tracks.filter((t) => indexData?.tracks[t.slug]) : []),
    [catalogue, indexData],
  );

  if (!started) {
    return catalogue ? (
      <GridBuilder
        drivers={catalogue.drivers}
        teams={catalogue.teams}
        tracks={builtTracks}
        trackSlug={trackSlug}
        laps={laps}
        seed={seed}
        selected={selectedDrivers}
        onTrackChange={(slug) => {
          setTrackSlug(slug);
          const meta = catalogue.tracks.find((t) => t.slug === slug);
          if (meta?.raceLaps) setLaps(meta.raceLaps);
        }}
        onLapsChange={setLaps}
        onSeedChange={setSeed}
        onToggleDriver={(code) => setSelectedDrivers((prev) => {
          const next = new Set(prev);
          if (next.has(code)) next.delete(code); else next.add(code);
          return next;
        })}
        onSelectAll={() => setSelectedDrivers(new Set(catalogue.drivers.map((d) => d.code)))}
        onClear={() => setSelectedDrivers(new Set())}
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
          <button type="button" onClick={() => store.play()}>Play</button>
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
                <DriverPanel row={selectedRow} />
                <RaceControlFeed events={meta?.events ?? []} sessionTime={dashboard.sessionTime} />
              </div>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
