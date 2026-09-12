"use client";

import type { WeatherSeries } from "../contract/types";
import styles from "./panels.module.css";

function nearestIndex(tS: number[], t: number): number {
  let best = 0, bestD = Infinity;
  for (let i = 0; i < tS.length; i++) {
    const d = Math.abs(tS[i] - t);
    if (d < bestD) { bestD = d; best = i; }
  }
  return best;
}

export function WeatherStrip({ weather, sessionTime }: { weather: WeatherSeries | null; sessionTime: number }) {
  if (!weather || weather.tS.length === 0) return null;
  const i = nearestIndex(weather.tS, sessionTime);
  return (
    <div className={styles.weatherStrip}>
      <span>Air {weather.airTempC[i]?.toFixed(1)}°C</span>
      <span>Track {weather.trackTempC[i]?.toFixed(1)}°C</span>
      <span>Humidity {weather.humidityPct[i]?.toFixed(0)}%</span>
      <span>Wind {weather.windMps[i]?.toFixed(1)} m/s</span>
      {weather.rain[i] ? <span className={styles.rain}>RAIN</span> : null}
    </div>
  );
}
