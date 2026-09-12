"use client";

import type { NeutralisationInterval, WeatherSeries } from "../contract/types";
import styles from "./panels.module.css";

/** Index of the last weather reading at or before `t`. The feed samples roughly once
 * a minute, so values step rather than glide; holding the last reading is what the
 * data supports, and interpolating between them would invent intermediate weather. */
function readingAt(tS: number[], t: number): number {
  if (!tS.length) return -1;
  let lo = 0, hi = tS.length - 1, best = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (tS[mid] <= t) { best = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  return tS[0] > t ? 0 : best;
}

function clock(s: number): string {
  if (!Number.isFinite(s)) return "—";
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

function show(v: number | undefined, digits: number, unit: string): string {
  return v === undefined || !Number.isFinite(v) ? "—" : `${v.toFixed(digits)} ${unit}`;
}

/**
 * The session's environment variables, all of them OBSERVED by the trackside feed.
 *
 * These are exactly the inputs the energy twin runs on: air density comes from air
 * temperature and humidity, so the numbers here are not decoration -- they are why the
 * ERS estimate in the driver panel reads the way it does.
 */
export function EnvironmentPanel({
  weather, sessionTime, duration, totalLaps, neutralisation,
}: {
  weather: WeatherSeries | null;
  sessionTime: number;
  duration: number;
  totalLaps: number | null;
  neutralisation: NeutralisationInterval | null;
}) {
  const i = weather ? readingAt(weather.tS, sessionTime) : -1;
  const raining = i >= 0 && weather!.rain[i] === true;
  const bearing = i >= 0 ? weather!.windFromDeg?.[i] : undefined;

  return (
    <div className={styles.panel}>
      <div className={styles.panelTitle}>Environment</div>

      <dl className={styles.grid}>
        <dt>Elapsed</dt><dd>{clock(sessionTime)} / {clock(duration)}</dd>
        <dt>Scheduled</dt><dd>{totalLaps !== null ? `${totalLaps} laps` : "—"}</dd>
        <dt>Flag</dt>
        <dd className={neutralisation ? styles.flagActive : undefined}>
          {neutralisation ? neutralisation.kind : "GREEN"}
        </dd>
      </dl>

      <div className={styles.provRow}>
        <span className={styles.provPill} data-kind="observed">Observed</span>
        <span>trackside weather feed</span>
      </div>

      {i >= 0 ? (
        <dl className={styles.grid}>
          <dt>Air temp</dt><dd>{show(weather!.airTempC[i], 1, "°C")}</dd>
          <dt>Track temp</dt><dd>{show(weather!.trackTempC[i], 1, "°C")}</dd>
          <dt>Humidity</dt><dd>{show(weather!.humidityPct[i], 0, "%")}</dd>
          <dt>Wind</dt>
          <dd>
            {show(weather!.windMps[i], 1, "m/s")}
            {bearing !== undefined && Number.isFinite(bearing) ? (
              <>
                {" "}
                {/* arrow points the way the wind BLOWS, i.e. 180 deg from the bearing it comes from */}
                <span
                  className={styles.windArrow}
                  style={{ transform: `rotate(${bearing + 180}deg)` }}
                  aria-hidden="true"
                >
                  ↑
                </span>
                <span className={styles.windDeg}>from {Math.round(bearing)}°</span>
              </>
            ) : null}
          </dd>
          <dt>Rain</dt>
          <dd className={raining ? styles.flagActive : undefined}>{raining ? "WET" : "dry"}</dd>
        </dl>
      ) : (
        <p className={styles.provNote}>This session carries no weather series.</p>
      )}

      <details className={styles.noteFold}>
        <summary>Why these matter</summary>
        <p className={styles.provNote}>
          Air temperature and humidity set the air density the energy twin runs on, so
          they drive the drag term behind the ERS estimate rather than merely describing
          the day. The feed samples weather about once a minute, so the values step
          rather than glide; the last reading is held, not interpolated.
        </p>
      </details>
    </div>
  );
}
