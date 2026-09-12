/**
 * Wraps a GeneratedRaceResult (raceEngine.ts) in the same RaceTimeline contract the
 * replay adapter implements, so the renderer and every dashboard panel are unaware
 * which source produced the race. Positions come from the motion warp
 * (profileWarp.ts) rather than decoded telemetry, but the crossing-tower ordering and
 * station-based gap logic are the same idea as timeline.ts, simplified because a
 * generated race has none of the real data's missing-lap/mismatched-frame problems.
 *
 * Lap 1 is a STANDING START. Until the field-wide handover it is placed by
 * scripts/simdata/launch.py's model -- grid box, reaction, constant-acceleration launch
 * -- with a hard non-interpenetration gap of one car length applied in grid order,
 * exactly as launch_state's `min_gap_m` does. After it, each car joins the reference
 * profile at the position the launch left it in. Every number behind that comes from
 * params.json standingStart; this file only applies it.
 */
import type {
  CarState, NeutralisationInterval, Provenance, RaceEvent, RaceTimeline, TrackModel,
  WeatherSeries,
} from "../contract/types";
import { trackPointAt } from "../data/manifest";
import {
  buildLapWarp, buildRefTimeTable, buildStandingStartLap, launchStateAt,
  type LaunchGridCar, type LaunchPhase, type MotionSample, type RefTimeTable,
} from "../motion/profileWarp";
import type { GeneratedRaceResult, GridPlacement } from "./raceEngine";

/** One car's resolved state for a single sampleAt, before it becomes a CarState. */
interface Progress {
  entry: GeneratedRaceResult["entries"][number];
  idx: number;
  motion: MotionSample;
  finished: boolean;
}

export class GeneratedTimeline implements RaceTimeline {
  readonly provenance = "SIMULATED" as const;
  readonly runId: string;
  readonly track: TrackModel;
  readonly driverList: string[];
  readonly totalLaps: number;
  readonly duration: number;

  private refTable: RefTimeTable;
  private warpCache = new Map<string, (t: number) => MotionSample>();
  private result: GeneratedRaceResult;
  /** The grid, in grid order, as launchStateAt consumes it. */
  private launchGrid: LaunchGridCar[] = [];
  /**
   * ONE instant at which the WHOLE field leaves the fitted launch for the pace model:
   * the last car's own handover, reaction + v / a. The caller has to choose this, and
   * choosing it per car is what does not work -- a car that reached 120 kph half a second
   * early would join the flying-lap profile at ~260 kph while the car 8 m in front was
   * still at 120, and measured, that drove 9 to 17 pairs straight through each other
   * inside the launch. Holding early finishers at 120 kph until the field is ready is not
   * an invention either: it is launch_profile's own third phase, which runs at constant
   * handoverSpeed for as long as the caller keeps asking.
   */
  private handoverTimeS = 0;
  /** Each car's progress at handoverTimeS, AFTER the non-interpenetration constraint.
   * Computed once: it is what every lap-1 tail is entered at, so the launch and the pace
   * model join with no jump even for a car that was held. */
  private handoverProgressM = new Map<string, number>();

  constructor(result: GeneratedRaceResult, track: TrackModel) {
    this.result = result;
    this.track = track;
    this.runId = result.runId;
    this.driverList = result.entries.map((e) => e.driver);
    this.totalLaps = result.totalLaps;
    this.duration = result.duration;
    this.refTable = buildRefTimeTable(track);
    const start = result.standingStart;
    if (start) {
      this.launchGrid = start.placements.map((p) => ({
        driver: p.driver, offsetM: p.offsetM, launch: p.launch,
      }));
      for (const p of start.placements) {
        this.handoverTimeS = Math.max(
          this.handoverTimeS,
          p.launch.reactionS + p.launch.handoverSpeedMps / p.launch.accelMps2,
        );
      }
      for (const car of this.launchField(this.handoverTimeS)) {
        this.handoverProgressM.set(car.driver, car.progressM);
      }
    }
  }

  /** The constrained field at `t`, or an empty list when this race has no grid. */
  private launchField(t: number) {
    const start = this.result.standingStart;
    if (!start) return [];
    return launchStateAt(this.launchGrid, t, {
      ringLengthM: this.track.lengthMetres,
      minGapM: start.minGapMetres,
    });
  }

  /** The standing start this race was laid out from, or null when params.json carried no
   * fitted block. Exposed so a panel can show the anchor, the pitch, their provenance and
   * the artifact's own words when a circuit has none, instead of re-deriving any of it. */
  standingStart(): GeneratedRaceResult["standingStart"] {
    return this.result.standingStart;
  }

  /** Lap 1 is the launch + reference profile; every later lap is the three-sector warp.
   * Both are returned as the same MotionSample so the sampler has one code path. */
  private motionFor(driver: string, lap: number): ((t: number) => MotionSample) | null {
    const key = `${driver}:${lap}`;
    const cached = this.warpCache.get(key);
    if (cached) return cached;
    const entry = this.result.entries.find((e) => e.driver === driver);
    const lapRow = entry?.laps.find((l) => l.lap === lap);
    if (!lapRow) return null;
    const L = this.track.lengthMetres;
    const start = this.result.standingStart;
    const placement = lap === 1 ? start?.byDriver.get(driver) ?? null : null;

    let sampler: (t: number) => MotionSample;
    if (placement && start) {
      const ssLap = buildStandingStartLap(this.track, this.refTable, {
        handoverTimeS: this.handoverTimeS,
        handoverProgressM: this.handoverProgressM.get(driver) ?? placement.offsetM,
        lapTime: lapRow.sesT - lapRow.lST,
      });
      sampler = (t) => ssLap.sampleAt(t);
    } else {
      const warp = buildLapWarp(this.track, this.refTable, {
        s1: lapRow.s1, s2: lapRow.s2, s3: lapRow.s3,
      });
      const base = (lap - 1) * L;
      sampler = (t) => {
        const s = warp.sampleAt(t);
        return {
          progressM: base + s.stationM,
          stationM: s.stationM,
          speedKph: s.speedKph,
          gear: s.gear,
          phase: "HANDOVER" as LaunchPhase,
        };
      };
    }
    this.warpCache.set(key, sampler);
    return sampler;
  }

  sampleAt(t: number): Map<string, CarState> {
    const L = this.track.lengthMetres;
    // Inside the launch window the field is ONE computation, not a car at a time: the
    // non-interpenetration constraint couples every car to the one ahead of it.
    const launching = this.result.standingStart !== null && t < this.handoverTimeS;
    const field = launching
      ? new Map(this.launchField(t).map((c) => [c.driver, c as MotionSample]))
      : null;

    const progress: Progress[] = this.result.entries.map((entry) => {
      let idx = -1;
      for (let i = 0; i < entry.laps.length; i++) {
        if (entry.laps[i].lST <= t) idx = i; else break;
      }
      const placement = this.result.standingStart?.byDriver.get(entry.driver) ?? null;
      const launched = idx <= 0 ? field?.get(entry.driver) : undefined;
      if (launched) {
        return { entry, idx: Math.max(0, idx), motion: launched, finished: false };
      }
      if (idx < 0) {
        // Unreachable while the worker keeps sessionTime >= 0 and lap 1 starts at the
        // signal, but a car with no lap yet is on its box, not at station 0 doing 250.
        return { entry, idx, finished: false, motion: gridMotion(placement) };
      }
      const lapRow = entry.laps[idx];
      const relT = Math.min(lapRow.sesT - lapRow.lST, Math.max(0, t - lapRow.lST));
      const sampler = this.motionFor(entry.driver, lapRow.lap);
      const motion = sampler
        ? sampler(relT)
        : { progressM: idx * L, stationM: 0, speedKph: 0, gear: 1, phase: "HANDOVER" as LaunchPhase };
      return {
        entry, idx, motion,
        finished: t > lapRow.sesT && idx === entry.laps.length - 1,
      };
    });

    const ranked = progress.slice().sort((a, b) => b.motion.progressM - a.motion.progressM);
    const out = new Map<string, CarState>();
    ranked.forEach((p, i) => {
      const { entry, idx, motion } = p;
      const lapRow = idx >= 0 ? entry.laps[idx] : null;
      const pt = trackPointAt(this.track, motion.stationM);
      const onGrid = motion.phase === "GRID";
      const inPit = lapRow !== null && lapRow.pin !== null && t >= lapRow.pin;
      const lapSpan = lapRow ? Math.max(1e-6, lapRow.sesT - lapRow.lST) : 1;
      const relT = lapRow ? Math.min(lapSpan, Math.max(0, t - lapRow.lST)) : 0;

      out.set(entry.driver, {
        driver: entry.driver, team: entry.team,
        stationM: motion.stationM,
        // The two-column stagger's metric offset is NOT in the feed (it ships as null,
        // tagged RULE), so no lateral is invented here. GridPlacement.lateralColumnSign
        // carries the regulation STRUCTURE for a renderer that wants to draw it from the
        // track's own measured half-width and say that is where it came from.
        lateralM: 0,
        elevationM: pt.z, headingRad: pt.heading,
        speedKph: motion.speedKph, gear: motion.gear,
        throttlePct: onGrid ? 0 : (motion.speedKph > 5 ? 100 : 30),
        brake: false,
        tyreCompound: lapRow?.compound ?? null, tyreLife: lapRow?.life ?? null,
        lapsDone: lapRow ? idx + (t > lapRow.sesT ? 1 : 0) : 0,
        lapProgress: lapRow ? Math.max(0, Math.min(0.9999, relT / lapSpan)) : 0,
        position: i + 1,
        gapToLeaderS: i === 0 ? null : this.gapToLeader(ranked, i),
        lapsDownFromLeader: this.lapsDownFromLeader(ranked, i),
        intervalS: i === 0 ? null : this.intervalToAhead(ranked, i),
        inPit,
        status: onGrid ? "grid"
          : p.finished ? "finished"
          : inPit ? "pit" : "track",
        provenance: "SIMULATED",
        // Every position in a generated race is SIMULATED, including the grid boxes: the
        // grid's SPACING and order are fitted, and where a circuit has no measured anchor
        // the whole grid's position along the lap is a documented DEFAULT that
        // standingStart().placementProvenance reports. That distinction belongs on the
        // grid block, not here -- downgrading a car's positionProvenance would make the
        // dashboard suppress car-to-car gaps that the fitted pitch fully supports.
        positionProvenance: "SIMULATED" as Provenance,
        energy: null,
      });
    });
    return out;
  }

  /** Laps of progress a car is down on the leader, from METRES rather than lap numbers:
   * a car is only lapped once it is a FULL lap behind. Comparing bare lap NUMBERS
   * reported every car that had not yet crossed the line as "+1 LAP" the moment the
   * leader crossed it, even when it was a second behind. */
  private lapsDownFromLeader(ranked: Progress[], i: number): number {
    const lead = ranked[0].motion.progressM;
    const mine = ranked[i].motion.progressM;
    return Math.max(0, Math.floor((lead - mine) / this.track.lengthMetres));
  }

  /** Gap to the leader, in seconds, for a car on the SAME lap number as the leader.
   * Approximated as the difference between when each car started this shared lap --
   * a reasonable stand-in for the replay adapter's exact station-crossing search
   * (plan section 4), since both cars are, by construction, on self-consistent
   * generated laps rather than real telemetry with its per-car distance-frame noise.
   *
   * On lap 1 the cars share a lap START (the signal) but not a starting POINT, so that
   * stand-in reports 0.0 for the whole field. The lap-1 gap is taken from the metres
   * between the cars instead, converted at the car's own current speed. */
  private gapToLeader(ranked: Progress[], i: number): number | null {
    if (this.lapsDownFromLeader(ranked, i) > 0) return null;
    const leader = ranked[0];
    const mine = ranked[i];
    if (leader.idx < 0 || mine.idx < 0) return null;
    const leaderLap = leader.entry.laps[leader.idx];
    const myLap = mine.entry.laps[mine.idx];
    if (myLap.lap !== leaderLap.lap) return null;
    if (myLap.lap === 1) return this.metresToSeconds(leader.motion, mine.motion);
    return Math.max(0, myLap.lST - leaderLap.lST);
  }

  private intervalToAhead(ranked: Progress[], i: number): number | null {
    const ahead = ranked[i - 1];
    const mine = ranked[i];
    if (ahead.idx < 0 || mine.idx < 0) return null;
    const aheadLap = ahead.entry.laps[ahead.idx];
    const myLap = mine.entry.laps[mine.idx];
    if (myLap.lap !== aheadLap.lap) return null;
    if (myLap.lap === 1) return this.metresToSeconds(ahead.motion, mine.motion);
    return Math.max(0, myLap.lST - aheadLap.lST);
  }

  /** Metres of deficit, expressed as the seconds the trailing car needs to cover them at
   * its own current speed. Null while it is stationary: a stopped car's deficit is not a
   * time, and a made-up divisor would turn one into a number that looks like timing. */
  private metresToSeconds(ahead: MotionSample, mine: MotionSample): number | null {
    const metres = Math.max(0, ahead.progressM - mine.progressM);
    const mps = mine.speedKph / 3.6;
    if (!(mps > 1)) return null;
    return metres / mps;
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

/** A car with no lap row yet, sitting in its box. Station 0 only if there is no grid. */
function gridMotion(placement: GridPlacement | null): MotionSample {
  return {
    progressM: placement ? placement.offsetM : 0,
    stationM: placement ? placement.stationM : 0,
    speedKph: 0,
    gear: 0,
    phase: "GRID",
  };
}
