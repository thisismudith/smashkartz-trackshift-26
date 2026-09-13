"use client";

import { useState, type ReactNode } from "react";
import styles from "./sim.module.css";

/**
 * A left-rail HUD panel that folds to its title bar.
 *
 * Distinct from CollapsiblePanel, which is absolutely positioned into a screen
 * corner: these stack in the rail's flex column, so they must size to content
 * rather than to a viewport fraction. Sharing one component for both would mean
 * one of the two layouts fighting `position: absolute`.
 *
 * `hidden` removes the panel entirely rather than collapsing it, which is what
 * the HUD's own show/hide toggles drive -- a folded panel still costs a title
 * bar, and with six of them that is most of the rail.
 */
export function RailPanel({
  title, children, defaultOpen = true, hidden = false, badge,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
  hidden?: boolean;
  badge?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (hidden) return null;
  return (
    <section className={styles.railPanel} data-open={open}>
      <button
        type="button"
        className={styles.railHeader}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={styles.railTitle}>{title}</span>
        {badge ? <span className={styles.railBadge}>{badge}</span> : null}
        <span className={styles.railChevron}>{open ? "−" : "+"}</span>
      </button>
      {open ? <div className={styles.railBody}>{children}</div> : null}
    </section>
  );
}
