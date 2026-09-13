"use client";

import styles from "./sim.module.css";

/** Explains the surfaces and car states on the 3D stage. Kept inside the Haas
 * palette; the only colours outside it are the per-team car colours, which are the
 * one agreed exception. */
export function TrackLegend({
  hasPitLane, hasDetection = false, hasZones = false,
}: {
  hasPitLane: boolean;
  /** Whether this circuit has a sourced Detection Line to explain. */
  hasDetection?: boolean;
  /** Whether it has at least one activation zone. */
  hasZones?: boolean;
}) {
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
      {hasDetection ? (
        <span className={styles.legendItem}>
          <i className={styles.swatchDetection} /> Detection Line
          <em className={styles.legendProv}>measured</em>
        </span>
      ) : null}
      {hasZones ? (
        <>
          <span className={styles.legendItem}>
            <i className={styles.swatchActivation} /> Activation Line
            <em className={styles.legendProv}>proxy</em>
          </span>
          <span className={styles.legendItem}>
            <i className={styles.swatchZone} /> Overtake zone
            <em className={styles.legendProv}>proxy</em>
          </span>
        </>
      ) : null}
      <span className={styles.legendNote}>Track width &amp; pit width are RULE values, not measured</span>
      {hasZones ? (
        <span className={styles.legendNote}>
          Solid = a measured position of a stated rule. Dashed = a development
          stand-in derived from 2022-25 DRS, not a 2026 Overtake fact.
        </span>
      ) : null}
      <span className={styles.legendNote}>
        Space play/pause · middle-click lock/free camera · when free: drag to orbit,
        right-drag to pan, scroll to zoom at the cursor
      </span>
    </div>
  );
}
