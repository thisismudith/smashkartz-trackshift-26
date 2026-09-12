import type {
  CarState, DashboardRow, DashboardSnapshot, NeutralisationInterval, RaceEvent,
} from "../contract/types";

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
  states: Map<string, CarState>,
  sessionTime: number,
  events: RaceEvent[],
  neutralisations: NeutralisationInterval[],
  recentWindowS = 30,
): DashboardSnapshot {
  const leaderboard: DashboardRow[] = [...states.values()]
    .sort((a, b) => a.position - b.position)
    .map((car) => ({
      position: car.position,
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
