"use client";

import type { WeatherSeries } from "../contract/types";
import { describeHead, windComponents } from "../replay/wind";
import styles from "./panels.module.css";

function nearestIndex(tS: number[], t: number): number {
  let best = 0, bestD = Infinity;
  for (let i = 0; i < tS.length; i++) {
    const d = Math.abs(tS[i] - t);
    if (d < bestD) { bestD = d; best = i; }
  }
  return best;
}

export function WeatherStrip({
  weather, sessionTime, headingRad,
}: {
  weather: WeatherSeries | null;
  sessionTime: number;
  /** Direction of travel to resolve the wind against. Without one there is no
   * head/tail split to show, and API.md section 8 forbids falling back to the
   * raw compass bearing -- so the strip shows speed alone. */
  headingRad?: number | null;
}) {
  if (!weather || weather.tS.length === 0) return null;
  const i = nearestIndex(weather.tS, sessionTime);
  const wind = windComponents(weather.windMps[i], weather.windFromDeg?.[i], headingRad);
  return (
    <div className={styles.weatherStrip}>
      <span>Air {weather.airTempC[i]?.toFixed(1)}°C</span>
      <span>Track {weather.trackTempC[i]?.toFixed(1)}°C</span>
      <span>Humidity {weather.humidityPct[i]?.toFixed(0)}%</span>
      <span className={styles.wind}>
        {wind ? `Wind ${describeHead(wind.headMps)}` : `Wind ${weather.windMps[i]?.toFixed(1)} m/s`}
      </span>
      {weather.rain[i] ? <span className={styles.rain}>RAIN</span> : null}
    </div>
  );
}
