/** Typed access to sim/params.<hash>.json, built by scripts/simdata/fit_params.py.
 * Every leaf there is {value, se?, n?, provenance, note?, ...}; this file only pulls
 * out the `value` the engine composes with, but keeps the object around so the UI can
 * still show provenance/n/se for any parameter it surfaces. */

export interface Leaf {
  value: number;
  se?: number;
  /** 95% interval as [low, high]. Present on every fitted leaf; the UI is expected to draw
   * it rather than collapse the estimate to its point value. */
  ci95?: [number, number];
  n?: number;
  provenance: string;
  note?: string;
}

export interface FittedParams {
  sessionPaceTrendPerLap: { pooled: Leaf; perTrack: Record<string, Leaf> };
  tyreDegradation: {
    trackIndex: Record<string, Leaf>;
    compoundMultiplierDefault: Record<string, Leaf>;
  };
  driverOffsetSeconds: Record<string, Leaf>;
  teamOffsetSeconds: Record<string, Leaf>;
  /** R-squared of the two offset fits and the lap counts behind them. Shown next to the
   * offsets so a reader can see how much of lap-time variation the model actually explains.
   * OPTIONAL on purpose: the race engine never reads it, and requiring it would force every
   * engine test fixture to carry a field its subject does not use. */
  modelFitR2?: { driver: number; team: number; n_driver: number; n_team: number };
  noise: {
    coreSigma: Leaf;
    incidentProbability: Leaf;
    incidentMeanExcessSeconds: Leaf;
    perDriverSigma?: Record<string, Leaf>;
  };
  dirtyAirLossPerSecondOfProximity: Record<string, Leaf>;
  pitLoss: Record<string, { netLossSeconds: Leaf; iqr: [number, number]; neutralisedNetLossSeconds?: Leaf }>;
  neutralisation: {
    safetyCarLapHazard: Leaf; virtualSafetyCarLapHazard: Leaf; redFlagLapHazard: Leaf;
    perTrackMultiplierDefault: Leaf;
  };
  retirement: { perCarPerRace: Leaf; perLapHazard: Leaf };
  freshTyreGainSecondsPerLapDefault: Leaf;
  defaultLaps: { formula: string };
}

export function leafValue(leaf: Leaf | undefined, fallback: number): number {
  return leaf && Number.isFinite(leaf.value) ? leaf.value : fallback;
}

/** Lap-time multiplier under a neutralisation. NOT present in params.json: the
 * pipeline in scripts/simdata/fit_params.py fits neutralisation HAZARD rates but does
 * not fit the lap-time multiplier itself (that requires reconstructing green-vs-
 * neutralised pace per lap, a follow-up derivation). These are DEFAULTs, not DERIVED
 * -- shipped openly as such rather than silently invented inside the engine loop. */
export const NEUTRALISATION_MULTIPLIER_DEFAULT = { SC: 1.4, VSC: 1.19 } as const;
