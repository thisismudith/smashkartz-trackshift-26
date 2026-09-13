"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { SimStore } from "../store/simStore";
import {
  CAMERA_CONTROL_HELP, SimRenderer,
  type CameraMode, type GpuInfo, type GpuPreference, type PerfStats,
} from "../render/scene";
import { parseTrackModel, trackPointAt, type RawTrackModel } from "../data/manifest";
import { defaultSimSource, type RuleSet } from "../data/source";
import { DriverPanel } from "./DriverPanel";
import { EnvironmentPanel } from "./EnvironmentPanel";
import { GpuBadge } from "./GpuBadge";
import { RaceControlFeed } from "./RaceControlFeed";
import { type CatalogueTrack, type Selection, SessionSelector } from "./SessionSelector";
import { TimelineScrubber } from "./TimelineScrubber";
import { TrackLegend } from "./TrackLegend";
import { CollapsiblePanel } from "./CollapsiblePanel";
import { OvertakePanel } from "./OvertakePanel";
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
  drivers: { code: string; team: string | null; number: string | null }[];
}

const CAMERA_MODES: CameraMode[] = ["broadcast", "onboard", "helicopter", "orbit"];
// 100x was dropped deliberately: telemetry arrives every ~130 ms, so at 100x a
// sample lasts 1.3 ms of wall time and the renderer skips data it cannot draw.
// 20x is already the point where one drawn frame ~ one telemetry sample.
const SPEEDS = [1, 2, 5, 10, 20];

export default function SimCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // A lazy useState initializer (not a ref) creates the singleton exactly once
  // without ever reading a ref's `.current` during render, which is required for the
  // subscribe/getSnapshot functions handed to useSyncExternalStore below.
  const [store] = useState(() => new SimStore());
  const rendererRef = useRef<SimRenderer | null>(null);
  // The same renderer, mirrored into state purely so panels that need to talk
  // to it re-render when it is (re)built. A ref alone never triggers one, so a
  // panel reading rendererRef.current would hold the null it saw on first paint.
  const [renderer, setRenderer] = useState<SimRenderer | null>(null);
  // Held so panels can resolve a station into a point on the ring -- the wind's
  // head/cross split needs the direction of travel, which only the ring knows.
  const [trackModel, setTrackModel] = useState<ReturnType<typeof parseTrackModel> | null>(null);
  const [cameraMode, setCameraMode] = useState<CameraMode>("broadcast");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedDriver, setSelectedDriver] = useState<string | null>(null);
  const [gpuInfo, setGpuInfo] = useState<GpuInfo | null>(null);
  const [perf, setPerf] = useState<PerfStats | null>(null);
  const [gpuPref, setGpuPref] = useState<GpuPreference>("high-performance");
  const [hasPitLane, setHasPitLane] = useState(false);
  const [showTags, setShowTags] = useState(true);
  const [dimField, setDimField] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [cameraLocked, setCameraLocked] = useState(true);
  /** driver code -> car number / team colour, for the compact leaderboard */
  const [driverMeta, setDriverMeta] = useState<Map<string, { num: string | null; colour: string | null }>>(new Map());

  const [rules, setRules] = useState<RuleSet | null>(null);
  const [index, setIndex] = useState<SimIndex | null>(null);
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);

  const state = useSyncExternalStore(store.subscribe, store.getState, store.getState);
  const dashboard = useSyncExternalStore(store.subscribe, store.getDashboard, store.getDashboard);
  const meta = useSyncExternalStore(store.subscribe, store.getMeta, store.getMeta);

  const selectedRow = selectedDriver
    ? dashboard?.leaderboard.find((r) => r.driver === selectedDriver) ?? null
    : null;

  // Direction of travel at the focused car, for resolving the wind into head and
  // cross components (API.md section 8). The board publishes lap PROGRESS rather
  // than a station, so it is scaled by the ring's own length here rather than
  // carrying a second copy of the station through the dashboard snapshot.
  const focusHeadingRad = selectedRow && trackModel
    ? trackPointAt(trackModel, selectedRow.lapProgress * trackModel.lengthMetres).heading
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

  // `state.playing` comes from the worker, which owns the clock. Mirroring it in a
  // local useState let the two drift: switching session resets the worker to paused
  // while the UI still read "playing", and the next click sent a pause that did
  // nothing, which looked like the controls had died.
  function togglePlay() {
    if (store.getState().playing) store.pause(); else store.play();
  }

  function chooseSpeed(x: number) {
    setSpeed(x);
    store.setSpeed(x);
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
        // everything Python-side arrives through the one seam (data/source.ts), so
        // swapping the static artifacts for the HTTP API later touches only that file
        const idx = await defaultSimSource.index();
        const [cat, ruleSet] = await Promise.all([
          defaultSimSource.catalogue<Catalogue>(),
          // null when the artifacts predate the rule engine; the panel then shows
          // numbers with no threshold colouring rather than inventing limits.
          defaultSimSource.rules(),
        ]);
        if (disposed) return;
        setIndex(idx);
        setCatalogue(cat);
        setRules(ruleSet);
        const colourByTeam = new Map(cat.teams.map((t) => [t.team, `#${t.colour}`]));
        setDriverMeta(new Map(cat.drivers.map((d) => [d.code, {
          num: d.number, colour: d.team ? colourByTeam.get(d.team) ?? null : null,
        }])));
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
        const { trackUrl, manifestUrl, binUrl } = await defaultSimSource.sessionUrls({
          trackSlug: sel.trackSlug, session: sel.session,
        });

        store.start(trackUrl, manifestUrl, binUrl);

        const [trackRaw, manifestRaw, cat]: [RawTrackModel, RawManifestLite, Catalogue] =
          await Promise.all([
            fetch(trackUrl).then((r) => r.json()),
            fetch(manifestUrl).then((r) => r.json()),
            defaultSimSource.catalogue<Catalogue>(),
          ]);
        if (disposed || !canvasRef.current) return;

        const teamColourBySlug = new Map(cat.teams.map((t) => [t.team, `#${t.colour}`]));
        const track = parseTrackModel(trackRaw);
        setTrackModel(track);
        setHasPitLane(track.pitLanePath !== null);
        rendererRef.current?.dispose();
        setRenderer(null); // the old one is gone; no panel may keep talking to it
        const renderer = new SimRenderer(canvasRef.current, store, gpuPref);
        renderer.setTrack(track);
        renderer.setDimUnfocused(dimField);
        const teamColours: (string | null)[] = manifestRaw.drivers.map(
          (d) => (d.team ? teamColourBySlug.get(d.team) ?? null : null),
        );
        renderer.setDrivers(
          manifestRaw.drivers.length,
          teamColours,
          manifestRaw.drivers.map((d) => d.driver),
        );
        renderer.setCameraMode(cameraMode);
        renderer.setLabelsVisible(showTags);
        renderer.onCameraLockChange(setCameraLocked);
        renderer.setCameraLocked(cameraLocked);
        renderer.onPerf(setPerf);
        renderer.start();
        rendererRef.current = renderer;
        setRenderer(renderer);
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

  // Spacebar is play/pause, unless the user is typing in a control.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== "Space") return;
      const el = e.target as HTMLElement | null;
      if (el && /^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(el.tagName)) return;
      e.preventDefault();
      togglePlay();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const builtSessions: Record<string, string[]> = index
    ? Object.fromEntries(Object.entries(index.sessions).map(([slug, s]) => [slug, Object.keys(s)]))
    : {};

  return (
    <div className={styles.stage}>
      <canvas ref={canvasRef} className={styles.canvas} />
      {/* top left: what you are watching, and how you are watching it */}
      <div className={styles.topLeft}>
        <div className={styles.titleRow}>
          {state.error || loadError ? (
            <span className={styles.error}>{state.error ?? loadError}</span>
          ) : (
            <>
              <span className={styles.provTag}>Replay</span>
              <span className={styles.provText}>Official result</span>
            </>
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

        <div className={styles.segmented} role="group" aria-label="Camera">
          <button
            type="button"
            data-active={!cameraLocked}
            onClick={() => {
              const next = !cameraLocked;
              setCameraLocked(next);
              rendererRef.current?.setCameraLocked(next);
            }}
            title="Lock / unlock the camera (middle mouse). Unlocked: drag to orbit, right-drag to pan, scroll to zoom at the cursor"
          >
            {cameraLocked ? "locked" : "free"}
          </button>
          <button
            type="button"
            data-active={showTags}
            onClick={() => {
              const next = !showTags;
              setShowTags(next);
              rendererRef.current?.setLabelsVisible(next);
            }}
          >
            tags
          </button>
          <button
            type="button"
            data-active={dimField}
            title="Fade every car except the one in focus, so it is easy to pick out of a pack"
            onClick={() => {
              const next = !dimField;
              setDimField(next);
              rendererRef.current?.setDimUnfocused(next);
            }}
          >
            dim field
          </button>
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

        <details className={styles.infoFold}>
          <summary>Display &amp; hardware</summary>
          <TrackLegend hasPitLane={hasPitLane} />
          <GpuBadge
            gpu={gpuInfo}
            perf={perf}
            preference={gpuPref}
            onPreferenceChange={setGpuPref}
          />
        </details>

        {/* The keyboard/mouse rig has real depth (WASD flight, boost, per-mode chase
            panning, free-orbit) that a first-time viewer cannot discover by trial. The
            list itself lives beside the input handlers in scene.ts so the two can never
            drift apart -- this component only renders it. */}
        <details className={styles.infoFold}>
          <summary>Camera controls</summary>
          <dl className={styles.cameraHelp}>
            {CAMERA_CONTROL_HELP.map(({ keys, action }) => (
              <div key={keys} className={styles.cameraHelpRow}>
                <dt>{keys}</dt>
                <dd>{action}</dd>
              </div>
            ))}
          </dl>
        </details>

        {/* The focused car takes every pixel the controls above leave, because its
            energy readout is the densest thing on screen. The environment panel is
            pinned to the bottom of the same column: it is short, fixed-height, and
            wanted at a glance rather than scrolled to. Both used to be nested under the
            leaderboard, where a 20-row table pushed them below the fold. */}
        <div className={styles.leftDock}>
          <DriverPanel row={selectedRow} rules={rules} />
          {/* The rules service knows circuits by the slug the artifacts use, so
              the selection's own trackSlug is the key -- no second mapping to
              drift out of step with the one the sim already loads by. */}
          <OvertakePanel
            event={selection?.trackSlug ?? null}
            renderer={renderer}
            dashboard={dashboard}
          />
        </div>

        <EnvironmentPanel
          weather={meta?.weather ?? null}
          headingRad={focusHeadingRad}
          headingLabel={selectedRow ? `${selectedRow.driver}'s heading` : null}
          sessionTime={dashboard?.sessionTime ?? 0}
          duration={state.duration}
          totalLaps={state.totalLaps}
          neutralisation={dashboard?.activeNeutralisation ?? null}
        />
      </div>

      {/* bottom centre: transport, the way a player behaves */}
      <div className={styles.transport}>
        <div className={styles.transportRow}>
          <button
            type="button"
            className={styles.playBtn}
            onClick={togglePlay}
            title="Play / pause (space)"
          >
            {state.playing ? "Pause" : state.atEnd ? "Replay" : "Play"}
          </button>
          <span className={styles.speedGroup}>
            {SPEEDS.map((x) => (
              <button
                key={x}
                type="button"
                data-active={speed === x}
                onClick={() => chooseSpeed(x)}
              >
                {x}x
              </button>
            ))}
          </span>
          <WeatherStrip
            weather={meta?.weather ?? null}
            sessionTime={dashboard?.sessionTime ?? 0}
            headingRad={focusHeadingRad}
          />
        </div>
        {dashboard ? (
          <TimelineScrubber
            sessionTime={dashboard.sessionTime}
            duration={state.duration}
            neutralisations={meta?.neutralisations ?? []}
            onSeek={(t) => store.seek(t)}
          />
        ) : null}
      </div>

      <CollapsiblePanel
        title="Leaderboard"
        corner="topRight"
        badge={dashboard ? `${dashboard.leaderboard.length} cars` : undefined}
      >
        <table className={styles.board}>
          <tbody>
            {dashboard?.leaderboard.map((row) => {
              const meta = driverMeta.get(row.driver);
              return (
                <tr
                  key={row.driver}
                  data-selected={row.driver === selectedDriver}
                  onClick={() => selectDriver(row.driver)}
                >
                  <td className={styles.colPos}>{row.position}</td>
                  <td className={styles.colTeam}>
                    <span
                      className={styles.teamBar}
                      style={{ background: meta?.colour ?? "var(--haas-grey)" }}
                    />
                  </td>
                  <td className={styles.colNum}>{meta?.num ?? ""}</td>
                  <td className={styles.colDrv}>{row.driver}</td>
                  <td className={styles.colGap}>{row.gapToLeader}</td>
                  <td className={styles.colTyre}>{row.compound ? row.compound[0] : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </CollapsiblePanel>

      <CollapsiblePanel title="Race control" corner="bottomRight" defaultOpen={false}>
        <RaceControlFeed
          events={meta?.events ?? []}
          sessionTime={dashboard?.sessionTime ?? 0}
          showTitle={false}
        />
      </CollapsiblePanel>
    </div>
  );
}
