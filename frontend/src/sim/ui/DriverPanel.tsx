"use client";

import type { DashboardRow } from "../contract/types";
import styles from "./panels.module.css";

export function DriverPanel({ row }: { row: DashboardRow | null }) {
  if (!row) return null;
  return (
    <div className={styles.panel}>
      <div className={styles.panelTitle}>{row.driver} · {row.team ?? "—"}</div>
      <dl className={styles.grid}>
        <dt>Speed</dt><dd>{Math.round(row.speedKph)} km/h</dd>
        <dt>Gear</dt><dd>{row.gear || "N"}</dd>
        <dt>Throttle</dt><dd>{Math.round(row.throttlePct)}%</dd>
        <dt>Brake</dt><dd>{row.brake ? "ON" : "off"}</dd>
        <dt>Tyre</dt><dd>{row.compound ?? "—"} ({row.tyreLife ?? "—"})</dd>
        <dt>Lap</dt><dd>{Math.round(row.lapProgress * 100)}%</dd>
        <dt>Gap</dt><dd>{row.gapToLeader}</dd>
        <dt>Interval</dt><dd>{row.interval}</dd>
        <dt>Status</dt><dd>{row.status}</dd>
      </dl>
    </div>
  );
}
