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
      <span className={styles.wind}>
        Wind {weather.windMps[i]?.toFixed(1)} m/s
        {weather.windFromDeg?.[i] !== undefined ? (
          <>
            {/* the bearing is where the wind comes FROM, so the arrow points the
                opposite way: the direction the air is actually travelling */}
            <span
              className={styles.windArrow}
              style={{ transform: `rotate(${weather.windFromDeg[i] + 180}deg)` }}
              aria-hidden="true"
            >
              &#8593;
            </span>
            <span className={styles.windDeg}>{Math.round(weather.windFromDeg[i])}&deg;</span>
          </>
        ) : null}
      </span>
      {weather.rain[i] ? <span className={styles.rain}>RAIN</span> : null}
    </div>
  );
}
