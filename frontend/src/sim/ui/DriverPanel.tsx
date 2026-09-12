"use client";

import type { DashboardRow } from "../contract/types";
import styles from "./panels.module.css";

function Bar({ label, pct, kind }: { label: string; pct: number; kind?: "brake" }) {
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div className={styles.barRow}>
      <span className={styles.barLabel}>{label}</span>
      <span className={styles.barTrack}>
        <span className={styles.barFill} data-kind={kind} style={{ width: `${clamped}%` }} />
      </span>
      <span className={styles.barValue}>{Math.round(clamped)}%</span>
    </div>
  );
}

/**
 * The focused car: measured telemetry on top, the Python energy twin's estimate below.
 *
 * The split is the point. Speed, gear, throttle and brake are in the feed. Battery
 * charge, MGU-K power, harvest and deployment are NOT -- the 2026 public feed carries
 * none of them, and the DRS channel it does carry reads zero in all 11.57 M samples of
 * the season. Everything under the Inferred heading is reconstructed from a
 * longitudinal power balance in scripts/simdata/twin.py with a stated uncertainty, and
 * is never allowed to sit in the same block as a measurement.
 */
export function DriverPanel({ row }: { row: DashboardRow | null }) {
  if (!row) {
    return (
      <div className={styles.panel}>
        <div className={styles.panelTitle}>Focused car</div>
        <p className={styles.provNote}>
          Click a car in the scene, or a row in the leaderboard, to read its telemetry and
          energy estimate here.
        </p>
      </div>
    );
  }

  return (
    <div className={styles.panel}>
      <div className={styles.panelTitle}>{row.driver} · {row.team ?? "—"}</div>

      <div className={styles.provRow}>
        <span className={styles.provPill} data-kind="observed">Observed</span>
        <span>measured telemetry</span>
      </div>

      <div className={styles.speedBig}>
        {Math.round(row.speedKph)}<span className={styles.speedUnit}>km/h</span>
        <span className={styles.gearBadge}>{row.gear ? `G${row.gear}` : "N"}</span>
      </div>
      <Bar label="Throttle" pct={row.throttlePct} />
      <Bar label="Brake" pct={row.brake ? 100 : 0} kind="brake" />

      <dl className={styles.grid}>
        <dt>Tyre</dt><dd>{row.compound ?? "—"} ({row.tyreLife ?? "—"} laps)</dd>
        <dt>Lap</dt><dd>{Math.round(row.lapProgress * 100)}%</dd>
        <dt>Last lap</dt><dd>{row.lastLapS !== null ? `${row.lastLapS.toFixed(3)} s` : "—"}</dd>
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
            <dt>ERS deployed</dt><dd>{row.energy.ersEnergyUsedMj.toFixed(2)} MJ</dd>
            <dt>ERS harvested</dt><dd>{row.energy.ersEnergyHarvestedMj.toFixed(2)} MJ</dd>
            <dt>Net this lap</dt><dd>{row.energy.energyBalanceMj >= 0 ? "+" : ""}{row.energy.energyBalanceMj.toFixed(2)} MJ</dd>
            <dt>Store</dt>
            <dd>{row.energy.socEndMj.toFixed(2)} ± {row.energy.socUncertaintyMj.toFixed(2)} MJ</dd>
          </dl>
          {row.energy.envelopeCapViolations > 0 ? (
            <p className={styles.provNote}>
              {row.energy.envelopeCapViolations} sample(s) this lap needed more electrical
              power than the 2026 envelope allows — the reconstruction, the envelope, or
              both are wrong there. Shown rather than clipped away.
            </p>
          ) : null}
          {row.energy.warnings.length > 0 ? (
            <ul className={styles.warnList}>
              {row.energy.warnings.map((w) => <li key={w}>{w}</li>)}
            </ul>
          ) : null}
          {/* The provenance pill above is the always-visible tag the contract requires;
              this is the elaboration, folded so the numbers stay on screen. */}
          <details className={styles.noteFold}>
            <summary>Where these come from</summary>
            <p className={styles.provNote}>
              Battery charge, MGU-K power, harvest and deployment are not in the 2026
              public feed. They are reconstructed in Python from a longitudinal power
              balance (mass·a·v plus drag, rolling and gradient terms) against the
              measured speed trace, with a stated uncertainty. They are estimates, never
              measurements.
            </p>
          </details>
        </>
      ) : (
        <p className={styles.provNote}>No energy estimate for this lap.</p>
      )}
    </div>
  );
}
