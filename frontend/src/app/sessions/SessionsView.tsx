/**
 * The session browser.
 *
 * Two sources are deliberately shown side by side: the CATALOGUE says what the raw mirror
 * holds, the INDEX says what has actually been built into artifacts. A circuit can list five
 * sessions and have one replay pack, and pretending otherwise means offering a link that dies.
 *
 * It also carries the data-quality warnings from UI.md section 1.4. Three of the thirteen
 * circuits have measured faults severe enough to make a position-based view wrong rather than
 * merely sparse — Monaco's Race has almost no positions at all, Hungary's Race holds a stale
 * anchor for two thirds of its laps, and Suzuka's ring self-crosses. Those are stated on the
 * card, before the user opens anything, rather than discovered inside a chart.
 */
"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { defaultSimSource, type SimIndex } from "@/sim/data/source";
import type { Catalogue, CatalogueTrack } from "@/sim/data/catalogue";
import s from "./sessions.module.css";

/**
 * Measured faults, keyed by track slug and session. Each was proven with numbers during the
 * September audit and independently re-measured. They live here, next to the browser that
 * must disclose them, until the build pipeline emits a per-session quality block of its own
 * (UI.md section 1.4, build step 7) — at which point this table is replaced by that data.
 */
const KNOWN_FAULTS: Record<string, { session: string; severity: "blocking" | "caution"; text: string }[]> = {
  "monaco-grand-prix": [
    {
      session: "Race",
      severity: "blocking",
      text: "1324 of 1452 laps carry no position data. Coverage is all-or-nothing per lap; the 128 laps that do have positions are accurate. Position-based views are unavailable for this session.",
    },
  ],
  "hungarian-grand-prix": [
    {
      session: "Race",
      severity: "blocking",
      text: "Laps 15–70 are a stale-anchor sample-and-hold: only 53.6% of position samples per lap are distinct, and the integrated path runs 10% long. 920 of 1136 clean laps are affected, and because the fastest laps are late-race a naive fastest-lap selection lands inside the corrupt window.",
    },
  ],
  "japanese-grand-prix": [
    {
      session: "Race",
      severity: "caution",
      text: "The circuit's ring self-crosses: 529 vertex pairs lie within 12 m of each other but more than 150 m apart in station. Distance-along-lap can alias across the crossover unless projection carries state.",
    },
  ],
};

export default function SessionsView() {
  const [cat, setCat] = useState<Catalogue | null>(null);
  const [index, setIndex] = useState<SimIndex | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([defaultSimSource.catalogue<Catalogue>(), defaultSimSource.index()])
      .then(([c, i]) => {
        if (!live) return;
        setCat(c);
        setIndex(i);
      })
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, []);

  const tracks = useMemo(() => {
    if (!cat || !index) return [];
    return [...cat.tracks]
      .map((t) => ({ track: t, built: index.sessions[t.slug] ?? {} }))
      .sort((a, b) => a.track.event.localeCompare(b.track.event));
  }, [cat, index]);

  const builtCount = tracks.reduce((n, t) => n + Object.keys(t.built).length, 0);

  if (error) {
    return (
      <main className={s.main}>
        <p className={s.lede}>Could not load the session catalogue: {error}</p>
      </main>
    );
  }
  if (!cat || !index) {
    return (
      <main className={s.main}>
        <p className={s.lede}>Loading sessions…</p>
      </main>
    );
  }

  return (
    <main className={s.main}>
      <header className={s.head}>
        <p className={s.kicker}>
          {tracks.length} circuits · {builtCount} built sessions
        </p>
        <h1 className={s.title}>
          Session <em>Browser</em>
        </h1>
        <p className={s.lede}>
          Everything the 2026 mirror holds, and what has actually been built from it. A session
          with a replay pack can be opened; one without is listed so the gap is visible rather
          than silent. Where the telemetry itself has a measured fault, the card says so before
          you open it.
        </p>
      </header>

      <ul className={s.grid}>
        {tracks.map(({ track, built }) => (
          <TrackCard key={track.slug} track={track} built={Object.keys(built)} />
        ))}
      </ul>
    </main>
  );
}

function TrackCard({ track, built }: { track: CatalogueTrack; built: string[] }) {
  const faults = KNOWN_FAULTS[track.slug] ?? [];
  const w = track.weather;

  return (
    <li className={s.card}>
      <div className={s.cardHead}>
        <h2 className={s.cardTitle}>{track.event.replace(/\s+Grand Prix$/i, "")}</h2>
        <span className={s.cardYear}>{track.year}</span>
      </div>

      <dl className={s.facts}>
        <div>
          <dt>Race laps</dt>
          <dd>{track.raceLaps ?? "—"}</dd>
        </div>
        <div>
          <dt>Entries</dt>
          <dd>{track.entries.length}</dd>
        </div>
        <div>
          <dt>Track temp</dt>
          <dd>{w ? `${w.trackTempC.median.toFixed(0)} °C` : "—"}</dd>
        </div>
        <div>
          <dt>Compounds</dt>
          <dd>{track.tyres?.compounds.map((c) => c[0]).join(" ") ?? "—"}</dd>
        </div>
      </dl>

      <div className={s.sessions}>
        {track.sessions.map((sess) => {
          const isBuilt = built.includes(sess);
          const fault = faults.find((f) => f.session === sess);
          if (!isBuilt) {
            return (
              <span key={sess} className={s.sessionChip} data-state="unbuilt" title="No replay pack built for this session">
                {sess}
              </span>
            );
          }
          return (
            <Link
              key={sess}
              href={`/sessions/${track.slug}/${encodeURIComponent(sess)}`}
              className={s.sessionChip}
              data-state={fault ? fault.severity : "built"}
            >
              {sess}
              {fault ? <span className={s.faultDot} aria-hidden="true" /> : null}
            </Link>
          );
        })}
      </div>

      {faults.length > 0 ? (
        <ul className={s.faults}>
          {faults.map((f) => (
            <li key={f.session} className={s.fault} data-severity={f.severity}>
              <span className={s.faultHead}>
                {f.session} — {f.severity === "blocking" ? "data fault" : "caution"}
              </span>
              <span className={s.faultText}>{f.text}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}
