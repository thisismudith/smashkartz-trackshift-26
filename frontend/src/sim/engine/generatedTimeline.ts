/**
 * Wraps a GeneratedRaceResult (raceEngine.ts) in the same RaceTimeline contract the
 * replay adapter implements, so the renderer and every dashboard panel are unaware
 * which source produced the race. Positions come from the motion warp
 * (profileWarp.ts) rather than decoded telemetry, but the crossing-tower ordering and
 * station-based gap logic are the same idea as timeline.ts, simplified because a
 * generated race has none of the real data's missing-lap/mismatched-frame problems.
 */
import type {
  CarState, NeutralisationInterval, RaceEvent, RaceTimeline, TrackModel, WeatherSeries,
} from "../contract/types";
import { trackPointAt } from "../data/manifest";
import { buildLapWarp, buildRefTimeTable, type LapWarp, type RefTimeTable } from "../motion/profileWarp";
import type { GeneratedRaceResult } from "./raceEngine";

export class GeneratedTimeline implements RaceTimeline {
  readonly provenance = "SIMULATED" as const;
  readonly runId: string;
  readonly track: TrackModel;
  readonly driverList: string[];
  readonly totalLaps: number;
  readonly duration: number;

  private refTable: RefTimeTable;
  private warpCache = new Map<string, LapWarp>();
  private result: GeneratedRaceResult;

  constructor(result: GeneratedRaceResult, track: TrackModel) {
    this.result = result;
    this.track = track;
    this.runId = result.runId;
    this.driverList = result.entries.map((e) => e.driver);
    this.totalLaps = result.totalLaps;
    this.duration = result.duration;
    this.refTable = buildRefTimeTable(track);
  }

  private warpFor(driver: string, lap: number): LapWarp | null {
    const key = `${driver}:${lap}`;
    const cached = this.warpCache.get(key);
    if (cached) return cached;
    const entry = this.result.entries.find((e) => e.driver === driver);
    const lapRow = entry?.laps.find((l) => l.lap === lap);
    if (!lapRow) return null;
    const warp = buildLapWarp(this.track, this.refTable, { s1: lapRow.s1, s2: lapRow.s2, s3: lapRow.s3 });
    this.warpCache.set(key, warp);
    return warp;
  }

  sampleAt(t: number): Map<string, CarState> {
    const out = new Map<string, CarState>();
    const progress = this.result.entries.map((entry) => {
      let idx = -1;
      for (let i = 0; i < entry.laps.length; i++) {
        if (entry.laps[i].lST <= t) idx = i; else break;
      }
      return { entry, idx };
    });

    const ranked = progress.slice().sort(
      (a, b) => GeneratedTimeline.progressOf(b, t) - GeneratedTimeline.progressOf(a, t),
    );

    ranked.forEach(({ entry, idx }, i) => {
      if (idx < 0) {
        out.set(entry.driver, this.gridState(entry.driver, entry.team, i + 1));
        return;
      }
      const lapRow = entry.laps[idx];
      const finished = t > lapRow.sesT && idx === entry.laps.length - 1;
      const relT = Math.min(lapRow.sesT - lapRow.lST, Math.max(0, t - lapRow.lST));
      const warp = this.warpFor(entry.driver, lapRow.lap);
      const sample = warp ? warp.sampleAt(relT) : { stationM: 0, speedKph: 0, gear: 1 };
      const pt = trackPointAt(this.track, sample.stationM);

      out.set(entry.driver, {
        driver: entry.driver, team: entry.team,
        stationM: sample.stationM, lateralM: 0, elevationM: pt.z, headingRad: pt.heading,
        speedKph: sample.speedKph, gear: sample.gear, throttlePct: sample.speedKph > 5 ? 100 : 30,
        brake: false,
        tyreCompound: lapRow.compound, tyreLife: lapRow.life,
        lapsDone: idx + (t > lapRow.sesT ? 1 : 0),
        lapProgress: Math.max(0, Math.min(0.9999, relT / Math.max(1e-6, lapRow.sesT - lapRow.lST))),
        position: i + 1,
        gapToLeaderS: i === 0 ? null : this.gapToLeader(ranked, i, t),
        lapsDownFromLeader: this.lapsDownFromLeader(ranked, i, t),
        intervalS: i === 0 ? null : this.intervalToAhead(ranked, i),
        inPit: lapRow.pin !== null && t >= lapRow.pin,
        status: finished ? "finished" : (lapRow.pin !== null && t >= lapRow.pin ? "pit" : "track"),
        provenance: "SIMULATED",
        energy: null,
      });
    });
    return out;
  }

  private gridState(driver: string, team: string | null, position: number): CarState {
    return {
      driver, team, stationM: 0, lateralM: 0, elevationM: 0, headingRad: 0,
      speedKph: 0, gear: 0, throttlePct: 0, brake: false, tyreCompound: null, tyreLife: null,
      lapsDone: 0, lapProgress: 0, position, gapToLeaderS: null, lapsDownFromLeader: 0,
      intervalS: null, inPit: false, status: "grid", provenance: "SIMULATED", energy: null,
    };
  }

  /** Laps + fraction of the current lap: the car's true position in the race. */
  private static progressOf(
    p: { entry: GeneratedRaceResult["entries"][number]; idx: number }, t: number,
  ): number {
    if (p.idx < 0) return -1e9;
    const row = p.entry.laps[p.idx];
    const span = row.sesT - row.lST;
    const frac = t > row.sesT ? 1 : (span > 0 ? (t - row.lST) / span : 0);
    return row.lap + frac;
  }

  /** A car is only lapped once it is a FULL lap of progress behind. Comparing bare
   * lap NUMBERS reported every car that had not yet crossed the line as "+1 LAP"
   * the moment the leader crossed it, even when it was a second behind. */
  private lapsDownFromLeader(
    ranked: { entry: GeneratedRaceResult["entries"][number]; idx: number }[], i: number, t: number,
  ): number {
    const lead = GeneratedTimeline.progressOf(ranked[0], t);
    const mine = GeneratedTimeline.progressOf(ranked[i], t);
    if (mine < -1e8) return 0;
    return Math.max(0, Math.floor(lead - mine));
  }

  /** Gap to the leader, in seconds, for a car on the SAME lap number as the leader.
   * Approximated as the difference between when each car started this shared lap --
   * a reasonable stand-in for the replay adapter's exact station-crossing search
   * (plan section 4), since both cars are, by construction, on self-consistent
   * generated laps rather than real telemetry with its per-car distance-frame noise. */
  private gapToLeader(
    ranked: { entry: GeneratedRaceResult["entries"][number]; idx: number }[], i: number, t: number,
  ): number | null {
    if (this.lapsDownFromLeader(ranked, i, t) > 0) return null;
    const leader = ranked[0];
    const leaderLap = leader.idx < 0 ? null : leader.entry.laps[leader.idx];
    const mine = ranked[i].entry.laps[ranked[i].idx];
    if (!leaderLap || mine.lap !== leaderLap.lap) return null;
    return Math.max(0, mine.lST - leaderLap.lST);
  }

  private intervalToAhead(
    ranked: { entry: GeneratedRaceResult["entries"][number]; idx: number }[], i: number,
  ): number | null {
    const ahead = ranked[i - 1];
    const mine = ranked[i].entry.laps[ranked[i].idx];
    const aheadLap = ahead.idx < 0 ? null : ahead.entry.laps[ahead.idx];
    if (!aheadLap || mine.lap !== aheadLap.lap) return null;
    return Math.max(0, mine.lST - aheadLap.lST);
  }

  events(): RaceEvent[] {
    return this.result.neutralisations.map((n) => {
      const startLap = this.result.entries[0]?.laps.find((l) => l.lap === n.startLap);
      return {
        sessionTime: startLap ? startLap.lST : 0,
        kind: n.kind === "SC" ? "safetyCar" : "vsc",
        message: `${n.kind} DEPLOYED (simulated)`,
        drivers: [],
        provenance: "SIMULATED" as const,
      };
    });
  }

  neutralisations(): NeutralisationInterval[] {
    return this.result.neutralisations.map((n) => {
      const start = this.result.entries[0]?.laps.find((l) => l.lap === n.startLap)?.lST ?? 0;
      const end = this.result.entries[0]?.laps.find((l) => l.lap === n.endLap)?.sesT ?? start;
      return { kind: n.kind, start, end };
    });
  }

  weather(): WeatherSeries | null {
    return null; // New Race's configured weather is a fixed value, not a series; the
    // configurator surfaces it directly rather than through this time-series shape.
  }
}
