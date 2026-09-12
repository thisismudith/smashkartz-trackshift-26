"use client";

import styles from "./sim.module.css";

export interface CatalogueTrack {
  event: string;
  slug: string;
  year: number | null;
  sessions: string[];
  raceLaps: number | null;
  sprintLaps: number | null;
}

export interface Selection {
  trackSlug: string;
  session: string;
}

/**
 * Every option here comes from the built catalogue/index (one season per build, via
 * scripts/simdata/catalogue.py and build_sim_data.py) -- a track only appears if it
 * is actually present in data/, and a session only appears if a replay pack has
 * actually been built for it. Nothing in this list is typed in by hand.
 */
export function SessionSelector({
  tracks, builtSessions, value, onChange,
}: {
  tracks: CatalogueTrack[];
  builtSessions: Record<string, string[]>; // trackSlug -> session names actually built
  value: Selection;
  onChange: (next: Selection) => void;
}) {
  const years = [...new Set(tracks.map((t) => t.year).filter((y): y is number => y !== null))].sort();
  const available = tracks.filter((t) => (builtSessions[t.slug]?.length ?? 0) > 0);
  const currentTrack = available.find((t) => t.slug === value.trackSlug) ?? available[0];
  const sessionsForTrack = currentTrack ? builtSessions[currentTrack.slug] ?? [] : [];

  return (
    <div className={styles.controls}>
      <label>
        Year
        <select
          value={currentTrack?.year ?? ""}
          onChange={() => { /* single year today; kept as a real control for when data/ gains more */ }}
        >
          {years.map((y) => <option key={y} value={y}>{y}</option>)}
        </select>
      </label>
      <label>
        Track
        <select
          value={currentTrack?.slug ?? ""}
          onChange={(e) => {
            const t = available.find((tr) => tr.slug === e.target.value);
            if (!t) return;
            const sessions = builtSessions[t.slug] ?? [];
            onChange({ trackSlug: t.slug, session: sessions.includes(value.session) ? value.session : sessions[0] });
          }}
        >
          {available.map((t) => <option key={t.slug} value={t.slug}>{t.event}</option>)}
        </select>
      </label>
      <label>
        Session
        <select
          value={value.session}
          onChange={(e) => onChange({ trackSlug: value.trackSlug, session: e.target.value })}
        >
          {sessionsForTrack.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </label>
    </div>
  );
}
