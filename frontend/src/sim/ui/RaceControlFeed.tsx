"use client";

import type { RaceEvent } from "../contract/types";
import styles from "./panels.module.css";

function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

/** Shows every event up to the current session time, most recent first, capped to a
 * scrollable window rather than the plan's full 25-kind classifier -- see the
 * classify() subset in scripts/simdata/rcm.py for exactly which message kinds are
 * recognised versus falling through to "other". */
export function RaceControlFeed({ events, sessionTime }: { events: RaceEvent[]; sessionTime: number }) {
  // Pre-race administrative chatter (pit lane opening, formation-lap notices) carries
  // a negative session time once normalised to lights-out at t=0 -- genuine data, but
  // not meaningful race-progress commentary, so the feed starts at the green flag.
  const visible = events
    .filter((e) => e.sessionTime >= 0 && e.sessionTime <= sessionTime)
    .sort((a, b) => b.sessionTime - a.sessionTime)
    .slice(0, 40);
  return (
    <div className={styles.panel}>
      <div className={styles.panelTitle}>Race control</div>
      <ul className={styles.feed}>
        {visible.map((e, i) => (
          <li key={`${e.sessionTime}-${i}`} data-kind={e.kind}>
            <span className={styles.feedTime}>{fmtTime(e.sessionTime)}</span>
            <span>{e.message}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
