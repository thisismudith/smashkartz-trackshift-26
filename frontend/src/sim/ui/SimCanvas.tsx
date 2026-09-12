"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { SimStore } from "../store/simStore";
import {
  SimRenderer, type CameraMode, type GpuInfo, type GpuPreference, type PerfStats,
} from "../render/scene";
import { parseTrackModel, type RawTrackModel } from "../data/manifest";
import { DriverPanel } from "./DriverPanel";
import { GpuBadge } from "./GpuBadge";
import { RaceControlFeed } from "./RaceControlFeed";
import { type CatalogueTrack, type Selection, SessionSelector } from "./SessionSelector";
import { TimelineScrubber } from "./TimelineScrubber";
import { TrackLegend } from "./TrackLegend";
import { WeatherStrip } from "./WeatherStrip";
import styles from "./sim.module.css";

interface SimIndex {
  catalogue: string;
  params: string;
  tracks: Record<string, string>;
  sessions: Record<string, Record<string, { manifest: string; bin: string }>>;
}

interface RawManifestLite {
  drivers: { driver: string; team: string | null }[];
}

interface Catalogue {
  teams: { team: string; colour: string }[];
  tracks: CatalogueTrack[];
}

const CAMERA_MODES: CameraMode[] = ["broadcast", "onboard", "helicopter", "orbit"];

export default function SimCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // A lazy useState initializer (not a ref) creates the singleton exactly once
  // without ever reading a ref's `.current` during render, which is required for the
  // subscribe/getSnapshot functions handed to useSyncExternalStore below.
  const [store] = useState(() => new SimStore());
  const rendererRef = useRef<SimRenderer | null>(null);
  const [cameraMode, setCameraMode] = useState<CameraMode>("broadcast");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedDriver, setSelectedDriver] = useState<string | null>(null);
  const [gpuInfo, setGpuInfo] = useState<GpuInfo | null>(null);
  const [perf, setPerf] = useState<PerfStats | null>(null);
  const [gpuPref, setGpuPref] = useState<GpuPreference>("high-performance");
  const [hasPitLane, setHasPitLane] = useState(false);

  const [index, setIndex] = useState<SimIndex | null>(null);
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);

  const state = useSyncExternalStore(store.subscribe, store.getState, store.getState);
  const dashboard = useSyncExternalStore(store.subscribe, store.getDashboard, store.getDashboard);
  const meta = useSyncExternalStore(store.subscribe, store.getMeta, store.getMeta);

  const selectedRow = selectedDriver
    ? dashboard?.leaderboard.find((r) => r.driver === selectedDriver) ?? null
    : null;

  // The single place a session change is requested from: resets the per-session UI
  // state as part of the SAME event/callback that changes the selection, rather than
  // reactively inside the boot effect (setState directly in an effect body causes an
  // extra, avoidable render pass).
  function changeSelection(next: Selection) {
    setSelection(next);
    setSelectedDriver(null);
    setGpuInfo(null);
    setPerf(null);
  }

  function selectDriver(driver: string) {
    setSelectedDriver(driver);
    const idx = state.driverList.indexOf(driver);
    if (idx >= 0) rendererRef.current?.setFocusIndex(idx);
  }

  // Load the index + catalogue once, purely to populate the selector; does not boot
  // a session by itself (that happens in the effect below once a selection exists).
  useEffect(() => {
    let disposed = false;
    async function load() {
      try {
        const pointer = await fetch("/sim/index.json").then((r) => r.json());
        const idx: SimIndex = await fetch(`/sim/${pointer.latest}`).then((r) => r.json());
        const cat: Catalogue = await fetch(`/sim/${idx.catalogue}`).then((r) => r.json());
        if (disposed) return;
        setIndex(idx);
        setCatalogue(cat);
        const firstSlug = Object.keys(idx.sessions)[0];
        const firstSession = firstSlug ? Object.keys(idx.sessions[firstSlug])[0] : null;
        if (firstSlug && firstSession) changeSelection({ trackSlug: firstSlug, session: firstSession });
      } catch (err) {
        if (!disposed) setLoadError(err instanceof Error ? err.message : String(err));
      }
    }
    void load();
    return () => { disposed = true; };
  }, []);

  // Boots (or re-boots, on a selection change) the worker + renderer for one
  // session. Tears down the previous renderer/store cleanly first so switching
  // track or session never leaks a WebGL context or a running worker.
  useEffect(() => {
    if (!index || !selection) return;
    let disposed = false;

    async function boot() {
      try {
        const sel = selection!;
        const trackFile = index!.tracks[sel.trackSlug];
        const sessionFiles = index!.sessions[sel.trackSlug]?.[sel.session];
        if (!trackFile || !sessionFiles) throw new Error(`no built data for ${sel.trackSlug}/${sel.session}`);

        const trackUrl = `/sim/${trackFile}`;
        const manifestUrl = `/sim/${sessionFiles.manifest}`;
        const binUrl = `/sim/${sessionFiles.bin}`;

        store.start(trackUrl, manifestUrl, binUrl);

        const [trackRaw, manifestRaw, cat]: [RawTrackModel, RawManifestLite, Catalogue] =
          await Promise.all([
            fetch(trackUrl).then((r) => r.json()),
            fetch(manifestUrl).then((r) => r.json()),
            fetch(`/sim/${index!.catalogue}`).then((r) => r.json()),
          ]);
        if (disposed || !canvasRef.current) return;

        const teamColourBySlug = new Map(cat.teams.map((t) => [t.team, `#${t.colour}`]));
        const track = parseTrackModel(trackRaw);
        setHasPitLane(track.pitLanePath !== null);
        rendererRef.current?.dispose();
        const renderer = new SimRenderer(canvasRef.current, store, gpuPref);
        renderer.setTrack(track);
        const teamColours: (string | null)[] = manifestRaw.drivers.map(
          (d) => (d.team ? teamColourBySlug.get(d.team) ?? null : null),
        );
        renderer.setDrivers(manifestRaw.drivers.length, teamColours);
        renderer.setCameraMode(cameraMode);
        renderer.onPerf(setPerf);
        renderer.start();
        rendererRef.current = renderer;
        setGpuInfo(renderer.getGpuInfo());

        const ro = new ResizeObserver(() => renderer.resize());
        ro.observe(canvasRef.current);
        return () => ro.disconnect();
      } catch (err) {
        if (!disposed) setLoadError(err instanceof Error ? err.message : String(err));
      }
    }
    void boot();
    return () => { disposed = true; };
    // cameraMode intentionally excluded: it is applied via the dedicated effect below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, selection, store, gpuPref]);

  useEffect(() => () => {
    rendererRef.current?.dispose();
    store.dispose();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    rendererRef.current?.setCameraMode(cameraMode);
  }, [cameraMode]);

  const builtSessions: Record<string, string[]> = index
    ? Object.fromEntries(Object.entries(index.sessions).map(([slug, s]) => [slug, Object.keys(s)]))
    : {};

  return (
    <div className={styles.stage}>
      <canvas ref={canvasRef} className={styles.canvas} />
      <div className={styles.hud}>
        <div className={styles.provenance}>
          {state.error || loadError ? (
            <span className={styles.error}>{state.error ?? loadError}</span>
          ) : (
            <span>Official result — replay</span>
          )}
        </div>
        {catalogue && selection ? (
          <SessionSelector
            tracks={catalogue.tracks}
            builtSessions={builtSessions}
            value={selection}
            onChange={changeSelection}
          />
        ) : null}
        <TrackLegend hasPitLane={hasPitLane} />
        <GpuBadge
          gpu={gpuInfo}
          perf={perf}
          preference={gpuPref}
          onPreferenceChange={setGpuPref}
        />
        <div className={styles.controls}>
          <button type="button" onClick={() => store.play()}>Play</button>
          <button type="button" onClick={() => store.pause()}>Pause</button>
          <button type="button" onClick={() => store.setSpeed(1)}>1x</button>
          <button type="button" onClick={() => store.setSpeed(4)}>4x</button>
          <button type="button" onClick={() => store.setSpeed(20)}>20x</button>
          {CAMERA_MODES.map((m) => (
            <button
              key={m}
              type="button"
              data-active={cameraMode === m}
              onClick={() => setCameraMode(m)}
            >
              {m}
            </button>
          ))}
        </div>
        {dashboard ? (
          <TimelineScrubber
            sessionTime={dashboard.sessionTime}
            duration={state.duration}
            neutralisations={meta?.neutralisations ?? []}
            onSeek={(t) => store.seek(t)}
          />
        ) : null}
        <WeatherStrip weather={meta?.weather ?? null} sessionTime={dashboard?.sessionTime ?? 0} />
        <div className={styles.row}>
          <table className={styles.leaderboard}>
            <tbody>
              {dashboard?.leaderboard.map((row) => (
                <tr
                  key={row.driver}
                  data-selected={row.driver === selectedDriver}
                  onClick={() => selectDriver(row.driver)}
                >
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
            <RaceControlFeed events={meta?.events ?? []} sessionTime={dashboard?.sessionTime ?? 0} />
          </div>
        </div>
      </div>
    </div>
  );
}
