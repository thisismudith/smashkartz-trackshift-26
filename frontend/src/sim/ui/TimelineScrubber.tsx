"use client";

import type { NeutralisationInterval } from "../contract/types";
import styles from "./panels.module.css";

const KIND_LABEL: Record<NeutralisationInterval["kind"], string> = {
  SC: "SC", VSC: "VSC", RED: "RED FLAG",
};

export function TimelineScrubber({
  sessionTime, duration, neutralisations, onSeek,
}: {
  sessionTime: number;
  duration: number;
  neutralisations: NeutralisationInterval[];
  onSeek: (t: number) => void;
}) {
  return (
    <div className={styles.scrubberWrap}>
      <div className={styles.scrubberTrack}>
        {neutralisations.map((n, i) => (
          <div
            key={i}
            className={styles.marker}
            data-kind={n.kind}
            style={{
              left: `${(n.start / duration) * 100}%`,
              width: `${Math.max(0.3, ((n.end - n.start) / duration) * 100)}%`,
            }}
            title={`${KIND_LABEL[n.kind]} ${Math.round(n.start)}s–${Math.round(n.end)}s`}
          />
        ))}
      </div>
      <input
        type="range"
        min={0}
        max={duration || 1}
        step={1}
        value={sessionTime}
        onChange={(e) => onSeek(Number(e.target.value))}
        className={styles.scrubberInput}
      />
    </div>
  );
}
