"use client";

import { useState, type ReactNode } from "react";
import styles from "./sim.module.css";

/** A HUD panel that can be folded away to a title bar, so the circuit is never
 * permanently hidden behind a table. Collapse state is per-panel and local. */
export function CollapsiblePanel({
  title, corner, children, defaultOpen = true, badge,
}: {
  title: string;
  corner: "topRight" | "bottomRight" | "left";
  children: ReactNode;
  defaultOpen?: boolean;
  badge?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className={`${styles.dock} ${styles[corner]}`} data-open={open}>
      <button
        type="button"
        className={styles.dockHeader}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span>{title}</span>
        {badge ? <span className={styles.dockBadge}>{badge}</span> : null}
        <span className={styles.dockChevron} data-open={open}>{open ? "\u2212" : "+"}</span>
      </button>
      {open ? <div className={styles.dockBody}>{children}</div> : null}
    </section>
  );
}
