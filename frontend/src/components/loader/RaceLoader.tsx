"use client";

import { useEffect, useRef, useState } from "react";
import HaasCarTop from "./HaasCarTop";
import { LIGHTS, TIMELINE } from "./physics/constants";
import { runLoader } from "./runtime";
import styles from "./loader.module.css";

/** Wordmark, one span per letter so the runtime can ignite them as the rear axle passes. */
const LETTERS: ReadonlyArray<{ ch: string; red: boolean }> = [
  ...[..."SMASH"].map((ch) => ({ ch, red: false })),
  ...[..."KARTZ"].map((ch) => ({ ch, red: true })),
];

const LIGHT_COLUMNS = Array.from({ length: LIGHTS.count }, (_, i) => i);

/**
 * F1 standing-start intro animation (NOT the simulator).
 *
 * Mounted once in the root layout, so it plays on every full page load and never on App Router
 * navigation. The SSR frame is the static pose — car on the grid, gantry dark, name hidden — and
 * all physics, canvases and randomness start inside the effect. Unmounts itself when the overlay
 * fade ends, or on the fail-safe timer. The overlay is opaque and captures pointer events until it
 * starts fading, so nothing underneath can be clicked blind; page scroll is deliberately not locked
 * (a ~4 s overlay does not justify the scrollbar reflow that overflow:hidden causes on release).
 */
export default function RaceLoader() {
  const [mounted, setMounted] = useState(true);
  const overlayRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const marksRef = useRef<HTMLCanvasElement>(null);
  const smokeRef = useRef<HTMLCanvasElement>(null);
  const carRef = useRef<HTMLDivElement>(null);
  const nameRef = useRef<HTMLDivElement>(null);
  const taglineRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const gantryRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!mounted) return;
    const overlay = overlayRef.current;
    const stage = stageRef.current;
    const marks = marksRef.current;
    const smoke = smokeRef.current;
    const car = carRef.current;
    const name = nameRef.current;
    const tagline = taglineRef.current;
    const grid = gridRef.current;
    const gantry = gantryRef.current;
    if (!overlay || !stage || !marks || !smoke || !car || !name || !tagline || !grid || !gantry) return;

    const done = () => setMounted(false);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") done();
    };
    window.addEventListener("keydown", onKeyDown);

    let dispose: () => void;
    let failSafe: number;

    if (reduced) {
      // Static composition: car + name visible at once, CSS fade, no canvases, no lights, no physics.
      overlay.classList.add(styles.reduced);
      const onEnd = (e: AnimationEvent) => {
        if (e.target === overlay) done();
      };
      overlay.addEventListener("animationend", onEnd);
      failSafe = window.setTimeout(done, TIMELINE.reducedMotionFailSafeMs);
      dispose = () => {
        overlay.removeEventListener("animationend", onEnd);
        overlay.classList.remove(styles.reduced);
      };
    } else {
      failSafe = window.setTimeout(done, TIMELINE.failSafeMs);
      dispose = runLoader(
        {
          overlay,
          stage,
          marks,
          smoke,
          car,
          letters: Array.from(name.querySelectorAll<HTMLElement>("[data-letter]")),
          tagline,
          grid,
          gantry,
          lightColumns: Array.from(gantry.querySelectorAll<HTMLElement>("[data-light]")),
        },
        {
          classes: {
            pushIn: styles.pushIn,
            kick: styles.kick,
            lit: styles.lit,
            shown: styles.shown,
            fading: styles.fading,
            gone: styles.gone,
            dimmed: styles.dimmed,
            clearing: styles.clearing,
          },
          onDone: done,
        },
      );
    }

    return () => {
      dispose();
      window.clearTimeout(failSafe);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [mounted]);

  if (!mounted) return null;

  return (
    <div ref={overlayRef} className={styles.overlay} role="status" aria-live="polite">
      <span className={styles.srOnly}>Loading SmashKartz</span>

      <div ref={stageRef} className={styles.stage}>
        <canvas ref={marksRef} className={styles.marks} aria-hidden="true" />

        {/* grid slot: an open L, not a box — lateral line ahead of the front wheels + one side line */}
        <div ref={gridRef} className={styles.grid} aria-hidden="true">
          <div className={styles.gridFront} />
          <div className={styles.gridSide} />
        </div>

        {/* wordmark centred on the car axis, so the two rubber stripes bracket it */}
        <div className={styles.brand} aria-hidden="true">
          <div ref={nameRef} className={styles.name}>
            {LETTERS.map((l, i) => (
              <span key={i} data-letter="" className={l.red ? styles.red : undefined}>
                {l.ch}
              </span>
            ))}
          </div>
        </div>
        {/* tagline sits below the lower stripe, never on it */}
        <div ref={taglineRef} className={styles.tagline} aria-hidden="true">
          E-Delta · Energy &amp; Overtake Intelligence
        </div>

        <div ref={carRef} className={styles.car} aria-hidden="true">
          <HaasCarTop />
        </div>

        <canvas ref={smokeRef} className={styles.smoke} aria-hidden="true" />

        {/* start lights: five columns of two lamps, rendered dark server-side so there is no pop-in */}
        <div ref={gantryRef} className={styles.gantry} aria-hidden="true">
          {LIGHT_COLUMNS.map((i) => (
            <div key={i} className={styles.lightCol} data-light={i}>
              <span className={styles.lamp}>
                <i className={styles.bulb} />
              </span>
              <span className={styles.lamp}>
                <i className={styles.bulb} />
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
