/**
 * The shared filter bar: year, circuit, session, team, driver.
 *
 * Facets cross-filter downward (team narrows driver, circuit narrows both) but a selection that
 * falls out of scope is NOT silently dropped -- it is reported, because a filter that quietly
 * discards your pick is a filter that misrepresents what is on screen.
 *
 * Two honesty details baked in:
 *   - The YEAR facet usually has exactly one value, because a build directory holds exactly one
 *     season (artifact names carry the circuit slug but no year -- scripts/build_sim_data.py
 *     --year). It is shown, disabled, with the reason, rather than omitted (which would imply
 *     the dimension does not exist) or faked with years we have no data for.
 *   - A driver who changed team mid-season appears in the catalogue once PER TEAM. Those entries
 *     are merged into one option here, with the teams listed, so the list shows people rather than
 *     contracts -- but the merge is stated in the panel note rather than done silently.
 */
"use client";

import { useMemo } from "react";
import type { Catalogue } from "@/sim/data/catalogue";
import type { SimIndex } from "@/sim/data/source";
import { MultiSelect, type SelectOption } from "./MultiSelect";
import { FACETS, type Facet, type FilterState } from "./filterState";
import s from "./filters.module.css";

export interface FilterBarProps {
  catalogue: Catalogue;
  index: SimIndex;
  state: FilterState;
  onChange: (facet: Facet, values: string[]) => void;
  onReset: () => void;
  /** Facets to render. Defaults to all five. A page that has no use for a facet should omit it
   * rather than render one that does nothing. */
  show?: Facet[];
}

export function FilterBar({
  catalogue,
  index,
  state,
  onChange,
  onReset,
  show = [...FACETS],
}: FilterBarProps) {
  const facets = useFacetOptions(catalogue, index, state);
  const active = FACETS.reduce((n, f) => n + state[f].length, 0);

  return (
    <div className={s.bar} role="group" aria-label="Filters">
      {show.includes("years") ? (
        <MultiSelect
          label="Year"
          options={facets.years}
          selected={state.years}
          onChange={(v) => onChange("years", v)}
          placeholder="All"
          emptyMeans="all"
          showChips={false}
          disabled={facets.years.length <= 1}
          disabledReason={
            facets.years.length === 1
              ? `Only ${facets.years[0].label} artifacts are built. Other seasons are mirrored raw but not processed.`
              : "No built artifacts carry a season."
          }
        />
      ) : null}

      {show.includes("circuits") ? (
        <MultiSelect
          label="Circuit"
          options={facets.circuits}
          selected={state.circuits}
          onChange={(v) => onChange("circuits", v)}
          placeholder="All"
          emptyMeans="all"
        />
      ) : null}

      {show.includes("sessions") ? (
        <MultiSelect
          label="Session"
          options={facets.sessions}
          selected={state.sessions}
          onChange={(v) => onChange("sessions", v)}
          placeholder="All"
          emptyMeans="all"
          showChips={false}
        />
      ) : null}

      {show.includes("teams") ? (
        <MultiSelect
          label="Team"
          options={facets.teams}
          selected={state.teams}
          onChange={(v) => onChange("teams", v)}
          placeholder="All"
          emptyMeans="all"
        />
      ) : null}

      {show.includes("drivers") ? (
        <MultiSelect
          label="Driver"
          options={facets.drivers}
          selected={state.drivers}
          onChange={(v) => onChange("drivers", v)}
          placeholder="All"
          emptyMeans="all"
          note={facets.driverNote}
        />
      ) : null}

      <div className={s.barActions}>
        <span className={s.barSummary}>
          {active === 0 ? "no filters" : `${active} selected`}
        </span>
        <button type="button" className={s.barReset} onClick={onReset} disabled={active === 0}>
          Reset
        </button>
      </div>
    </div>
  );
}

/**
 * Build the option lists, applying downward cross-filtering.
 *
 * Ordering is stable and meaningful, never by selection state: circuits alphabetically, teams
 * alphabetically, drivers grouped by team then by car number. A list that reorders as you pick
 * things is a list you cannot learn the shape of.
 */
export function useFacetOptions(catalogue: Catalogue, index: SimIndex, state: FilterState) {
  return useMemo(() => {
    const builtSlugs = new Set(Object.keys(index.sessions));

    const years: SelectOption[] = [...new Set(catalogue.tracks.map((t) => String(t.year)))]
      .sort()
      .map((y) => ({ value: y, label: y }));

    const circuits: SelectOption[] = catalogue.tracks
      .filter((t) => builtSlugs.has(t.slug))
      .filter((t) => state.years.length === 0 || state.years.includes(String(t.year)))
      .sort((a, b) => a.event.localeCompare(b.event))
      .map((t) => ({
        value: t.slug,
        label: t.event.replace(/\s+Grand Prix$/i, ""),
        keywords: t.event,
        meta: `${Object.keys(index.sessions[t.slug] ?? {}).length} sess`,
      }));

    const sessionKinds = [
      ...new Set(Object.values(index.sessions).flatMap((v) => Object.keys(v))),
    ].sort();
    const sessions: SelectOption[] = sessionKinds.map((k) => ({ value: k, label: k }));

    // Which circuits are in scope right now: the selection, or everything built.
    const scopeSlugs =
      state.circuits.length > 0 ? new Set(state.circuits) : new Set(circuits.map((c) => c.value));

    // Entries actually present at the in-scope circuits, so the driver list reflects who raced
    // there rather than the whole season's roster.
    //
    // `tracks[].entries` rather than `catalogue.drivers` on purpose, and it matters: drivers has
    // 35 rows / 32 distinct codes because it includes practice-only and reserve entries (ARO,
    // BEG, BRO, CRA, FOR, HER, HIR, IWA, VES), none of whom have a fitted parameter. entries has
    // exactly 23 codes, which is exactly the key set of params.driverOffsetSeconds -- verified.
    // Offering the other nine would mean offering filters that can only ever empty a chart.
    const entriesInScope = catalogue.tracks
      .filter((t) => scopeSlugs.has(t.slug))
      .flatMap((t) => t.entries);

    const teamNames = [...new Set(entriesInScope.map((e) => e.team))].sort();
    const teams: SelectOption[] = teamNames.map((name) => {
      const any = entriesInScope.find((e) => e.team === name);
      return {
        value: name,
        label: name.replace(/\s+F1 Team$/i, ""),
        keywords: name,
        colour: any ? `#${any.colour}` : undefined,
        meta: `${new Set(entriesInScope.filter((e) => e.team === name).map((e) => e.code)).size} cars`,
      };
    });

    // Merge a driver's per-team catalogue entries into one person.
    const byCode = new Map<string, { code: string; number: number; teams: Set<string>; colour: string }>();
    // car number is stored as a string in the artifact; parse once here so the sort is numeric
    // (a lexicographic sort puts #10 before #2)
    for (const e of entriesInScope) {
      const hit = byCode.get(e.code);
      if (hit) hit.teams.add(e.team);
      else byCode.set(e.code, { code: e.code, number: Number(e.number), teams: new Set([e.team]), colour: e.colour });
    }

    const teamScope = state.teams.length > 0 ? new Set(state.teams) : null;
    const multiTeam = [...byCode.values()].filter((d) => d.teams.size > 1);

    const drivers: SelectOption[] = [...byCode.values()]
      .filter((d) => !teamScope || [...d.teams].some((t) => teamScope.has(t)))
      .sort((a, b) => {
        const ta = [...a.teams].sort()[0];
        const tb = [...b.teams].sort()[0];
        return ta === tb ? a.number - b.number : ta.localeCompare(tb);
      })
      .map((d) => ({
        value: d.code,
        label: d.code,
        group: [...d.teams].sort().join(" / "),
        colour: `#${d.colour}`,
        meta: `#${d.number}`,
        keywords: [...d.teams].join(" "),
      }));

    const driverNote =
      multiTeam.length > 0
        ? `${multiTeam.map((d) => d.code).join(", ")} appear under more than one team in the catalogue and are merged into a single entry here.`
        : undefined;

    return { years, circuits, sessions, teams, drivers, driverNote };
  }, [catalogue, index, state.years, state.circuits, state.teams]);
}
