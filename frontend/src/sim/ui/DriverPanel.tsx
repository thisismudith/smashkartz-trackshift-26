"use client";

import type { DashboardRow } from "../contract/types";
import styles from "./panels.module.css";

/**
 * Everything here is OBSERVED telemetry for the focused car.
 *
 * Deliberately absent: battery state of charge, MGU-K deployment, harvest and the
 * rest of the ERS picture. The 2026 public feed does not contain them (the project
 * contract in AGENTS.md is explicit that they must never be fabricated), and the
 * DRS channel it does carry reads zero in all 11.57 M samples of the season, so
 * showing it as an energy cue would be misleading too. Those values are the E-Delta
 * planner's job: it infers them in Python with an uncertainty band, and when that is
 * wired in they belong here tagged INFERRED, never mixed in with the measured rows.
 */
export function DriverPanel({ row }: { row: DashboardRow | null }) {
  if (!row) return null;
  return (
    <div className={styles.panel}>
      <div className={styles.panelTitle}>{row.driver} · {row.team ?? "—"}</div>

      <div className={styles.provRow}>
        <span className={styles.provPill} data-kind="observed">Observed</span>
        <span>measured telemetry</span>
      </div>
      <dl className={styles.grid}>
        <dt>Speed</dt><dd>{Math.round(row.speedKph)} km/h</dd>
        <dt>Gear</dt><dd>{row.gear || "N"}</dd>
        <dt>Throttle</dt><dd>{Math.round(row.throttlePct)}%</dd>
        <dt>Brake</dt><dd>{row.brake ? "ON" : "off"}</dd>
        <dt>Tyre</dt><dd>{row.compound ?? "—"} ({row.tyreLife ?? "—"} laps)</dd>
        <dt>Lap</dt><dd>{Math.round(row.lapProgress * 100)}%</dd>
        <dt>Gap</dt><dd>{row.gapToLeader}</dd>
        <dt>Interval</dt><dd>{row.interval}</dd>
        <dt>Status</dt><dd>{row.status}</dd>
      </dl>

      <div className={styles.provRow}>
        <span className={styles.provPill} data-kind="inferred">Inferred</span>
        <span>energy twin · computed in Python</span>
      </div>
      {row.energy ? (
        <>
          <dl className={styles.grid}>
            <dt>Peak power</dt><dd>{row.energy.peakWheelPowerKw.toFixed(0)} kW</dd>
            <dt>Peak braking</dt><dd>{row.energy.peakBrakingKw.toFixed(0)} kW</dd>
            <dt>ERS used</dt><dd>{row.energy.ersEnergyUsedMj.toFixed(2)} MJ</dd>
            <dt>ERS harvested</dt><dd>{row.energy.ersEnergyHarvestedMj.toFixed(2)} MJ</dd>
            <dt>Store</dt>
            <dd>{row.energy.socEndMj.toFixed(2)} ± {row.energy.socUncertaintyMj.toFixed(2)} MJ</dd>
          </dl>
          {row.energy.warnings.length > 0 ? (
            <ul className={styles.warnList}>
              {row.energy.warnings.map((w) => <li key={w}>{w}</li>)}
            </ul>
          ) : null}
          <p className={styles.provNote}>
            Battery charge and MGU-K power are not in the public feed. These are estimates
            from a longitudinal power balance with documented assumptions, never measurements.
          </p>
        </>
      ) : (
        <p className={styles.provNote}>No energy estimate for this lap.</p>
      )}
    </div>
  );
}
