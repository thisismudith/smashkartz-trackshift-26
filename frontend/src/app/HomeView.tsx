/**
 * The hub.
 *
 * It replaces four hard-coded cards that all read "planned" and linked nowhere. The counts are
 * computed from the artifacts at render time rather than typed in, so the page cannot drift
 * away from what has actually been built — if a circuit is added or dropped, this changes.
 *
 * Surfaces whose models do not exist still appear, but their badge names the blocking model
 * instead of saying "planned": a reader can tell the difference between work not started and
 * work waiting on something specific.
 */
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { defaultSimSource } from "@/sim/data/source";
import type { Catalogue } from "@/sim/data/catalogue";
import s from "./page.module.css";

interface Stats {
  circuits: number;
  sessions: number;
  driverLaps: number;
}

const SURFACES = [
  {
    href: "/sessions",
    name: "Sessions",
    note: "Estimated energy, overtaking and pace for every built session — with each circuit's measured data faults stated up front.",
  },
  {
    href: "/rules",
    name: "Regulation",
    note: "The two speed-dependent power-envelope curves and the three energy quantities, every value badged with its verification state.",
  },
  {
    href: "/insights",
    name: "Fitted parameters",
    note: "Cross-circuit league tables: overtaking difficulty, pace trend and driver offsets — each drawn with the confidence interval it was fitted to.",
  },
  {
    href: "/lab",
    name: "Parameter lab",
    note: "Every input the race model takes, with the uncertainty it was fitted to.",
  },
  {
    href: "/sim",
    name: "Race simulator",
    note: "Full-race replay in 3D from the telemetry, with per-driver energy estimates and race control.",
  },
  {
    href: "/decision",
    name: "Decision engine",
    note: "Shadow price, pass probability, rival belief and the recommended action.",
    badge: "awaiting 4 models",
  },
];

export default function HomeView() {
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      const [cat, index] = await Promise.all([
        defaultSimSource.catalogue<Catalogue>(),
        defaultSimSource.index(),
      ]);
      if (!live) return;
      const sessions = Object.values(index.sessions).reduce((n, v) => n + Object.keys(v).length, 0);
      // An exact lap count needs every session manifest, which is far too much to fetch for a
      // headline figure, so this is raceLaps x entries and is marked with a "~" on screen.
      // Deliberately NOT paired with a coverage percentage: the denominator here is an estimate,
      // and a percentage computed against an estimate would read as a measurement.
      const driverLaps = cat.tracks.reduce(
        (n, t) => n + (t.raceLaps ?? 0) * t.entries.length,
        0,
      );
      setStats({ circuits: Object.keys(index.tracks).length, sessions, driverLaps });
    })().catch(() => {
      /* the hero still reads correctly without counts; a failed fetch must not blank the page */
    });
    return () => {
      live = false;
    };
  }, []);

  return (
    <main className={s.main}>
      <header className={s.hero}>
        <p className={s.kicker}>TrackShift 2026 · PS1</p>
        <h1 className={s.title}>
          Smash<em>Kartz</em>
        </h1>
        <p className={s.lede}>
          E-Delta — a segment-level decision engine for where, not just whether, a 2026 F1 car
          should spend or recover electrical energy in a battle.
        </p>
        <nav className={s.nav}>
          <Link href="/sessions" className={s.button}>
            Browse sessions
          </Link>
          <Link href="/about" className={s.buttonQuiet}>
            About the project
          </Link>
        </nav>
      </header>

      {stats ? (
        <section className={s.stats} aria-label="Built data">
          <Stat value={stats.circuits} label="Circuits" />
          <Stat value={stats.sessions} label="Sessions built" />
          <Stat value={stats.driverLaps.toLocaleString()} label="Driver-laps" approx />
          <Stat value={2026} label="Season" />
        </section>
      ) : null}

      <section className={s.grid} aria-label="Surfaces">
        {SURFACES.map((m) => (
          <Link key={m.href} href={m.href} className={s.card}>
            <h2>{m.name}</h2>
            <p>{m.note}</p>
            {m.badge ? <span className={s.badgePending}>{m.badge}</span> : null}
          </Link>
        ))}
      </section>

      <footer className={s.footer}>
        Public telemetry only. Energy state is inferred, never measured — provenance is always
        shown. No regulation value in this build has been traced to a specific FIA article; every
        such number is badged unverified where it appears.
      </footer>
    </main>
  );
}

function Stat({ value, label, approx }: { value: string | number; label: string; approx?: boolean }) {
  return (
    <div className={s.stat}>
      <span className={s.statValue}>
        {approx ? <span className={s.approx}>≈</span> : null}
        {value}
      </span>
      <span className={s.statLabel}>{label}</span>
    </div>
  );
}
