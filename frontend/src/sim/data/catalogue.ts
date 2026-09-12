/** Typed access to sim/catalogue.<hash>.json, built by scripts/simdata/catalogue.py.
 *
 * The catalogue describes what the RAW data contains. The index (SimIndex) describes what has
 * actually been BUILT into artifacts. They differ on purpose — a circuit can list five sessions
 * while only its Race has a replay pack — and the session browser must show the difference
 * rather than offering a link that 404s. */

export interface CatalogueRange {
  min: number;
  median: number;
  max: number;
}

export interface CatalogueEntry {
  code: string;
  /** The car number as the artifact stores it: a STRING, e.g. "1", "44". Parse before sorting. */
  number: string;
  team: string;
  /** Hex WITHOUT the leading '#'. */
  colour: string;
  firstName: string;
  lastName: string;
}

export interface CatalogueTrack {
  event: string;
  slug: string;
  year: number;
  /** Every session the raw mirror holds for this event, built or not. */
  sessions: string[];
  raceLaps: number | null;
  sprintLaps: number | null;
  hasSprint: boolean;
  entries: CatalogueEntry[];
  weather: {
    airTempC: CatalogueRange;
    trackTempC: CatalogueRange;
    humidityPct: CatalogueRange;
    pressureMbar: CatalogueRange;
    windMps: CatalogueRange;
    rainObservedFraction: number;
    provenance: string;
  } | null;
  tyres: { compounds: string[]; counts: Record<string, number>; provenance: string } | null;
}

export interface CatalogueTeam {
  team: string;
  /** Hex WITHOUT the leading '#', as the artifact stores it. */
  colour: string;
}

export interface Catalogue {
  schemaVersion: number;
  /** One entry PER (code, team): a driver who changes team mid-season appears twice. */
  drivers: (CatalogueEntry & { sessions: number })[];
  teams: CatalogueTeam[];
  tracks: CatalogueTrack[];
  provenance: string;
}

/** Team colour as a CSS hex. The catalogue stores it bare, and forgetting the '#' silently
 * yields an invalid colour that inherits instead of throwing. */
export function teamColour(teams: CatalogueTeam[], team: string | null): string {
  const hit = team ? teams.find((t) => t.team === team) : undefined;
  return hit ? `#${hit.colour}` : "#AEAEAE";
}

/**
 * Per-driver line style for a set of drivers.
 *
 * Team colour alone cannot separate two cars from the same team -- both Ferraris are the same
 * red, and a chart with LEC and HAM on it is then a chart with two identical lines. The second
 * and subsequent drivers of a team get a dashed stroke, which is the convention every F1 timing
 * graphic uses and which survives colour-blindness and greyscale printing.
 *
 * Order matters and is the caller's: pass drivers in the order they are drawn.
 */
export function driverStyles(
  teams: CatalogueTeam[],
  drivers: { driver: string; team: string | null }[],
): Record<string, { colour: string; dashed: boolean }> {
  const seen = new Map<string, number>();
  const out: Record<string, { colour: string; dashed: boolean }> = {};
  for (const d of drivers) {
    const key = d.team ?? "__none__";
    const n = seen.get(key) ?? 0;
    seen.set(key, n + 1);
    out[d.driver] = { colour: teamColour(teams, d.team), dashed: n > 0 };
  }
  return out;
}
