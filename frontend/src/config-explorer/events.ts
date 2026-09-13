/**
 * Per-event snapshot of config/rules/2026/<event>.yaml — the values that differ by
 * circuit rather than by season. Season-wide values (power envelope, energy
 * budgets, detection gap threshold) live in data.ts and apply to every event
 * unless that event's `overrides:` block sets one — currently EMPTY in all 14
 * files, so every event inherits the season default for those.
 *
 * Only 2026 has a rule config at all: 2022-2025 are the pre-Overtake DRS era
 * and are not represented here (AGENTS.md §41 — a historical DRS zone is not a
 * 2026 Overtake fact). "Year selection" for this explorer is therefore a
 * single fixed year; what varies is the EVENT.
 */

export type EventValueSource = "DERIVED_TELEMETRY" | "OBSERVED" | "OBSERVED_RCM" | "UNVERIFIED";

export interface EventProfile {
  circuit: string;
  event: string;
  eventDisplay: string;
  completeInMirror: boolean;
  lapLengthM: number;
  publishedLengthM: number;
  deltaVsPublishedPct: number;
  cornerCount: number;
  rotationDeg: number;
  /** Session-time detection line position, null where no FIA note states it for this event. */
  detectionLineM: number | null;
  overtakeEnabledMessages: number;
  overtakeDisabledMessages: number;
}

export const EVENT_PROFILES: EventProfile[] = [
  { circuit: "australian", event: "australian_grand_prix", eventDisplay: "Australian Grand Prix", completeInMirror: true, lapLengthM: 5226.7, publishedLengthM: 5278, deltaVsPublishedPct: -0.97, cornerCount: 14, rotationDeg: 44.0, detectionLineM: 4940.0, overtakeEnabledMessages: 5, overtakeDisabledMessages: 1 },
  { circuit: "austrian", event: "austrian_grand_prix", eventDisplay: "Austrian Grand Prix", completeInMirror: true, lapLengthM: 4277.7, publishedLengthM: 4318, deltaVsPublishedPct: -0.93, cornerCount: 10, rotationDeg: 1.0, detectionLineM: 4010.0, overtakeEnabledMessages: 1, overtakeDisabledMessages: 1 },
  { circuit: "barcelona", event: "barcelona_grand_prix", eventDisplay: "Barcelona Grand Prix", completeInMirror: true, lapLengthM: 4630.7, publishedLengthM: 4657, deltaVsPublishedPct: -0.56, cornerCount: 14, rotationDeg: 303.0, detectionLineM: 4520.0, overtakeEnabledMessages: 2, overtakeDisabledMessages: 1 },
  { circuit: "belgian", event: "belgian_grand_prix", eventDisplay: "Belgian Grand Prix", completeInMirror: true, lapLengthM: 6936.4, publishedLengthM: 7004, deltaVsPublishedPct: -0.97, cornerCount: 19, rotationDeg: 91.0, detectionLineM: 6900.0, overtakeEnabledMessages: 2, overtakeDisabledMessages: 1 },
  { circuit: "british", event: "british_grand_prix", eventDisplay: "British Grand Prix", completeInMirror: true, lapLengthM: 5811.0, publishedLengthM: 5891, deltaVsPublishedPct: -1.36, cornerCount: 18, rotationDeg: 92.0, detectionLineM: 5520.0, overtakeEnabledMessages: 2, overtakeDisabledMessages: 3 },
  { circuit: "canadian", event: "canadian_grand_prix", eventDisplay: "Canadian Grand Prix", completeInMirror: true, lapLengthM: 4330.0, publishedLengthM: 4361, deltaVsPublishedPct: -0.71, cornerCount: 14, rotationDeg: 62.0, detectionLineM: 3900.0, overtakeEnabledMessages: 9, overtakeDisabledMessages: 2 },
  { circuit: "chinese", event: "chinese_grand_prix", eventDisplay: "Chinese Grand Prix", completeInMirror: true, lapLengthM: 5410.2, publishedLengthM: 5451, deltaVsPublishedPct: -0.75, cornerCount: 16, rotationDeg: 237.0, detectionLineM: 5400.0, overtakeEnabledMessages: 4, overtakeDisabledMessages: 2 },
  { circuit: "dutch", event: "dutch_grand_prix", eventDisplay: "Dutch Grand Prix", completeInMirror: true, lapLengthM: 4222.5, publishedLengthM: 4259, deltaVsPublishedPct: -0.86, cornerCount: 14, rotationDeg: 0.0, detectionLineM: null, overtakeEnabledMessages: 5, overtakeDisabledMessages: 3 },
  { circuit: "hungarian", event: "hungarian_grand_prix", eventDisplay: "Hungarian Grand Prix", completeInMirror: true, lapLengthM: 4268.0, publishedLengthM: 4381, deltaVsPublishedPct: -2.58, cornerCount: 16, rotationDeg: 40.0, detectionLineM: null, overtakeEnabledMessages: 2, overtakeDisabledMessages: 1 },
  { circuit: "italian", event: "italian_grand_prix", eventDisplay: "Italian Grand Prix", completeInMirror: true, lapLengthM: 5757.7, publishedLengthM: 5793, deltaVsPublishedPct: -0.61, cornerCount: 11, rotationDeg: 95.0, detectionLineM: 5660.0, overtakeEnabledMessages: 3, overtakeDisabledMessages: 2 },
  { circuit: "japanese", event: "japanese_grand_prix", eventDisplay: "Japanese Grand Prix", completeInMirror: true, lapLengthM: 5742.1, publishedLengthM: 5807, deltaVsPublishedPct: -1.12, cornerCount: 18, rotationDeg: 49.0, detectionLineM: 5710.0, overtakeEnabledMessages: 2, overtakeDisabledMessages: 1 },
  { circuit: "miami", event: "miami_grand_prix", eventDisplay: "Miami Grand Prix", completeInMirror: true, lapLengthM: 5332.9, publishedLengthM: 5412, deltaVsPublishedPct: -1.46, cornerCount: 19, rotationDeg: 2.0, detectionLineM: 5200.0, overtakeEnabledMessages: 3, overtakeDisabledMessages: 3 },
  { circuit: "monaco", event: "monaco_grand_prix", eventDisplay: "Monaco Grand Prix", completeInMirror: true, lapLengthM: 3284.5, publishedLengthM: 3337, deltaVsPublishedPct: -1.57, cornerCount: 19, rotationDeg: 315.0, detectionLineM: 2920.0, overtakeEnabledMessages: 6, overtakeDisabledMessages: 2 },
  { circuit: "spanish", event: "spanish_grand_prix", eventDisplay: "Spanish Grand Prix", completeInMirror: false, lapLengthM: 5325.9, publishedLengthM: 5474, deltaVsPublishedPct: -2.71, cornerCount: 0, rotationDeg: 0, detectionLineM: null, overtakeEnabledMessages: 0, overtakeDisabledMessages: 0 },
];

export const DEFAULT_EVENT_CIRCUIT = "british";

export function eventProfile(circuit: string): EventProfile {
  return EVENT_PROFILES.find((e) => e.circuit === circuit) ?? EVENT_PROFILES[0];
}
