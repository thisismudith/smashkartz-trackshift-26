/**
 * Geometry audit for the banded (one-row-per-entity) marks.
 *
 * These marks place text in the margins -- row labels in the left gutter, the value column in
 * the right one -- which is exactly where a chart silently breaks: the plot still looks fine
 * and the labels have quietly walked off the edge of the viewBox. That failure is invisible to
 * a type check and to every other test here, and it is the one that actually shipped (Plot used
 * to hard-clamp the left margin to 44px below a 520px breakpoint, discarding the 104px gutter
 * the circuit chart asks for and spilling every circuit name out of the plot).
 *
 * So this renders the real marks to static SVG and asserts on the coordinates. No DOM is
 * involved: the marks are pure functions of a width and a domain, which is what makes them
 * checkable this way at all.
 */
import { describe, it, expect } from "vitest";
import { createElement as h, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  Plot,
  Grid,
  XAxis,
  RefLine,
  BandLabels,
  RowBands,
  RowValues,
  RowHover,
  ErrorBarsH,
} from "./Plot";

/** The real shape of the circuit chart: 13 rows, a wide value, a long name, one muted row. */
const ROWS = [
  { label: "Monaco", value: 0.4966, lo: 0.427, hi: 0.566, n: 966 },
  { label: "Australian", value: 0.1846, lo: 0.132, hi: 0.237, n: 626 },
  { label: "Hungarian", value: 0.1578, lo: 0.115, hi: 0.201, n: 1016 },
  { label: "Canadian", value: 0.062, lo: -0.006, hi: 0.13, n: 602 },
  { label: "Italian", value: -0.0365, lo: -0.074, hi: 0.001, n: 735 },
];

const ns = (i: number) => ROWS[i].lo <= 0 && ROWS[i].hi >= 0;

function render(width: number, gutter: number, rowHeight: number): string {
  const height = ROWS.length * rowHeight + 54;
  const kids: ReactNode[] = [
    h(RowBands, { key: "b", n: ROWS.length, highlight: (i: number) => i === 0 }),
    h(Grid, { key: "g", x: true, y: false }),
    h(RefLine, { key: "r", x: 0, label: "no measurable effect" }),
    h(XAxis, { key: "x", label: "s lost per s of proximity", count: 5, format: (v: number) => v.toFixed(2) }),
    h(BandLabels, { key: "l", labels: ROWS.map((r) => r.label), emphasis: (i: number) => i === 0 }),
    h(ErrorBarsH, {
      key: "e",
      values: ROWS.map((r) => r.value),
      lo: ROWS.map((r) => r.lo),
      hi: ROWS.map((r) => r.hi),
      colour: (i: number) => (ns(i) ? "#AEAEAE" : "#DA291C"),
      r: 4,
      stem: true,
      caps: true,
    }),
    h(RowValues, {
      key: "v",
      values: ROWS.map((r) => r.value),
      format: (v: number) => v.toFixed(3),
      tag: (i: number) => (ns(i) ? "ns" : null),
      meta: (i: number) => `n=${ROWS[i].n}`,
    }),
    h(RowHover, { key: "h", titles: ROWS.map((r) => r.label) }),
  ];
  return renderToStaticMarkup(
    h(Plot, {
      xDomain: [-0.1, 0.6] as const,
      yDomain: [0, ROWS.length] as const,
      width,
      height,
      margin: { left: gutter, right: 76, top: 8, bottom: 38 },
      children: kids,
    }),
  );
}

/** Every numeric geometry attribute in the markup. */
function coords(svg: string): string[] {
  return [...svg.matchAll(/(?:\b(?:x|y|x1|x2|y1|y2|cx|cy|width|height|r|rx)=)"(-?[\d.]+|NaN)"/g)].map(
    (m) => m[1],
  );
}

/** Text nodes with their anchor x, so a label that walked out of the box can be caught. */
function texts(svg: string): { x: number; text: string }[] {
  return [...svg.matchAll(/<text[^>]*\bx="(-?[\d.]+)"[^>]*>(.*?)<\/text>/g)].map((m) => ({
    x: Number(m[1]),
    text: m[2].replace(/<[^>]*>/g, ""),
  }));
}

describe("banded marks geometry", () => {
  const WIDTHS = [360, 480, 720, 1100];

  it.each(WIDTHS)("emits only finite, non-negative coordinates at %ipx", (w) => {
    const bad = coords(render(w, 104, 26)).filter((v) => v === "NaN" || Number(v) < -0.01);
    expect(bad).toEqual([]);
  });

  it.each(WIDTHS)("keeps every row label inside the viewBox at %ipx", (w) => {
    // Labels are end-anchored in the left gutter, so their anchor is the RIGHT edge of the
    // text and the glyphs run leftwards from it. ~6.2px per character at this font size.
    for (const t of texts(render(w, 104, 26))) {
      if (t.x > 200) continue; // value column, checked below
      expect(t.x - t.text.length * 6.2).toBeGreaterThan(-1);
    }
  });

  it.each(WIDTHS)("keeps the value column inside the viewBox at %ipx", (w) => {
    const svg = render(w, 104, 26);
    for (const t of texts(svg)) {
      if (t.x < w - 90) continue; // start-anchored value column only
      expect(t.x + t.text.length * 6.2).toBeLessThanOrEqual(w);
    }
  });

  it("gives every row a hover target carrying its identity", () => {
    const svg = render(720, 104, 26);
    expect((svg.match(/<title>/g) ?? []).length).toBe(ROWS.length);
    for (const r of ROWS) expect(svg).toContain(`<title>${r.label}</title>`);
  });

  it("tags a zero-crossing row in the gutter, not by colour alone", () => {
    const svg = render(720, 104, 26);
    // The tag rides in a tspan after the value, with a separating space.
    expect((svg.match(/<tspan[^>]*> ns<\/tspan>/g) ?? []).length).toBe(
      ROWS.filter((_, i) => ns(i)).length,
    );
  });

  it("draws the stem from the zero baseline, so bar length reads as magnitude", () => {
    const svg = render(720, 104, 26);
    // The stem is the only 3-wide, 0.22-opacity stroke; all of them start at the same x.
    const stems = [...svg.matchAll(/<line x1="([\d.]+)" x2="([\d.]+)"[^>]*stroke-width="3"[^>]*>/g)];
    expect(stems.length).toBe(ROWS.length);
    const starts = new Set(stems.map((m) => m[1]));
    expect(starts.size).toBe(1);
  });

  it("keeps rows evenly pitched so a label lines up with its estimate", () => {
    const svg = render(720, 104, 26);
    // Only the band rects: the clip rect and the per-row hover rects are also <rect>.
    const bands = [...svg.matchAll(/<rect class="[^"]*rowBand[^"]*"[^>]*\by="([\d.]+)"/g)].map((m) =>
      Number(m[1]),
    );
    expect(bands.length).toBe(ROWS.length);
    const pitches = bands.slice(1).map((y, i) => y - bands[i]);
    for (const p of pitches) expect(Math.abs(p - pitches[0])).toBeLessThan(0.01);
  });
});
