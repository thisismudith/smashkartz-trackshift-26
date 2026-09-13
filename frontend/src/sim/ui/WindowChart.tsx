"use client";

/**
 * The measured seconds either side of the Detection Line, drawn.
 *
 * A probability and an outcome are two numbers; they say the model was right,
 * not why. These traces are the "why": the gap collapsing, the defender's speed
 * falling away, both cars' throttle. All of it OBSERVED telemetry at the feed's
 * own 20 m resolution -- nothing interpolated, nothing smoothed, and the sample
 * count is printed because ten points across six seconds is what the resampling
 * gives at racing speed and the reader should be able to see that.
 *
 * SVG rather than a chart library: this is a handful of points in a box the size
 * of a postage stamp, and the sim's frame budget belongs to the circuit.
 */
import { useMemo } from "react";
import { HAAS } from "@/lib/palette";
import type { ShowcaseSample } from "../data/source";
import styles from "./sim.module.css";

const W = 260;
const H = 64;
const PAD_X = 4;
const PAD_Y = 6;

type Channel = "speedKph" | "gapAheadM" | "throttlePct" | "gapToRivalM"
  | "wheelPowerKw" | "ersDeployKw" | "ersHarvestKw";

interface Series {
  label: string;
  points: ShowcaseSample[];
  colour: string;
  dashed?: boolean;
}

/** Finite values of one channel across every series, for a shared y-range. */
function extent(series: Series[], channel: Channel): [number, number] | null {
  let lo = Infinity, hi = -Infinity;
  for (const s of series) {
    for (const p of s.points) {
      const v = p[channel];
      if (typeof v !== "number" || !Number.isFinite(v)) continue;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
  // A flat trace still needs a band to sit in, or every point lands on one row.
  if (hi - lo < 1e-6) return [lo - 1, hi + 1];
  return [lo, hi];
}

/**
 * One channel's polyline, with x fixed to the window and y to the shared extent.
 *
 * x is anchored to the WINDOW, not to the points: two cars sampled at different
 * stations have different timestamps, and scaling each to its own extent would
 * slide them apart in time and make the gap between them unreadable.
 */
function path(
  points: ShowcaseSample[], channel: Channel,
  tSpan: [number, number], yRange: [number, number],
): string {
  const [t0, t1] = tSpan;
  const [lo, hi] = yRange;
  const parts: string[] = [];
  let open = false;
  for (const p of points) {
    const v = p[channel];
    if (typeof v !== "number" || !Number.isFinite(v)) {
      // A gap in the feed breaks the line rather than being bridged: a straight
      // segment across missing samples is a claim about data that is not there.
      open = false;
      continue;
    }
    const x = PAD_X + ((p.t - t0) / (t1 - t0 || 1)) * (W - PAD_X * 2);
    const y = H - PAD_Y - ((v - lo) / (hi - lo)) * (H - PAD_Y * 2);
    parts.push(`${open ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`);
    open = true;
  }
  return parts.join(" ");
}

export function WindowChart({
  title, unit, channel, series, tSpan, digits = 0, headS = null, zeroLine = false,
}: {
  title: string;
  unit: string;
  channel: Channel;
  series: Series[];
  tSpan: [number, number];
  digits?: number;
  /** The replay's own position, seconds from the centre. Drawn as a moving line
   * so the chart and the cars on the circuit are the same moment. */
  headS?: number | null;
  /** Mark y = 0. On the pair gap that line IS the overtake: above it the other
   * car is ahead, below it this one is. */
  zeroLine?: boolean;
}) {
  const yRange = useMemo(() => extent(series, channel), [series, channel]);
  if (!yRange) {
    return (
      <div className={styles.chartBlock}>
        <p className={styles.chartTitle}>
          {title}
          <span className={styles.chartAbsent}>not in this session&rsquo;s feed</span>
        </p>
      </div>
    );
  }
  const [lo, hi] = yRange;
  // Where t = 0 sits, so the Detection Line is a mark on the chart and not just
  // an idea. Everything left of it is what the model could see.
  const zeroX = PAD_X + ((0 - tSpan[0]) / (tSpan[1] - tSpan[0] || 1)) * (W - PAD_X * 2);

  return (
    <div className={styles.chartBlock}>
      <p className={styles.chartTitle}>
        {title}
        <span className={styles.chartRange}>
          {lo.toFixed(digits)}–{hi.toFixed(digits)} {unit}
        </span>
      </p>
      <svg viewBox={`0 0 ${W} ${H}`} className={styles.chartSvg} role="img"
           aria-label={`${title} across the overtake window`}>
        {/* y = 0, where it means something. On the pair gap this is the pass. */}
        {zeroLine && lo < 0 && hi > 0 ? (
          <line
            x1={PAD_X} x2={W - PAD_X}
            y1={H - PAD_Y - ((0 - lo) / (hi - lo)) * (H - PAD_Y * 2)}
            y2={H - PAD_Y - ((0 - lo) / (hi - lo)) * (H - PAD_Y * 2)}
            stroke={HAAS.white} strokeWidth={0.6} strokeDasharray="2 2" opacity={0.45}
          />
        ) : null}
        <line x1={zeroX} y1={2} x2={zeroX} y2={H - 2}
              stroke={HAAS.white} strokeWidth={0.8} opacity={0.55} />
        {/* The replay's position. Clamped to the window so a clock that has run
            past the end parks on the edge rather than drawing outside the box. */}
        {headS !== null && Number.isFinite(headS) ? (
          <line
            x1={PAD_X + (Math.min(Math.max(headS, tSpan[0]), tSpan[1]) - tSpan[0])
                / (tSpan[1] - tSpan[0] || 1) * (W - PAD_X * 2)}
            x2={PAD_X + (Math.min(Math.max(headS, tSpan[0]), tSpan[1]) - tSpan[0])
                / (tSpan[1] - tSpan[0] || 1) * (W - PAD_X * 2)}
            y1={0} y2={H}
            stroke={HAAS.yellow} strokeWidth={1.2} opacity={0.9}
          />
        ) : null}
        {series.map((s) => (
          <path
            key={s.label}
            d={path(s.points, channel, tSpan, yRange)}
            fill="none"
            stroke={s.colour}
            strokeWidth={1.6}
            strokeDasharray={s.dashed ? "3 2" : undefined}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}
      </svg>
    </div>
  );
}

/** The shared key, drawn once rather than on every chart. */
export function ChartLegend({ attacker, defender }: { attacker: string; defender: string }) {
  return (
    <p className={styles.chartLegend}>
      <span><i style={{ background: HAAS.red }} />{attacker}</span>
      <span><i style={{ background: HAAS.grey }} />{defender}</span>
      <span><i className={styles.chartTick} />Detection Line</span>
    </p>
  );
}

export type { Series as ChartSeries };
