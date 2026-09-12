"use client";

import { useMemo } from "react";
import styles from "./gridBuilder.module.css";

export interface CatalogueDriver {
  code: string; team: string | null; colour: string | null; number: string | null;
}
export interface CatalogueTeam { team: string; colour: string }
export interface CatalogueTrackLite { event: string; slug: string; raceLaps: number | null }

/**
 * The New Race setup screen: a full-page step, not a corner widget, because picking
 * the field is the actual decision the user is making here. Every option (drivers,
 * teams, tracks, lap counts) comes from the built catalogue -- selecting none of a
 * team's drivers simply leaves that team out of the generated race.
 */
export function GridBuilder({
  drivers, teams, tracks, trackSlug, laps, seed, selected,
  onTrackChange, onLapsChange, onSeedChange, onToggleDriver, onSelectAll, onClear, onStart, error,
}: {
  drivers: CatalogueDriver[];
  teams: CatalogueTeam[];
  tracks: CatalogueTrackLite[];
  trackSlug: string;
  laps: number;
  seed: number;
  selected: Set<string>;
  onTrackChange: (slug: string) => void;
  onLapsChange: (n: number) => void;
  onSeedChange: (n: number) => void;
  onToggleDriver: (code: string) => void;
  onSelectAll: () => void;
  onClear: () => void;
  onStart: () => void;
  error: string | null;
}) {
  const byTeam = useMemo(() => {
    const groups = new Map<string, CatalogueDriver[]>();
    for (const d of drivers) {
      const key = d.team ?? "Independent";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(d);
    }
    return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [drivers]);

  const teamColour = (team: string | null) =>
    (team && teams.find((t) => t.team === team)?.colour) || "AEAEAE";

  return (
    <div className={styles.wrap}>
      <header className={styles.header}>
        <div className={styles.titleBlock}>
          <p className={styles.kicker}>TrackShift 2026 · New Race</p>
          <h1 className={styles.title}>Build the grid</h1>
        </div>
        <div className={styles.setupRow}>
          <label className={styles.field}>
            <span>Track</span>
            <select value={trackSlug} onChange={(e) => onTrackChange(e.target.value)}>
              {tracks.map((t) => <option key={t.slug} value={t.slug}>{t.event}</option>)}
            </select>
          </label>
          <label className={styles.field}>
            <span>Laps</span>
            <input type="number" min={3} max={90} value={laps} onChange={(e) => onLapsChange(Number(e.target.value))} />
          </label>
          <label className={styles.field}>
            <span>Seed</span>
            <input type="number" value={seed} onChange={(e) => onSeedChange(Number(e.target.value))} />
          </label>
          <div className={styles.countBadge}>
            {selected.size} car{selected.size === 1 ? "" : "s"} selected
          </div>
        </div>
      </header>

      <div className={styles.toolbar}>
        <button type="button" onClick={onSelectAll}>Select all</button>
        <button type="button" onClick={onClear}>Clear</button>
        {error ? <span className={styles.error}>{error}</span> : null}
        <button type="button" className={styles.startButton} disabled={selected.size < 2} onClick={onStart}>
          Start race →
        </button>
      </div>

      <div className={styles.grid}>
        {byTeam.map(([team, list]) => (
          <section key={team} className={styles.teamGroup}>
            <h2 className={styles.teamName} style={{ borderColor: `#${teamColour(team)}` }}>
              <span className={styles.swatch} style={{ background: `#${teamColour(team)}` }} />
              {team}
            </h2>
            <div className={styles.cards}>
              {list.map((d) => {
                const isOn = selected.has(d.code);
                return (
                  <button
                    key={d.code}
                    type="button"
                    className={styles.card}
                    data-selected={isOn}
                    onClick={() => onToggleDriver(d.code)}
                    style={isOn ? { borderColor: `#${teamColour(team)}` } : undefined}
                  >
                    <span className={styles.cardCheck} data-selected={isOn} />
                    <span className={styles.cardCode}>{d.code}</span>
                    {d.number ? <span className={styles.cardNumber}>#{d.number}</span> : null}
                  </button>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
