"use client";

import { Fragment } from "react";
import type { NeutralisationInterval, WeatherSeries } from "../contract/types";
import {
  describePositionIntegrity, positionIntegrityUnknown, type SessionPositionIntegrity,
} from "../replay/dashboard";
import { describeCross, describeHead, windComponents } from "../replay/wind";
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
  weather, sessionTime, duration, totalLaps, neutralisation, positionIntegrity,
  headingRad, headingLabel,
}: {
  weather: WeatherSeries | null;
  sessionTime: number;
  duration: number;
  totalLaps: number | null;
  neutralisation: NeutralisationInterval | null;
  /** What the build reports about the positions it could and could not measure in this
   * session. Undefined means the build did not report it -- which the panel prints as
   * "unknown", never as "none". */
  positionIntegrity?: SessionPositionIntegrity | null;
  /** Direction of travel to resolve the wind against, radians in the ring frame.
   * Undefined when no car is focused -- head/cross are only meaningful somewhere,
   * so without a somewhere the panel shows the raw reading and says so. */
  headingRad?: number | null;
  /** Where that heading was taken, named on screen beside the components. */
  headingLabel?: string | null;
}) {
  const i = weather ? readingAt(weather.tS, sessionTime) : -1;
  const raining = i >= 0 && weather!.rain[i] === true;
  const bearing = i >= 0 ? weather!.windFromDeg?.[i] : undefined;
  // API.md section 8: wind is shown as head/cross RELATIVE TO THE TRACK, never as a
  // compass bearing -- the same 265 degrees is a headwind on one straight and a
  // tailwind on the next, so the bearing alone cannot be acted on.
  const wind = windComponents(
    i >= 0 ? weather!.windMps[i] : undefined, bearing, headingRad,
  );
  const integrity = describePositionIntegrity(positionIntegrity);
  const integrityUnknown = positionIntegrityUnknown(positionIntegrity);

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
          {wind ? (
            <>
              <dt>Head/tail</dt>
              <dd>
                {describeHead(wind.headMps)}
                <span className={styles.windDeg}>
                  {headingLabel ? `at ${headingLabel}` : "at the focused car"}
                </span>
              </dd>
              <dt>Crosswind</dt>
              <dd>{describeCross(wind.crossMps)}</dd>
            </>
          ) : (
            <>
              <dt>Wind</dt>
              <dd>
                {show(weather!.windMps[i], 1, "m/s")}
                {/* No heading to resolve against, so no head/cross exists yet. The
                    raw bearing is shown as the reading it is, explicitly not as
                    something to act on -- see API.md section 8. */}
                {bearing !== undefined && Number.isFinite(bearing) ? (
                  <span className={styles.windDeg}>
                    from {Math.round(bearing)}° — focus a car for head/cross
                  </span>
                ) : null}
              </dd>
            </>
          )}
          <dt>Rain</dt>
          <dd className={raining ? styles.flagActive : undefined}>{raining ? "WET" : "dry"}</dd>
        </dl>
      ) : (
        <p className={styles.provNote}>This session carries no weather series.</p>
      )}

      {/* Whether this session's positions are measurements at all is a property of the
          session, not of any one car, so it belongs here beside the flag and the clock.
          A build that does not report the counts leaves them UNKNOWN: printing "none"
          would turn a missing measurement into a clean bill of health. */}
      <div className={styles.provRow}>
        <span className={styles.provPill} data-kind={integrityUnknown ? "missing" : "observed"}>
          Position data
        </span>
        <span>what this build could measure</span>
      </div>

      <dl className={styles.grid}>
        {integrity.map((line) => (
          <Fragment key={line.label}>
            <dt>{line.label}</dt>
            <dd className={line.alert ? styles.flagActive : undefined}>{line.value}</dd>
          </Fragment>
        ))}
      </dl>

      {integrityUnknown ? (
        <p className={styles.provNote}>
          This build does not report how many position samples it withdrew or how many
          laps it placed from the distance channel, so neither is known. Unknown is not
          zero.
        </p>
      ) : null}

      <details className={styles.noteFold}>
        <summary>Why these matter</summary>
        <p className={styles.provNote}>
          Air temperature and humidity set the air density the energy twin runs on, so
          they drive the drag term behind the ERS estimate rather than merely describing
          the day. The feed samples weather about once a minute, so the values step
          rather than glide; the last reading is held, not interpolated.
        </p>
        <p className={styles.provNote}>
          Withdrawn samples are positions the feed reported but the build refused: the
          car&apos;s own speed channel says it moved while its x/y did not, so the
          coordinate is a stuck placeholder rather than a place. Derived laps are laps
          with no usable x/y at all, positioned by stretching the wheel-speed distance
          channel onto the ring — the car is on the centreline because nothing measured
          says otherwise, and no gap is computed from it.
        </p>
      </details>
    </div>
  );
}
