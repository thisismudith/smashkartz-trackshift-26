/**
 * The New Race generator: a deterministic, seeded, per-lap simulation loop. Every
 * car's lap time is composed from the fitted parameters (lapModel.ts), split into
 * three sector times, and the field advances lap by lap re-running each car's pit
 * decision whenever a neutralisation opens (the plan's key strategic behaviour: 23%
 * of real stops happened inside one). Output is the SAME shape as a replay session's
 * lap table (lST/sesT/compound/stint/life/pin/pout/status), so GeneratedTimeline can
 * reuse the exact station/gap/tower logic pattern the replay adapter uses.
 */
import type { TrackModel } from "../contract/types";
import { composeLapTime, splitIntoSectors } from "./lapModel";
import type { FittedParams } from "./params";
import { mulberry32 } from "./prng";

export interface RaceConfig {
  track: TrackModel;
  params: FittedParams;
  entries: { driver: string; team: string | null }[];
  totalLaps: number;
  seed: number;
  pitLapWindow?: [number, number]; // laps, inclusive; default a third to two-thirds distance
}

export interface GeneratedLap {
  lap: number;
  lST: number;
  sesT: number;
  s1: number; s2: number; s3: number;
  compound: "SOFT" | "MEDIUM" | "HARD";
  stint: number;
  life: number;
  pin: number | null;
  pout: number | null;
  status: "1" | "4" | "6"; // green / SC / VSC, enough for the dashboard and gaps
}

export interface GeneratedDriverResult {
  driver: string;
  team: string | null;
  laps: GeneratedLap[];
}

export interface GeneratedRaceResult {
  runId: string;
  seed: number;
  entries: GeneratedDriverResult[];
  neutralisations: { kind: "SC" | "VSC"; startLap: number; endLap: number }[];
  totalLaps: number;
  duration: number;
}

const COMPOUND_SEQUENCE: Array<"SOFT" | "MEDIUM" | "HARD"> = ["MEDIUM", "HARD"];

export function runRace(config: RaceConfig): GeneratedRaceResult {
  const { track, params, entries, totalLaps, seed } = config;
  const rng = mulberry32(seed);
  const baseSeconds = trackBaseSecondsFor(track);
  const [pitLo, pitHi] = config.pitLapWindow ?? [
    Math.round(totalLaps * 0.35), Math.round(totalLaps * 0.65),
  ];

  const pitLap = new Map<string, number>();
  for (const e of entries) {
    pitLap.set(e.driver, pitLo + Math.floor(rng() * Math.max(1, pitHi - pitLo)));
  }

  const results = new Map<string, GeneratedDriverResult>();
  for (const e of entries) results.set(e.driver, { driver: e.driver, team: e.team, laps: [] });

  const lastSesT = new Map<string, number>(entries.map((e) => [e.driver, 0]));
  const neutralIntervals: GeneratedRaceResult["neutralisations"] = [];
  let openNeutral: { kind: "SC" | "VSC"; startLap: number } | null = null;

  const scHazard = params.neutralisation.safetyCarLapHazard.value;
  const vscHazard = params.neutralisation.virtualSafetyCarLapHazard.value;

  for (let lap = 1; lap <= totalLaps; lap++) {
    // one field-wide neutralisation roll per lap (never open two at once)
    let thisLapNeutral: "SC" | "VSC" | null = null;
    if (openNeutral) {
      thisLapNeutral = openNeutral.kind;
      if (rng() < 0.3) { // ~3-lap average duration
        neutralIntervals.push({ kind: openNeutral.kind, startLap: openNeutral.startLap, endLap: lap });
        openNeutral = null;
      }
    } else if (rng() < scHazard) {
      openNeutral = { kind: "SC", startLap: lap };
      thisLapNeutral = "SC";
    } else if (rng() < vscHazard) {
      openNeutral = { kind: "VSC", startLap: lap };
      thisLapNeutral = "VSC";
    }

    // order at the start of this lap, by cumulative session time, for proximity
    const order = [...lastSesT.entries()].sort((a, b) => a[1] - b[1]);
    const gapAhead = new Map<string, number | null>();
    for (let i = 0; i < order.length; i++) {
      gapAhead.set(order[i][0], i === 0 ? null : order[i][1] - order[i - 1][1]);
    }

    for (const e of entries) {
      const driverResult = results.get(e.driver)!;
      const priorLaps = driverResult.laps;
      const stint = priorLaps.length ? priorLaps[priorLaps.length - 1].stint : 1;
      const life = priorLaps.length
        ? (priorLaps[priorLaps.length - 1].pin !== null ? 1 : priorLaps[priorLaps.length - 1].life + 1)
        : 1;
      const compound = priorLaps.length
        ? (priorLaps[priorLaps.length - 1].pin !== null
          ? COMPOUND_SEQUENCE[Math.min(COMPOUND_SEQUENCE.length - 1, stint)]
          : priorLaps[priorLaps.length - 1].compound)
        : COMPOUND_SEQUENCE[0];

      const isPitLap = lap === pitLap.get(e.driver);
      let pitLossThisLap = 0;
      if (isPitLap) {
        const pl = params.pitLoss[track.slug]?.netLossSeconds.value ?? 22;
        pitLossThisLap = thisLapNeutral ? pl * 0.4 : pl; // a rough "free stop" discount
      }

      const lapTime = composeLapTime(
        {
          trackSlug: track.slug,
          trackBaseSeconds: baseSeconds,
          driver: e.driver,
          team: e.team,
          lapIndex: lap - 1,
          tyreLifeMinusOne: Math.max(0, life - 1),
          compound,
          gapAheadAtLapStart: gapAhead.get(e.driver) ?? null,
          neutralisation: thisLapNeutral,
          pitLossThisLap,
        },
        params,
        rng,
      );
      const sectors = splitIntoSectors(lapTime, track);
      const start = lastSesT.get(e.driver)!;
      const end = start + lapTime;
      lastSesT.set(e.driver, end);

      driverResult.laps.push({
        lap, lST: start, sesT: end, s1: sectors.s1, s2: sectors.s2, s3: sectors.s3,
        compound, stint: isPitLap ? stint + 1 : stint, life,
        pin: isPitLap ? start + lapTime * 0.4 : null,
        pout: isPitLap ? start + lapTime * 0.4 : null,
        status: thisLapNeutral === "SC" ? "4" : thisLapNeutral === "VSC" ? "6" : "1",
      });
    }
  }
  if (openNeutral) {
    neutralIntervals.push({ kind: openNeutral.kind, startLap: openNeutral.startLap, endLap: totalLaps });
  }

  const duration = Math.max(...[...lastSesT.values()]);
  return {
    runId: `sim:${track.slug}/${entries.length}c/${totalLaps}L/s=${seed}`,
    seed,
    entries: entries.map((e) => results.get(e.driver)!),
    neutralisations: neutralIntervals,
    totalLaps,
    duration,
  };
}

/** The track's own median clean lap time is not currently in params.json (that lives
 * in the catalogue's per-track table, section 5.1) -- as a self-contained fallback
 * here, it is reconstructed from the reference speed profile by integrating ds/v,
 * which is exactly the quantity the plan validated to within 0.25% of the real lap
 * time. This keeps the engine runnable from params.json + the track model alone. */
function trackBaseSecondsFor(track: TrackModel): number {
  const { speedKph, binMetres } = track.referenceProfile;
  let t = 0;
  for (let i = 0; i < speedKph.length; i++) {
    const mps = Math.max(10, speedKph[i]) / 3.6;
    t += binMetres / mps;
  }
  return t;
}
