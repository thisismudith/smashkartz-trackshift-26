/**
 * Builds a RaceTimeline (the shared contract) from a decoded replay pack.
 *
 * Leaderboard rule (validated in the plan against the official classification): order
 * by (laps completed via the crossing tower, then live station progress). This
 * implementation is a pragmatic subset of the plan's full method: it uses the tower
 * for lap counts and falls back to live progress for ordering within a lap, but does
 * not apply the plan's extra hysteresis band on near-tied on-track swaps (sign must
 * persist >= 1 s) — a documented simplification, not an unknown gap.
 *
 * Gaps are computed by scanning the leader's (or car-ahead's) decoded lap for the
 * closest station match to the target car's current station, which is coarser than
 * the plan's crossing-interpolated method but shares its unit and sign convention.
 */
import type {
  CarState, NeutralisationInterval, RaceEvent, RaceTimeline, TrackModel,
} from "../contract/types";
import { type DecodedLap, decodeLap, sampleLap } from "../data/codec";
import type { RawDriverEntry, RawLapEntry, RawSessionManifest } from "../data/manifest";
import { trackPointAt } from "../data/manifest";

interface DriverLaps {
  entry: RawDriverEntry;
  laps: RawLapEntry[]; // sorted by lap number
  decoded: Map<number, DecodedLap>;
}

function decodeAll(bin: ArrayBuffer, drivers: RawDriverEntry[]): Map<string, DriverLaps> {
  const out = new Map<string, DriverLaps>();
  for (const d of drivers) {
    const laps = [...d.laps].sort((a, b) => a.lap - b.lap);
    const decoded = new Map<number, DecodedLap>();
    for (const l of laps) {
      if (l.sampleCount > 0) decoded.set(l.lap, decodeLap(bin, l.byteOffset, l.sampleCount));
    }
    out.set(d.driver, { entry: d, laps, decoded });
  }
  return out;
}

/** lST/sesT/pin/pout in the raw data are SESSION-ABSOLUTE (measured: lap 1's lST is
 * identical across every driver but is typically 2000-3500 s into the recorded
 * session, not 0 -- the recording starts well before lights-out). The public
 * RaceTimeline contract promises sampleAt(t) for t in [0, duration], so every
 * absolute timestamp is shifted here, once, by the earliest lap-1 lST -- the same
 * "race start" instant the plan identifies from race-control text. Returns a deep
 * copy; the raw manifest passed in is never mutated. */
function normaliseToRaceStart(manifest: RawSessionManifest): {
  drivers: RawDriverEntry[]; raceControl: RawSessionManifest["raceControl"];
  neutralisation: RawSessionManifest["neutralisation"]; weather: RawSessionManifest["weather"];
  offset: number;
} {
  let offset = Infinity;
  for (const d of manifest.drivers) {
    for (const l of d.laps) {
      if (l.lST !== null && l.lST < offset) offset = l.lST;
    }
  }
  if (!Number.isFinite(offset)) offset = 0;

  const shift = (v: number | null) => (v === null ? null : v - offset);
  const drivers = manifest.drivers.map((d) => ({
    ...d,
    laps: d.laps.map((l) => ({ ...l, lST: shift(l.lST), sesT: shift(l.sesT), pin: shift(l.pin), pout: shift(l.pout) })),
  }));
  const raceControl = manifest.raceControl.map((m) => ({ ...m, sessionTime: m.sessionTime - offset }));
  const neutralisation = manifest.neutralisation.map((n) => ({ ...n, start: n.start - offset, end: n.end - offset }));
  const weather = manifest.weather
    ? { ...manifest.weather, wT: manifest.weather.wT.map((v) => v - offset) }
    : null;
  return { drivers, raceControl, neutralisation, weather, offset };
}

/** Closest-station lookup within one decoded lap; returns the session time (lST +
 * lap-relative time) at the sample nearest `targetStation` (circular distance). */
function stationTimeInLap(
  lap: DecodedLap, lST: number, targetStation: number, trackLength: number,
): number | null {
  if (lap.n === 0) return null;
  let bestI = 0, bestD = Infinity;
  for (let i = 0; i < lap.n; i++) {
    let d = Math.abs(lap.stationM[i] - targetStation);
    if (d > trackLength / 2) d = trackLength - d;
    if (d < bestD) { bestD = d; bestI = i; }
  }
  return lST + lap.tS[bestI];
}

export class ReplayTimeline implements RaceTimeline {
  readonly provenance = "OBSERVED" as const;
  readonly runId: string;
  readonly track: TrackModel;
  readonly driverList: string[];
  readonly totalLaps: number | null;
  readonly duration: number;

  private byDriver: Map<string, DriverLaps>;
  private raceControl: RawSessionManifest["raceControl"];
  private neutral: RawSessionManifest["neutralisation"];
  private weatherRaw: RawSessionManifest["weather"];
  private retiredOrder: string[] = [];
  private finishOrder: string[] = [];
  private parkIndex = new Map<string, number>();

  constructor(manifest: RawSessionManifest, track: TrackModel, bin: ArrayBuffer) {
    this.runId = `obs:${manifest.trackSlug}/${manifest.session.toLowerCase()}`;
    this.track = track;
    const normalised = normaliseToRaceStart(manifest);
    this.byDriver = decodeAll(bin, normalised.drivers);
    this.driverList = normalised.drivers.map((d) => d.driver);
    this.raceControl = normalised.raceControl;
    this.neutral = normalised.neutralisation;
    this.weatherRaw = normalised.weather;

    let maxLap = 0;
    let maxSesT = 0;
    for (const dl of this.byDriver.values()) {
      for (const l of dl.laps) {
        if (l.lap > maxLap) maxLap = l.lap;
        if (l.sesT !== null && l.sesT > maxSesT) maxSesT = l.sesT;
      }
    }
    this.totalLaps = maxLap || null;
    this.duration = maxSesT;

    // Finished vs retired must be judged by LAP COUNT REACHED, never by session time:
    // the winner typically has the EARLIEST final sesT of any finisher (they simply
    // took less time), so comparing "last sesT" against the race maximum would
    // misclassify the winner as retired. A driver is retired only if their total
    // completed (non-generated) laps fall short of the race's own lap count.
    const stops: { driver: string; t: number }[] = [];
    for (const [driver, dl] of this.byDriver) {
      const completed = dl.laps.filter((l) => !l.ff1G && l.sesT !== null).length;
      if (this.totalLaps !== null && completed < this.totalLaps) {
        const last = dl.laps[dl.laps.length - 1];
        stops.push({ driver, t: last?.sesT ?? 0 });
      }
    }
    stops.sort((a, b) => a.t - b.t);
    this.retiredOrder = stops.map((s) => s.driver);

    // finish order (winner first): needed only for the presentation "parking" spread
    // below, never for the leaderboard itself (which is ranked live in sampleAt).
    const finishes: { driver: string; t: number }[] = [];
    for (const [driver, dl] of this.byDriver) {
      if (this.retiredOrder.includes(driver)) continue;
      const last = dl.laps[dl.laps.length - 1];
      if (last?.sesT !== null && last?.sesT !== undefined) finishes.push({ driver, t: last.sesT });
    }
    finishes.sort((a, b) => a.t - b.t);
    this.finishOrder = finishes.map((f) => f.driver);
    this.finishOrder.forEach((d, i) => this.parkIndex.set(d, i));
    this.retiredOrder.forEach((d, i) => this.parkIndex.set(d, this.finishOrder.length + i));
  }

  /** Real telemetry ends the instant a car crosses the line, so two cars that finish
   * a few seconds apart would otherwise sit at (almost) the exact same point forever
   * after -- multiple 5.6 x 2 m boxes pinned to one spot is not a rendering choice,
   * it is real width-spread data (audit: p98 0.3-0.6 m) being smaller than a car's own
   * footprint. Once a car is finished/retired this queues it, nose-to-tail, behind the
   * line at the real grid spacing -- a labelled PRESENTATION placement, not a claim
   * about where the car actually is (it does not feed gaps, laps, or any other
   * reported number). */
  private parkedStation(driver: string): number {
    const idx = this.parkIndex.get(driver) ?? 0;
    const back = (idx + 1) * this.track.grid.pitchMetres;
    return ((this.track.timingLines.sf - back) % this.track.lengthMetres + this.track.lengthMetres)
      % this.track.lengthMetres;
  }

  /** The last real sample of a lap: used for the brief, effectively-instantaneous
   * "gap" state between one lap's sesT and the next lap's lST (see currentLap),
   * rather than snapping every such car to the same canonical line point. */
  private lastKnownPosition(dl: DriverLaps, lap: RawLapEntry): number {
    const decoded = dl.decoded.get(lap.lap);
    if (decoded && decoded.n > 0) return decoded.stationM[decoded.n - 1];
    return this.track.timingLines.sf;
  }

  private currentLap(dl: DriverLaps, t: number): { lap: RawLapEntry; idx: number } | null {
    let idx = -1;
    for (let i = 0; i < dl.laps.length; i++) {
      const l = dl.laps[i];
      if (l.lST !== null && l.lST <= t) idx = i; else break;
    }
    return idx >= 0 ? { lap: dl.laps[idx], idx } : null;
  }

  /** Laps completed (excluding FastF1-generated partial laps, per the plan) at time t,
   * plus fractional progress through the current lap, 0..1. */
  private progress(dl: DriverLaps, t: number): {
    lapsDone: number; lapProgress: number; stationM: number; lapEntry: RawLapEntry | null;
    kind: CarState["status"];
  } {
    const cur = this.currentLap(dl, t);
    if (!cur) {
      const first = dl.laps[0];
      const gridStation = (this.track.grid.order.indexOf(dl.entry.driver) + 1)
        * -this.track.grid.pitchMetres;
      return {
        lapsDone: 0, lapProgress: 0,
        stationM: ((gridStation % this.track.lengthMetres) + this.track.lengthMetres)
          % this.track.lengthMetres,
        lapEntry: first ?? null, kind: "grid",
      };
    }
    const { lap, idx } = cur;
    const decoded = dl.decoded.get(lap.lap);
    const nonFf1gLapsBefore = dl.laps.slice(0, idx).filter((l) => !l.ff1G && l.sesT !== null).length;

    if (lap.sesT !== null && t > lap.sesT) {
      // this lap is finished; are we between laps or at the end of the session?
      const next = dl.laps[idx + 1];
      const lapsDone = nonFf1gLapsBefore + (lap.ff1G ? 0 : 1);
      if (!next) {
        const isFinisher = !this.retiredOrder.includes(dl.entry.driver);
        return {
          lapsDone, lapProgress: 1, stationM: this.parkedStation(dl.entry.driver),
          lapEntry: lap, kind: isFinisher ? "finished" : "retired",
        };
      }
      return {
        lapsDone, lapProgress: 1, stationM: this.lastKnownPosition(dl, lap), lapEntry: lap, kind: "gap",
      };
    }

    if (!decoded || decoded.n === 0 || lap.lST === null) {
      return {
        lapsDone: nonFf1gLapsBefore, lapProgress: 0, stationM: this.lastKnownPosition(dl, lap),
        lapEntry: lap, kind: "gap",
      };
    }
    const rel = t - lap.lST;
    const sample = sampleLap(decoded, rel, this.track.lengthMetres);
    const inPit = (lap.pin !== null && t >= lap.lST + (lap.pin - lap.lST))
      || (lap.pout !== null && rel <= 0);
    return {
      lapsDone: nonFf1gLapsBefore,
      lapProgress: Math.max(0, Math.min(0.9999, rel / (lap.time ?? (decoded.tS[decoded.n - 1] || 1)))),
      stationM: sample ? sample.stationM : this.track.timingLines.sf,
      lapEntry: lap, kind: inPit ? "pit" : "track",
    };
  }

  sampleAt(t: number): Map<string, CarState> {
    const out = new Map<string, CarState>();
    const progressByDriver = new Map<string, ReturnType<ReplayTimeline["progress"]>>();
    for (const [driver, dl] of this.byDriver) progressByDriver.set(driver, this.progress(dl, t));

    // order: lapsDone desc, then lapProgress desc, retired appended in retirement order
    const active = [...progressByDriver.entries()].filter(([d]) => !this.retiredOrder.includes(d)
      || progressByDriver.get(d)!.kind !== "retired");
    const ranked = active.slice().sort((a, b) => {
      const sa = a[1], sb = b[1];
      if (sa.kind === "grid" && sb.kind !== "grid") return 1;
      if (sb.kind === "grid" && sa.kind !== "grid") return -1;
      const scoreA = sa.lapsDone + sa.lapProgress;
      const scoreB = sb.lapsDone + sb.lapProgress;
      return scoreB - scoreA;
    });
    const retiredRanked = this.retiredOrder
      .filter((d) => progressByDriver.get(d)?.kind === "retired")
      .map((d) => [d, progressByDriver.get(d)!] as const);
    const order = [...ranked, ...retiredRanked];

    const leaderLapsDone = order.length ? order[0][1].lapsDone : 0;

    order.forEach(([driver, p], i) => {
      const dl = this.byDriver.get(driver)!;
      const pt = trackPointAt(this.track, p.stationM);
      const { z, heading } = pt;

      let lateralM = 0, speedKph = 0, gear = 0, throttlePct = 0, brake = false;
      const cur = this.currentLap(dl, t);
      if (cur && cur.lap.lST !== null) {
        const decoded = dl.decoded.get(cur.lap.lap);
        if (decoded) {
          const s = sampleLap(decoded, t - cur.lap.lST, this.track.lengthMetres);
          if (s) {
            lateralM = s.lateralM;
            speedKph = s.speedKph;
            gear = s.gear;
            throttlePct = s.throttlePct;
            brake = s.brake === 1;
          }
        }
      }

      let gapToLeaderS: number | null = null;
      let intervalS: number | null = null;
      const lapsDownFromLeader = leaderLapsDone - p.lapsDone;
      if (i > 0 && lapsDownFromLeader === 0) {
        const leaderDriver = order[0][0];
        const leaderDl = this.byDriver.get(leaderDriver)!;
        const leaderLapEntry = leaderDl.laps.find((l) => l.lap === p.lapEntry?.lap);
        const leaderLap = leaderLapEntry ? leaderDl.decoded.get(leaderLapEntry.lap) : null;
        if (leaderLap && leaderLapEntry?.lST !== null && leaderLapEntry?.lST !== undefined) {
          const tAtStation = stationTimeInLap(leaderLap, leaderLapEntry.lST, p.stationM, this.track.lengthMetres);
          if (tAtStation !== null) gapToLeaderS = Math.max(0, t - tAtStation);
        }
        const aheadDriver = order[i - 1][0];
        const aheadDl = this.byDriver.get(aheadDriver)!;
        const aheadLapEntry = aheadDl.laps.find((l) => l.lap === p.lapEntry?.lap);
        const aheadLap = aheadLapEntry ? aheadDl.decoded.get(aheadLapEntry.lap) : null;
        if (aheadLap && aheadLapEntry?.lST !== null && aheadLapEntry?.lST !== undefined) {
          const tAtStation = stationTimeInLap(aheadLap, aheadLapEntry.lST, p.stationM, this.track.lengthMetres);
          if (tAtStation !== null) intervalS = Math.max(0, t - tAtStation);
        }
      }

      out.set(driver, {
        driver,
        team: dl.entry.team,
        stationM: p.stationM,
        lateralM,
        elevationM: z,
        headingRad: heading,
        speedKph,
        gear,
        throttlePct,
        brake,
        tyreCompound: p.lapEntry?.compound ?? null,
        tyreLife: p.lapEntry?.life ?? null,
        lapsDone: p.lapsDone,
        lapProgress: p.lapProgress,
        position: i + 1,
        gapToLeaderS,
        lapsDownFromLeader,
        intervalS,
        inPit: p.kind === "pit",
        status: p.kind,
        provenance: "OBSERVED",
      });
    });
    return out;
  }

  events(): RaceEvent[] {
    return this.raceControl.map((m) => ({
      sessionTime: m.sessionTime,
      kind: m.kind,
      message: m.message,
      drivers: m.cars,
      provenance: "OBSERVED" as const,
    }));
  }

  neutralisations(): NeutralisationInterval[] {
    return this.neutral;
  }

  weather() {
    if (!this.weatherRaw) return null;
    return {
      tS: this.weatherRaw.wT,
      airTempC: this.weatherRaw.wAT,
      trackTempC: this.weatherRaw.wTT,
      humidityPct: this.weatherRaw.wH,
      rain: this.weatherRaw.wR,
      windMps: this.weatherRaw.wWS,
      provenance: "OBSERVED" as const,
    };
  }
}
