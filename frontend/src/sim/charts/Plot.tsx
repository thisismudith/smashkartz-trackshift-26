/**
 * The SVG plotting surface and its marks.
 *
 * `Plot` owns the viewBox, the margins and the two scales; marks are children that read them
 * from context, so a chart reads as its own description:
 *
 *   <Plot xDomain={[0, 360]} yDomain={[0, 400]} ...>
 *     <Grid y /><XAxis label="speed km/h" /><YAxis label="kW" />
 *     <Line x={speeds} y={normal} colour={CHART.series[0]} />
 *   </Plot>
 *
 * The viewBox is fixed and the <svg> is width:100% -- the chart scales with its container and
 * needs no resize observer. Stroke widths are therefore specified in viewBox units and chosen
 * so they land near 2px at the widths these pages actually use.
 */
"use client";

import {
  createContext,
  useCallback,
  useContext,
  useId,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import s from "./charts.module.css";
import { linearScale, niceTicks, linePath, stepPath, bandPath, type LinearScale } from "./scale";

interface PlotCtx {
  xs: LinearScale;
  ys: LinearScale;
  inner: { left: number; right: number; top: number; bottom: number };
  /** id of the clip path covering the plotting rect. Marks wear it; axes do not. */
  clipId: string;
}

const Ctx = createContext<PlotCtx | null>(null);

function usePlot(): PlotCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("chart mark used outside <Plot>");
  return c;
}

/** One series the hover layer can read. Give it the SAME arrays you gave the mark. */
export interface HoverSeries {
  label: string;
  colour: string;
  x: readonly number[];
  y: readonly number[];
  /** Formats this series' value in the tooltip. Defaults to 2 decimal places. */
  format?: (v: number) => string;
}

export interface PlotProps {
  xDomain: readonly [number, number];
  yDomain: readonly [number, number];
  width?: number;
  height?: number;
  margin?: { left?: number; right?: number; top?: number; bottom?: number };
  /** Accessible description of what the plot shows. */
  ariaLabel?: string;
  /**
   * Turns the plot into a hoverable chart: a vertical crosshair that snaps to the nearest x,
   * a marker on each series at that x, and a shared tooltip. Omit for a static plot.
   */
  hover?: {
    series: HoverSeries[];
    /** Label for the x value in the tooltip header, e.g. "lap" or "km/h". */
    xLabel?: string;
    xFormat?: (v: number) => string;
  };
  children: ReactNode;
}

/** Index of the entry in `xs` nearest to `target`. Assumes xs is ascending, which every series
 * here is (lap number, station, speed). Linear scan: series are short enough that a binary
 * search would be more code than it saves, and this runs once per pointer move. */
function nearestIndex(xs: readonly number[], target: number): number {
  let best = -1;
  let bestD = Infinity;
  for (let i = 0; i < xs.length; i++) {
    const v = xs[i];
    if (!Number.isFinite(v)) continue;
    const d = Math.abs(v - target);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  }
  return best;
}

export function Plot({
  xDomain,
  yDomain,
  width = 720,
  height = 280,
  margin,
  ariaLabel,
  hover,
  children,
}: PlotProps) {
  const m = {
    left: margin?.left ?? 48,
    right: margin?.right ?? 16,
    top: margin?.top ?? 12,
    bottom: margin?.bottom ?? 34,
  };
  const rawId = useId();
  const clipId = `plotclip-${rawId.replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const ctx = useMemo<PlotCtx>(() => {
    const inner = { left: m.left, right: width - m.right, top: m.top, bottom: height - m.bottom };
    return {
      xs: linearScale(xDomain, [inner.left, inner.right]),
      // y grows downward in SVG, so the range is inverted
      ys: linearScale(yDomain, [inner.bottom, inner.top]),
      inner,
      clipId,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [xDomain[0], xDomain[1], yDomain[0], yDomain[1], width, height, m.left, m.right, m.top, m.bottom, clipId]);

  // hovered x in DATA units, or null. Kept as data rather than pixels so it survives a resize.
  const [hoverX, setHoverX] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  /**
   * Pointer -> data x.
   *
   * The SVG has a FIXED viewBox and is scaled by CSS (width:100%), so a client x means nothing
   * until it is divided by the rendered width and multiplied back up by the viewBox width. Doing
   * this by hand rather than via getScreenCTM() keeps it cheap enough to run on every pointermove
   * and avoids a layout read per axis.
   */
  const toDataX = useCallback(
    (clientX: number): number | null => {
      const el = svgRef.current;
      if (!el) return null;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0) return null;
      const vbX = ((clientX - rect.left) / rect.width) * width;
      if (vbX < ctx.inner.left - 2 || vbX > ctx.inner.right + 2) return null;
      return ctx.xs.invert(vbX);
    },
    [ctx, width],
  );

  const onMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (!hover) return;
    setHoverX(toDataX(e.clientX));
  };

  // Keyboard access: the chart is focusable when hoverable, and arrows step the crosshair along
  // the first series. Without this the tooltip is pointer-only, which puts the numbers out of
  // reach of anyone not using a mouse.
  const onKey = (e: ReactKeyboardEvent<SVGSVGElement>) => {
    if (!hover || hover.series.length === 0) return;
    const xs0 = hover.series[0].x;
    if (xs0.length === 0) return;
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight" && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    const cur = hoverX === null ? -1 : nearestIndex(xs0 as number[], hoverX);
    let next = cur;
    if (e.key === "ArrowLeft") next = cur <= 0 ? 0 : cur - 1;
    else if (e.key === "ArrowRight") next = cur < 0 ? 0 : Math.min(xs0.length - 1, cur + 1);
    else if (e.key === "Home") next = 0;
    else next = xs0.length - 1;
    setHoverX(xs0[next]);
  };

  // Resolve the hover into one snapped x plus a readout per series.
  const readout = useMemo(() => {
    if (!hover || hoverX === null) return null;
    const rows: { label: string; colour: string; value: number; y: number }[] = [];
    let snapX: number | null = null;
    let snapD = Infinity;
    for (const sr of hover.series) {
      const i = nearestIndex(sr.x as number[], hoverX);
      if (i < 0) continue;
      const d = Math.abs(sr.x[i] - hoverX);
      if (d < snapD) {
        snapD = d;
        snapX = sr.x[i];
      }
    }
    if (snapX === null) return null;
    for (const sr of hover.series) {
      const i = nearestIndex(sr.x as number[], snapX);
      if (i < 0 || !Number.isFinite(sr.y[i])) continue;
      // only report a series that actually has a sample at (or very near) the snapped x --
      // carrying a distant point forward would invent a value the series does not have
      if (Math.abs(sr.x[i] - snapX) > Math.max(1e-9, Math.abs(snapX) * 0.02 + 0.5)) continue;
      rows.push({ label: sr.label, colour: sr.colour, value: sr.y[i], y: ctx.ys(sr.y[i]) });
    }
    return { snapX, px: ctx.xs(snapX), rows };
  }, [hover, hoverX, ctx]);

  // tooltip position as a percentage of the rendered box, so it tracks CSS scaling for free
  const tipLeftPct = readout ? (readout.px / width) * 100 : 0;
  const flip = tipLeftPct > 60;

  return (
    <Ctx.Provider value={ctx}>
      <div className={s.plotInner}>
        <svg
          ref={svgRef}
          className={s.plot}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={ariaLabel}
          tabIndex={hover ? 0 : undefined}
          onPointerMove={hover ? onMove : undefined}
          onPointerLeave={hover ? () => setHoverX(null) : undefined}
          onKeyDown={hover ? onKey : undefined}
          onBlur={hover ? () => setHoverX(null) : undefined}
        >
          <defs>
            <clipPath id={clipId}>
              <rect
                x={ctx.inner.left}
                y={ctx.inner.top}
                width={Math.max(0, ctx.inner.right - ctx.inner.left)}
                height={Math.max(0, ctx.inner.bottom - ctx.inner.top)}
              />
            </clipPath>
          </defs>
          {children}
          {readout ? (
            <g className={s.hoverLayer} aria-hidden="true">
              <line
                className={s.crosshair}
                x1={readout.px}
                x2={readout.px}
                y1={ctx.inner.top}
                y2={ctx.inner.bottom}
              />
              <g clipPath={`url(#${clipId})`}>
                {readout.rows.map((r) => (
                  <circle
                    key={r.label}
                    cx={readout.px}
                    cy={r.y}
                    r={4}
                    fill={r.colour}
                    stroke="#111111"
                    strokeWidth={1.5}
                  />
                ))}
              </g>
            </g>
          ) : null}
        </svg>

        {readout && readout.rows.length > 0 ? (
          <div
            className={s.tooltip}
            style={{
              left: `${tipLeftPct}%`,
              transform: flip ? "translate(-100%, 0)" : "none",
              marginLeft: flip ? "-10px" : "10px",
              top: 8,
            }}
            role="status"
          >
            <div className={s.tooltipRow}>
              <span className={s.tooltipKey}>{hover?.xLabel ?? "x"}</span>
              <span className={s.tooltipVal}>
                {(hover?.xFormat ?? ((v: number) => String(v)))(readout.snapX)}
              </span>
            </div>
            {readout.rows.map((r) => (
              <div className={s.tooltipRow} key={r.label}>
                <span className={s.swatch} data-shape="dot" style={{ background: r.colour }} />
                <span className={s.tooltipKey}>{r.label}</span>
                <span className={s.tooltipVal}>
                  {(hover?.series.find((x) => x.label === r.label)?.format ??
                    ((v: number) => v.toFixed(2)))(r.value)}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </Ctx.Provider>
  );
}

/* ---------------------------------------------------------------- axes --- */

export function Grid({ x = false, y = true, count = 5 }: { x?: boolean; y?: boolean; count?: number }) {
  const { xs, ys, inner } = usePlot();
  // niceTicks rounds OUTWARD, so it can return a tick outside the domain. XAxis/YAxis already
  // drop those; Grid must too, or a stray rule is painted in the margin outside the plot frame.
  const yt = y ? niceTicks(ys.domain[0], ys.domain[1], count).ticks : [];
  const xt = x ? niceTicks(xs.domain[0], xs.domain[1], count).ticks : [];
  const inX = (px: number) => px >= inner.left - 0.5 && px <= inner.right + 0.5;
  const inY = (py: number) => py >= inner.top - 0.5 && py <= inner.bottom + 0.5;
  return (
    <g aria-hidden="true">
      {yt.map((t) => ys(t)).filter(inY).map((py, i) => (
        <line key={`y${i}`} className={s.gridLine} x1={inner.left} x2={inner.right} y1={py} y2={py} />
      ))}
      {xt.map((t) => xs(t)).filter(inX).map((px, i) => (
        <line key={`x${i}`} className={s.gridLine} y1={inner.top} y2={inner.bottom} x1={px} x2={px} />
      ))}
    </g>
  );
}

export function XAxis({
  label,
  count = 5,
  format = (v: number) => String(v),
}: {
  label?: string;
  count?: number;
  format?: (v: number) => string;
}) {
  const { xs, ys, inner } = usePlot();
  const { ticks } = niceTicks(xs.domain[0], xs.domain[1], count);
  const baseline = ys.range[0];
  return (
    <g>
      <line className={s.axisLine} x1={inner.left} x2={inner.right} y1={baseline} y2={baseline} />
      {ticks.map((t) => {
        const px = xs(t);
        if (px < inner.left - 0.5 || px > inner.right + 0.5) return null;
        return (
          <g key={t}>
            <line className={s.axisLine} x1={px} x2={px} y1={baseline} y2={baseline + 4} />
            <text className={s.tickLabel} x={px} y={baseline + 15} textAnchor="middle">
              {format(t)}
            </text>
          </g>
        );
      })}
      {label ? (
        <text className={s.axisLabel} x={(inner.left + inner.right) / 2} y={baseline + 30} textAnchor="middle">
          {label}
        </text>
      ) : null}
    </g>
  );
}

export function YAxis({
  label,
  count = 5,
  format = (v: number) => String(v),
}: {
  label?: string;
  count?: number;
  format?: (v: number) => string;
}) {
  const { ys, inner } = usePlot();
  const { ticks } = niceTicks(ys.domain[0], ys.domain[1], count);
  return (
    <g>
      <line className={s.axisLine} x1={inner.left} x2={inner.left} y1={inner.top} y2={inner.bottom} />
      {ticks.map((t) => {
        const py = ys(t);
        if (py < inner.top - 0.5 || py > inner.bottom + 0.5) return null;
        return (
          <g key={t}>
            <line className={s.axisLine} x1={inner.left - 4} x2={inner.left} y1={py} y2={py} />
            <text className={s.tickLabel} x={inner.left - 7} y={py + 3.5} textAnchor="end">
              {format(t)}
            </text>
          </g>
        );
      })}
      {label ? (
        <text
          className={s.axisLabel}
          transform={`translate(11 ${(inner.top + inner.bottom) / 2}) rotate(-90)`}
          textAnchor="middle"
        >
          {label}
        </text>
      ) : null}
    </g>
  );
}

/* --------------------------------------------------------------- marks --- */

export function Line({
  x,
  y,
  colour,
  width = 2,
  step = false,
  dashed = false,
  label,
  labelIndex,
}: {
  x: readonly number[];
  y: readonly number[];
  colour: string;
  width?: number;
  step?: boolean;
  dashed?: boolean;
  /** Direct label. Use for <= 4 series (UI.md section 7.3). */
  label?: string;
  /** Which vertex carries the label. Defaults to the last finite point; set it when two
   * series end at the same place and their end labels would collide. */
  labelIndex?: number;
}) {
  const { xs, ys, inner, clipId } = usePlot();
  const px = x.map((v) => xs(v));
  const py = y.map((v) => ys(v));
  const d = step ? stepPath(px, py) : linePath(px, py);

  let lastX = NaN;
  let lastY = NaN;
  if (
    labelIndex !== undefined &&
    Number.isFinite(px[labelIndex]) &&
    Number.isFinite(py[labelIndex])
  ) {
    lastX = px[labelIndex];
    lastY = py[labelIndex];
  } else {
    for (let i = x.length - 1; i >= 0; i--) {
      if (Number.isFinite(px[i]) && Number.isFinite(py[i])) {
        lastX = px[i];
        lastY = py[i];
        break;
      }
    }
  }
  // near the right edge, hang the label to the LEFT so it stays inside the plot
  const nearRight = lastX > inner.left + 0.72 * (inner.right - inner.left);

  return (
    <g>
      <path
        clipPath={`url(#${clipId})`}
        d={d}
        fill="none"
        stroke={colour}
        strokeWidth={width}
        strokeLinejoin="round"
        strokeLinecap="round"
        strokeDasharray={dashed ? "5 4" : undefined}
      />
      {label && Number.isFinite(lastX) ? (
        <text
          className={s.directLabel}
          x={lastX + (nearRight ? -5 : 5)}
          y={lastY + 3.5}
          textAnchor={nearRight ? "end" : "start"}
        >
          {label}
        </text>
      ) : null}
    </g>
  );
}

/** Uncertainty ribbon. Always drawn under its line, never on its own without one. */
export function Band({
  x,
  lo,
  hi,
  colour,
  opacity = 0.18,
}: {
  x: readonly number[];
  lo: readonly number[];
  hi: readonly number[];
  colour: string;
  opacity?: number;
}) {
  const { xs, ys, clipId } = usePlot();
  const d = bandPath(x.map((v) => xs(v)), lo.map((v) => ys(v)), hi.map((v) => ys(v)));
  if (!d) return null;
  return <path clipPath={`url(#${clipId})`} d={d} fill={colour} fillOpacity={opacity} stroke="none" />;
}

/** Shaded x-range, e.g. a safety-car window or a "mode not discriminable" region. */
export function XRegion({
  from,
  to,
  colour = "#AEAEAE",
  opacity = 0.1,
  label,
}: {
  from: number;
  to: number;
  colour?: string;
  opacity?: number;
  label?: string;
}) {
  const { xs, ys, inner } = usePlot();
  const a = Math.max(inner.left, Math.min(xs(from), xs(to)));
  const b = Math.min(inner.right, Math.max(xs(from), xs(to)));
  if (!(b > a)) return null;
  return (
    <g>
      <rect x={a} y={ys.range[1]} width={b - a} height={ys.range[0] - ys.range[1]} fill={colour} fillOpacity={opacity} />
      {label ? (
        <text className={s.refLabel} x={(a + b) / 2} y={ys.range[1] + 12} textAnchor="middle">
          {label}
        </text>
      ) : null}
    </g>
  );
}

export function RefLine({
  x,
  y,
  label,
  colour,
}: {
  x?: number;
  y?: number;
  label?: string;
  colour?: string;
}) {
  const { xs, ys, inner } = usePlot();
  if (x !== undefined) {
    const px = xs(x);
    if (px < inner.left || px > inner.right) return null;
    return (
      <g>
        <line className={s.refLine} style={colour ? { stroke: colour } : undefined} x1={px} x2={px} y1={inner.top} y2={inner.bottom} />
        {label ? (
          <text className={s.refLabel} x={px + 4} y={inner.top + 10}>
            {label}
          </text>
        ) : null}
      </g>
    );
  }
  if (y !== undefined) {
    const py = ys(y);
    if (py < inner.top || py > inner.bottom) return null;
    return (
      <g>
        <line className={s.refLine} style={colour ? { stroke: colour } : undefined} x1={inner.left} x2={inner.right} y1={py} y2={py} />
        {label ? (
          <text className={s.refLabel} x={inner.right} y={py - 4} textAnchor="end">
            {label}
          </text>
        ) : null}
      </g>
    );
  }
  return null;
}

export function Dots({
  x,
  y,
  colour,
  r = 4,
}: {
  x: readonly number[];
  y: readonly number[];
  colour: string;
  r?: number;
}) {
  const { xs, ys, clipId } = usePlot();
  return (
    <g clipPath={`url(#${clipId})`}>
      {x.map((v, i) =>
        Number.isFinite(v) && Number.isFinite(y[i]) ? (
          // 2px surface ring so overlapping points stay countable
          <circle key={i} cx={xs(v)} cy={ys(y[i])} r={r} fill={colour} stroke="#111111" strokeWidth={1.5} />
        ) : null,
      )}
    </g>
  );
}

/**
 * Horizontal bars with a 4px rounded data-end anchored to the baseline, and a 2px surface gap
 * between adjacent bars. Used for the ranked circuit charts.
 */
export function HBars({
  values,
  colour,
  baseline = 0,
  gap = 2,
}: {
  values: readonly number[];
  colour: string | ((i: number) => string);
  baseline?: number;
  gap?: number;
}) {
  const { xs, inner, clipId } = usePlot();
  const n = values.length;
  const step = n > 0 ? (inner.bottom - inner.top) / n : 0;
  const h = Math.max(1, step - gap);
  const x0 = xs(baseline);
  return (
    <g clipPath={`url(#${clipId})`}>
      {values.map((v, i) => {
        if (!Number.isFinite(v)) return null;
        const x1 = xs(v);
        const left = Math.min(x0, x1);
        const w = Math.abs(x1 - x0);
        return (
          <rect
            key={i}
            x={left}
            y={inner.top + step * i + gap / 2}
            width={Math.max(1, w)}
            height={h}
            rx={Math.min(4, h / 2)}
            fill={typeof colour === "function" ? colour(i) : colour}
          />
        );
      })}
    </g>
  );
}

/** Point + confidence interval, one row per entity. The forest plot for params.json leaves. */
export function ErrorBarsH({
  values,
  lo,
  hi,
  colour,
  r = 3.5,
}: {
  values: readonly number[];
  lo: readonly number[];
  hi: readonly number[];
  colour: string | ((i: number) => string);
  r?: number;
}) {
  const { xs, inner, clipId } = usePlot();
  const n = values.length;
  const step = n > 0 ? (inner.bottom - inner.top) / n : 0;
  return (
    <g clipPath={`url(#${clipId})`}>
      {values.map((v, i) => {
        if (!Number.isFinite(v)) return null;
        const cy = inner.top + step * i + step / 2;
        const c = typeof colour === "function" ? colour(i) : colour;
        const a = Number.isFinite(lo[i]) ? xs(lo[i]) : null;
        const b = Number.isFinite(hi[i]) ? xs(hi[i]) : null;
        return (
          <g key={i}>
            {a !== null && b !== null ? (
              <line x1={a} x2={b} y1={cy} y2={cy} stroke={c} strokeWidth={1.5} strokeOpacity={0.7} />
            ) : null}
            <circle cx={xs(v)} cy={cy} r={r} fill={c} stroke="#111111" strokeWidth={1.5} />
          </g>
        );
      })}
    </g>
  );
}

/** Category labels down the left of a banded chart (one per bar). */
export function BandLabels({ labels }: { labels: readonly string[] }) {
  const { inner } = usePlot();
  const n = labels.length;
  const step = n > 0 ? (inner.bottom - inner.top) / n : 0;
  return (
    <g>
      {labels.map((l, i) => (
        <text
          key={l + i}
          className={s.tickLabel}
          x={inner.left - 7}
          y={inner.top + step * i + step / 2 + 3.5}
          textAnchor="end"
        >
          {l}
        </text>
      ))}
    </g>
  );
}
