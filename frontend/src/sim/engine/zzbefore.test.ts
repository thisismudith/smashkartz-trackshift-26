import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, it } from "vitest";
import type { CarState } from "../contract/types";
import { parseTrackModel, type RawTrackModel } from "../data/manifest";
import { GeneratedTimeline } from "./generatedTimeline";
import type { FittedParams } from "./params";
import { runRace } from "./raceEngine";

const dir = fileURLToPath(new URL("../../../public/sim/", import.meta.url));
const CAR = 5.6;
const EPS = 1e-6;

function unwrapped(states: CarState[], L: number): number[] {
  const lead = states[0].stationM;
  return states.map((s) => {
    let d = s.stationM - lead;
    if (d > L / 2) d -= L;
    if (d < -L / 2) d += L;
    return d;
  }).sort((a, b) => a - b);
}

function overlaps(states: CarState[], L: number) {
  const p = unwrapped(states, L);
  let all = 0, consec = 0, minGap = Infinity;
  for (let i = 0; i < p.length; i++) {
    for (let j = i + 1; j < p.length; j++) {
      const d = Math.abs(p[i] - p[j]);
      if (Math.min(d, L - d) < CAR - EPS) all++;
    }
  }
  for (let i = 1; i < p.length; i++) {
    const g = p[i] - p[i - 1];
    minGap = Math.min(minGap, g);
    if (g < CAR - EPS) consec++;
  }
  return { all, consec, minGap, spread: p[p.length - 1] - p[0] };
}

describe("AUDIT-COMMON", () => {
  it("measures t=0/10/30 with the SAME metric on both code versions", () => {
    if (!existsSync(`${dir}index.json`)) return;
    const latest = JSON.parse(readFileSync(`${dir}index.json`, "utf8")).latest as string;
    const top = JSON.parse(readFileSync(`${dir}${latest}`, "utf8"));
    const params = JSON.parse(readFileSync(`${dir}${top.params}`, "utf8")) as FittedParams;
    const pool = Object.keys(params.driverOffsetSeconds).sort();
    const rows: string[] = [];
    for (const slug of Object.keys(top.tracks)) {
      const raw = JSON.parse(readFileSync(`${dir}${top.tracks[slug]}`, "utf8")) as RawTrackModel;
      const track = parseTrackModel(raw);
      const L = track.lengthMetres;
      const order = track.grid.order.length >= 10 ? track.grid.order : pool.slice(0, 20);
      const entries = order.map((d) => ({ driver: d, team: null }));
      const result = runRace({ track, params, entries, totalLaps: 10, seed: 7 });
      const tl = new GeneratedTimeline(result, track);
      const cells: string[] = [];
      for (const t of [0, 10, 30]) {
        const states = [...tl.sampleAt(t).values()];
        const o = overlaps(states, L);
        const spd = states.map((s) => s.speedKph);
        cells.push(
          `t=${String(t).padStart(2)} ovl=${String(o.all).padStart(3)}/${(states.length * (states.length - 1)) / 2}`
          + ` spread=${o.spread.toFixed(2).padStart(8)} minGap=${o.minGap.toFixed(2).padStart(6)}`
          + ` spd=[${Math.min(...spd).toFixed(1)},${Math.max(...spd).toFixed(1)}]`,
        );
      }
      rows.push(`${slug.padEnd(24)} n=${entries.length}  ${cells.join(" | ")}`);
    }
    writeFileSync(String(process.env.AUDIT_OUT), rows.join("\n"));
  });
});
