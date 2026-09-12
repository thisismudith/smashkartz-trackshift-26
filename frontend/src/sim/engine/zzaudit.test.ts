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

/** Unwrapped positions relative to the leader, so a field straddling the timing line is
 * not reported as a 5 km spread. */
function unwrapped(states: CarState[], L: number): number[] {
  const lead = states[0].stationM;
  return states.map((s) => {
    let d = s.stationM - lead;
    if (d > L / 2) d -= L;
    if (d < -L / 2) d += L;
    return d;
  }).sort((a, b) => a - b);
}

function overlaps(states: CarState[], L: number): { all: number; consec: number; minGap: number } {
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
  return { all, consec, minGap };
}

describe("AUDIT", () => {
  it("measures the standing start on every shipped circuit", () => {
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
      const ss = result.standingStart!;
      const parts: string[] = [];
      parts.push(
        `  anchor=${ss.anchorMetres === null ? "NULL (" + ss.placementProvenance + ")" : ss.anchorMetres.toFixed(2) + " m"}`
        + ` pitch=${ss.pitchMetres.toFixed(3)} from ${ss.pitchSource}`
        + ` | lap1 +${ss.lap1.remainderSeconds.toFixed(3)} s and +${ss.lap1.appliedPerGridSlotSeconds.toFixed(4)} s/slot`
        + ` (shipped ${ss.lap1.excessPerGridSlotSeconds.toFixed(3)} less geometric ${ss.lap1.geometricPerGridSlotSeconds.toFixed(3)})`,
      );
      if (ss.anchorUnavailable) parts.push(`  anchorUnavailable: ${ss.anchorUnavailable.join(" | ")}`);
      for (const t of [0, 1, 2, 5, 10, 30, 60]) {
        const states = [...tl.sampleAt(t).values()];
        const o = overlaps(states, L);
        const p = unwrapped(states, L);
        const spd = states.map((s) => s.speedKph);
        const grid = states.filter((s) => s.status === "grid").length;
        parts.push(
          `  t=${String(t).padStart(2)}: overlapPairs=${o.all}/${(states.length * (states.length - 1)) / 2}`
          + ` consec=${o.consec} spread=${(p[p.length - 1] - p[0]).toFixed(2)} m minGap=${o.minGap.toFixed(2)} m`
          + ` speed=[${Math.min(...spd).toFixed(1)},${Math.max(...spd).toFixed(1)}] kph onGrid=${grid}`,
        );
      }
      // worst overlap anywhere inside the launch window, sampled at 20 Hz
      const clampEnd = Math.max(
        ...ss.placements.map((p) => p.launch.reactionS + p.launch.handoverSpeedMps / p.launch.accelMps2),
      );
      let worstLaunchOverlap = 0, worstLaunchAt = 0;
      for (let k = 0; k * 0.05 < clampEnd; k++) {
        const t = k / 20;
        const o = overlaps([...tl.sampleAt(t).values()], L);
        if (o.all > worstLaunchOverlap) { worstLaunchOverlap = o.all; worstLaunchAt = t; }
      }
      parts.push(`  launch window 0-${clampEnd.toFixed(2)} s: worst overlapPairs=${worstLaunchOverlap} (t=${worstLaunchAt.toFixed(2)})`);
      // largest single-frame forward jump beyond free motion, 60 Hz over 0-12 s
      let worstJump = 0, worstAt = 0, worstDriver = "";
      const prev = new Map<string, number>();
      for (let k = 0; k <= 12 * 60; k++) {
        const t = k / 60;
        for (const [drv, s] of tl.sampleAt(t)) {
          const before = prev.get(drv);
          if (before !== undefined) {
            let step = s.stationM - before;
            if (step < -L / 2) step += L;
            const jump = step - (s.speedKph / 3.6) / 60;
            if (jump > worstJump) { worstJump = jump; worstAt = t; worstDriver = drv; }
          }
          prev.set(drv, s.stationM);
        }
      }
      parts.push(`  worst single-frame jump beyond free motion, 0-12 s: ${worstJump.toFixed(3)} m (${worstDriver} at t=${worstAt.toFixed(2)})`);
      const lap1 = result.entries.map((e) => e.laps[0].sesT - e.laps[0].lST);
      const lap2 = result.entries.map((e) => e.laps[1].sesT - e.laps[1].lST);
      const excess = lap1.map((v, i) => v - lap2[i]).sort((a, b) => a - b);
      parts.push(`  lap1 - lap2 s: min=${excess[0].toFixed(2)} median=${excess[excess.length >> 1].toFixed(2)} max=${excess[excess.length - 1].toFixed(2)}`);
      rows.push(`${slug} (n=${entries.length}, L=${L.toFixed(0)})\n${parts.join("\n")}`);
    }
    writeFileSync(String(process.env.AUDIT_OUT), rows.join("\n"));
  });
});
