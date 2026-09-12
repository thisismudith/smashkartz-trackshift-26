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
import { halfWidthAt, trackPointAt } from "../data/manifest";
import {
  buildLapWarp, buildRefTimeTable, buildStandingStartLap, launchStateAt, referenceAccelMps2,
  wrapStation,
  type LaunchGridCar, type LaunchPhase, type MotionSample, type RefTimeTable,
} from "../motion/profileWarp";
// The car's true length and width, 5.6 x 2.0 m, from the ONE place they are defined.
// They are read here as SPACE, not as pixels: two solid bodies cannot share a patch of
// road, which is geometry rather than a tuned parameter.
import { CAR_RENDER_LENGTH_M, CAR_RENDER_WIDTH_M } from "../render/presentation";
import type { GeneratedRaceResult, GridPlacement } from "./raceEngine";

/** One car's resolved state for a single sampleAt, before it becomes a CarState. */
interface Progress {
  entry: GeneratedRaceResult["entries"][number];
  idx: number;
  motion: MotionSample;
  finished: boolean;
  /** Signed metres off the racing line, from the contact pass below. Exactly 0 for a car
   * that is not within a car length of any other -- which is nearly always. */
  lateralM: number;
  /** True when the road was full abreast and this car had to be held a car length back.
   * Distinct from "it is simply behind": a held car's published position is no longer
   * the one its own lap time produced. */
  held: boolean;
}

/** Floating-point slack on the one-car-length test, metres. The launch leaves queued
 * cars at EXACTLY the gap, so a strict comparison would read them as touching. */
const CONTACT_EPS_M = 1e-6;

/**
 * How many cars this circuit's road can hold abreast at `stationM`.
 *
 * `halfWidthAt` is the same width the renderer's own declutter pass reasons with, and the
 * artifact is explicit about what it is: the SHAPE is DERIVED from per-station lateral
 * extremes and corner markers, the SCALE is a stated RULE constant of 6.0-7.5 m, because
 * the position feed does not measure track width. So this count is a RULE-scaled
 * geometric capacity, not a measurement of the circuit -- which is exactly how the replay
 * adapter already treats the same number for the two-column grid stagger. It is used only
 * to decide how many cars may be SIDE BY SIDE; it never moves a car that is on its own.
 */
function lanesAcross(track: TrackModel, stationM: number): number {
  const half = halfWidthAt(track, stationM);
  if (!Number.isFinite(half) || !(half > 0)) return 1;
  return Math.max(1, Math.floor((2 * half) / CAR_RENDER_WIDTH_M));
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
  /** Each car's SPEED at handoverTimeS, m/s, also after the constraint: a car that spent
   * the launch held behind a slower one leaves it below the 120 kph ceiling, and the
   * blend has to start from the speed the car actually had. */
  private handoverSpeedMps = new Map<string, number>();
  /** This circuit's own measured acceleration above the launch's validity ceiling, which
   * is what sets the length of the handover blend. Null only when there is no standing
   * start to blend out of. */
  private blendAccelMps2: number | null = null;

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
        this.handoverSpeedMps.set(car.driver, car.speedKph / 3.6);
      }
      // Refused rather than defaulted: without a measured acceleration above the launch's
      // validity ceiling there is no basis for the length of the handover blend, and a
      // picked one would be exactly the invented number AGENTS.md 42.5 forbids. No
      // shipped circuit hits this -- the 13 measure 6.1-6.5 m/s^2 -- so it names the
      // circuit and the quantity rather than degrading silently.
      const accel = referenceAccelMps2(track, start.validToSpeedKph);
      if (accel === null) {
        throw new Error(
          `${track.slug}: its reference speed profile never accelerates at or above the `
          + `${start.validToSpeedKph} kph launch validity ceiling, so the handover out of `
          + "the launch has no measured acceleration to blend along",
        );
      }
      this.blendAccelMps2 = accel;
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
    if (placement && start && this.blendAccelMps2 !== null) {
      const ssLap = buildStandingStartLap(this.track, this.refTable, {
        handoverTimeS: this.handoverTimeS,
        handoverProgressM: this.handoverProgressM.get(driver) ?? placement.offsetM,
        // A car with no entry here never launched, so its speed at the handover is zero;
        // that is the truth, not a missing value to be filled with the ceiling.
        handoverSpeedMps: this.handoverSpeedMps.get(driver) ?? 0,
        lapTime: lapRow.sesT - lapRow.lST,
        blendAccelMps2: this.blendAccelMps2,
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
        return {
          entry, idx: Math.max(0, idx), motion: launched, finished: false,
          lateralM: 0, held: false,
        };
      }
      if (idx < 0) {
        // Unreachable while the worker keeps sessionTime >= 0 and lap 1 starts at the
        // signal, but a car with no lap yet is on its box, not at station 0 doing 250.
        return {
          entry, idx, finished: false, motion: gridMotion(placement),
          lateralM: 0, held: false,
        };
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
        lateralM: 0, held: false,
      };
    });

    const ranked = progress.slice().sort((a, b) => b.motion.progressM - a.motion.progressM);
    this.resolveContact(ranked);
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
        // Exactly 0 unless resolveContact had to put this car BESIDE another one, which
        // it does only for cars that are within a car length along the road. In
        // particular the whole grid publishes 0: the two-column stagger's metric offset
        // is not in the feed (it ships as null, tagged RULE) and none is invented here.
        // GridPlacement.lateralColumnSign carries the regulation STRUCTURE for a renderer
        // that wants to draw it from the track's own half-width and say so.
        lateralM: p.lateralM,
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

  /**
   * FIELD-WIDE NON-INTERPENETRATION, applied at EVERY instant of the race rather than
   * only inside the launch window. `ranked` arrives sorted by progress, leader first, and
   * is rewritten in place.
   *
   * WHAT IS FORBIDDEN, precisely: two cars occupying the same metres of road ON THE SAME
   * LINE. That is the physical statement -- two solid 5.6 x 2.0 m bodies cannot share a
   * patch of tarmac -- and it is deliberately NOT "stations may not cross". Stations
   * crossing IS an overtake, and forbidding it would freeze the running order: the
   * generated race decides who passes whom from the fitted lap times, and nothing here
   * may overrule that. Measured over ten laps on the 13 shipped circuits, this pass
   * changes the pass count by zero.
   *
   * WHAT HAPPENS INSTEAD is the thing that actually happens on a circuit: cars that
   * occupy the same metres of road are SIDE BY SIDE. Cars are assigned lanes greedily in
   * running order -- the leader takes the inside line, and each following car takes the
   * innermost line whose last occupant is at least a car length ahead of it. Two cars in
   * the same lane are therefore a car length apart along the road, and two cars in
   * different lanes are a car width apart across it. Neither pair intersects. Because the
   * assignment is by running order and never reorders anybody, it cannot create or
   * destroy an overtake; it only says which of two cars that are alongside is on the
   * inside.
   *
   * A car that is on its own -- no other car within a car length -- is left on lane 0 and
   * publishes a lateral of exactly 0, so this pass is invisible everywhere except in the
   * packs it exists for, and the stationary grid is untouched.
   *
   * The ONE case that does move a car along the road is a pack with more cars in a car
   * length than the road can hold abreast (see lanesAcross). Then the excess car is held
   * exactly one car length back and tagged QUEUED -- the same rule, and the same tag,
   * that launchStateAt applies during the launch, rather than a car-following model this
   * project has not fitted. Measured over ten laps on all 13 shipped circuits, the
   * deepest pack is 4 cars against 5-6 lanes of road, so this branch does not fire; it is
   * here because "it does not fire today" is not a guarantee.
   */
  private resolveContact(ranked: Progress[]): void {
    const L = this.track.lengthMetres;
    const minGapM = this.result.standingStart?.minGapMetres ?? CAR_RENDER_LENGTH_M;
    /** progress of the furthest-back car placed in each lane, innermost lane first. */
    const laneLast: number[] = [];
    const lane: number[] = new Array(ranked.length).fill(0);

    for (let i = 0; i < ranked.length; i++) {
      const p = ranked[i];
      p.lateralM = 0;
      p.held = false;
      const lanesHere = lanesAcross(this.track, p.motion.stationM);
      let chosen = -1;
      for (let k = 0; k < lanesHere; k++) {
        if (k >= laneLast.length
          || laneLast[k] - p.motion.progressM >= minGapM - CONTACT_EPS_M) {
          chosen = k;
          break;
        }
      }
      if (chosen < 0) {
        let back = 0;
        for (let k = 1; k < lanesHere; k++) if (laneLast[k] < laneLast[back]) back = k;
        const limit = laneLast[back] - minGapM;
        chosen = back;
        p.motion = {
          ...p.motion,
          progressM: limit,
          stationM: wrapStation(limit, L),
          phase: "QUEUED",
        };
        p.held = true;
      }
      lane[i] = chosen;
      laneLast[chosen] = p.motion.progressM;
    }

    // Lane index -> metres off the racing line, centred per PACK so a pack sits on the
    // line rather than fanning out from it. A pack is a run of cars each within a car
    // length of the one ahead; a lone car is a pack of one and keeps lateral 0.
    let packStart = 0;
    for (let i = 1; i <= ranked.length; i++) {
      const isBreak = i === ranked.length
        || ranked[i - 1].motion.progressM - ranked[i].motion.progressM
          >= minGapM - CONTACT_EPS_M;
      if (!isBreak) continue;
      let lo = lane[packStart], hi = lane[packStart];
      for (let k = packStart; k < i; k++) {
        lo = Math.min(lo, lane[k]);
        hi = Math.max(hi, lane[k]);
      }
      const mid = (lo + hi) / 2;
      for (let k = packStart; k < i; k++) {
        ranked[k].lateralM = (lane[k] - mid) * CAR_RENDER_WIDTH_M;
      }
      packStart = i;
    }
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
