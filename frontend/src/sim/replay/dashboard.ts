import type {
  CarState, DashboardRow, DashboardSnapshot, NeutralisationInterval, RaceEvent,
} from "../contract/types";
import type { OrderSource } from "./timeline";

export type { OrderSource };

/** A CarState that may carry the replay timeline's order label. Sources that do not
 * produce one (the generated New Race timeline) simply omit it. */
export type OrderedCarState = CarState & { orderSource?: OrderSource };

/** DashboardRow plus the label. Widening rather than editing the shared contract keeps
 * every existing consumer of DashboardRow/DashboardSnapshot valid. */
export interface OrderedDashboardRow extends DashboardRow {
  /** Which signal decided this row's place: the official classification at the last
   * line crossing, live measured distance within the current lap, or a rule placement.
   * The UI can mark a MEASURED row as provisional -- it is an on-track order the timing
   * tower has not confirmed at a line crossing yet. */
  orderSource: OrderSource;
}

export interface OrderedDashboardSnapshot extends DashboardSnapshot {
  leaderboard: OrderedDashboardRow[];
}

function fmtGap(car: CarState): string {
  if (car.status === "grid") return "—";
  if (car.lapsDownFromLeader > 0) return `+${car.lapsDownFromLeader} LAP${car.lapsDownFromLeader > 1 ? "S" : ""}`;
  if (car.inPit) return "PIT";
  if (car.position === 1) return "LEADER";
  if (car.gapToLeaderS === null) return "—";
  return `+${car.gapToLeaderS.toFixed(1)}`;
}

function fmtInterval(car: CarState): string {
  if (car.position === 1) return "—";
  if (car.inPit) return "PIT";
  if (car.intervalS === null) return "—";
  return `+${car.intervalS.toFixed(1)}`;
}

export function buildDashboardSnapshot(
  states: Map<string, OrderedCarState>,
  sessionTime: number,
  events: RaceEvent[],
  neutralisations: NeutralisationInterval[],
  recentWindowS = 30,
): OrderedDashboardSnapshot {
  const leaderboard: OrderedDashboardRow[] = [...states.values()]
    .sort((a, b) => a.position - b.position)
    .map((car) => ({
      position: car.position,
      // A source that does not label its order still gets an honest label: a measured
      // position means the row was placed by measurement, a placed one by rule.
      orderSource: car.orderSource
        ?? (car.positionProvenance === "OBSERVED" ? "MEASURED" : "RULE"),
      driver: car.driver,
      team: car.team,
      gapToLeader: fmtGap(car),
      interval: fmtInterval(car),
      compound: car.tyreCompound,
      tyreLife: car.tyreLife,
      status: car.status,
      lastLapS: null,
      energy: car.energy,
      speedKph: car.speedKph,
      gear: car.gear,
      throttlePct: car.throttlePct,
      brake: car.brake,
      lapProgress: car.lapProgress,
    }));

  const activeNeutralisation = neutralisations.find(
    (n) => n.start <= sessionTime && sessionTime <= n.end,
  ) ?? null;

  const recentEvents = events
    .filter((e) => e.sessionTime <= sessionTime && e.sessionTime > sessionTime - recentWindowS)
    .sort((a, b) => b.sessionTime - a.sessionTime);

  return { sessionTime, leaderboard, activeNeutralisation, recentEvents };
}
