"use client";

import { useState } from "react";
import styles from "./sim.module.css";
import type { GpuInfo, GpuPreference, PerfStats } from "../render/scene";

const PREFS: GpuPreference[] = ["high-performance", "low-power", "default"];

/** Shows whether the scene is actually on the GPU (vs a software WebGL fallback),
 * WHICH GPU got used, and a live FPS readout. The preference selector is the only
 * lever a web page has over adapter choice -- it is a hint the browser and OS can
 * overrule -- so when an integrated GPU is picked anyway, the badge says so and
 * explains where the real setting lives instead of pretending the page can force it. */
export function GpuBadge({
  gpu, perf, preference, onPreferenceChange,
}: {
  gpu: GpuInfo | null;
  perf: PerfStats | null;
  preference: GpuPreference;
  onPreferenceChange: (p: GpuPreference) => void;
}) {
  const [showHelp, setShowHelp] = useState(false);
  if (!gpu) return null;
  const short = gpu.renderer.replace(/^ANGLE \(|\)$/g, "").split(",").slice(0, 2).join(",");
  const notDiscrete = gpu.isIntegrated && preference === "high-performance";

  return (
    <div className={styles.gpuBadge} data-software={gpu.isSoftware}>
      <span className={styles.gpuDot} data-software={gpu.isSoftware} />
      <span>{gpu.isSoftware ? "Software rendering" : "GPU rendering"}</span>
      <span className={styles.gpuName} title={gpu.renderer}>{short}</span>

      <label className={styles.gpuPref}>
        Prefer
        <select value={preference} onChange={(e) => onPreferenceChange(e.target.value as GpuPreference)}>
          {PREFS.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      </label>

      {notDiscrete ? (
        <button type="button" className={styles.gpuWarn} onClick={() => setShowHelp((v) => !v)}>
          integrated GPU in use — why?
        </button>
      ) : null}

      {perf ? (
        <span className={styles.gpuFps} data-low={perf.fps < 45}>
          {perf.fps} fps · {perf.frameMs.toFixed(1)} ms
          {perf.qualityTier > 0 ? ` · quality -${perf.qualityTier}` : ""}
        </span>
      ) : null}

      {showHelp ? (
        <div className={styles.gpuHelp}>
          <p>
            The page already requests the high-performance adapter, but WebGL cannot choose a
            physical GPU — the browser and Windows decide. To force the discrete GPU:
          </p>
          <ol>
            <li>Windows Settings → System → Display → Graphics → pick your browser → Options → High performance.</li>
            <li>NVIDIA Control Panel → Manage 3D settings → Preferred graphics processor → High-performance NVIDIA processor.</li>
            <li>Fully quit and reopen the browser (the GPU process is chosen at launch).</li>
          </ol>
          <p>Check <code>chrome://gpu</code> or <code>edge://gpu</code> to confirm which adapter the browser bound.</p>
        </div>
      ) : null}
    </div>
  );
}
