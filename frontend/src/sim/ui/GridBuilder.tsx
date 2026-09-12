"use client";

import { useMemo } from "react";
import styles from "./gridBuilder.module.css";

export interface CatalogueDriver {
  code: string; team: string | null; colour: string | null; number: string | null;
}
export interface CatalogueEntry {
  code: string;
  number: string | null;
  team: string | null;
  colour: string | null;
  firstName: string | null;
  lastName: string | null;
}
export interface CatalogueTeam { team: string; colour: string }
export interface WeatherBand { min: number; median: number; max: number }
export interface CatalogueTrackLite {
  event: string;
  slug: string;
  year: number | null;
  raceLaps: number | null;
  entries?: CatalogueEntry[];
  weather?: {
    airTempC: WeatherBand | null;
    trackTempC: WeatherBand | null;
    rainObservedFraction?: number;
  } | null;
  tyres?: { compounds: string[] } | null;
}

const GREY = "AEAEAE";

/** "Australian Grand Prix" -> "Australian" — the GP suffix is already the page's context. */
function shortEvent(event: string) {
  return event.replace(/\s+Grand Prix$/i, "");
}

function fullName(e: CatalogueEntry) {
  const name = [e.firstName, e.lastName].filter(Boolean).join(" ");
  return name || e.code;
}

/**
 * The New Race setup screen: a full-page step, not a corner widget, because picking
 * the field is the actual decision the user is making here.
 *
 * The field offered is the Grand Prix's own entry list (catalogue `track.entries`,
 * built from the Race session), NOT the season-wide driver registry: that registry is
 * the union over every session and so carries reserve and rookie drivers who only ran
 * a Friday practice, and those cars were never on the grid on Sunday. Switching Grand
 * Prix therefore re-seats the whole field.
 */
export function GridBuilder({
  entries, teams, tracks, track, trackSlug, laps, seed, selected,
  onTrackChange, onLapsChange, onSeedChange, onToggleDriver, onToggleTeam,
  onSelectAll, onClear, onStart, error,
}: {
  entries: CatalogueEntry[];
  teams: CatalogueTeam[];
  tracks: CatalogueTrackLite[];
  track: CatalogueTrackLite | null;
  trackSlug: string;
  laps: number;
  seed: number;
  selected: Set<string>;
  onTrackChange: (slug: string) => void;
  onLapsChange: (n: number) => void;
  onSeedChange: (n: number) => void;
  onToggleDriver: (code: string) => void;
  onToggleTeam: (codes: string[], on: boolean) => void;
  onSelectAll: () => void;
  onClear: () => void;
  onStart: () => void;
  error: string | null;
}) {
  // Teams in the order their lead car appears in the entry list, i.e. by car number:
  // the same order the FIA entry list uses, so the column reads like the real thing.
  const byTeam = useMemo(() => {
    const groups = new Map<string, CatalogueEntry[]>();
    for (const e of entries) {
      const key = e.team ?? "Independent";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(e);
    }
    return [...groups.entries()];
  }, [entries]);

  const colourOf = useMemo(() => {
    const byTeamName = new Map(teams.map((t) => [t.team, t.colour]));
    return (e: { team: string | null; colour: string | null }) =>
      e.colour || (e.team && byTeamName.get(e.team)) || GREY;
  }, [teams]);

  const grid = useMemo(() => entries.filter((e) => selected.has(e.code)), [entries, selected]);
  const canStart = grid.length >= 2;
  const air = track?.weather?.airTempC;
  const trackTemp = track?.weather?.trackTempC;
  const compounds = track?.tyres?.compounds ?? [];

  return (
    <div className={styles.wrap}>
      <header className={styles.header}>
        <div className={styles.titleBlock}>
          <p className={styles.kicker}>
            <span className={styles.kickerBar} aria-hidden="true" />
            TrackShift 2026 · New Race
          </p>
          <h1 className={styles.title}>Build the grid</h1>
          <p className={styles.lede}>
            {track
              ? `${entries.length} cars were entered for the ${track.event}${
                  track.year ? ` ${track.year}` : ""
                }. Drop any of them and the rest line up without it.`
              : "Pick a Grand Prix to load its entry list."}
          </p>
        </div>

        <div className={styles.setupRow}>
          <label className={styles.field}>
            <span>Grand Prix</span>
            <select value={trackSlug} onChange={(e) => onTrackChange(e.target.value)}>
              {tracks.map((t) => <option key={t.slug} value={t.slug}>{t.event}</option>)}
            </select>
          </label>
          <label className={styles.field}>
            <span>Laps</span>
            <input
              type="number" min={3} max={90} value={laps}
              onChange={(e) => onLapsChange(Number(e.target.value))}
            />
            <small>
              {track?.raceLaps ? `full distance ${track.raceLaps}` : "race distance unknown"}
            </small>
          </label>
          <label className={styles.field}>
            <span>Seed</span>
            <input type="number" value={seed} onChange={(e) => onSeedChange(Number(e.target.value))} />
            <small>same seed, same race</small>
          </label>
        </div>
      </header>

      <div className={styles.body}>
        <section className={styles.entryList} aria-labelledby="entry-list-heading">
          <div className={styles.sectionBar}>
            <h2 id="entry-list-heading" className={styles.sectionTitle}>Entry list</h2>
            <p className={styles.count} aria-live="polite">
              <strong>{grid.length}</strong>
              <span className={styles.countSlash}>/</span>
              {entries.length} cars
            </p>
            <div className={styles.sectionActions}>
              <button type="button" onClick={onSelectAll}>All</button>
              <button type="button" onClick={onClear}>None</button>
            </div>
          </div>

          <div className={styles.teams}>
            {byTeam.map(([team, list]) => {
              const codes = list.map((d) => d.code);
              const on = codes.filter((c) => selected.has(c)).length;
              const colour = `#${colourOf(list[0])}`;
              return (
                <section key={team} className={styles.teamGroup} data-muted={on === 0}>
                  <button
                    type="button"
                    className={styles.teamHeader}
                    onClick={() => onToggleTeam(codes, on < codes.length)}
                    aria-label={`${on === codes.length ? "Remove" : "Add"} every ${team} car`}
                  >
                    <span className={styles.teamStripe} style={{ background: colour }} />
                    <span className={styles.teamName}>{team}</span>
                    <span className={styles.teamCount}>{on}/{codes.length}</span>
                  </button>

                  <div className={styles.cards}>
                    {list.map((d) => {
                      const isOn = selected.has(d.code);
                      return (
                        <button
                          key={d.code}
                          type="button"
                          className={styles.card}
                          data-selected={isOn}
                          aria-pressed={isOn}
                          onClick={() => onToggleDriver(d.code)}
                          style={{ "--team": `#${colourOf(d)}` } as React.CSSProperties}
                        >
                          <span className={styles.cardStripe} aria-hidden="true" />
                          <span className={styles.cardNumber}>{d.number ?? "—"}</span>
                          <span className={styles.cardIdent}>
                            <span className={styles.cardCode}>{d.code}</span>
                            <span className={styles.cardName}>{fullName(d)}</span>
                          </span>
                          <span className={styles.cardCheck} data-selected={isOn} aria-hidden="true" />
                        </button>
                      );
                    })}
                  </div>
                </section>
              );
            })}
          </div>
        </section>

        <aside className={styles.rail}>
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>{track ? shortEvent(track.event) : "Circuit"}</h2>
            <dl className={styles.facts}>
              <dt>Distance</dt>
              <dd>{laps} laps{track?.raceLaps ? ` of ${track.raceLaps}` : ""}</dd>
              <dt>Air</dt>
              <dd>{air ? `${air.median.toFixed(1)} °C` : "—"}</dd>
              <dt>Track</dt>
              <dd>{trackTemp ? `${trackTemp.median.toFixed(1)} °C` : "—"}</dd>
              <dt>Tyres</dt>
              <dd>{compounds.length ? compounds.map((c) => c[0]).join(" · ") : "—"}</dd>
            </dl>
            <p className={styles.provenance}>Observed, {track?.year ?? "2026"} race weekend</p>
          </section>

          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>Starting grid</h2>
            {grid.length ? (
              <ol className={styles.slots}>
                {grid.map((e, i) => (
                  <li key={e.code} className={styles.slot} data-side={i % 2 ? "right" : "left"}>
                    <span className={styles.slotPos}>{i + 1}</span>
                    <span className={styles.slotStripe} style={{ background: `#${colourOf(e)}` }} />
                    <span className={styles.slotCode}>{e.code}</span>
                    <span className={styles.slotNum}>{e.number ?? ""}</span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className={styles.empty}>No cars selected.</p>
            )}
            <p className={styles.provenance}>Entry-list order · starts are simulated</p>
          </section>

          <div className={styles.launch}>
            {error ? <p className={styles.error} role="alert">{error}</p> : null}
            <button type="button" className={styles.startButton} disabled={!canStart} onClick={onStart}>
              Start race
              <span aria-hidden="true">→</span>
            </button>
            {!canStart ? <p className={styles.hint}>Select at least two cars.</p> : null}
          </div>
        </aside>
      </div>
    </div>
  );
}
