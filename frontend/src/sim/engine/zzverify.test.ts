/* TEMPORARY independent verification harness (verifier). Deleted after measurement. */
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, it } from "vitest";
import type { CarState, TrackModel } from "../contract/types";
import { parseTrackModel, trackPointAt, type RawTrackModel } from "../data/manifest";
import { GeneratedTimeline } from "./generatedTimeline";
import { runRace } from "./raceEngine";
import type { FittedParams } from "./params";

const SIM = fileURLToPath(new URL("../../../public/sim/", import.meta.url));
const J = (f: string) => JSON.parse(readFileSync(SIM + f, "utf8"));
const latest = J("index.json").latest as string;
const IDX = J(latest);
const PARAMS = J(IDX.params) as FittedParams;
const CAT = J(IDX.catalogue) as {
  tracks: { slug: string; event: string; raceLaps: number | null;
            entries?: { code: string; team: string | null }[] }[];
};

const CAR_L = 5.6, CAR_W = 2.0;

/** Oriented-rectangle overlap by SAT. */
function overlaps(a: {x:number;y:number;h:number}, b: {x:number;y:number;h:number}): boolean {
  const dx = b.x - a.x, dy = b.y - a.y;
  if (dx*dx + dy*dy > (CAR_L*CAR_L)) return false; // beyond the max possible extent
  const axes = [
    { x: Math.cos(a.h), y: Math.sin(a.h) }, { x: -Math.sin(a.h), y: Math.cos(a.h) },
    { x: Math.cos(b.h), y: Math.sin(b.h) }, { x: -Math.sin(b.h), y: Math.cos(b.h) },
  ];
  const corners = (c: {x:number;y:number;h:number}) => {
    const ux = Math.cos(c.h), uy = Math.sin(c.h);
    const vx = -uy, vy = ux;
    const out: [number, number][] = [];
    for (const s1 of [-1, 1]) for (const s2 of [-1, 1]) {
      out.push([c.x + s1*ux*CAR_L/2 + s2*vx*CAR_W/2, c.y + s1*uy*CAR_L/2 + s2*vy*CAR_W/2]);
    }
    return out;
  };
  const ca = corners(a), cb = corners(b);
  for (const ax of axes) {
    let amin = Infinity, amax = -Infinity, bmin = Infinity, bmax = -Infinity;
    for (const [px, py] of ca) { const p = px*ax.x + py*ax.y; amin = Math.min(amin,p); amax = Math.max(amax,p); }
    for (const [px, py] of cb) { const p = px*ax.x + py*ax.y; bmin = Math.min(bmin,p); bmax = Math.max(bmax,p); }
    if (amax <= bmin + 1e-9 || bmax <= amin + 1e-9) return false;
  }
  return true;
}

/** Along-ring signed offset of every car relative to the reported leader, wrapped. */
function rel(states: CarState[], L: number): number[] {
  const lead = states.find((s) => s.position === 1)!.stationM;
  return states.map((s) => {
    let d = s.stationM - lead;
    if (d > L/2) d -= L;
    if (d < -L/2) d += L;
    return d;
  });
}

function build(slug: string, seed: number) {
  const track: TrackModel = parseTrackModel(J(IDX.tracks[slug]) as RawTrackModel);
  const meta = CAT.tracks.find((t) => t.slug === slug)!;
  const entries = (meta.entries ?? []).map((e) => ({ driver: e.code, team: e.team }));
  const totalLaps = meta.raceLaps ?? 20;
  const result = runRace({ track, params: PARAMS, entries, totalLaps, seed });
  return { track, entries, result, tl: new GeneratedTimeline(result, track), meta };
}

const TIMES = [0, 1, 5, 10, 30, 60];

describe("ZZ VERIFY standing start", () => {
  it("measures all 13 circuits", () => {
    const slugs = Object.keys(IDX.tracks);
    const lines: string[] = [];
    for (const slug of slugs) {
      const { track, entries, result, tl } = build(slug, 7);
      const ss = result.standingStart;
      lines.push(`\n#### ${slug}  event="${track.event}" L=${track.lengthMetres.toFixed(1)} cars=${entries.length}`);
      if (!ss) { lines.push(`  NO STANDING START: ${result.standingStartRefusal}`); continue; }
      const hand = Math.max(...ss.placements.map((p) => p.launch.reactionS + p.launch.handoverSpeedMps / p.launch.accelMps2));
      lines.push(`  pitch=${ss.pitchMetres.toFixed(3)} src=${ss.pitchSource} anchor=${ss.anchorMetres === null ? "null" : ss.anchorMetres.toFixed(2)} prov=${ss.placementProvenance} handover=${hand.toFixed(3)}s`);
      lines.push(`  lap1: remainder=${ss.lap1.remainderSeconds.toFixed(3)} perSlotShipped=${ss.lap1.excessPerGridSlotSeconds.toFixed(4)} geom=${ss.lap1.geometricPerGridSlotSeconds.toFixed(4)} applied=${ss.lap1.appliedPerGridSlotSeconds.toFixed(4)}`);
      for (const t of TIMES) {
        const m = tl.sampleAt(t);
        const states = [...m.values()];
        const r = rel(states, track.lengthMetres);
        const spread = Math.max(...r) - Math.min(...r);
        const maxAbs = Math.max(...r.map(Math.abs));
        const speeds = states.map((s) => s.speedKph);
        // 2D geometric interpenetration
        const pts = states.map((s) => {
          const p = trackPointAt(track, s.stationM);
          return { x: p.x, y: p.y, h: p.heading };
        });
        let pairs2d = 0;
        for (let i = 0; i < pts.length; i++) for (let j = i+1; j < pts.length; j++) if (overlaps(pts[i], pts[j])) pairs2d++;
        // along-ring gap metric
        const sorted = r.slice().sort((a,b)=>a-b);
        let pairs1d = 0, minGap = Infinity;
        for (let i = 0; i < sorted.length; i++) for (let j = i+1; j < sorted.length; j++) {
          const d = Math.abs(sorted[i]-sorted[j]);
          const dd = Math.min(d, track.lengthMetres - d);
          if (dd < CAR_L - 1e-9) pairs1d++;
        }
        for (let i = 1; i < sorted.length; i++) minGap = Math.min(minGap, sorted[i]-sorted[i-1]);
        // grid-order inversions vs entry order
        const byPos = states.slice().sort((a,b)=>a.position-b.position).map((s)=>s.driver);
        const gridIdx = new Map(entries.map((e,i)=>[e.driver,i]));
        let inv = 0;
        for (let i = 0; i < byPos.length; i++) for (let j = i+1; j < byPos.length; j++)
          if (gridIdx.get(byPos[i])! > gridIdx.get(byPos[j])!) inv++;
        const lap1Done = states.filter((s)=>s.lapsDone>=1).length;
        const statuses = new Set(states.map((s)=>s.status));
        lines.push(`  t=${String(t).padStart(2)}s spread=${spread.toFixed(2)}m maxAbsRel=${maxAbs.toFixed(1)} minGap=${minGap===Infinity?"-":minGap.toFixed(3)} kph=[${Math.min(...speeds).toFixed(1)},${Math.max(...speeds).toFixed(1)}] pairs2D=${pairs2d} pairs1D=${pairs1d} inversions=${inv} lapsDone>=1:${lap1Done} status={${[...statuses].join(",")}}`);
      }
    }
    console.log(lines.join("\n"));
  }, 600000);
});
