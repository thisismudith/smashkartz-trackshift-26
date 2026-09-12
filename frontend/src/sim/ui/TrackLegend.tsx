"use client";

import styles from "./sim.module.css";

/** Explains the surfaces and car states on the 3D stage. Kept inside the Haas
 * palette; the only colours outside it are the per-team car colours, which are the
 * one agreed exception. */
export function TrackLegend({ hasPitLane }: { hasPitLane: boolean }) {
  return (
    <div className={styles.legend}>
      <span className={styles.legendTitle}>Legend</span>
      <span className={styles.legendItem}>
        <i className={styles.swatchTrack} /> Racing surface
      </span>
      {hasPitLane ? (
        <span className={styles.legendItem}>
          <i className={styles.swatchPit} /> Pit lane
        </span>
      ) : null}
      <span className={styles.legendItem}>
        <i className={styles.swatchCar} /> Car (team colour)
      </span>
      <span className={styles.legendNote}>Track width &amp; pit width are RULE values, not measured</span>
      <span className={styles.legendNote}>
        Space play/pause · middle-click lock/free camera · when free: drag to orbit,
        right-drag to pan, scroll to zoom at the cursor
      </span>
    </div>
  );
}
