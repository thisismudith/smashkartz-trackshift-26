"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { SimStore } from "../store/simStore";
import {
  CAMERA_CONTROL_HELP, SimRenderer,
  type CameraMode, type EnvironmentLoad, type GpuInfo, type GpuPreference, type PerfStats,
} from "../render/scene";
import { parseTrackModel, trackPointAt, type RawTrackModel } from "../data/manifest";
import { defaultSimSource, type RivalryShowcaseSet, type RuleSet } from "../data/source";
import { DriverPanel } from "./DriverPanel";
import { EnvironmentPanel } from "./EnvironmentPanel";
import { GpuBadge } from "./GpuBadge";
import { RaceControlFeed } from "./RaceControlFeed";
import { type CatalogueTrack, type Selection, SessionSelector } from "./SessionSelector";
import { TimelineScrubber } from "./TimelineScrubber";
import { TrackLegend } from "./TrackLegend";
import { CollapsiblePanel } from "./CollapsiblePanel";
import { OvertakePanel, useOvertakeGeometry, useTrackSegments } from "./OvertakePanel";
import { HelpModal } from "./HelpModal";
import { Minimap } from "./Minimap";
import { RailPanel } from "./RailPanel";
import { ShowcasePanel } from "./ShowcasePanel";
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

/** Opened on by default: see the comment at the selection that uses it. */
const PREFERRED_SLUG = "british-grand-prix";

/**
 * How long the overlay stays up after the download ends, ms, per outcome.
 *
 * A SUCCESS goes immediately: the model appearing on screen IS the notification, so a
 * banner saying so is a second, redundant one sitting over the thing it is announcing.
 *
 * A FAILURE stays, because nothing else reports it. The circuit keeps the procedural
 * ribbon for the rest of the session and there is no later moment at which the viewer
 * would find out why, so this is the only place it is ever said.
 */
const ENV_DISMISS_MS = { ready: 0, failed: 8000 } as const;

/**
 * Says the circuit's real 3D model is on its way, and then that it has arrived.
 *
 * It exists because NOTHING WAITS FOR THE MODEL. The race starts on the procedural
 * ribbon and the 126 MB Silverstone asset is swapped in whenever it lands, which is the
 * right behaviour (a slow download must never hold up the session) and is indistinguishable
 * on screen from "this circuit has no model" -- the state twelve of the thirteen circuits
 * are permanently in. A failed fetch reads the same way again. This is the one place the
 * three are told apart.
 *
 * Deliberately NOT a modal: the ribbon underneath is live and usable, and blocking it
 * would make a background download feel like a loading screen.
 */
function ModelLoadOverlay({ load, onDone }: {
  load: EnvironmentLoad | null;
  onDone: () => void;
}) {
  const status = load?.status;
  useEffect(() => {
    if (status !== "ready" && status !== "failed") return;
    const t = setTimeout(onDone, ENV_DISMISS_MS[status]);
    return () => clearTimeout(t);
  }, [status, onDone]);

  // "ready" is never DRAWN, only scheduled for dismissal above: the model appearing on
  // the circuit is the notification, and a banner announcing it would cover the thing it
  // is announcing. Rendering it for the one frame before a 0 ms timer fires is exactly
  // the flash this avoids.
  if (!load || load.status === "ready") return null;
  const mb = (b: number) => (b / (1024 * 1024)).toFixed(0);
  // 0 means the response carried no Content-Length, which is a real state and not 0 %:
  // the bar goes indeterminate rather than pretending to know.
  const pct = load.totalBytes > 0
    ? Math.min(100, Math.round((load.loadedBytes / load.totalBytes) * 100))
    : null;
  const text = load.status === "failed"
    ? `${load.event} 3D model unavailable — showing the track outline`
    : pct === null ? `Loading ${load.event} 3D model…`
    : `Loading ${load.event} 3D model — ${mb(load.loadedBytes)} / ${mb(load.totalBytes)} MB`;

  return (
    <div className={styles.modelLoad} data-status={load.status} role="status" aria-live="polite">
      <span className={styles.modelLoadText}>{text}</span>
      <span className={styles.modelLoadTrack}>
        <span
          className={styles.modelLoadBar}
          data-indeterminate={pct === null && load.status === "loading"}
          style={pct === null ? undefined : { width: `${pct}%` }}
        />
      </span>
    </div>
  );
}

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
  /** The circuit model's download, or null when this circuit has none in flight. */
  const [envLoad, setEnvLoad] = useState<EnvironmentLoad | null>(null);
  const [gpuPref, setGpuPref] = useState<GpuPreference>("high-performance");
  const [hasPitLane, setHasPitLane] = useState(false);
  const [showTags, setShowTags] = useState(true);
  const [dimField, setDimField] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [cameraLocked, setCameraLocked] = useState(true);
  /** driver code -> car number / team colour, for the compact leaderboard */
  const [driverMeta, setDriverMeta] = useState<Map<string, { num: string | null; colour: string | null }>>(new Map());

  /** Which rail panels are on screen. A hidden panel is removed entirely rather
   * than folded: a folded panel still costs a title bar, and with five of them
   * that is most of the rail. "Show all" restores every one at once. */
  const [visible, setVisible] = useState<Record<string, boolean>>({
    minimap: true, driver: true, overtake: true, environment: true,
  });
  const allVisible = Object.values(visible).every(Boolean);
  function toggle(key: string) {
    setVisible((v) => ({ ...v, [key]: !v[key] }));
  }
  function showAll() {
    setVisible({ minimap: true, driver: true, overtake: true, environment: true });
  }

  /** Shift+/ opens the reference overlay; Escape closes it. */
  const [helpOpen, setHelpOpen] = useState(false);
  /** Escape with no modal open hides every HUD layer, leaving only the circuit.
   * Pressing it again brings them all back -- the same key both ways, because a
   * viewer who hid the HUD by accident should not have to find a different one. */
  const [hudHidden, setHudHidden] = useState(false);
  /** The model's evidence reel for this circuit, or null when the artifacts
   * predate the exporter -- the sim then simply offers no showcase button. */
  const [showcase, setShowcase] = useState<RivalryShowcaseSet | null>(null);
  /** Which reel entry is on screen; null means the reel is not running. */
  const [reelIndex, setReelIndex] = useState<number | null>(null);

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

  // One fetch per circuit, shared by the panel, the plan view and the legend.
  const { geometry: overtakeGeometry, error: overtakeError } =
    useOvertakeGeometry(selection?.trackSlug ?? null);
  // The circuit's derived segmentation, for the pass model's `corner_type`.
  // Fetched at the stage rather than inside the panel so one request serves
  // every consumer, the same way the Overtake geometry already does.
  const trackSegments = useTrackSegments(selection?.trackSlug ?? null);

  // This circuit's reel, narrowed to the session actually on screen. A weekend
  // produces Race and Sprint opportunities and lap 3 of one is not lap 3 of the
  // other, so an unfiltered reel would seek to moments that never existed here.
  const showcaseEvent = selection ? showcase?.events[selection.trackSlug] ?? null : null;
  const reelEntries = showcaseEvent && selection
    ? showcaseEvent.rivalries.filter(
        (r) => r.session === null || r.session === selection.session)
    : [];
  const reelActive = reelIndex !== null && reelIndex < reelEntries.length;

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

  /**
   * Show one entry of the evidence reel: focus the attacker, dim everything that
   * is not part of this fight, and seek to the lap it happened on.
   *
   * The seek is PROPORTIONAL -- lap start times are not in the dashboard
   * snapshot, only the session's total duration and lap count -- so it lands at
   * the top of the lap and the approach plays out from there. That is honest for
   * a reel (you watch the lap the model was called on) and is why the panel
   * names the lap rather than claiming to jump to the Detection Line itself.
   */
  function showReelEntry(next: number) {
    const list = reelEntries;
    if (next < 0 || next >= list.length) return;
    setReelIndex(next);
    const entry = list[next];
    selectDriver(entry.attacker);
    setDimField(true);
    // Both cars in the fight stay at full strength. Dimming keyed on the focused
    // car alone left the DEFENDER dimmed, which is the car the overtake is of.
    const pair = [entry.attacker, entry.defender]
      .map((d) => state.driverList.indexOf(d))
      .filter((i) => i >= 0);
    rendererRef.current?.setHighlightIndices(pair);
    rendererRef.current?.setDimUnfocused(true);

    // Seek to the EXACT session second the window opens on. This used to be a
    // proportional guess from the lap number (duration * lap / totalLaps), which
    // is wrong by minutes: the session clock starts well before the race does,
    // so lap 4 sits at 3737 s of a session whose 4/52 point is nowhere near it.
    // The replay simply never reached the overtake. The manifest's own `lST` and
    // the telemetry's `lap_start_session_s` are the same clock, so this lands on
    // the pass to the sample.
    const w = entry.window;
    if (w) {
      // Disarmed until a tick lands inside the new window, so the clamp cannot
      // end this entry on the previous one's clock.
      reelArmed.current = false;
      // ABSOLUTE -> replay clock. The showcase's moments come from the telemetry
      // parquet and are absolute session seconds; the replay starts at 0 and is
      // shifted by the earliest lap-1 start. Seeking without this subtraction
      // landed ~3340 s late -- a different phase of the race, where the focused
      // car is genuinely alone and the battle it names is nowhere near it.
      store.seek(w.centreSessionTimeS - state.clockOffsetS - w.beforeS);
      // Real time. A reel played at 20x is six seconds of race in a third of a
      // second, which is not a thing anyone can watch.
      setSpeed(1);
      store.setSpeed(1);
      store.play();
    }
  }

  function exitReel() {
    setReelIndex(null);
    setDimField(false);
    rendererRef.current?.setHighlightIndices(null);
    rendererRef.current?.setDimUnfocused(false);
  }

  /**
   * Where the replay is inside the current reel window, in seconds from the
   * pass. Null outside the reel, or before the clock reaches the window.
   *
   * The charts read this so their playhead is the replay's own position rather
   * than a second animation running beside it -- two clocks would drift and the
   * line would stop agreeing with the cars.
   */
  const reelEntry = reelActive ? reelEntries[reelIndex!] : null;
  const reelWindow = reelEntry?.window ?? null;
  const reelHeadS = reelWindow && dashboard
    ? dashboard.sessionTime - (reelWindow.centreSessionTimeS - state.clockOffsetS)
    : null;

  // Stop at the end of the window -- but ONLY once the seek has actually landed.
  //
  // The clamp used to fire on the stale clock. Clicking a reel entry posts the
  // seek and calls play(), then React re-renders before the worker's next
  // dashboard tick, so `dashboard.sessionTime` is still wherever the race was:
  // headS came out at +326 s, the clamp read that as "past the window" and
  // paused immediately. The seek then landed on the right second with playback
  // already stopped, which is the frozen car on screen.
  //
  // `reelArmed` is set only after a tick is observed INSIDE the window, so a
  // stale reading can no longer end the reel before it starts.
  const reelArmed = useRef(false);
  useEffect(() => {
    if (!reelWindow || reelHeadS === null) return;
    if (reelHeadS < reelWindow.afterS) { reelArmed.current = true; return; }
    if (reelArmed.current && state.playing) store.pause();
  }, [reelWindow, reelHeadS, state.playing, store]);

  // Load the index + catalogue once, purely to populate the selector; does not boot
  // a session by itself (that happens in the effect below once a selection exists).
  useEffect(() => {
    let disposed = false;
    async function load() {
      try {
        // everything Python-side arrives through the one seam (data/source.ts), so
        // swapping the static artifacts for the HTTP API later touches only that file
        const idx = await defaultSimSource.index();
        const [cat, ruleSet, reel] = await Promise.all([
          defaultSimSource.catalogue<Catalogue>(),
          // null when the artifacts predate the rule engine; the panel then shows
          // numbers with no threshold colouring rather than inventing limits.
          defaultSimSource.rules(),
          // null when they predate the showcase exporter; no button is offered
          // rather than the browser scoring the model against the telemetry.
          defaultSimSource.rivalryShowcase().catch(() => null),
        ]);
        if (disposed) return;
        setIndex(idx);
        setCatalogue(cat);
        setRules(ruleSet);
        setShowcase(reel);
        const colourByTeam = new Map(cat.teams.map((t) => [t.team, `#${t.colour}`]));
        setDriverMeta(new Map(cat.drivers.map((d) => [d.code, {
          num: d.number, colour: d.team ? colourByTeam.get(d.team) ?? null : null,
        }])));
        // British is the circuit that HAS a real 3D model (the only one clearing the
        // quality gate today), so it is what the simulator should open on -- the
        // alphabetically-first slug opened on Melbourne's procedural ribbon instead.
        // Still a preference, not an assumption: a build without it falls back to
        // whatever the index does list.
        const slugs = Object.keys(idx.sessions);
        const slug = slugs.includes(PREFERRED_SLUG) ? PREFERRED_SLUG : slugs[0];
        const session = slug ? Object.keys(idx.sessions[slug])[0] : null;
        if (slug && session) changeSelection({ trackSlug: slug, session });
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
        // BEFORE setTrack, which is what starts the circuit model downloading. Registered
        // after it, the very first report -- "a download has begun", the one that puts the
        // overlay on screen -- was made while the callback was still null and dropped, and
        // a response with no Content-Length emits no progress events to recover with. The
        // overlay then never appeared at all for the whole 126 MB fetch.
        setEnvLoad(null);
        renderer.onEnvironmentLoad(setEnvLoad);
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

  // Stage keys: space plays, Shift+/ opens the reference overlay, Escape hides
  // and restores the HUD. None of them fire while the viewer is typing in a
  // control, or the session selector would swallow its own keystrokes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) return;
      if (e.code === "Space") {
        if (el && el.tagName === "BUTTON") return; // space already activates it
        e.preventDefault();
        togglePlay();
        return;
      }
      // "?" is Shift+/ on most layouts, but not all -- accept either spelling
      // rather than making the shortcut depend on the keyboard's country.
      if (e.key === "?" || (e.shiftKey && e.code === "Slash")) {
        e.preventDefault();
        setHelpOpen((v) => !v);
        return;
      }
      // Arrow keys step the evidence reel, and only while it is running -- they
      // are otherwise the scrubber's and the browser's.
      if (reelActive && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
        e.preventDefault();
        showReelEntry(reelIndex! + (e.key === "ArrowRight" ? 1 : -1));
        return;
      }
      if (e.key === "Escape") {
        // The modal handles its own Escape in a capturing listener, so reaching
        // here means no modal is open and the key belongs to the HUD.
        e.preventDefault();
        // The reel is the innermost layer, so it unwinds first: hiding the HUD
        // underneath a reel the viewer was trying to leave would strand them.
        if (reelActive) { exitReel(); return; }
        setHudHidden((v) => !v);
      }
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

      <ModelLoadOverlay load={envLoad} onDone={() => setEnvLoad(null)} />

      <HelpModal open={helpOpen} title="Camera & display" onClose={() => setHelpOpen(false)}>
        {/* The key list lives beside the input handlers in scene.ts so the two
            can never drift apart; this only renders it. */}
        <dl className={styles.cameraHelp}>
          {CAMERA_CONTROL_HELP.map(({ keys, action }) => (
            <div key={keys} className={styles.cameraHelpRow}>
              <dt>{keys}</dt>
              <dd>{action}</dd>
            </div>
          ))}
          <div className={styles.cameraHelpRow}><dt>Esc</dt><dd>hide / show the HUD</dd></div>
          <div className={styles.cameraHelpRow}><dt>?</dt><dd>this panel</dd></div>
        </dl>
        <TrackLegend
          hasPitLane={hasPitLane}
          hasDetection={overtakeGeometry?.detectionM != null}
          hasZones={Boolean(overtakeGeometry?.zones.some((z) => z.activationM != null))}
        />
        <GpuBadge
          gpu={gpuInfo}
          perf={perf}
          preference={gpuPref}
          onPreferenceChange={setGpuPref}
        />
      </HelpModal>

      {hudHidden ? (
        <button
          type="button"
          className={styles.hudRestore}
          onClick={() => setHudHidden(false)}
        >
          Esc · show HUD
        </button>
      ) : null}

      {/* The evidence reel replaces the HUD rather than sitting on top of it:
          the whole point is two cars and one claim about them, and the rail,
          the board and the feed are all about the other twenty. */}
      {reelActive && showcaseEvent ? (
        <ShowcasePanel
          showcase={showcaseEvent}
          entries={reelEntries}
          index={reelIndex!}
          headS={reelHeadS}
          onStep={showReelEntry}
          onReplay={() => showReelEntry(reelIndex!)}
          onExit={exitReel}
        />
      ) : null}

      {/* top left: what you are watching, and how you are watching it */}
      <div className={styles.topLeft} data-hidden={hudHidden || reelActive}>
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


        {/* The focused car takes every pixel the controls above leave, because its
            energy readout is the densest thing on screen. The environment panel is
            pinned to the bottom of the same column: it is short, fixed-height, and
            wanted at a glance rather than scrolled to. Both used to be nested under the
            leaderboard, where a 20-row table pushed them below the fold. */}
        <div className={styles.leftDock}>
          <div className={styles.hudToggles}>
            {([
              ["minimap", "Map"], ["driver", "Car"],
              ["overtake", "Overtake"], ["environment", "Env"],
            ] as const).map(([key, label]) => (
              <button
                key={key} type="button" data-on={visible[key]}
                onClick={() => toggle(key)}
                aria-pressed={visible[key]}
              >
                {label}
              </button>
            ))}
            {!allVisible ? (
              <button type="button" data-all="true" onClick={showAll}>Show all</button>
            ) : null}
          </div>

          <RailPanel title="Circuit" hidden={!visible.minimap}>
            <Minimap
              track={trackModel}
              dashboard={dashboard}
              geometry={overtakeGeometry}
              selectedDriver={selectedDriver}
              onSelectDriver={selectDriver}
            />
          </RailPanel>

          <RailPanel title="Focused car" hidden={!visible.driver}>
            <DriverPanel row={selectedRow} rules={rules} />
          </RailPanel>

          {/* The rules service knows circuits by the slug the artifacts use, so
              the selection's own trackSlug is the key -- no second mapping to
              drift out of step with the one the sim already loads by. */}
          <RailPanel title="Overtake" hidden={!visible.overtake}>
            <OvertakePanel
              event={selection?.trackSlug ?? null}
              renderer={renderer}
              dashboard={dashboard}
              selectedDriver={selectedDriver}
              geometry={overtakeGeometry}
              geometryError={overtakeError}
              weather={meta?.weather ?? null}
              headingRad={focusHeadingRad}
              segments={trackSegments}
            />
          </RailPanel>
        </div>

        <RailPanel title="Environment" hidden={!visible.environment} defaultOpen={false}>
          <EnvironmentPanel
            weather={meta?.weather ?? null}
            headingRad={focusHeadingRad}
            headingLabel={selectedRow ? `${selectedRow.driver}'s heading` : null}
            sessionTime={dashboard?.sessionTime ?? 0}
            duration={state.duration}
            totalLaps={state.totalLaps}
            neutralisation={dashboard?.activeNeutralisation ?? null}
          />
        </RailPanel>
      </div>

      {/* bottom centre: transport, the way a player behaves */}
      <div className={styles.transport} data-hidden={hudHidden}>
        {/* Its own full-width row, not another chip in the control strip. In the
            strip it wrapped onto a clipped line and was unclickable -- present in
            the DOM, invisible on screen, which is the worst state for the one
            control that opens the model's evidence. It is also the thing a viewer
            is looking for, so it gets the top line and says what it holds. */}
        {reelEntries.length > 0 ? (
          <button
            type="button"
            className={styles.reelBar}
            data-active={reelActive}
            onClick={() => (reelActive ? exitReel() : showReelEntry(0))}
            title={`Replay the ${reelEntries.length} approaches where the pass model's Detection-Line call matched what the cars did`}
          >
            {reelActive
              ? "Exit model evidence"
              : `Model evidence — replay ${reelEntries.length} calls the model got right`}
          </button>
        ) : null}

        <div className={styles.transportRow}>
          {/* Moved down from the top-left corner: the stage reads better with
              the left side free for panels, and these belong with the transport. */}
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
        hidden={hudHidden || reelActive}
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
