"use client";

import styles from "./sim.module.css";
import type { GpuInfo, PerfStats } from "../render/scene";

/** Shows whether the scene is actually on the GPU (vs a software WebGL fallback,
 * which some locked-down browsers/VMs silently substitute), which renderer, and a
 * live FPS/frame-time readout -- the user asked to be able to see this directly
 * rather than guess from how the page feels. */
export function GpuBadge({ gpu, perf }: { gpu: GpuInfo | null; perf: PerfStats | null }) {
  if (!gpu) return null;
  const short = gpu.renderer.replace(/^ANGLE \(|\)$/g, "").split(",").slice(0, 2).join(",");
  return (
    <div className={styles.gpuBadge} data-software={gpu.isSoftware}>
      <span className={styles.gpuDot} data-software={gpu.isSoftware} />
      <span>{gpu.isSoftware ? "Software rendering" : "GPU rendering"}</span>
      <span className={styles.gpuName} title={gpu.renderer}>{short}</span>
      {perf ? (
        <span className={styles.gpuFps} data-low={perf.fps < 45}>
          {perf.fps} fps · {perf.frameMs.toFixed(1)} ms
          {perf.qualityTier > 0 ? ` · quality -${perf.qualityTier}` : ""}
        </span>
      ) : null}
    </div>
  );
}
