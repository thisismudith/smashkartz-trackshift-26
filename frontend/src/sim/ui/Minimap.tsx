"use client";

/**
 * A flat plan of the circuit with the field on it, so a viewer can tell WHERE on
 * the lap the chase camera currently is.
 *
 * SVG rather than a second WebGL view on purpose: this is a few hundred static
 * path points and up to 22 dots, which the DOM draws for free, and the plan
 * measured that screen-covering GL geometry is what collapses this GPU. The 3D
 * scene keeps the whole frame budget.
 *
 * The Overtake geometry is drawn here as well as in 3D, because in 3D a line
 * across the road is only visible when the camera happens to be pointing at it.
 * On the plan both lines are in view the whole time, which is what makes "the
 * detection line is behind us, the zone is coming" readable at a glance.
 *
 * Everything is projected with ONE transform derived from the ring's own bounds,
 * so a car dot and the track under it cannot disagree.
 */
import { useMemo } from "react";
import { HAAS } from "@/lib/palette";
import type { DashboardSnapshot, TrackModel } from "../contract/types";
import { trackPointAt } from "../data/manifest";
import type { OvertakeGeometry } from "../render/overtakeZones";
import styles from "./sim.module.css";

const VIEW = 200;
const PAD = 10;

interface Projection {
  toX: (x: number) => number;
  toY: (y: number) => number;
}

/** One transform for the ring and everything drawn on it. Y is flipped because
 * SVG's y grows downward while the track frame's grows upward. */
export function ringProjection(track: Pick<TrackModel, "x" | "y">): Projection | null {
  const n = track.x.length;
  if (!n) return null;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (let i = 0; i < n; i++) {
    if (track.x[i] < minX) minX = track.x[i];
    if (track.x[i] > maxX) maxX = track.x[i];
    if (track.y[i] < minY) minY = track.y[i];
    if (track.y[i] > maxY) maxY = track.y[i];
  }
  const spanX = maxX - minX, spanY = maxY - minY;
  const span = Math.max(spanX, spanY);
  if (!(span > 0)) return null;
  const scale = (VIEW - PAD * 2) / span;
  // Centre the circuit in the box rather than anchoring it, so a long thin
  // layout (Monza) is not jammed against one edge.
  const offX = PAD + (VIEW - PAD * 2 - spanX * scale) / 2;
  const offY = PAD + (VIEW - PAD * 2 - spanY * scale) / 2;
  return {
    toX: (x) => offX + (x - minX) * scale,
    toY: (y) => VIEW - (offY + (y - minY) * scale),
  };
}

/** The ring as one closed SVG path, sampled at most `maxPoints` times. */
export function ringPath(
  track: Pick<TrackModel, "x" | "y">, projection: Projection, maxPoints = 320,
): string {
  const n = track.x.length;
  if (!n) return "";
  // ceil, not floor: flooring the step lets the emitted count exceed maxPoints
  // (6000 vertices at step 18 is 334 points for a cap of 320).
  const step = Math.max(1, Math.ceil(n / maxPoints));
  const parts: string[] = [];
  for (let i = 0; i < n; i += step) {
    parts.push(`${i === 0 ? "M" : "L"}${projection.toX(track.x[i]).toFixed(2)} ${projection.toY(track.y[i]).toFixed(2)}`);
  }
  return `${parts.join(" ")} Z`;
}

/** A short tick across the track at `stationM`, for a timing or Overtake line. */
function tickAt(
  track: TrackModel, projection: Projection, stationM: number, halfLengthPx = 5,
): { x1: number; y1: number; x2: number; y2: number } | null {
  const point = trackPointAt(track, stationM);
  if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) return null;
  const cx = projection.toX(point.x);
  const cy = projection.toY(point.y);
  // Perpendicular to travel. The projection flips y, so the normal's y flips too.
  const nx = Math.cos(point.heading + Math.PI / 2);
  const ny = -Math.sin(point.heading + Math.PI / 2);
  return {
    x1: cx - nx * halfLengthPx, y1: cy - ny * halfLengthPx,
    x2: cx + nx * halfLengthPx, y2: cy + ny * halfLengthPx,
  };
}

export function Minimap({
  track, dashboard, geometry, selectedDriver, onSelectDriver,
}: {
  track: TrackModel | null;
  dashboard: DashboardSnapshot | null;
  geometry: OvertakeGeometry | null;
  selectedDriver: string | null;
  onSelectDriver?: (driver: string) => void;
}) {
  const projection = useMemo(() => (track ? ringProjection(track) : null), [track]);
  const path = useMemo(
    () => (track && projection ? ringPath(track, projection) : ""),
    [track, projection],
  );

  if (!track || !projection) {
    return <p className={styles.minimapNote}>circuit not loaded</p>;
  }

  const detection = geometry?.detectionM !== null && geometry?.detectionM !== undefined
    ? tickAt(track, projection, geometry.detectionM, 6)
    : null;

  return (
    <svg
      viewBox={`0 0 ${VIEW} ${VIEW}`}
      className={styles.minimapSvg}
      role="img"
      aria-label="Circuit plan with car positions"
    >
      {/* the road itself */}
      <path d={path} fill="none" stroke={HAAS.grey} strokeWidth={3.2} opacity={0.35} />
      <path d={path} fill="none" stroke={HAAS.grey} strokeWidth={0.6} opacity={0.9} />

      {/* activation zones: a thicker arc of the ring between the two stations */}
      {geometry?.zones.map((zone) => {
        if (zone.activationM === null || zone.endM === null) return null;
        const L = track.lengthMetres;
        const span = ((zone.endM - zone.activationM) % L + L) % L;
        if (!(span > 0)) return null;
        const steps = Math.max(2, Math.ceil(span / 20));
        const pts: string[] = [];
        for (let i = 0; i <= steps; i++) {
          const p = trackPointAt(track, zone.activationM + (span * i) / steps);
          pts.push(`${i === 0 ? "M" : "L"}${projection.toX(p.x).toFixed(2)} ${projection.toY(p.y).toFixed(2)}`);
        }
        return (
          <path
            key={`zone-${zone.zone}`}
            d={pts.join(" ")}
            fill="none"
            stroke={HAAS.red}
            strokeWidth={3.4}
            opacity={0.3}
            strokeLinecap="round"
          />
        );
      })}

      {/* activation lines: dashed, matching the 3D treatment for a proxy value */}
      {geometry?.zones.map((zone) => {
        if (zone.activationM === null) return null;
        const t = tickAt(track, projection, zone.activationM, 5);
        if (!t) return null;
        return (
          <line
            key={`act-${zone.zone}`}
            x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2}
            stroke={HAAS.red} strokeWidth={1.2} strokeDasharray="1.6 1.2"
          />
        );
      })}

      {/* detection line: solid and bright, a measured position of a stated rule */}
      {detection ? (
        <line
          x1={detection.x1} y1={detection.y1} x2={detection.x2} y2={detection.y2}
          stroke={HAAS.white} strokeWidth={1.6}
        />
      ) : null}

      {/* start/finish */}
      {(() => {
        const t = tickAt(track, projection, track.timingLines.sf, 4);
        return t ? (
          <line x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2}
                stroke={HAAS.white} strokeWidth={1} opacity={0.5} />
        ) : null;
      })()}

      {/* the field. lapProgress is what the board publishes, so the dot and the
          leaderboard row can never disagree about where a car is. */}
      {dashboard?.leaderboard.map((row) => {
        if (row.status === "pit" || row.status === "retired") return null;
        const p = trackPointAt(track, row.lapProgress * track.lengthMetres);
        if (!Number.isFinite(p.x)) return null;
        const focused = row.driver === selectedDriver;
        return (
          <circle
            key={row.driver}
            cx={projection.toX(p.x)}
            cy={projection.toY(p.y)}
            r={focused ? 3.4 : 2}
            fill={focused ? HAAS.red : HAAS.white}
            opacity={focused ? 1 : 0.75}
            stroke={focused ? HAAS.white : "none"}
            strokeWidth={focused ? 0.8 : 0}
            onClick={onSelectDriver ? () => onSelectDriver(row.driver) : undefined}
            style={onSelectDriver ? { cursor: "pointer" } : undefined}
          >
            <title>{`${row.position}. ${row.driver}`}</title>
          </circle>
        );
      })}
    </svg>
  );
}
