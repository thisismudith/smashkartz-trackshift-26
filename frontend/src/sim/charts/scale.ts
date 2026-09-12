/**
 * Pure chart maths: domain -> pixel mapping, tick selection, and decimation.
 *
 * No React, no DOM, no data-source knowledge. This is the only file in charts/ that does
 * arithmetic worth testing, which is the point -- everything else is markup over these.
 *
 * Decimation lives here rather than in a component because it must run BEFORE anything is
 * rendered. A Silverstone lap is ~890 samples and the telemetry view stacks six panels for
 * two drivers; bounding the point count by the chart's pixel width rather than by the lap
 * length is what keeps that view an SVG instead of a canvas (UI.md section 7.4).
 */

export interface LinearScale {
  (value: number): number;
  invert(px: number): number;
  readonly domain: readonly [number, number];
  readonly range: readonly [number, number];
}

/**
 * Maps a numeric domain onto a pixel range. A zero-width domain maps everything to the
 * range midpoint rather than dividing by zero -- a single-valued series draws as a flat
 * line through the middle, which is the honest picture of "one value, no spread".
 */
export function linearScale(
  domain: readonly [number, number],
  range: readonly [number, number],
): LinearScale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0;
  const mid = (r0 + r1) / 2;

  const scale = ((value: number) => {
    if (span === 0) return mid;
    return r0 + ((value - d0) / span) * (r1 - r0);
  }) as { (value: number): number; invert(px: number): number; domain: readonly [number, number]; range: readonly [number, number] };

  scale.invert = (px: number) => {
    const rSpan = r1 - r0;
    if (rSpan === 0) return d0;
    return d0 + ((px - r0) / rSpan) * span;
  };
  scale.domain = domain;
  scale.range = range;
  return scale as LinearScale;
}

/** Evenly spaced band centres, e.g. one per lap or per driver. */
export function bandScale(count: number, range: readonly [number, number], padding = 0.1) {
  const [r0, r1] = range;
  const step = count > 0 ? (r1 - r0) / count : 0;
  const bandWidth = step * (1 - padding);
  return {
    /** Left edge of band i. */
    start: (i: number) => r0 + step * i + (step - bandWidth) / 2,
    /** Centre of band i. */
    centre: (i: number) => r0 + step * i + step / 2,
    bandWidth,
    step,
  };
}

/**
 * Ticks on 1/2/5 x 10^n boundaries covering [min, max]. Returns the *rounded* bounds too,
 * so an axis can extend to a tick rather than ending on a raw data value.
 */
export function niceTicks(min: number, max: number, count = 5): { ticks: number[]; niceMin: number; niceMax: number } {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { ticks: [], niceMin: 0, niceMax: 0 };
  if (min === max) {
    // a flat series still deserves a readable axis: pad by 1 (or 10% if the value is large)
    const pad = Math.max(1, Math.abs(min) * 0.1);
    min -= pad;
    max += pad;
  }
  const raw = (max - min) / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const stepMul = norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1;
  const step = stepMul * mag;

  const niceMin = Math.floor(min / step) * step;
  const niceMax = Math.ceil(max / step) * step;

  const ticks: number[] = [];
  // accumulate by index, not by repeated addition, so float error does not drift along the axis
  const n = Math.round((niceMax - niceMin) / step);
  for (let i = 0; i <= n; i++) ticks.push(niceMin + i * step);
  return { ticks, niceMin, niceMax };
}

/**
 * Min/max over finite values only. Returns null when nothing is finite, which callers must
 * treat as "no data" rather than defaulting to [0, 1] -- an axis drawn over absent data is
 * the kind of quiet fabrication the project contract forbids.
 */
export function extent(values: ArrayLike<number>): [number, number] | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (!Number.isFinite(v)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return lo === Infinity ? null : [lo, hi];
}

/**
 * Largest-Triangle-Three-Buckets downsampling.
 *
 * Chosen over naive stride sampling because it preserves visual extrema: a stride that
 * happens to skip the braking point makes a speed trace look like the driver never braked.
 * LTTB keeps the point in each bucket that forms the largest triangle with its neighbours,
 * which is exactly the point a reader would have noticed.
 *
 * Non-finite samples are dropped first (a null gap is not a zero). Returns the input
 * unchanged when it is already under `threshold`.
 */
export function decimate(
  xs: ArrayLike<number>,
  ys: ArrayLike<number>,
  threshold: number,
): { x: number[]; y: number[] } {
  const x: number[] = [];
  const y: number[] = [];
  const n = Math.min(xs.length, ys.length);
  for (let i = 0; i < n; i++) {
    if (Number.isFinite(xs[i]) && Number.isFinite(ys[i])) {
      x.push(xs[i]);
      y.push(ys[i]);
    }
  }
  const len = x.length;
  if (threshold >= len || threshold < 3) return { x, y };

  const outX: number[] = [x[0]];
  const outY: number[] = [y[0]];
  // one bucket per output point, excluding the retained first and last
  const every = (len - 2) / (threshold - 2);
  let a = 0;

  for (let i = 0; i < threshold - 2; i++) {
    // average of the NEXT bucket, used as the triangle's third vertex
    const avgStart = Math.floor((i + 1) * every) + 1;
    const avgEnd = Math.min(Math.floor((i + 2) * every) + 1, len);
    let avgX = 0;
    let avgY = 0;
    const avgN = Math.max(1, avgEnd - avgStart);
    for (let j = avgStart; j < avgEnd; j++) {
      avgX += x[j];
      avgY += y[j];
    }
    avgX /= avgN;
    avgY /= avgN;

    const rangeStart = Math.floor(i * every) + 1;
    const rangeEnd = Math.min(Math.floor((i + 1) * every) + 1, len);
    let bestArea = -1;
    let bestIdx = rangeStart;
    for (let j = rangeStart; j < rangeEnd; j++) {
      const area = Math.abs(
        (x[a] - avgX) * (y[j] - y[a]) - (x[a] - x[j]) * (avgY - y[a]),
      );
      if (area > bestArea) {
        bestArea = area;
        bestIdx = j;
      }
    }
    outX.push(x[bestIdx]);
    outY.push(y[bestIdx]);
    a = bestIdx;
  }

  outX.push(x[len - 1]);
  outY.push(y[len - 1]);
  return { x: outX, y: outY };
}

/** SVG path `d` for a polyline, skipping non-finite points by starting a new subpath.
 * A gap in the data becomes a gap in the line -- never a straight segment bridging it. */
export function linePath(x: readonly number[], y: readonly number[]): string {
  let d = "";
  let pen = false;
  for (let i = 0; i < x.length; i++) {
    if (!Number.isFinite(x[i]) || !Number.isFinite(y[i])) {
      pen = false;
      continue;
    }
    d += `${pen ? "L" : "M"}${x[i].toFixed(2)} ${y[i].toFixed(2)}`;
    pen = true;
  }
  return d;
}

/** SVG path `d` for a step line (value holds until the next x). Used for gear and position. */
export function stepPath(x: readonly number[], y: readonly number[]): string {
  let d = "";
  let pen = false;
  let prevY = 0;
  for (let i = 0; i < x.length; i++) {
    if (!Number.isFinite(x[i]) || !Number.isFinite(y[i])) {
      pen = false;
      continue;
    }
    if (!pen) {
      d += `M${x[i].toFixed(2)} ${y[i].toFixed(2)}`;
    } else {
      d += `L${x[i].toFixed(2)} ${prevY.toFixed(2)}L${x[i].toFixed(2)} ${y[i].toFixed(2)}`;
    }
    prevY = y[i];
    pen = true;
  }
  return d;
}

/** Closed path for an uncertainty band: forward along `hi`, back along `lo`. */
export function bandPath(
  x: readonly number[],
  lo: readonly number[],
  hi: readonly number[],
): string {
  const fwd: string[] = [];
  const back: string[] = [];
  for (let i = 0; i < x.length; i++) {
    if (!Number.isFinite(x[i]) || !Number.isFinite(lo[i]) || !Number.isFinite(hi[i])) continue;
    fwd.push(`${x[i].toFixed(2)} ${hi[i].toFixed(2)}`);
    back.unshift(`${x[i].toFixed(2)} ${lo[i].toFixed(2)}`);
  }
  if (fwd.length === 0) return "";
  return `M${fwd.join("L")}L${back.join("L")}Z`;
}
